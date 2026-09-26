#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cache Manager — v8 additions
=============================
Persistent cache for TikTok session data, msToken values, room_id lookups,
and HTTP response cache. Used by the v8 Pure Live Observer and future v9.

Features:
  - SQLite-backed cache (cache.db)
  - TTL support per cache entry
  - 4 cache namespaces:
    * session_cookies — the 16 captured cookies (with timestamps)
    * mstoken_pool     — harvested msToken values (5 min TTL)
    * room_id_lookup   — room_id per streamer (TTL 1 hour)
    * response_cache   — /webcast/room/enter/ response cache (10s TTL)
"""

import os, json, sqlite3, time, hashlib, threading
from datetime import datetime
from typing import Optional, Dict, Any, List

DEFAULT_DB_PATH = "/home/z/my-project/download/cache/cache.db"
CACHE_LOCK = threading.Lock()


class CacheManager:
    """Thread-safe SQLite-backed cache with TTL support."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with required tables."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    PRIMARY KEY (namespace, key)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS session_cookies_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at REAL NOT NULL,
                    source TEXT,
                    cookies_json TEXT NOT NULL,
                    cookie_count INTEGER,
                    living_user_id TEXT,
                    has_sessionid INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS mstoken_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    captured_at REAL NOT NULL,
                    source TEXT,
                    http_status INTEGER,
                    poll_n INTEGER,
                    expires_at REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS room_id_lookup (
                    streamer_user_id TEXT NOT NULL,
                    streamer_unique_id TEXT,
                    room_id TEXT NOT NULL,
                    captured_at REAL NOT NULL,
                    is_live INTEGER DEFAULT 0,
                    viewer_count INTEGER DEFAULT 0,
                    title TEXT,
                    PRIMARY KEY (streamer_user_id, room_id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS response_cache (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    response_body TEXT,
                    response_status INTEGER,
                    response_headers_json TEXT,
                    captured_at REAL NOT NULL,
                    expires_at REAL
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_cache_ns ON cache(namespace)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_log_ts ON session_cookies_log(captured_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mstoken_ts ON mstoken_pool(captured_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_room_lookup ON room_id_lookup(streamer_user_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_resp_cache_exp ON response_cache(expires_at)")
            conn.commit()
            conn.close()

    def set(self, namespace: str, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set a cache value with optional TTL (seconds)."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            expires_at = now + ttl if ttl else None
            value_str = json.dumps(value) if not isinstance(value, str) else value
            c.execute("""
                INSERT OR REPLACE INTO cache (namespace, key, value, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (namespace, key, value_str, now, expires_at))
            conn.commit()
            conn.close()
            return True

    def get(self, namespace: str, key: str) -> Optional[Any]:
        """Get a cache value, returning None if expired or missing."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                SELECT value, expires_at FROM cache
                WHERE namespace=? AND key=?
            """, (namespace, key))
            row = c.fetchone()
            conn.close()
            if row is None:
                return None
            value_str, expires_at = row
            if expires_at is not None and now > expires_at:
                return None
            try:
                return json.loads(value_str)
            except Exception:
                return value_str

    def delete(self, namespace: str, key: str) -> bool:
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("DELETE FROM cache WHERE namespace=? AND key=?", (namespace, key))
            conn.commit()
            conn.close()
            return True

    def list_keys(self, namespace: str) -> List[str]:
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT key FROM cache WHERE namespace=?", (namespace,))
            keys = [row[0] for row in c.fetchall()]
            conn.close()
            return keys

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count of deleted rows."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
            c.execute("DELETE FROM response_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
            c.execute("DELETE FROM mstoken_pool WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
            deleted = c.rowcount
            conn.commit()
            conn.close()
            return deleted

    # ─── Session cookies ───
    def record_session_cookies(self, cookies: Dict[str, str], source: str = "apk",
                                notes: str = "") -> int:
        """Log a session cookie capture (append-only log)."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            has_sid = 1 if "sessionid" in cookies else 0
            living_uid = cookies.get("living_user_id", "")
            c.execute("""
                INSERT INTO session_cookies_log
                (captured_at, source, cookies_json, cookie_count, living_user_id, has_sessionid, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                now, source,
                json.dumps(cookies, ensure_ascii=False),
                len(cookies), living_uid, has_sid, notes
            ))
            log_id = c.lastrowid
            c.execute("""
                INSERT OR REPLACE INTO cache (namespace, key, value, created_at, expires_at)
                VALUES ('session_cookies', 'latest', ?, ?, NULL)
            """, (json.dumps(cookies, ensure_ascii=False), now))
            conn.commit()
            conn.close()
            return log_id

    def get_latest_session_cookies(self) -> Optional[Dict[str, str]]:
        """Get the most recently captured session cookies."""
        return self.get("session_cookies", "latest")

    def list_session_captures(self, limit: int = 20) -> List[Dict]:
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT id, captured_at, source, cookie_count, living_user_id,
                       has_sessionid, notes
                FROM session_cookies_log
                ORDER BY captured_at DESC
                LIMIT ?
            """, (limit,))
            rows = c.fetchall()
            conn.close()
            return [
                {
                    "id": r[0],
                    "captured_at": datetime.utcfromtimestamp(r[1]).isoformat() + "Z",
                    "source": r[2],
                    "cookie_count": r[3],
                    "living_user_id": r[4],
                    "has_sessionid": bool(r[5]),
                    "notes": r[6],
                }
                for r in rows
            ]

    # ─── msToken pool ───
    def add_mstoken(self, token: str, source: str = "observer", http_status: int = 0,
                    poll_n: int = 0, ttl: int = 300) -> bool:
        """Add a fresh msToken to the pool (5 min TTL by default)."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                INSERT OR REPLACE INTO mstoken_pool
                (token, captured_at, source, http_status, poll_n, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (token, now, source, http_status, poll_n, now + ttl))
            conn.commit()
            conn.close()
            return True

    def get_valid_mstoken(self) -> Optional[str]:
        """Get the most recent non-expired msToken from the pool."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                SELECT token FROM mstoken_pool
                WHERE expires_at > ?
                ORDER BY captured_at DESC LIMIT 1
            """, (now,))
            row = c.fetchone()
            conn.close()
            return row[0] if row else None

    def list_mstokens(self, limit: int = 50) -> List[Dict]:
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT token, captured_at, source, http_status, poll_n, expires_at
                FROM mstoken_pool
                ORDER BY captured_at DESC LIMIT ?
            """, (limit,))
            rows = c.fetchall()
            conn.close()
            return [
                {
                    "token_preview": r[0][:60] + "...",
                    "captured_at": datetime.utcfromtimestamp(r[1]).isoformat() + "Z",
                    "source": r[2],
                    "http_status": r[3],
                    "poll_n": r[4],
                    "is_expired": time.time() > r[5] if r[5] else False,
                }
                for r in rows
            ]

    # ─── room_id lookup ───
    def record_room_id(self, streamer_user_id: str, room_id: str,
                       streamer_unique_id: str = "", is_live: bool = False,
                       viewer_count: int = 0, title: str = "") -> bool:
        """Record a discovered room_id for a streamer."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                INSERT OR REPLACE INTO room_id_lookup
                (streamer_user_id, streamer_unique_id, room_id, captured_at, is_live, viewer_count, title)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                streamer_user_id, streamer_unique_id, room_id, now,
                1 if is_live else 0, viewer_count, title
            ))
            conn.commit()
            conn.close()
            return True

    def lookup_room_id(self, streamer_user_id: str) -> Optional[str]:
        """Find the most recent room_id for a streamer (cached lookup)."""
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT room_id FROM room_id_lookup
                WHERE streamer_user_id=?
                ORDER BY captured_at DESC LIMIT 1
            """, (streamer_user_id,))
            row = c.fetchone()
            conn.close()
            return row[0] if row else None

    # ─── Response cache ───
    def cache_response(self, url: str, body: str, status: int, headers: Dict,
                       ttl: int = 10) -> bool:
        """Cache an HTTP response for short-term reuse."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                INSERT OR REPLACE INTO response_cache
                (url_hash, url, response_body, response_status, response_headers_json, captured_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                url_hash, url, body, status,
                json.dumps(dict(headers), ensure_ascii=False),
                now, now + ttl
            ))
            conn.commit()
            conn.close()
            return True

    def get_cached_response(self, url: str) -> Optional[Dict]:
        """Return cached response if not expired."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                SELECT response_body, response_status, response_headers_json, captured_at, expires_at
                FROM response_cache
                WHERE url_hash=? AND expires_at > ?
            """, (url_hash, now))
            row = c.fetchone()
            conn.close()
            if row is None:
                return None
            return {
                "body": row[0],
                "status": row[1],
                "headers": json.loads(row[2]) if row[2] else {},
                "captured_at": datetime.utcfromtimestamp(row[3]).isoformat() + "Z",
                "expires_at": datetime.utcfromtimestamp(row[4]).isoformat() + "Z",
            }

    # ─── Stats ───
    def stats(self) -> Dict:
        with CACHE_LOCK:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            stats = {}
            for table in ["cache", "session_cookies_log", "mstoken_pool",
                           "room_id_lookup", "response_cache"]:
                c.execute(f"SELECT COUNT(*) FROM {table}")
                stats[table] = c.fetchone()[0]
            conn.close()
            return stats


_default_cache = None

def get_cache() -> CacheManager:
    """Get the default cache instance (singleton)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = CacheManager()
    return _default_cache


if __name__ == "__main__":
    cache = CacheManager()
    print(f"Cache DB: {cache.db_path}")
    print(f"Stats: {cache.stats()}")

    test_cookies = {
        "ttwid": "1%7Ctest%7C1234",
        "msToken": "test_token_xyz",
        "living_user_id": "887827915334",
    }
    log_id = cache.record_session_cookies(test_cookies, source="test")
    print(f"Logged session cookies, log_id={log_id}")

    latest = cache.get_latest_session_cookies()
    print(f"Latest cookies: {list(latest.keys()) if latest else None}")

    cache.add_mstoken("test_token_1", source="test", http_status=200, poll_n=1)
    cache.add_mstoken("test_token_2", source="test", http_status=200, poll_n=2)
    print(f"Valid msToken: {cache.get_valid_mstoken()[:30]}...")

    cache.record_room_id("887827915334", "7683963746938555152",
                        streamer_unique_id="live_fest2026", is_live=False)
    print(f"Lookup room_id: {cache.lookup_room_id('887827915334')}")

    print(f"\nFinal stats: {cache.stats()}")
