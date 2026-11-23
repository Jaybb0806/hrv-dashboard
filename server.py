import argparse
import bisect
import csv
import io
import json
import math
import os
import queue
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import export
import hrv
from store import Store

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
PHASES = ("idle", "baseline", "music", "post", "done")
MODES = ("ppi", "hr")
LIVE_WINDOW_S = 60
PULSE_WINDOW_S = 15
PULSE_MIN_CLEAN = 0.8
PULSE_HOLD_S = 10
PULSE_OFF_CLEAN = 0.5
HOLD_S = 120
HOLD_MIN_S = 30
MONITOR_BEATS_S = 30
HR_HISTORY_S = 600
PHASE_TRIM_S = 30
TREND_STEP_S = 5
ONLINE_S, STALE_S = 5, 30
ADMIN_TOKEN = os.environ.get("HRV_ADMIN_TOKEN", "")

class Sensor:
    def __init__(self, sid, participant, gateway=None):
        self.id = sid
        self.participant = participant
        self.gateway = gateway
        self.reset()
        self.hr = self.rssi = self.bat = None
        self.last_seen = None

    def reset(self):
        self.beats = []
        self.times = []
        self.clf = hrv.BeatClassifier()
        self.trend = []
        self.skin_since = None
        self.worn = None
        self.held_hr = None
        self.held_live = None
        self.hr_since = self.live_since = None

    def add(self, t, ppi, err, blocker, contact):
        if self.times and t <= self.times[-1]:
            t = self.times[-1] + 0.001
        ok, diff = self.clf.classify(ppi, err, blocker, contact)
        self.beats.append(hrv.Beat(t, ppi, ok, diff))
        self.times.append(t)
        return t

    def window(self, t0, t1):
        i = bisect.bisect_left(self.times, t0)
        j = bisect.bisect_right(self.times, t1)
        return self.beats[i:j]

    def on_skin(self, now):

        recent = self.window(now - PULSE_WINDOW_S, now)
        if not recent:
            self.skin_since = None
            return None
        clean = sum(b.clean for b in recent) / len(recent)
        if self.worn and clean >= PULSE_OFF_CLEAN:
            return True
        if clean < PULSE_MIN_CLEAN:
            self.skin_since = None
            return False
        if self.skin_since is None:
            self.skin_since = now
        return now - self.skin_since >= PULSE_HOLD_S

def load_participants(path):

    out = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sid = row["sensor_id"].strip().upper()
                if sid:
                    out[sid] = (row["participant"].strip(), (row.get("gateway") or "").strip().upper() or None)
    return out

