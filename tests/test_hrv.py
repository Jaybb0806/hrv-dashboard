import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hrv
from server import HOLD_MIN_S, HOLD_S, PULSE_HOLD_S, Hub
from store import Store

def beats_from(ppis, t0=0.0):
    clf, out, t = hrv.BeatClassifier(), [], t0
    for p in ppis:
        t += p / 1000
        ok, diff = clf.classify(p)
        out.append(hrv.Beat(t, p, ok, diff))
    return out

class RmssdTest(unittest.TestCase):
    def test_known_value(self):
        s = hrv.summarize(beats_from([800, 820] * 30))
        self.assertAlmostEqual(s["rmssd"], 20.0)
        self.assertTrue(s["valid"])
        self.assertAlmostEqual(s["hr"], 60000 / 810, places=3)

    def test_artifact_does_not_inflate_rmssd(self):
        ppis = [800, 820] * 30
        ppis[30] = 400
        s = hrv.summarize(beats_from(ppis))
        self.assertAlmostEqual(s["rmssd"], 20.0)
        self.assertEqual(s["beats"] - 1, sum(1 for b in beats_from(ppis) if b.clean))

    def test_flags_reject(self):
        clf = hrv.BeatClassifier()
        self.assertFalse(clf.classify(800, err=40)[0])
        self.assertFalse(clf.classify(800, blocker=1)[0])
        self.assertFalse(clf.classify(800, contact=0)[0])
        self.assertFalse(clf.classify(2500)[0])

    def test_recovers_after_real_hr_shift(self):
        b = beats_from([1000] * 10 + [700] * 20)
        self.assertTrue(all(x.clean for x in b[-10:]))

    def test_unworn_rhythm_near_350ms_rejected(self):
        s = hrv.summarize(beats_from([350, 352] * 60))
        self.assertEqual(s["quality"], 0)
        self.assertFalse(s["valid"])

    def test_too_few_pairs_is_invalid(self):
        s = hrv.summarize(beats_from([800, 820] * 5))
        self.assertIsNotNone(s["rmssd"])
        self.assertFalse(s["valid"])

class CompareTest(unittest.TestCase):
    def test_paired_only(self):
        ok = lambda r: {"rmssd": r, "valid": True}
        pre = {"P1": ok(20), "P2": ok(40), "P3": ok(30)}
        post = {"P1": ok(30), "P2": ok(36), "P3": {"rmssd": 99, "valid": False}}
        g = hrv.compare_groups(pre, post)
        self.assertEqual(g["n"], 2)
        self.assertEqual(g["n_increased"], 1)
        self.assertAlmostEqual(g["pre_mean"], 30)
        self.assertAlmostEqual(g["post_mean"], 33)
        self.assertAlmostEqual(g["pct_change"], 10)
        self.assertAlmostEqual(g["ln_pre"], (math.log(20) + math.log(40)) / 2)

    def test_empty(self):
        self.assertEqual(hrv.compare_groups({}, {})["n"], 0)

