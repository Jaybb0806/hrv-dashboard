import argparse
import asyncio
import contextlib
import json
import time
import urllib.request

from bleak import BleakClient, BleakScanner

PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

TYPE_PPI, OP_START, OP_STOP = 0x03, 0x02, 0x03
STATUS = ["success", "invalid op code", "invalid measurement type", "not supported",
          "invalid length", "invalid parameter", "already in state", "invalid resolution",
          "invalid sample rate", "invalid range", "invalid MTU", "invalid number of channels",
          "invalid state", "device in charger"]

PPI_START_GRACE_S = 40
PPI_RESTART_AFTER_S = 20
PPI_DISCONNECT_AFTER_S = 60
MAX_SAMPLES_PER_POST = 120
SETUP_TIMEOUT_S = 15
HR_SILENT_S = 20
CONNECT_TIMEOUT_S = 20

def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)

def parse_ppi_frame(d):

    if len(d) < 10 or d[0] != TYPE_PPI or d[9] != 0x00:
        return None
    out = []
    for i in range(10, len(d) - 5, 6):
        ppi = d[i + 1] | d[i + 2] << 8
        if ppi:
            flags = d[i + 5]
            out.append((ppi, d[i + 3] | d[i + 4] << 8, d[i], flags & 1, (flags >> 1) & 1))
    return out

def parse_hr(d):
    if len(d) < 2:
        return None
    return (d[1] | d[2] << 8) if d[0] & 1 and len(d) >= 3 else d[1]

class Sensor:
    def __init__(self, sid):
        self.id = sid
        self.hr = self.bat = self.rssi = None
        self.buf = []
        self.connected = False
        self.connected_at = self.last_data = self.last_start = self.ppi_since = 0.0
        self.first_ppi_logged = False
        self.last_hr_at = 0.0
        self.step = ""
        self.debug = 0
        self.want_ppi = True
        self.ppi_on = False

    def on_data(self, _, data):
        samples = parse_ppi_frame(bytes(data))
        if self.debug > 0:
            self.debug -= 1
            log(f"{self.id} PMD data {bytes(data).hex(' ')}")
        if not samples:
            return
        now = time.time()
        if not self.first_ppi_logged:
            log(f"{self.id} first PPI {now - self.ppi_since:.1f} s after starting")
            self.first_ppi_logged = True
        for ppi, err, hr, blocker, contact in samples:
            self.buf.append([ppi, err, blocker, contact])
        self.last_data = now
        if samples[-1][2]:
            self.hr = samples[-1][2]

    def on_control(self, _, data):
        d = bytes(data)
        if self.debug > 0:
            log(f"{self.id} PMD control {d.hex(' ')}")
        if len(d) >= 4 and d[0] == 0xF0 and d[1] == OP_START and d[2] == TYPE_PPI:
            status = STATUS[d[3]] if d[3] < len(STATUS) else f"unknown ({d[3]})"
            if d[3] in (0, 6):
                log(f"{self.id} PPI stream started ({status})")
            else:
                log(f"{self.id} PPI start refused: {status}")

    def on_hr(self, _, data):
        if self.debug > 0:
            log(f"{self.id} HR {bytes(data).hex(' ')}")
        self.last_hr_at = time.time()
        self.hr = parse_hr(bytes(data)) or None

    def on_battery(self, _, data):
        if data:
            self.bat = data[0]

async def setup_sensor(c, s):
    s.step = "stop PPI"
    with contextlib.suppress(Exception):
        await c.write_gatt_char(PMD_CONTROL, bytes([OP_STOP, TYPE_PPI]), response=True)
    s.step = "subscribe HR"
    await c.start_notify(HR_MEASUREMENT, s.on_hr)
    s.step = "battery"
    try:
        s.bat = (await c.read_gatt_char(BATTERY_LEVEL))[0]
        await c.start_notify(BATTERY_LEVEL, s.on_battery)
    except Exception:
        pass
    s.step = "subscribe PMD"
    await c.start_notify(PMD_CONTROL, s.on_control)
    await c.start_notify(PMD_DATA, s.on_data)
    s.ppi_on = False
    s.step = "apply mode"
    await apply_mode(c, s)
    s.step = ""

async def apply_mode(c, s):

    if s.want_ppi == s.ppi_on:
        return
    op = OP_START if s.want_ppi else OP_STOP
    await c.write_gatt_char(PMD_CONTROL, bytes([op, TYPE_PPI]), response=True)
    s.ppi_on = s.want_ppi
    if s.ppi_on:
        s.ppi_since = s.last_start = time.time()
        s.first_ppi_logged = False
        log(f"{s.id} mode ppi: waiting for PPI")
    else:
        log(f"{s.id} mode hr: PPI stopped, HR only")

