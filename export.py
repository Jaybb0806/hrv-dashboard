import bisect
import datetime
import time

import hrv
import xlsx

PHASES = ("baseline", "music", "post")
TH = {"idle": "ยังไม่เริ่ม", "baseline": "ก่อนฟัง", "music": "ระหว่างฟังเพลง", "post": "หลังฟัง", "done": "จบรอบ"}
MAX_ROWS = 1_000_000

class _Pinned:

    def __init__(self, store, row):
        self._store, self._row = store, row

    def latest_session(self):
        return self._row

    def __getattr__(self, name):
        return getattr(self._store, name)

def _dt(t):
    return None if t is None else datetime.datetime.fromtimestamp(t)

def _r(v, nd=2):
    return None if v is None else round(v, nd)

def _pct(a, b):
    return _r((b["rmssd"] / a["rmssd"] - 1) * 100, 1) if a and b and a["valid"] and b["valid"] else None

def collect(hub, live, settings):

    with hub.lock:
        sid = hub.session["id"]
        phases = list(hub.phases)
        sensors = sorted(hub.sensors.values(), key=lambda s: s.participant)
        last_beat = max((s.times[-1] for s in sensors if s.times), default=hub.session["started"])
        end = max(time.time(), last_beat) if live else max(last_beat, phases[-1][1])
        if phases[-1][0] == "done":
            end = min(end, max(phases[-1][1], last_beat))
        windows = {p: hub.phase_window(p, end) for p in PHASES}
        people = []
        for s in sensors:
            ph = {p: (hrv.summarize(s.window(*w)) if w else None) for p, w in windows.items()}
            people.append({"p": s.participant, "id": s.id, "gw": s.gateway or hub.participants.get(s.id, (None, None))[1],
                           "ph": ph, "trend": list(s.trend), "beats": list(s.beats),
                           "bat": s.bat, "rssi": s.rssi, "last_seen": s.last_seen})
        data = {
            "session": dict(hub.session), "phases": phases, "windows": windows, "end": end, "live": live,
            "people": people, "group_trend": list(hub.group_trend), "mode": hub.mode,
            "gateways": [dict(g) for g in hub.gateways.values()] if live else [],
            "settings": settings,
        }
    data["hr"] = hub.store.hr(sid)
    data["raw_beats"] = hub.store.beats(sid)
    return data

def collect_session(hub_cls, store, participants, row, settings):

    return collect(hub_cls(_Pinned(store, row), participants), False, settings)