class HubTest(unittest.TestCase):
    def make_hub(self, db):
        return Hub(Store(db), {"AAAA0001": ("P01", "GW-01")})

    def feed(self, hub, t0, seconds, ppis):
        t, i = t0, 0
        while t < t0 + seconds:
            chunk = ppis[i % len(ppis):i % len(ppis) + 1]
            i += 1
            t += chunk[0] / 1000
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 70,
                        "ppi": [[p, 5, 0, 1] for p in chunk]}]}, "10.0.0.2", now=t)
        return t

    def test_hr_hidden_when_not_worn(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            t = hub.session["started"]
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 64,
                        "ppi": [[700, 300, 0, 0]] * 10}]}, "10.0.0.2", now=t + 10)
            self.assertIsNone(hub.sensors["AAAA0001"].hr)

            def clean(at, beats=2):
                hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 72,
                            "ppi": [[830, 5, 0, 1]] * beats}]}, "10.0.0.2", now=at)
                return hub.sensors["AAAA0001"].hr

            self.assertIsNone(clean(t + 40, beats=20))
            self.assertIsNone(clean(t + 40 + PULSE_HOLD_S / 2))
            self.assertEqual(clean(t + 40 + PULSE_HOLD_S), 72)
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 64,
                        "ppi": [[400, 300, 0, 1]] * 40}]}, "10.0.0.2", now=t + 51)
            self.assertIsNone(hub.sensors["AAAA0001"].hr)
            self.assertIsNone(clean(t + 66, beats=20))
            live = hub._snapshot(t + 66)["people"][0]["live"]
            self.assertIsNone(live["hr"])
            self.assertFalse(live["valid"])
            self.assertEqual(clean(t + 66 + PULSE_HOLD_S), 72)
            self.assertIsNotNone(hub._snapshot(t + 66 + PULSE_HOLD_S)["people"][0]["live"]["hr"])
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 75}]}, "10.0.0.2", now=t + 100)
            self.assertIsNone(hub.sensors["AAAA0001"].hr)
            hub.set_mode("hr")
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 75}]}, "10.0.0.2", now=t + 101)
            self.assertEqual(hub.sensors["AAAA0001"].hr, 75)

    def test_hrv_stays_through_small_movements(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            s = hub.sensors["AAAA0001"]
            t = self.feed(hub, hub.session["started"], 90, [800, 820])
            self.assertTrue(s.worn)
            for k in range(HOLD_MIN_S + 1):
                hub._snapshot(t - HOLD_MIN_S + k)

            def flagged(at, beats):
                hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 74,
                            "ppi": [[800, 5, 1, 1]] * beats}]}, "10.0.0.2", now=at)
                return hub._snapshot(at)["people"][0]

            p = flagged(t + 3.2, 4)
            self.assertTrue(s.worn)
            self.assertEqual(p["hr"], 74)
            self.assertTrue(p["live"]["valid"])
            p = flagged(t + 15.2, 15)
            self.assertFalse(s.worn)
            self.assertEqual(p["hr"], 74)
            self.assertTrue(p["live"]["valid"])
            self.assertEqual(p["held"], {"hr": 12, "live": 12})
            p = hub._snapshot(t + 3.2 + HOLD_S + 1)["people"][0]
            self.assertIsNone(p["hr"])
            self.assertFalse(p["live"]["valid"])
            self.assertIsNone(p["held"])

    def test_short_reading_is_not_held(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            t = self.feed(hub, hub.session["started"], 90, [800, 820])
            for k in range(1, HOLD_MIN_S - 5):
                hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "ppi": [[800, 5, 0, 1], [820, 5, 0, 1]]}]}, "10.0.0.2", now=t + k)
                self.assertTrue(hub._snapshot(t + k)["people"][0]["live"]["valid"])
            hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "ppi": [[800, 5, 1, 1]] * 40}]}, "10.0.0.2", now=t + 30)
            p = hub._snapshot(t + 30)["people"][0]
            self.assertFalse(p["live"]["valid"])
            self.assertIsNone(p["held"])

    def test_never_worn_has_nothing_to_hold(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            t = hub.session["started"]
            for i in range(30):
                hub.ingest({"gw": "GW-01", "sensors": [{"id": "aaaa0001", "hr": 64,
                            "ppi": [[700, 300, 0, 0]] * 2}]}, "10.0.0.2", now=t + i)
                p = hub._snapshot(t + i)["people"][0]
                self.assertIsNone(p["hr"])
                self.assertIsNone(p["held"])

    def test_end_to_end_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            db = f"{d}/t.db"
            hub = self.make_hub(db)
            t0 = hub.session["started"]
            hub.set_phase("baseline", now=t0)
            t = self.feed(hub, t0, 120, [800, 820])
            hub.set_phase("post", now=t)
            t = self.feed(hub, t, 120, [800, 830])
            hub.tick(now=t)
            snap = hub._snapshot(t)
            g = snap["group"]["post"]
            self.assertEqual(g["n"], 1)
            self.assertAlmostEqual(g["pre_mean"], 20, delta=0.5)
            self.assertAlmostEqual(g["post_mean"], 30, delta=0.5)
            self.assertEqual(snap["people"][0]["gw"], "GW-01")
            self.assertIsNone(snap["gateways"][0]["wifi"])
            hub.ingest({"gw": "GW-01", "wifi": -61, "sensors": []}, "10.0.0.2", now=t)
            self.assertEqual(hub._snapshot(t)["gateways"][0]["wifi"], -61)

            hub2 = self.make_hub(db)
            g2 = hub2._snapshot(t)["group"]["post"]
            self.assertEqual(g2["n"], 1)
            self.assertAlmostEqual(g2["post_mean"], g["post_mean"], places=6)
            hub2.tick(now=t)
            self.assertIn("P01", hub2.export_csv())

    def test_excel_export(self):
        import io
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            t0 = hub.session["started"]
            hub.set_phase("baseline", now=t0)
            t = self.feed(hub, t0, 120, [800, 820])
            hub.set_phase("post", now=t)
            t = self.feed(hub, t, 120, [800, 830])
            hub.tick(now=t)
            data, name = hub.export_xlsx()
            self.assertTrue(name.endswith(".xlsx"))
            wb = load_workbook(io.BytesIO(data))
            self.assertEqual(wb.sheetnames, ["อ่านก่อน", "Summary", "Participants", "Phase detail", "Group trend",
                                             "Person trend", "HR", "Beats", "Devices"])
            head = [c.value for c in wb["Participants"][1]]
            row = dict(zip(head, [c.value for c in wb["Participants"][2]]))
            self.assertEqual(row["participant"], "P01")
            self.assertAlmostEqual(row["baseline_rmssd"], 20, delta=0.5)
            self.assertAlmostEqual(row["post_rmssd"], 30, delta=0.5)
            self.assertEqual(row["post_direction"], "up")
            beats = wb["Beats"].max_row - 1
            self.assertEqual(beats, len(hub.store.beats(hub.session["id"])))
            self.assertEqual(wb["Group trend"].max_row - 1, len(hub.group_trend))
            summary = [[c.value for c in r] for r in wb["Summary"].iter_rows()]
            self.assertTrue(any(r[0] == "หลังฟัง vs ก่อนฟัง" and abs(r[4] - 50) < 3 for r in summary))
            first = hub.session["id"]
            hub.new_session("next")
            data2, _ = hub.export_xlsx(first)
            row2 = dict(zip(head, [c.value for c in load_workbook(io.BytesIO(data2))["Participants"][2]]))
            self.assertAlmostEqual(row2["post_rmssd"], row["post_rmssd"], delta=0.05)
            self.assertEqual(hub.export_xlsx(999), (None, None))

    def test_gateway_sensor_list(self):
        with tempfile.TemporaryDirectory() as d:
            hub = Hub(Store(f"{d}/t.db"), {"BBBB0002": ("P02", "GW-01"), "AAAA0001": ("P01", "GW-01"),
                                           "CCCC0003": ("P03", "GW-02")})
            self.assertEqual(hub.gateway_sensors("GW-01"), "AAAA0001\nBBBB0002\n")
            self.assertEqual(hub.gateway_sensors("GW-09"), "")

    def test_summary2_switch(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            hub.set_summary(True)
            hub.set_summary2(True)
            snap = hub._snapshot(hub.session["started"] + 1)
            self.assertEqual((snap["summary"], snap["summary2"]), (False, True))
            hub.set_summary(True)
            self.assertEqual((hub.summary, hub.summary2), (True, False))
            hub.set_summary2(True)
            hub.new_session("next")
            self.assertEqual((hub.summary, hub.summary2), (False, False))

    def test_summary_switch(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            self.assertFalse(hub._snapshot(hub.session["started"])["summary"])
            hub.set_summary(True)
            self.assertTrue(hub._snapshot(hub.session["started"])["summary"])
            hub.new_session("รอบที่ 2")
            self.assertFalse(hub._snapshot(hub.session["started"])["summary"])

    def test_new_session_clears_beats(self):
        with tempfile.TemporaryDirectory() as d:
            hub = self.make_hub(f"{d}/t.db")
            self.feed(hub, hub.session["started"], 30, [800, 820])
            hub.new_session("รอบที่ 2")
            self.assertEqual(hub.sensors["AAAA0001"].beats, [])
            self.assertEqual(hub.phases[0][0], "idle")

if __name__ == "__main__":
    unittest.main()