async def watch_sensor(c, s, gone):

    while not gone.is_set():
        try:
            await asyncio.wait_for(gone.wait(), 1)
        except asyncio.TimeoutError:
            pass
        await asyncio.wait_for(apply_mode(c, s), SETUP_TIMEOUT_S)
        if time.time() - max(s.last_hr_at, s.connected_at) > HR_SILENT_S:
            log(f"{s.id} no HR for {HR_SILENT_S} s, reconnecting")
            return
        if not s.ppi_on:
            continue
        now = time.time()
        quiet = now - max(s.last_data, s.ppi_since + PPI_START_GRACE_S)
        if quiet > PPI_DISCONNECT_AFTER_S:
            log(f"{s.id} no PPI for {quiet:.0f} s, reconnecting")
            return
        if quiet > PPI_RESTART_AFTER_S and now - s.last_start > PPI_RESTART_AFTER_S:
            log(f"{s.id} PPI quiet, sending start again")
            await c.write_gatt_char(PMD_CONTROL, bytes([OP_STOP, TYPE_PPI]), response=True)
            await c.write_gatt_char(PMD_CONTROL, bytes([OP_START, TYPE_PPI]), response=True)
            s.last_start = now

async def run_sensor(s):
    name = f"Polar Sense {s.id}"
    while True:
        log(f"scanning for '{name}'")
        dev = await BleakScanner.find_device_by_filter(
            lambda d, adv: (adv.local_name or d.name or "").upper() == name.upper(), timeout=10)
        if not dev:
            continue
        gone = asyncio.Event()
        client = BleakClient(dev, disconnected_callback=lambda _: gone.set(), timeout=CONNECT_TIMEOUT_S)
        s.step = "connect"
        t_conn = time.time()
        try:
            async with contextlib.AsyncExitStack() as stack:
                c = await asyncio.wait_for(stack.enter_async_context(client), CONNECT_TIMEOUT_S)
                s.connected, s.connected_at = True, time.time()
                s.first_ppi_logged = False
                s.hr, s.last_hr_at = None, 0.0
                log(f"{s.id} connected in {time.time() - t_conn:.1f} s")
                if not c.services.get_service(PMD_SERVICE):
                    log(f"{s.id} has no PMD service: update its firmware in Polar Flow")
                    return
                await asyncio.wait_for(setup_sensor(c, s), SETUP_TIMEOUT_S)
                try:
                    await watch_sensor(c, s, gone)
                except asyncio.CancelledError:
                    if s.ppi_on:
                        try:
                            await asyncio.wait_for(
                                c.write_gatt_char(PMD_CONTROL, bytes([OP_STOP, TYPE_PPI]), response=True), 2)
                            log(f"{s.id} PPI stopped before exit")
                        except Exception as e:
                            log(f"{s.id} could not stop PPI before exit: {e!r}")
                    raise
        except asyncio.TimeoutError:
            log(f"{s.id} timed out at '{s.step}', reconnecting")
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 5)
        except Exception as e:
            log(f"{s.id} connection error: {e}")
        s.connected = False
        log(f"{s.id} disconnected")
        await asyncio.sleep(1)

def post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read() or b"{}")

async def poster(url, gw, sensors):
    seq, start, sent, last_report = 0, time.time(), 0, time.time()
    while True:
        await asyncio.sleep(1)
        seq += 1
        taken = {s.id: s.buf[:MAX_SAMPLES_PER_POST] for s in sensors}
        body = {"gw": gw, "seq": seq, "uptime": int(time.time() - start), "sensors": [
            {"id": s.id, "hr": s.hr, "rssi": s.rssi, "bat": s.bat, "ppi": taken[s.id]}
            for s in sensors if s.connected]}
        try:
            status, reply = await asyncio.to_thread(post, f"{url}/api/ingest", body)
        except Exception as e:
            log(f"ingest failed ({e}), keeping beats for the next try")
            status, reply = None, {}
        if status == 200:
            for s in sensors:
                del s.buf[:len(taken[s.id])]
            mode = reply.get("mode")
            if mode in ("ppi", "hr") and sensors[0].want_ppi != (mode == "ppi"):
                log(f"server asks for mode {mode}")
                for s in sensors:
                    s.want_ppi = mode == "ppi"
            sent += reply.get("beats", 0)
        if time.time() - last_report >= 10:
            last_report = time.time()
            for s in sensors:
                log(f"{s.id} {'online' if s.connected else 'offline'} hr={s.hr} bat={s.bat} "
                    f"beats sent={sent} phase={reply.get('phase')}")

def sensors_from_server(url, gw):
    with urllib.request.urlopen(f"{url}/api/gateway/{gw}/sensors", timeout=3) as r:
        return [line.strip().upper() for line in r.read().decode().splitlines() if line.strip()]

async def main():
    ap = argparse.ArgumentParser(description="BLE gateway for Polar Verity Sense using the Mac's Bluetooth")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--gw", default="mac")
    ap.add_argument("--ids", help="comma-separated Device IDs; default: ask the server")
    ap.add_argument("--debug", type=int, default=0, metavar="N", help="log the first N raw BLE frames")
    args = ap.parse_args()

    ids = [i.strip().upper() for i in args.ids.split(",")] if args.ids else sensors_from_server(args.url, args.gw)
    if not ids:
        raise SystemExit(f"no sensors for gateway '{args.gw}' in participants.csv; pass --ids")
    log(f"gateway {args.gw}: {', '.join(ids)} -> {args.url}")
    sensors = [Sensor(i) for i in ids]
    for s in sensors:
        s.debug = args.debug
    await asyncio.gather(poster(args.url, args.gw, sensors), *(run_sensor(s) for s in sensors))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