class Hub:
    def __init__(self, store, participants):
        self.lock = threading.RLock()
        self.store = store
        self.participants = participants
        self.sensors = {sid: Sensor(sid, p, gw) for sid, (p, gw) in participants.items()}
        self.gateways = {}
        self.subscribers = []
        self.group_trend = []
        self.hr_history = {"group": deque(maxlen=HR_HISTORY_S)}
        self.snapshot_json = "{}"
        self.mode = "ppi"
        self.summary = False
        self.summary2 = False
        self.real_people = []
        row = store.latest_session()
        if row:
            self.session = {"id": row[0], "name": row[1], "started": row[2]}
            try:
                self.summary, self.summary2 = (bool(x) for x in json.loads(store.get_setting(f"summary/{row[0]}", "[0,0]")))
            except (ValueError, TypeError):
                pass
            self._replay()
        else:
            self.new_session("Session 1")

    def new_session(self, name, now=None):
        now = now or time.time()
        with self.lock:
            sid = self.store.new_session(name, now)
            self.session = {"id": sid, "name": name, "started": now}
            self.phases = [("idle", now)]
            self.store.add_phase(sid, "idle", now)
            for s in self.sensors.values():
                s.reset()
            self.group_trend = []
            self.next_trend = now + TREND_STEP_S
            self.summary = self.summary2 = False

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        with self.lock:
            self.mode = mode

    def set_summary(self, show):
        with self.lock:
            self.summary = bool(show)
            if self.summary:
                self.summary2 = False
            self._save_summary()

    def set_summary2(self, show):
        with self.lock:
            self.summary2 = bool(show)
            if self.summary2:
                self.summary = False
            self._save_summary()

    def _save_summary(self):
        self.store.set_setting(f"summary/{self.session['id']}", json.dumps([self.summary, self.summary2]))

    def set_phase(self, phase, now=None):
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        now = now or time.time()
        with self.lock:
            self.phases.append((phase, now))
            self.store.add_phase(self.session["id"], phase, now)

    def _replay(self):
        sid = self.session["id"]
        self.phases = [tuple(p) for p in self.store.phases(sid)] or [("idle", self.session["started"])]
        last_t = self.session["started"]
        for sensor, t, ppi, err, blocker, contact in self.store.beats(sid):
            self._sensor(sensor).add(t, ppi, err, blocker, contact)
            last_t = max(last_t, t)
        t = self.session["started"] + TREND_STEP_S
        while t <= last_t:
            self._trend_point(t)
            t += TREND_STEP_S
        self.next_trend = t

    def _sensor(self, sid):
        s = self.sensors.get(sid)
        if s is None:
            s = self.sensors[sid] = Sensor(sid, sid)
        return s

    def phase_window(self, phase, now):

        for i in range(len(self.phases) - 1, -1, -1):
            if self.phases[i][0] == phase:
                start = self.phases[i][1]
                end = self.phases[i + 1][1] if i + 1 < len(self.phases) else now
                if end - start > 2 * PHASE_TRIM_S:
                    start += PHASE_TRIM_S
                return start, end
        return None

    def ingest(self, msg, ip, now=None):
        now = now or time.time()
        if not isinstance(msg, dict) or not isinstance(msg.get("sensors", []), list):
            raise ValueError("payload must be an object with a sensors list")
        gw = str(msg.get("gw", ip)).strip().upper()
        beat_rows, hr_rows = [], []
        with self.lock:
            sess = self.session["id"]
            g = self.gateways.setdefault(gw, {"id": gw})
            g.update(ip=ip, last_seen=now, seq=msg.get("seq"),
                     sensors=len(msg.get("sensors", [])), uptime=msg.get("uptime"),
                     wifi=msg.get("wifi"))
            for item in msg.get("sensors", []):
                s = self._sensor(str(item["id"]).strip().upper())
                s.gateway, s.last_seen = gw, now
                s.rssi = _num(item.get("rssi"), s.rssi)
                s.bat = _num(item.get("bat"), s.bat)
                ppis = [p if isinstance(p, list) else [p] for p in item.get("ppi", [])]
                t = now - sum(p[0] for p in ppis) / 1000
                for p in ppis:
                    ppi = int(p[0])
                    err = p[1] if len(p) > 1 else None
                    blocker = p[2] if len(p) > 2 else 0
                    contact = p[3] if len(p) > 3 else 1
                    t += ppi / 1000
                    bt = s.add(t, ppi, err, blocker, contact)
                    beat_rows.append((sess, s.id, bt, ppi, err, blocker, contact))
                s.hr = _num(item.get("hr")) or None
                s.worn = s.on_skin(now)
                if s.worn is False or (s.worn is None and self.mode == "ppi"):
                    s.hr = None
                if s.hr:
                    hr_rows.append((sess, s.id, now, int(s.hr)))
        self.store.add_beats(beat_rows)
        self.store.add_hr(hr_rows)
        return len(beat_rows)

    def _trend_point(self, t):
        vals = []
        for s in self.sensors.values():
            r = hrv.summarize(s.window(t - LIVE_WINDOW_S, t))
            v = r["rmssd"] if r["valid"] and s.worn is not False else None
            s.trend.append((t, v))
            if v is not None:
                vals.append(v)
        self.group_trend.append((t, sum(vals) / len(vals) if vals else None, len(vals)))

    def tick(self, now=None):
        now = now or time.time()
        with self.lock:
            while now >= self.next_trend:
                self._trend_point(self.next_trend)
                self.next_trend += TREND_STEP_S
            self._record_hr(now)
            snap = self._snapshot(now)
        self.snapshot_json = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
        for q in list(self.subscribers):
            try:
                q.put_nowait(self.snapshot_json)
            except queue.Full:
                pass

    def _snapshot(self, now):
        windows = {p: self.phase_window(p, now) for p in ("baseline", "music", "post")}
        people, pre, during, post = [], {}, {}, {}
        real_people = []
        for s in sorted(self.sensors.values(), key=lambda s: s.participant):
            live = hrv.summarize(s.window(now - LIVE_WINDOW_S, now))
            age = None if s.last_seen is None else now - s.last_seen
            status = ("offline" if age is None or age > STALE_S
                      else "stale" if age > ONLINE_S else "online")
            if s.worn is not True or status != "online":
                live = dict(live, rmssd=None, sdnn=None, hr=None, valid=False)
            ph ={p: (hrv.summarize(s.window(*w)) if w else None) for p, w in windows.items()}
            for p, bucket in (("baseline", pre), ("music", during), ("post", post)):
                if ph[p]:
                    bucket[s.participant] = ph[p]
            real_people.append((s.participant, s.id, {p: _slim(v) for p, v in ph.items()}))
            hr, slim, held = s.hr if status == "online" else None, _slim(live), {}
            s.hr_since = (s.hr_since or now) if hr else None
            s.live_since = (s.live_since or now) if slim["valid"] else None
            if hr:
                if now - s.hr_since >= HOLD_MIN_S:
                    s.held_hr = (now, hr)
            elif s.held_hr and now - s.held_hr[0] <= HOLD_S:
                hr, held["hr"] = s.held_hr[1], round(now - s.held_hr[0])
            if slim["valid"]:
                if now - s.live_since >= HOLD_MIN_S:
                    s.held_live = (now, slim)
            elif s.held_live and now - s.held_live[0] <= HOLD_S:
                slim, held["live"] = dict(s.held_live[1], quality=slim["quality"]), round(now - s.held_live[0])
            people.append({
                "id": s.id, "p": s.participant, "gw": s.gateway, "status": status,
                "age": age, "hr": hr, "held": held or None, "rssi": s.rssi, "bat": s.bat,
                "live": slim, **{p: _slim(v) for p, v in ph.items()},
                "trend": [_r(v) for t, v in s.trend[-120:]],
            })
        self.real_people = real_people
        cur_phase, cur_t = self.phases[-1]
        return {
            "now": now,
            "session": self.session,
            "phase": cur_phase,
            "phase_started": cur_t,
            "mode": self.mode,
            "summary": self.summary,
            "summary2": self.summary2,
            "phases": self.phases,
            "windows": windows,
            "people": people,
            "group": {
                "post": hrv.compare_groups(pre, post),
                "music": hrv.compare_groups(pre, during),
                "means": {p: _mean_valid(b) for p, b in
                          (("baseline", pre), ("music", during), ("post", post))},
            },
            "trend": [[t, _r(v), n] for t, v, n in self.group_trend],
            "gateways": sorted(
                ({**g, "age": now - g["last_seen"]} for g in self.gateways.values()),
                key=lambda g: g["id"]),
            "config": {"min_pairs": hrv.MIN_PAIRS, "min_quality": hrv.MIN_QUALITY,
                       "live_window": LIVE_WINDOW_S, "trim": PHASE_TRIM_S},
        }

    def _record_hr(self, now):
        vals = []
        for s in self.sensors.values():
            online = s.last_seen is not None and now - s.last_seen <= ONLINE_S
            v = s.hr if online else None
            self.hr_history.setdefault(s.id, deque(maxlen=HR_HISTORY_S)).append((round(now, 2), v))
            if v:
                vals.append(v)
        self.hr_history["group"].append((round(now, 2), round(sum(vals) / len(vals), 1) if vals else None))

    def hr_series(self, key):

        with self.lock:
            h = self.hr_history.get(key if key == "group" else key.strip().upper())
            return None if h is None else list(h)

    def recent_beats(self, sid, now=None):

        now = now or time.time()
        with self.lock:
            s = self.sensors.get(sid.strip().upper())
            if not s:
                return None
            return [[round(b.t, 3), b.ppi, b.clean] for b in s.window(now - MONITOR_BEATS_S, now)]

    def gateway_sensors(self, gw):

        return "".join(f"{sid}\n" for sid, (_, g) in sorted(self.participants.items()) if g == gw.upper())

    def export_xlsx(self, session_id=None):

        settings = {"RMSSD window for live values (s)": LIVE_WINDOW_S, "trend step (s)": TREND_STEP_S,
                    "phase start trimmed (s)": PHASE_TRIM_S, "worn: shown at clean ≥": PULSE_MIN_CLEAN,
                    "worn: shown after clean for (s)": PULSE_HOLD_S, "worn: hidden below clean": PULSE_OFF_CLEAN,
                    "last value held on screen (s)": HOLD_S}
        if session_id in (None, self.session["id"]):
            data = export.collect(self, True, settings)
        else:
            row = self.store.session(session_id)
            if row is None:
                return None, None
            data = export.collect_session(Hub, self.store, self.participants, row, settings)
        s = data["session"]
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(s["started"]))
        return export.build(data), f"hrv-session-{s['id']}-{stamp}.xlsx"

    def export_csv(self):

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["participant", "sensor_id", "phase", "rmssd_ms", "sdnn_ms",
                    "hr_bpm", "beats", "pairs", "quality", "valid"])
        for participant, sid, phases in self.real_people:
            for phase in ("baseline", "music", "post"):
                v = phases.get(phase)
                if v:
                    w.writerow([participant, sid, phase, v["rmssd"], v["sdnn"], v["hr"],
                                v["beats"], v["pairs"], v["quality"], v["valid"]])
        return buf.getvalue()

