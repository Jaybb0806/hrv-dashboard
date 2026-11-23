# HRV Dashboard — Music and Relaxation

Shows the heart rate variability (HRV, as RMSSD) of 24 participants before, during and after listening to music.

```
Polar Verity Sense ×24 ──BLE──▶ ESP32-C6 ×8 ──WiFi (HTTP POST)──▶ MacBook: server.py ──▶ browser / projector
```

- Python 3.10 or newer, standard library only: no `pip install`, no internet needed on the day.
- Every beat is stored in SQLite (`data/hrv.db`). If the server stops mid-session, restarting it brings the session back in full.
- Screens and exports show measured values only.

## Getting started

```bash
python3 server.py
```

| URL | Purpose |
| --- | --- |
| `http://localhost:8000/` | Audience screen for the projector (`1`–`4` switch views, `f` full screen) |
| `http://localhost:8000/?admin` | Operator page: phase buttons, sensor mode, results on screen, new session, Excel download |
| `http://<mac-ip>:8000/api/ingest` | Where the ESP32 gateways send data (the server prints its IP on start) |

Options: `--port` (default 8000), `--db` (default `data/hrv.db`), `--participants` (default `config/participants.csv`).
Set the environment variable `HRV_ADMIN_TOKEN` to require a token for the operator buttons.

Tests:

```bash
python3 -m unittest discover -s tests
```

The Excel export test needs `openpyxl` (`pip install openpyxl`); everything else runs on the standard library.

## Views

1. **Overview**: mean RMSSD before/after, % change, how many people's HRV went up, group trend coloured by phase.
2. **Individuals**: before → after dumbbell chart per participant (codes only, no names).
3. **Live**: one card per sensor with HR, HRV over the last 60 s, signal quality bar and sparkline.
4. **Devices**: status of every gateway and sensor (RSSI, battery, last data received).

Click a person's card or the group HR for the HR monitor (per-second HR and recent beats); `Esc` closes it.

## On the day

1. Make sure `config/participants.csv` lists the real Device ID of each Verity Sense (the 8 characters printed on the sensor).
2. Start `server.py`, open `?admin` and press **New session**.
3. Have participants put on their sensors; wait on **Devices** until all 24 are online.
4. Ask everyone to sit still for about 3 minutes, then press **Start baseline** (5 min) → **Start music** → **Start post** (5 min) → **End session**.
5. Press **Show results on audience screen** or **Show individual results** to present the results.
6. Press **Download Excel** to keep everything recorded in the session (any earlier session can be picked from the list).

Sensor mode: **HRV (PPI)** streams beats for HRV (participants should sit still). **HR (movement OK)** stops PPI so the sensor's own HR keeps up while people move.

## Calculation rules (set at the top of `hrv.py` and `server.py`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `MIN_PPI_MS` / `MAX_PPI_MS` | 400 / 1500 | Intervals outside this range are artifacts (400 ms = 150 bpm; an unworn Polar often reports ~350 ms) |
| `PULSE_MIN_CLEAN` / `PULSE_OFF_CLEAN` | 80% / 50% | A sensor's HR/HRV appears once ≥ 80% of beats in the last 15 s are clean for `PULSE_HOLD_S` (10 s), and hides only below 50% (small arm movements do not blank it) |
| `MAX_ERR_MS` | 20 | Beats whose Polar error estimate is above this are rejected |
| `MAX_REL_DIFF` | 25% | Beats that differ from the median of the previous 5 by more than this are artifacts |
| `MIN_PAIRS` | 40 | Minimum successive clean pairs per phase; fewer shows "not enough signal" |
| `MIN_QUALITY` | 60% | Minimum share of clean beats |
| `PHASE_TRIM_S` | 30 | Seconds dropped at the start of each phase |
| `LIVE_WINDOW_S` | 60 | Window for the live HRV value |

- RMSSD uses only pairs of adjacent beats that are both clean, so a rejected beat never creates a false difference.
- Before/after means are paired: only people with a valid signal in both phases count, and n is always shown.
- The Excel export includes mean lnRMSSD and the median % change for further analysis, plus every beat Polar sent.

---

## Protocol for the ESP32 firmware

Each board joins the room router's WiFi and sends an **HTTP POST every second**.

```
POST http://<mac-ip>:8000/api/ingest
Content-Type: application/json
```

```json
{
  "gw": "GW-01",
  "seq": 1234,
  "uptime": 3600,
  "sensors": [
    {
      "id": "A1B2C3D4",
      "hr": 72,
      "rssi": -61,
      "bat": 88,
      "ppi": [[812, 6, 0, 1], [798, 5, 0, 1]]
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `gw` | ✓ | Board name, `GW-01`…`GW-08` |
| `seq` | | Counter that goes up by 1, to spot lost packets |
| `uptime` | | Seconds since the board booted |
| `sensors[].id` | ✓ | Polar Device ID (case-insensitive) |
| `sensors[].hr` | | HR from the Heart Rate Service (0x2A37) |
| `sensors[].rssi` | | RSSI of the BLE link |
| `sensors[].bat` | | Battery % from the Battery Service (0x2A19) |
| `sensors[].ppi` | ✓ | Beats **received since the previous POST**, oldest first, each `[ppi_ms, error_ms, blocker, skin_contact]` from the PMD PPI frame (`ppi_ms` alone also works, with weaker artifact filtering) |

- Send every connected sensor even when it has no new beats (`"ppi": []`) so the Devices view knows it is online.
- The server timestamps on arrival: the last beat in `ppi` ends at that moment, so the ESP32 needs no accurate clock.
- If a POST fails, keep the beats and send them with the next one (never drop them).
- The server replies `{"ok": true, "beats": N, "phase": "music", "mode": "ppi"}`; boards follow `mode`, and can use `phase` for an LED.
- Give the MacBook a fixed IP (DHCP reservation on the router) and put that IP in the firmware.

Each board gets its sensor list from `GET /api/gateway/<gw>/sensors`: one Device ID per line, taken from the `gateway` column of `config/participants.csv`.

The firmware that implements this protocol is in [firmware/](firmware/README.md). Before the boards are ready, `tools/mac_gateway.py` does the same job with the Mac's own Bluetooth (`pip install bleak`).

### Getting PPI from Verity Sense (what the firmware does)

1. Scan for devices named `Polar Sense <Device ID>` and connect to the ones on the board's list.
2. Subscribe to Heart Rate Measurement `0x2A37` (HR) and Battery Level `0x2A19`.
3. PMD service `FB005C80-02E7-F387-1CAD-8ACD2D8DF0C8`:
   - enable indications on the control point `FB005C81-…` and notifications on data `FB005C82-…`;
   - write stop PPI (`03 03`), then start PPI (`02 03`) to the control point;
   - parse each PPI frame from the data characteristic into `ppi_ms`, `error_ms` and flags. The first PPI arrives about 25 s after starting.
4. Byte layout: *Polar Measurement Data Specification* in the GitHub repo `polar-ble-sdk` (`technical_documentation` folder).

## Files

```
server.py                 HTTP server: ingest, snapshot every second, live stream (SSE), CSV/Excel download
hrv.py                    Artifact filtering, RMSSD/SDNN, paired before/after comparison
store.py                  SQLite storage
export.py, xlsx.py        Excel export of a whole session (standard library only)
static/                   Dashboard (plain HTML/CSS/JS, no external libraries; IBM Plex Sans Thai font)
config/participants.csv   Device ID → participant code → gateway
firmware/                 ESP32-C6 gateway (ESP-IDF)
tools/mac_gateway.py      Gateway using the Mac's Bluetooth
tests/                    Unit tests
```
