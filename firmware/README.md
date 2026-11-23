# Firmware ESP32-C6 gateway

One board connects up to 7 Polar Verity Sense sensors (3 in practice), requests PPI over the PMD protocol and sends it to `server.py` over WiFi every second, following the protocol in [../README.md](../README.md).

```
Verity Sense ──BLE (HR 0x2A37, Battery 0x2A19, PMD PPI)──▶ ESP32-C6 ──WiFi POST /api/ingest──▶ MacBook
```

- **Requires:** ESP-IDF ≥ 5.3 (5.5 recommended) + the component `h2zero/esp-nimble-cpp` 2.x, which `idf.py` downloads on the first build.
- **Each board's sensor list** comes from the server at boot (`GET /api/gateway/GW-01/sensors`, from the `gateway` column of `config/participants.csv`). To move a sensor to another board, edit the CSV and restart the server and the board; no re-flash needed.
- **If WiFi drops,** beats are kept on the board (about 10 minutes per sensor) and sent once it reconnects. The board drops data only after the server replies 200.
- **If PPI goes quiet** for more than 20 s the board sends start again; after 60 s it disconnects and reconnects. The first 40 s after starting PPI do not count, because Verity Sense takes about 25 s to send its first PPI.
- **If HR goes quiet** for more than 20 s (no notification at all, not even 0) the link is treated as dead: the board disconnects and reconnects. Every reconnect stops PPI first, because a PPI stream left over from the previous connection keeps HR from arriving.
- **Sensor mode** follows the dashboard button sent in the ingest reply: `ppi` turns PPI on for HRV, `hr` turns PPI off so Verity Sense's HR keeps up while people move. Boards switch within about 5 s.

## Install ESP-IDF (once, on a Mac)

```bash
mkdir -p ~/esp && git clone -b v5.5.1 --recursive --depth 1 --shallow-submodules https://github.com/espressif/esp-idf.git ~/esp/esp-idf
```

```bash
~/esp/esp-idf/install.sh esp32c6
```

In every new terminal:

```bash
. ~/esp/esp-idf/export.sh
```

## Settings

Edit [main/config.h](main/config.h):

| Setting | Value |
| --- | --- |
| `WIFI_SSID` / `WIFI_PASS` | The room router's 2.4 GHz WiFi (ESP32-C6 does not support 5 GHz) |
| `SERVER_URL` | IP of the MacBook running `server.py`, e.g. `http://192.168.0.10:8000` (reserve the IP on the router so it does not change) |
| `GATEWAY_ID` | Default `GW-01`; set per board at build time instead (below) |

To keep your real WiFi details out of git, put them in `main/config_local.h` (git-ignored); it overrides `WIFI_*` and `SERVER_URL`.

## Build and flash one board at a time

```bash
cd firmware && idf.py set-target esp32c6
```

Plug in board 1 (find the port with `ls /dev/cu.usb*`):

```bash
idf.py -DGATEWAY_ID=GW-01 -p /dev/cu.usbmodem1101 build flash monitor
```

For the next boards change only `-DGATEWAY_ID=GW-02` … `GW-08`, and label each board with tape to match. Press `Ctrl+]` to leave the monitor.

## Expected log

```
hrv-gw: WiFi up, ip 192.168.0.21
hrv-gw: server assigned 3 sensors to GW-01
hrv-gw: found A1B2C3D4 rssi -58
hrv-gw: connecting A1B2C3D4
hrv-gw: A1B2C3D4 connected, waiting for PPI
hrv-gw: A1B2C3D4 PPI stream started
```

| Log | Meaning / what to do |
| --- | --- |
| `no sensor list from the server yet, retrying` | Cannot reach the server (check the server and the Mac's network). The board keeps asking and does not claim any sensor on its own |
| `no sensor list: claiming the first 7 …` | `AUTO_CLAIM` is on in config.h. Fine for a one-board PoC, **never on the day** |
| `has no PMD service` | The sensor does not expose PMD: update its firmware with the Polar Flow app |
| `PPI start refused: device in charger` | The sensor is still on its charger |
| `PPI start refused: invalid state` | Take the sensor off and put it on again, or restart it |
| `no free BLE client` | `MAX_SENSORS` is higher than `CONFIG_BT_NIMBLE_MAX_CONNECTIONS` |
| `ingest failed (-1)` | Cannot reach the Mac: check the IP, the macOS firewall, and that the server is running |

macOS may ask "Allow incoming connections" for python3 the first time the server runs. Press **Allow**, or the ESP32 boards cannot send data.

## PoC checklist (as soon as 2–3 sensors arrive)

1. [ ] Board 1 + 1 sensor: `PPI stream started` appears and the card on **Live** shows HR and HRV
2. [ ] Compare PPI with a Polar H10 or the Polar Sensor Logger app at the same time: RMSSD should be close
3. [ ] Board 1 + 3 sensors (or 5 if available) for 60 minutes: nothing drops on **Devices** and signal quality is ≥ 90% while sitting still
4. [ ] Unplug the router for 2 minutes and plug it back: the missed stretch comes back in full (no gap in the graph)
5. [ ] Turn a sensor off and on: the board reconnects by itself within 1 minute
6. [ ] Run all 8 boards in the same room: each connects only to its own sensors from the CSV

## Files

```
main/config.h          WiFi, server IP, timings
main/main.cpp          WiFi, BLE (scan/connect/PMD), HTTP POST
main/pmd.h|.cpp        Builds PMD commands and parses PMD frames (no ESP-IDF dependency)
main/payload.h|.cpp    Builds the /api/ingest JSON and reads the sensor list
main/sample_buffer.h   Per-sensor beat queue that keeps data when a POST fails
test/test_host.cpp     Unit tests that run on a Mac, no board needed
```

Run the tests on a Mac:

```bash
cd firmware/test && c++ -std=c++17 -Wall -I../main test_host.cpp ../main/pmd.cpp ../main/payload.cpp -o /tmp/test_host && /tmp/test_host
```
