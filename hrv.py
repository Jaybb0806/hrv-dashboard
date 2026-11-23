import math
import statistics
from array import array
from collections import deque
from dataclasses import dataclass

MIN_PPI_MS = 400
MAX_PPI_MS = 1500
MAX_ERR_MS = 20
MAX_REL_DIFF = 0.25
RESET_AFTER_REJECTS = 5
MIN_PAIRS = 40
MIN_QUALITY = 0.6

@dataclass(slots=True)
class Beat:
    t: float
    ppi: int
    clean: bool
    diff: int | None

class BeatClassifier:

    def __init__(self):
        self.recent = deque(maxlen=5)
        self.prev_ppi = None
        self.rejects = 0

    def classify(self, ppi, err=0, blocker=0, contact=1):
        ok = (MIN_PPI_MS <= ppi <= MAX_PPI_MS and not blocker and contact
              and (err is None or err <= MAX_ERR_MS))
        if ok and len(self.recent) >= 3:
            med = statistics.median(self.recent)
            if abs(ppi - med) / med > MAX_REL_DIFF:
                ok = False
                self.rejects += 1
                if self.rejects >= RESET_AFTER_REJECTS:
                    self.recent.clear()
                    self.rejects = 0
        diff = None
        if ok:
            self.rejects = 0
            self.recent.append(ppi)
            if self.prev_ppi is not None:
                diff = ppi - self.prev_ppi
        self.prev_ppi = ppi if ok else None
        return ok, diff

def summarize(beats):

    total = len(beats)
    clean = [b.ppi for b in beats if b.clean]
    diffs = [b.diff for b in beats if b.diff is not None]
    out = {
        "rmssd": None, "sdnn": None, "hr": None,
        "beats": total, "pairs": len(diffs),
        "quality": (len(clean) / total) if total else 0.0,
        "valid": False,
    }
    if len(diffs) >= 2:
        out["rmssd"] = math.sqrt(sum(d * d for d in diffs) / len(diffs))
    if len(clean) >= 2:
        out["sdnn"] = statistics.stdev(clean)
        out["hr"] = 60000 / statistics.fmean(clean)
    out["valid"] = (bool(out["rmssd"]) and len(diffs) >= MIN_PAIRS
                    and out["quality"] >= MIN_QUALITY)
    return out

class Prefix:

    __slots__ = ("clean", "s1", "s2", "pairs", "d2")

    def __init__(self):
        self.clean, self.s1, self.s2, self.pairs, self.d2 = (array("q", [0]) for _ in range(5))

    def add(self, b):
        c, d, p = b.clean, (None if b.diff is None else int(b.diff)), int(b.ppi)
        self.clean.append(self.clean[-1] + (1 if c else 0))
        self.s1.append(self.s1[-1] + (p if c else 0))
        self.s2.append(self.s2[-1] + (p * p if c else 0))
        self.pairs.append(self.pairs[-1] + (d is not None))
        self.d2.append(self.d2[-1] + (d * d if d is not None else 0))

    def summary(self, i, j):
        total, n, pairs = j - i, self.clean[j] - self.clean[i], self.pairs[j] - self.pairs[i]
        out = {"rmssd": None, "sdnn": None, "hr": None, "beats": total, "pairs": pairs,
               "quality": (n / total) if total else 0.0, "valid": False}
        if pairs >= 2:
            out["rmssd"] = math.sqrt((self.d2[j] - self.d2[i]) / pairs)
        if n >= 2:
            s1, s2 = self.s1[j] - self.s1[i], self.s2[j] - self.s2[i]
            out["sdnn"] = math.sqrt(max(0, n * s2 - s1 * s1) / (n * (n - 1)))
            out["hr"] = 60000 * n / s1
        out["valid"] = bool(out["rmssd"]) and pairs >= MIN_PAIRS and out["quality"] >= MIN_QUALITY
        return out

def compare_groups(pre, post):

    pids = [p for p in pre if p in post and pre[p]["valid"] and post[p]["valid"]
            and pre[p]["rmssd"] > 0 and post[p]["rmssd"] > 0]
    if not pids:
        return {"n": 0, "pre_mean": None, "post_mean": None, "pct_change": None,
                "median_pct": None, "n_increased": 0, "ln_pre": None, "ln_post": None}
    a = [pre[p]["rmssd"] for p in pids]
    b = [post[p]["rmssd"] for p in pids]
    pre_mean, post_mean = statistics.fmean(a), statistics.fmean(b)
    pcts = [(y - x) / x * 100 for x, y in zip(a, b)]
    return {
        "n": len(pids),
        "pre_mean": pre_mean,
        "post_mean": post_mean,
        "pct_change": (post_mean - pre_mean) / pre_mean * 100,
        "median_pct": statistics.median(pcts),
        "n_increased": sum(1 for x, y in zip(a, b) if y > x),
        "ln_pre": statistics.fmean(math.log(x) for x in a),
        "ln_post": statistics.fmean(math.log(y) for y in b),
    }