def _num(v, default=None):

    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else default

def _r(v, nd=1):
    return None if v is None else round(v, nd)

def _slim(s):
    if s is None:
        return None
    return {"rmssd": _r(s["rmssd"]), "sdnn": _r(s["sdnn"]), "hr": _r(s["hr"]),
            "beats": s["beats"], "pairs": s["pairs"], "quality": _r(s["quality"], 2),
            "valid": s["valid"]}

def _mean_valid(bucket):
    vals = [v["rmssd"] for v in bucket.values() if v["valid"]]
    return {"mean": _r(sum(vals) / len(vals)) if vals else None, "n": len(vals)}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hub: Hub = None
    timeout = 20

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/ingest") or (args and str(args[1]) != "200"):
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/state":
            return self._send(200, self.hub.snapshot_json)
        if path == "/api/stream":
            return self._stream()
        if path == "/api/hr":
            key = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            series = self.hub.hr_series(key)
            if series is None:
                return self._send(404, '{"error":"unknown sensor"}')
            return self._send(200, json.dumps({"now": time.time(), "hr": series}))
        if path == "/api/beats":
            sid = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            beats = self.hub.recent_beats(sid)
            if beats is None:
                return self._send(404, '{"error":"unknown sensor"}')
            return self._send(200, json.dumps({"now": time.time(), "beats": beats}))
        if path.startswith("/api/gateway/") and path.endswith("/sensors"):
            gw = path.removeprefix("/api/gateway/").removesuffix("/sensors")
            return self._send(200, self.hub.gateway_sensors(gw), "text/plain; charset=utf-8")
        if path == "/api/sessions":
            rows = self.hub.store.sessions()
            return self._send(200, json.dumps([{"id": i, "name": n, "started": t} for i, n, t in rows], ensure_ascii=False))
        if path == "/api/export.xlsx":
            q = parse_qs(urlsplit(self.path).query).get("session", [""])[0]
            try:
                data, name = self.hub.export_xlsx(int(q) if q else None)
            except ValueError:
                return self._send(400, '{"error":"session must be a number"}')
            if data is None:
                return self._send(404, '{"error":"no such session"}')
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/export.csv":
            name = f"hrv-session-{self.hub.session['id']}.csv"
            self.send_response(200)
            data = ("﻿" + self.hub.export_csv()).encode()
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        rel = "index.html" if path == "/" else path.removeprefix("/static/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() in f.parents and f.is_file():
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                     ".ttf": "font/ttf", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
                     }.get(f.suffix, "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype)
        self._send(404, '{"error":"not found"}')

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self._json_body()
        except (ValueError, json.JSONDecodeError):
            return self._send(400, '{"error":"bad json"}')
        if path == "/api/ingest":
            try:
                n = self.hub.ingest(body, self.client_address[0])
            except (KeyError, TypeError, ValueError) as e:
                return self._send(400, json.dumps({"error": str(e)}))
            return self._send(200, json.dumps({"ok": True, "beats": n,
                                               "phase": self.hub.phases[-1][0], "mode": self.hub.mode}))
        if ADMIN_TOKEN and self.headers.get("X-Admin-Token") != ADMIN_TOKEN:
            return self._send(403, '{"error":"admin token required"}')
        if path == "/api/phase":
            try:
                self.hub.set_phase(body.get("phase"))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
        elif path == "/api/mode":
            try:
                self.hub.set_mode(body.get("mode"))
            except ValueError as e:
                return self._send(400, json.dumps({"error": str(e)}))
        elif path == "/api/summary":
            self.hub.set_summary(body.get("show"))
        elif path == "/api/summary2":
            self.hub.set_summary2(body.get("show"))
        elif path == "/api/session":
            self.hub.new_session(str(body.get("name") or "New session")[:60])
        else:
            return self._send(404, '{"error":"not found"}')
        self.hub.tick()
        self._send(200, '{"ok":true}')

    def _stream(self):
        q = queue.Queue(maxsize=5)
        self.hub.subscribers.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(f"data: {self.hub.snapshot_json}\n\n".encode())
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.subscribers.remove(q)
            self.close_connection = True

def local_ips():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return [s.getsockname()[0]]
    except OSError:
        return []

def main():
    ap = argparse.ArgumentParser(description="HRV dashboard server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=str(ROOT / "data" / "hrv.db"))
    ap.add_argument("--participants", default=str(ROOT / "config" / "participants.csv"))
    args = ap.parse_args()

    participants = load_participants(Path(args.participants))
    if not participants:
        raise SystemExit(f"No participants in {args.participants} (wrong path?). Example: --participants config/participants.csv")
    if ADMIN_TOKEN and not all(32 <= ord(c) < 127 for c in ADMIN_TOKEN):
        raise SystemExit("HRV_ADMIN_TOKEN must be English letters/digits: browsers cannot send other characters")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    hub = Hub(Store(args.db), participants)
    hub.tick()
    Handler.hub = hub

    def ticker():
        while True:
            time.sleep(1)
            try:
                hub.tick()
            except Exception:
                import traceback
                traceback.print_exc()

    threading.Thread(target=ticker, daemon=True).start()
    ThreadingHTTPServer.request_queue_size = 128
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        raise SystemExit(f"Port {args.port} is busy ({e.strerror}): a server is probably already running. "
                         f"Stop it first (Ctrl+C in its terminal, or: lsof -ti tcp:{args.port} | xargs kill)")
    srv.daemon_threads = True
    print(f"HRV dashboard  http://localhost:{args.port}/   (operator: /?admin)")
    for ip in local_ips():
        print(f"ESP32 ingest   http://{ip}:{args.port}/api/ingest")
    print(f"session {hub.session['id']} '{hub.session['name']}', {len(hub.sensors)} sensors in config")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
