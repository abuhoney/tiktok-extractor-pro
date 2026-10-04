# v8 Additions — Continuous Live Monitor with Cache + Session Persistence

## Overview

This folder contains the v8 additions to the TikTok Extractor Pro project:

1. **scripts/agents/** — All harvester scripts from v3 to v8 (18 scripts)
2. **scripts/cache/** — Cache manager + session persistence + continuous monitor integration
3. **scripts/android_v8/** — Android service (ContinuousLiveMonitorService.java) for the v1.0.41 APK

## Architecture

```
APK (v1.0.41)
   │
   │ 1. User taps "Start Monitoring" with live URL
   │ 2. APK captures cookies (16 anonymous cookies from check session)
   │ 3. APK starts ContinuousLiveMonitorService (Android Service)
   │
   ▼
ContinuousLiveMonitorService (background)
   │
   │ 1. Polls /webcast/room/enter/?room_id=XXX every 10s
   │ 2. Captures: viewer_count, like_count, diamond_count
   │ 3. Detects events: viewer_gain/loss, likes, gifts, stream_offline
   │ 4. Auto-renews msToken from Set-Cookie
   │ 5. Saves to:
   │    - /sdcard/.../tiktok_deep_data_v8/<unique_id>/observer_<hash>/poll_NNNN.json
   │    - /sdcard/.../tiktok_cache/cache.db (SQLite)
   │    - Uploads to Render server (best-effort)
   │
   ▼
CacheManager (scripts/cache/cache_manager.py)
   │
   │ SQLite tables:
   │ - cache (generic K/V with TTL)
   │ - session_cookies_log (append-only cookie captures)
   │ - mstoken_pool (5-min TTL tokens)
   │ - room_id_lookup (streamer → room_id)
   │ - response_cache (10s TTL HTTP responses)
   │
   ▼
SessionPersistenceProcessor (scripts/cache/session_persistence.py)
   │
   │ SQLite tables:
   │ - sessions (one row per monitoring session)
   │ - session_polls (every poll captured)
   │ - session_events (every event captured)
   │
   │ JSON files:
   │ - /home/z/my-project/download/tiktok_deep_data_v8/_sessions/sess_<hash>.json
   │ - Hierarchical log file
```

## Usage

### On server (Python)

```python
from scripts.cache.continuous_monitor import ContinuousMonitorWithCache
from scripts.agents.v8_pure_observer import NEW_COOKIES

monitor = ContinuousMonitorWithCache()
result = monitor.start_monitoring(
    live_url="https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/",
    cookies=NEW_COOKIES,
)
# ... monitoring runs in background ...
status = monitor.get_status()
final = monitor.stop_monitoring()
```

### On APK (Android)

```java
// In MainActivity
Intent intent = new Intent(this, ContinuousLiveMonitorService.class);
intent.putExtra("action", "start");
intent.putExtra("url", "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/");
intent.putExtra("cookies", allCookiesAsString);
startService(intent);
```

## Files

### scripts/agents/
- `multi_agent_harvester_v2.py` through `multi_agent_harvester_v7.py` — 1000-agent harvesters
- `v8_pure_observer.py` — v8 pure live observer (no interactions)
- `v8_probe_v2.py` through `v8_probe_v6_bruteforce.py` — discovery probes
- `v8_quick_probe.py` — quick verification probe
- `generate_v4_report.py` through `generate_v8_report.py` — PDF report generators

### scripts/cache/
- `cache_manager.py` — SQLite cache with TTL support
- `session_persistence.py` — Session audit trail (SQLite + JSON)
- `continuous_monitor.py` — Integration layer (CacheManager + SessionPersistence + v8 Observer)

### scripts/android_v8/
- `ContinuousLiveMonitorService.java` — Android Service for background monitoring
- (Will be integrated into MainActivity in the v1.0.41 APK build)

## Cache DB Schema

Located at: `/home/z/my-project/download/cache/cache.db`

```sql
-- Generic K/V cache with TTL
CREATE TABLE cache (
    namespace TEXT,
    key TEXT,
    value TEXT,
    created_at REAL,
    expires_at REAL,
    PRIMARY KEY (namespace, key)
);

-- Session cookies log (append-only)
CREATE TABLE session_cookies_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL,
    source TEXT,           -- 'apk', 'server', 'test'
    cookies_json TEXT,
    cookie_count INTEGER,
    living_user_id TEXT,
    has_sessionid INTEGER,  -- 0 or 1
    notes TEXT
);

-- msToken pool (5-min TTL)
CREATE TABLE mstoken_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE,
    captured_at REAL,
    source TEXT,
    http_status INTEGER,
    poll_n INTEGER,
    expires_at REAL
);

-- room_id lookup (streamer → room_id cache)
CREATE TABLE room_id_lookup (
    streamer_user_id TEXT,
    streamer_unique_id TEXT,
    room_id TEXT,
    captured_at REAL,
    is_live INTEGER,
    viewer_count INTEGER,
    title TEXT,
    PRIMARY KEY (streamer_user_id, room_id)
);

-- HTTP response cache (10s TTL)
CREATE TABLE response_cache (
    url_hash TEXT PRIMARY KEY,
    url TEXT,
    response_body TEXT,
    response_status INTEGER,
    response_headers_json TEXT,
    captured_at REAL,
    expires_at REAL
);

-- Sessions (one row per monitoring session)
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    live_url TEXT,
    room_id TEXT,
    unique_id TEXT,
    streamer_user_id TEXT,
    started_at REAL,
    ended_at REAL,
    duration_s REAL,
    total_polls INTEGER,
    total_events INTEGER,
    total_gifts INTEGER,
    total_mstoken_renewals INTEGER,
    cookies_count INTEGER,
    has_sessionid INTEGER,
    final_viewer_count INTEGER,
    final_like_count INTEGER,
    final_diamond_count INTEGER,
    session_dir TEXT,
    json_path TEXT
);

-- Per-poll records
CREATE TABLE session_polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    poll_n INTEGER,
    timestamp REAL,
    elapsed_s REAL,
    http_status INTEGER,
    status_code INTEGER,
    is_live INTEGER,
    viewer_count INTEGER,
    like_count INTEGER,
    diamond_count INTEGER
);

-- Per-event records
CREATE TABLE session_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    event_type TEXT,
    event_timestamp REAL,
    poll_n INTEGER,
    event_data_json TEXT
);
```

## APK v1.0.41 Release

The `ContinuousLiveMonitorService.java` file in `scripts/android_v8/` should be added to the existing Android project under `app/src/main/java/com/tiktok/extractor/` and registered in `AndroidManifest.xml`:

```xml
<service android:name=".ContinuousLiveMonitorService"
         android:exported="false"
         android:foregroundServiceType="dataSync" />
```

The MainActivity should add a new menu option "Start Continuous Monitoring" that:
1. Reads the current cookies from the WebView (using CookieManager.getInstance().getCookie())
2. Starts the ContinuousLiveMonitorService with the live URL + cookies
3. Shows a notification with the current poll count + viewer count

## Integration with Render Server

The Render server (https://tiktok-extractor-pro.onrender.com) should add these new endpoints:

- `POST /api/v8/poll` — receive a single poll from APK
- `POST /api/v8/event` — receive a single event from APK
- `POST /api/v8/session/start` — receive session start metadata
- `POST /api/v8/session/end` — receive final session summary

These endpoints write to the same cache.db on the server, enabling server-side aggregation across multiple APK monitoring sessions.
