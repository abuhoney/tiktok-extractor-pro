#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Session Persistence Processor — v8 additions
=============================================
Records every TikTok session detail to disk + SQLite for audit, replay,
and historical comparison. Used by the v8 Pure Live Observer.

Three storage backends (all used in parallel):
  1. JSON file: tiktok_deep_data_v8/<unique_id>/sessions/sess_<hash>.json
  2. SQLite: sessions table in cache.db (queryable)
  3. Hierarchical: tiktok_deep_data_v8/<unique_id>/sessions/log_<N>.json
"""

import os, json, sqlite3, time, hashlib, threading
from datetime import datetime
from typing import Optional, Dict, Any, List

# Import cache manager (single source of truth for DB path)
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache_manager import CacheManager, DEFAULT_DB_PATH, CACHE_LOCK

SESSIONS_DIR = "/home/z/my-project/download/tiktok_deep_data_v8/_sessions"


class SessionPersistenceProcessor:
    """Records session data to JSON + SQLite for audit and replay.

    Each session is uniquely identified by:
      session_id = MD5(live_url + cookies_hash + timestamp)

    Storage:
      - JSON: SESSIONS_DIR/sess_<hash>.json (complete session snapshot)
      - SQLite: sessions table in cache.db (queryable metadata)
      - Hierarchical: SESSIONS_DIR/log_<N>.json (append-only log)
    """

    def __init__(self, sessions_dir: str = SESSIONS_DIR,
                 cache: Optional[CacheManager] = None):
        self.sessions_dir = sessions_dir
        self.cache = cache or CacheManager()
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._init_sessions_table()

    def _init_sessions_table(self):
        """Initialize sessions table in cache.db."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    live_url TEXT NOT NULL,
                    room_id TEXT,
                    unique_id TEXT,
                    streamer_user_id TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_s REAL,
                    total_polls INTEGER DEFAULT 0,
                    total_events INTEGER DEFAULT 0,
                    total_gifts INTEGER DEFAULT 0,
                    total_mstoken_renewals INTEGER DEFAULT 0,
                    cookies_count INTEGER,
                    has_sessionid INTEGER DEFAULT 0,
                    final_viewer_count INTEGER DEFAULT 0,
                    final_like_count INTEGER DEFAULT 0,
                    final_diamond_count INTEGER DEFAULT 0,
                    session_dir TEXT,
                    json_path TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS session_polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    poll_n INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    elapsed_s REAL,
                    http_status INTEGER,
                    status_code INTEGER,
                    is_live INTEGER,
                    viewer_count INTEGER,
                    like_count INTEGER,
                    diamond_count INTEGER,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_timestamp REAL NOT NULL,
                    poll_n INTEGER,
                    event_data_json TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_polls_sid ON session_polls(session_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_events_sid ON session_events(session_id)")
            conn.commit()
            conn.close()

    def create_session(self, live_url: str, cookies: Dict[str, str],
                       room_id: str = "", unique_id: str = "",
                       streamer_user_id: str = "") -> str:
        """Create a new session record. Returns session_id."""
        # Generate session_id from URL + cookie hash + timestamp
        cookie_hash = hashlib.md5(
            json.dumps(cookies, sort_keys=True).encode()
        ).hexdigest()[:8]
        session_id = hashlib.md5(
            f"{live_url}_{cookie_hash}_{int(time.time())}".encode()
        ).hexdigest()[:16]

        # Save full session JSON
        session_data = {
            "session_id": session_id,
            "live_url": live_url,
            "room_id": room_id,
            "unique_id": unique_id,
            "streamer_user_id": streamer_user_id,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "started_at_epoch": time.time(),
            "cookies": cookies,
            "cookies_count": len(cookies),
            "has_sessionid": "sessionid" in cookies,
            "polls": [],
            "events": [],
            "status": "active",
        }

        json_path = os.path.join(self.sessions_dir, f"sess_{session_id}.json")
        with open(json_path, "w") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)

        # Insert into SQLite
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                INSERT INTO sessions
                (session_id, live_url, room_id, unique_id, streamer_user_id,
                 started_at, cookies_count, has_sessionid, json_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, live_url, room_id, unique_id, streamer_user_id,
                time.time(), len(cookies), 1 if "sessionid" in cookies else 0,
                json_path
            ))
            conn.commit()
            conn.close()

        # Also append to hierarchical log
        self._append_to_log(session_data)

        return session_id

    def record_poll(self, session_id: str, poll_data: Dict):
        """Record a poll result to the session."""
        # Append to JSON file
        json_path = os.path.join(self.sessions_dir, f"sess_{session_id}.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                session_data = json.load(f)
            session_data["polls"].append(poll_data)
            with open(json_path, "w") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False, default=str)

        # Insert into SQLite
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                INSERT INTO session_polls
                (session_id, poll_n, timestamp, elapsed_s, http_status, status_code,
                 is_live, viewer_count, like_count, diamond_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                poll_data.get("poll_n", 0),
                time.time(),
                poll_data.get("elapsed_s"),
                poll_data.get("http_status"),
                poll_data.get("status_code"),
                1 if poll_data.get("is_live") else 0,
                poll_data.get("viewer_count", 0),
                poll_data.get("like_count", 0),
                poll_data.get("diamond_count", 0),
            ))
            # Update session totals
            c.execute("""
                UPDATE sessions SET total_polls = total_polls + 1,
                                    final_viewer_count = ?,
                                    final_like_count = ?,
                                    final_diamond_count = ?
                WHERE session_id = ?
            """, (
                poll_data.get("viewer_count", 0),
                poll_data.get("like_count", 0),
                poll_data.get("diamond_count", 0),
                session_id,
            ))
            conn.commit()
            conn.close()

    def record_event(self, session_id: str, event: Dict):
        """Record an event to the session."""
        # Append to JSON file
        json_path = os.path.join(self.sessions_dir, f"sess_{session_id}.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                session_data = json.load(f)
            session_data["events"].append(event)
            with open(json_path, "w") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False, default=str)

        # Insert into SQLite
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            event_type = event.get("type", "unknown")
            c.execute("""
                INSERT INTO session_events
                (session_id, event_type, event_timestamp, poll_n, event_data_json)
                VALUES (?, ?, ?, ?, ?)
            """, (
                session_id,
                event_type,
                time.time(),
                event.get("poll_n"),
                json.dumps(event, ensure_ascii=False, default=str),
            ))
            c.execute("""
                UPDATE sessions SET total_events = total_events + 1,
                                    total_gifts = total_gifts + ?,
                                    total_mstoken_renewals = total_mstoken_renewals + ?
                WHERE session_id = ?
            """, (
                1 if event_type == "gift_received" else 0,
                1 if event_type == "mstoken_renewed" else 0,
                session_id,
            ))
            conn.commit()
            conn.close()

    def end_session(self, session_id: str, session_dir: str = ""):
        """Mark session as ended."""
        # Update JSON file
        json_path = os.path.join(self.sessions_dir, f"sess_{session_id}.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                session_data = json.load(f)
            session_data["ended_at"] = datetime.utcnow().isoformat() + "Z"
            session_data["ended_at_epoch"] = time.time()
            session_data["duration_s"] = (
                session_data["ended_at_epoch"] - session_data.get("started_at_epoch", time.time())
            )
            session_data["status"] = "completed"
            session_data["session_dir"] = session_dir
            with open(json_path, "w") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False, default=str)

        # Update SQLite
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                UPDATE sessions SET ended_at = ?,
                                    duration_s = ?,
                                    session_dir = ?
                WHERE session_id = ?
            """, (time.time(), time.time() - time.time() if False else 0,
                  session_dir, session_id))
            # Compute actual duration from started_at
            c.execute("""
                SELECT started_at FROM sessions WHERE session_id = ?
            """, (session_id,))
            row = c.fetchone()
            if row:
                duration = time.time() - row[0]
                c.execute("UPDATE sessions SET duration_s = ? WHERE session_id = ?",
                         (duration, session_id))
            conn.commit()
            conn.close()

    def _append_to_log(self, session_data: Dict):
        """Append a session creation entry to the log file."""
        log_file = os.path.join(self.sessions_dir, "log.txt")
        with open(log_file, "a") as f:
            f.write(f"[{session_data['started_at']}] session_id={session_data['session_id']} "
                    f"url={session_data['live_url']} room_id={session_data.get('room_id', '')} "
                    f"cookies={session_data['cookies_count']} "
                    f"sessionid={'yes' if session_data['has_sessionid'] else 'no'}\n")

    def list_sessions(self, limit: int = 20) -> List[Dict]:
        """List recent sessions."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT session_id, live_url, room_id, unique_id, streamer_user_id,
                       started_at, ended_at, duration_s, total_polls, total_events,
                       total_gifts, total_mstoken_renewals, cookies_count, has_sessionid,
                       final_viewer_count, final_like_count, final_diamond_count
                FROM sessions
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))
            rows = c.fetchall()
            conn.close()
            return [
                {
                    "session_id": r[0],
                    "live_url": r[1],
                    "room_id": r[2],
                    "unique_id": r[3],
                    "streamer_user_id": r[4],
                    "started_at": datetime.utcfromtimestamp(r[5]).isoformat() + "Z" if r[5] else None,
                    "ended_at": datetime.utcfromtimestamp(r[6]).isoformat() + "Z" if r[6] else None,
                    "duration_s": r[7],
                    "total_polls": r[8],
                    "total_events": r[9],
                    "total_gifts": r[10],
                    "total_mstoken_renewals": r[11],
                    "cookies_count": r[12],
                    "has_sessionid": bool(r[13]),
                    "final_viewer_count": r[14],
                    "final_like_count": r[15],
                    "final_diamond_count": r[16],
                }
                for r in rows
            ]

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Load full session JSON from disk."""
        json_path = os.path.join(self.sessions_dir, f"sess_{session_id}.json")
        if not os.path.exists(json_path):
            return None
        with open(json_path) as f:
            return json.load(f)

    def stats(self) -> Dict:
        """Aggregate stats across all sessions."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.cache.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(total_polls), 0),
                       COALESCE(SUM(total_events), 0),
                       COALESCE(SUM(total_gifts), 0),
                       COALESCE(SUM(total_mstoken_renewals), 0),
                       COALESCE(SUM(duration_s), 0),
                       SUM(CASE WHEN has_sessionid=1 THEN 1 ELSE 0 END)
                FROM sessions
            """)
            row = c.fetchone()
            conn.close()
            return {
                "total_sessions": row[0],
                "total_polls": row[1],
                "total_events": row[2],
                "total_gifts": row[3],
                "total_mstoken_renewals": row[4],
                "total_duration_s": round(row[5], 1),
                "sessions_with_sessionid": row[6],
            }


if __name__ == "__main__":
    # Demo
    spp = SessionPersistenceProcessor()
    print(f"Sessions dir: {spp.sessions_dir}")
    print(f"Cache DB: {spp.cache.db_path}")
    print(f"Stats: {spp.stats()}")

    # Create a test session
    sid = spp.create_session(
        live_url="https://vt.tiktok.com/test",
        cookies={"ttwid": "test", "msToken": "test_tok"},
        room_id="7683963746938555152",
        unique_id="test_user",
    )
    print(f"Created session: {sid}")

    # Record a poll
    spp.record_poll(sid, {
        "poll_n": 1,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "http_status": 200,
        "viewer_count": 42,
        "like_count": 100,
        "diamond_count": 5,
        "is_live": True,
    })

    # Record an event
    spp.record_event(sid, {
        "type": "gift_received",
        "poll_n": 1,
        "diamond_delta": 5,
    })

    spp.end_session(sid)
    print(f"\nFinal stats: {spp.stats()}")
    print(f"Recent sessions: {spp.list_sessions(1)}")
