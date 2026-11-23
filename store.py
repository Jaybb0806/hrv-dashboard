import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS phases (
    session_id INTEGER NOT NULL, phase TEXT NOT NULL, t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS beats (
    session_id INTEGER NOT NULL, sensor TEXT NOT NULL, t REAL NOT NULL,
    ppi INTEGER NOT NULL, err INTEGER, blocker INTEGER, contact INTEGER);
CREATE TABLE IF NOT EXISTS hr (
    session_id INTEGER NOT NULL, sensor TEXT NOT NULL, t REAL NOT NULL,
    hr INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS beats_by_session ON beats(session_id, sensor, t);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def new_session(self, name, t):
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO sessions(name, started) VALUES (?, ?)", (name, t))
            return cur.lastrowid

    def add_phase(self, session_id, phase, t):
        with self.lock, self.db:
            self.db.execute("INSERT INTO phases VALUES (?, ?, ?)",
                            (session_id, phase, t))

    def add_beats(self, rows):

        if rows:
            with self.lock, self.db:
                self.db.executemany(
                    "INSERT INTO beats VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def add_hr(self, rows):
        if rows:
            with self.lock, self.db:
                self.db.executemany("INSERT INTO hr VALUES (?, ?, ?, ?)", rows)

    def get_setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))

    def sessions(self):
        with self.lock:
            return self.db.execute("SELECT id, name, started FROM sessions ORDER BY id DESC").fetchall()

    def session(self, session_id):
        with self.lock:
            return self.db.execute("SELECT id, name, started FROM sessions WHERE id=?", (session_id,)).fetchone()

    def hr(self, session_id):
        with self.lock:
            return self.db.execute(
                "SELECT sensor, t, hr FROM hr WHERE session_id=? ORDER BY t", (session_id,)).fetchall()

    def latest_session(self):
        with self.lock:
            return self.db.execute(
                "SELECT id, name, started FROM sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def phases(self, session_id):
        with self.lock:
            return self.db.execute(
                "SELECT phase, t FROM phases WHERE session_id=? ORDER BY t",
                (session_id,)).fetchall()

    def beats(self, session_id):
        with self.lock:
            return self.db.execute(
                "SELECT sensor, t, ppi, err, blocker, contact FROM beats "
                "WHERE session_id=? ORDER BY sensor, t", (session_id,)).fetchall()