def build(data):
    wb = xlsx.Workbook()
    title_font, note_font = "title", "note"
    starts = [t for _, t in data["phases"]]

    def phase_at(t):
        i = bisect.bisect_right(starts, t) - 1
        return TH.get(data["phases"][i][0], data["phases"][i][0]) if i >= 0 else TH["idle"]

    def sheet(name, widths, freeze=False):
        return wb.sheet(name, widths, freeze)

    def header(ws, cols):
        ws.row(cols, "head")

    def row(ws, values):
        ws.row(values)

    def text(ws, value, font=None):
        ws.row([value], font)

    s = data["session"]
    people = [p for p in data["people"] if p["gw"] or not any(q["gw"] for q in data["people"])] or data["people"]

    ws = sheet("อ่านก่อน", [26, 110])
    text(ws, f"HRV · {s['name']} (session {s['id']})", title_font)
    row(ws, ["ส่งออกเมื่อ", _dt(time.time())])
    row(ws, ["เริ่มรอบ", _dt(s["started"])])
    ws.row([])
    for name, what in (
        ("Summary", "ช่วงเวลาของแต่ละช่วง, ผลของกลุ่ม (จับคู่คนเดียวกัน), ค่าที่ตั้งในระบบ และประวัติการปรับค่าบนจอ"),
        ("Participants", "1 แถวต่อ 1 คน: HRV (RMSSD, SDNN), HR, จำนวนจังหวะ, คุณภาพสัญญาณ ของแต่ละช่วง และ % เทียบกับก่อนฟัง"),
        ("Phase detail", "ข้อมูลเดียวกับ Participants แต่ 1 แถวต่อ 1 คนต่อ 1 ช่วง (รูปแบบเดียวกับไฟล์ CSV เดิม)"),
        ("Group trend", "ค่า HRV ของทั้งกลุ่มทุก 5 วินาที (ค่าเฉลี่ยของคนที่มี RMSSD 60 วินาทีที่ใช้ได้) = เส้นกราฟบนจอ"),
        ("Person trend", "ค่า HRV (RMSSD 60 วินาทีล่าสุด) ของแต่ละคนทุก 5 วินาที แยกคอลัมน์ต่อคน"),
        ("HR", "HR ที่ Polar ส่งมาทุกวินาที (เฉพาะตอนที่ระบบเห็นว่าใส่อยู่)"),
        ("Beats", "ทุกจังหวะที่ Polar ส่งมา (PPI): ใช้คำนวณใหม่ได้ทั้งหมด; clean = ผ่านตัวกรองที่ใช้คิด HRV"),
        ("Devices", "สถานะบอร์ดและ Polar ตอนกดดาวน์โหลด (แบต, สัญญาณ)"),
    ):
        row(ws, [name, what])
    ws.row([])
    text(ws, "ทุกตัวเลขในไฟล์นี้เป็นค่าที่วัดได้จริง ไม่ผ่านการปรับค่าบนจอ (ดูประวัติการปรับที่ Summary)", note_font)
    text(ws, "RMSSD/SDNN หน่วย ms · HR หน่วย bpm · quality = สัดส่วนจังหวะที่สะอาด (0–1) · "
             f"valid = มีคู่จังหวะดี ≥ {hrv.MIN_PAIRS} และ quality ≥ {hrv.MIN_QUALITY}", note_font)
    text(ws, "ตัดช่วงแรกของแต่ละช่วงออก 30 วินาทีตอนคิดผล (คนกำลังนั่งนิ่ง) ถ้าช่วงยาวกว่า 1 นาที", note_font)

    ws = sheet("Summary", [30, 22, 22, 16, 22, 22, 14, 14, 14])
    text(ws, "Session", title_font)
    for k, v in (("session id", s["id"]), ("name", s["name"]), ("started", _dt(s["started"])),
                 ("data until", _dt(data["end"])), ("sensor mode", data["mode"]),
                 ("participants in file", len(people)),
                 ("participants with any beats", sum(1 for p in people if p["beats"]))):
        row(ws, [k, v])
    ws.row([])
    text(ws, "Phases", title_font)
    header(ws, ["phase", "started", "ended", "length (min)", "analysed from", "analysed to"])
    ph = data["phases"]
    for i, (p, t) in enumerate(ph):
        until = ph[i + 1][1] if i + 1 < len(ph) else (None if p == "done" else data["end"])
        w = data["windows"].get(p) if p in PHASES and i == max(j for j, x in enumerate(ph) if x[0] == p) else None
        row(ws, [TH.get(p, p), _dt(t), _dt(until), _r((until - t) / 60, 2) if until else None,
                 _dt(w[0]) if w else None, _dt(w[1]) if w else None])
    ws.row([])
    text(ws, "Group results (same people before and after, measured)", title_font)
    header(ws, ["comparison", "people (n)", "baseline mean RMSSD", "after mean RMSSD", "change %",
                "median change %", "increased", "decreased", "mean ln RMSSD before / after"])
    pre = {p["p"]: p["ph"]["baseline"] for p in people if p["ph"]["baseline"]}
    for label, key in (("ระหว่างฟังเพลง vs ก่อนฟัง", "music"), ("หลังฟัง vs ก่อนฟัง", "post")):
        g = hrv.compare_groups(pre, {p["p"]: p["ph"][key] for p in people if p["ph"][key]})
        row(ws, [label, g["n"], _r(g["pre_mean"]), _r(g["post_mean"]), _r(g["pct_change"], 1), _r(g["median_pct"], 1),
                 g["n_increased"], g["n"] - g["n_increased"],
                 f"{_r(g['ln_pre'], 3)} / {_r(g['ln_post'], 3)}" if g["n"] else None])
    ws.row([])
    text(ws, "Settings", title_font)
    for k, v in list(data["settings"].items()) + [("min clean pairs", hrv.MIN_PAIRS), ("min quality", hrv.MIN_QUALITY),
                                                    ("PPI accepted (ms)", f"{hrv.MIN_PPI_MS}–{hrv.MAX_PPI_MS}")]:
        row(ws, [k, v])

    stats = ("rmssd", "sdnn", "hr", "beats", "pairs", "quality", "valid")
    ws = sheet("Participants", [11, 12, 9] + [11] * (len(stats) * 3) + [13, 13, 11], True)
    header(ws, ["participant", "sensor_id", "gateway"]
           + [f"{p}_{k}" for p in PHASES for k in stats]
           + ["music_vs_baseline_%", "post_vs_baseline_%", "post_direction"])
    for p in people:
        vals = []
        for ph_ in PHASES:
            v = p["ph"][ph_]
            vals += [None] * len(stats) if not v else [_r(v["rmssd"]), _r(v["sdnn"]), _r(v["hr"], 1), v["beats"],
                                                       v["pairs"], _r(v["quality"], 3), v["valid"]]
        post = _pct(p["ph"]["baseline"], p["ph"]["post"])
        row(ws, [p["p"], p["id"], p["gw"]] + vals + [_pct(p["ph"]["baseline"], p["ph"]["music"]), post,
                                                      None if post is None else ("up" if post > 0 else "down")])

    ws = sheet("Phase detail", [11, 12, 16, 11, 11, 10, 9, 9, 9, 8], True)
    header(ws, ["participant", "sensor_id", "phase", "rmssd_ms", "sdnn_ms", "hr_bpm", "beats", "pairs", "quality", "valid"])
    for p in people:
        for ph_ in PHASES:
            v = p["ph"][ph_]
            if v:
                row(ws, [p["p"], p["id"], TH[ph_], _r(v["rmssd"]), _r(v["sdnn"]), _r(v["hr"], 1),
                         v["beats"], v["pairs"], _r(v["quality"], 3), v["valid"]])

    ws = sheet("Group trend", [20, 14, 16, 18, 12], True)
    header(ws, ["time", "unix_t", "phase", "group_rmssd_ms", "people"])
    for t, v, n in data["group_trend"]:
        row(ws, [_dt(t), round(t, 1), phase_at(t), _r(v), n])

    ws = sheet("Person trend", [20, 14, 16] + [9] * len(people), True)
    header(ws, ["time", "unix_t", "phase"] + [p["p"] for p in people])
    by_t = [dict((round(t, 1), v) for t, v in p["trend"]) for p in people]
    for t in sorted({round(t, 1) for p in people for t, _ in p["trend"]}):
        row(ws, [_dt(t), t, phase_at(t)] + [_r(m.get(t)) for m in by_t])

    code = {p["id"]: p["p"] for p in data["people"]}

    def long_sheet(name, widths, cols, rows):
        part, n, ws = 1, 0, None
        for r in rows:
            if ws is None or n >= MAX_ROWS:
                ws = sheet(name if part == 1 else f"{name} ({part})", widths, True)
                header(ws, cols)
                part, n = part + 1, 0
            row(ws, r)
            n += 1
        if ws is None:
            ws = sheet(name, widths)
            header(ws, cols)

    long_sheet("HR", [20, 14, 16, 11, 12, 8], ["time", "unix_t", "phase", "participant", "sensor_id", "hr_bpm"],
               ([_dt(t), round(t, 2), phase_at(t), code.get(sensor, sensor), sensor, hr] for sensor, t, hr in data["hr"]))
    clean = {(p["id"], round(b.t, 3)): b.clean for p in data["people"] for b in p["beats"]}
    long_sheet("Beats", [20, 16, 16, 11, 12, 8, 8, 8, 11, 8],
               ["time", "unix_t", "phase", "participant", "sensor_id", "ppi_ms", "err_ms", "blocker", "skin_contact", "clean"],
               ([_dt(t), round(t, 3), phase_at(t), code.get(s_, s_), s_, ppi, err, blocker, contact, clean.get((s_, round(t, 3)))]
                for s_, t, ppi, err, blocker, contact in sorted(data["raw_beats"], key=lambda r: r[1])))

    ws = sheet("Devices", [12, 16, 12, 12, 12, 20])
    if data["live"]:
        text(ws, "Gateways (at download)", title_font)
        header(ws, ["gateway", "ip", "wifi_dbm", "sensors", "uptime_s", "last_seen"])
        for g in sorted(data["gateways"], key=lambda g: g["id"]):
            row(ws, [g["id"], g.get("ip"), g.get("wifi"), g.get("sensors"), g.get("uptime"), _dt(g.get("last_seen"))])
        ws.row([])
    text(ws, "Sensors", title_font)
    header(ws, ["participant", "sensor_id", "gateway", "battery_%", "ble_rssi_dbm", "last_seen"])
    for p in data["people"]:
        row(ws, [p["p"], p["id"], p["gw"], p["bat"], p["rssi"], _dt(p["last_seen"])])

    return wb.save()
