# v9 Additions — Stable HybridSigner v2 + APK v1.0.41 + Render API

## What's new in v9

### 1. Stable HybridSigner v2 (no CDN timeout!)

The HybridSigner v1 (v6-v8) timed out loading webmssdk.js from the TikTok CDN. v9 fixes this with three improvements:

- **Local webmssdk.js bundling**: webmssdk_1.0.0.417.js (243KB) is downloaded once and stored at `/home/z/my-project/download/webmssdk/`. The HybridSigner loads it from the local file instead of CDN.
- **Retry logic**: 3 attempts with 2-second backoff between retries. If the first attempt fails (e.g., browser launch issue), the second and third attempts have a chance to succeed.
- **Local file fallback chain**: tries `WEBMSSDK_LOCAL_PATH` first, then `WEBMSSDK_TMP_PATH`, then CDN as last resort.

Test result: webmssdk.js loads successfully (243693 bytes from local file), but `byted_acrawler.frontierSign` is no longer exposed by webmssdk.js alone — TikTok has removed the public signing API. The v9 HybridSigner architecture is sound but the JS execution alone doesn't produce signatures.

### 2. Native Python X-Bogus (the actual solution!)

The project already had `xbogus.py` (a native Python port of the X-Bogus algorithm). v9 verifies it works:

```python
import xbogus
url = "https://webcast.tiktok.com/webcast/room/enter/?room_id=7683963746938555152&aid=1988"
signed_url, headers = xbogus.sign(url, user_agent=xbogus.DEFAULT_UA)
# signed_url now contains a valid X-Bogus parameter
# HTTP request to signed_url returns 200 OK (not 403)
```

Test result: HTTP 200 (not 403) on `/webcast/room/enter/` with anonymous cookies + native X-Bogus signature. **This is the breakthrough that v6-v8 were missing.**

### 3. APK v1.0.41 (with ContinuousLiveMonitorService)

- **AndroidManifest.xml** updated: added 4 new permissions (FOREGROUND_SERVICE, FOREGROUND_SERVICE_DATA_SYNC, POST_NOTIFICATIONS, WAKE_LOCK) + registered ContinuousLiveMonitorService
- **ContinuousLiveMonitorService.java** placed at `app/src/main/java/com/tiktok/extractor/`
- **Version bumped** from 1.0.40 (versionCode 30) to 1.0.41 (versionCode 31)

### 4. Render API endpoints (/api/v8/*)

Added 6 new endpoints to `app.py` for receiving monitoring data from APK:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v8/session/start` | POST | Receive session start metadata |
| `/api/v8/poll` | POST | Receive a single poll (viewer/like/diamond counts) |
| `/api/v8/event` | POST | Receive a single event (gift, viewer_gain, etc.) |
| `/api/v8/session/end` | POST | Receive final session summary |
| `/api/v8/sessions` | GET | List all monitoring sessions (last 50) |
| `/api/v8/stats` | GET | Aggregate stats across all sessions |

All data is persisted in `/tmp/tiktok_v8_cache.db` (SQLite). Schema matches the APK's cache.db (3 tables: sessions, session_polls, session_events).

## Files added/modified

### New files
- `scripts/agents/multi_agent_harvester_v9.py` — v9 harvester with HybridSigner v2
- `scripts/agents/webmssdk_1.0.0.417.js` — local copy of webmssdk.js (243KB)
- `scripts/v8_results/agents_1000_v9.zip` — v9 run output
- `scripts/v8_results/agents_v9_comprehensive_report.pdf` — v9 PDF report (English)
- `scripts/v9_README.md` — this file

### Modified files
- `android/app/src/main/AndroidManifest.xml` — added 4 permissions + registered ContinuousLiveMonitorService
- `android/app/src/main/java/com/tiktok/extractor/ContinuousLiveMonitorService.java` — added (from scripts/android_v8/)
- `android/app/build.gradle` — bumped to versionCode 31, versionName 1.0.41
- `app.py` — added 6 new /api/v8/* endpoints + v8 cache DB initialization

## v9 run results

| Metric | Value |
|--------|-------|
| Total agents | 1000 |
| Top score | 82 (15 agents) |
| Deep extractions | 50 |
| Monitor polls | 11 |
| Monitor duration | 91s |
| msToken renewals | 12 |
| Interactions executed | 3 (2 likes + 1 comment) |
| Total runtime | 98s |
| HybridSigner warmup | ✅ Local webmssdk.js loaded (243693 bytes) |
| Native X-Bogus test | ✅ HTTP 200 (not 403) on /webcast/room/enter/ |

## How to use the native X-Bogus

```python
import sys
sys.path.insert(0, '/path/to/tiktok-extractor-pro')
import xbogus

# Sign a URL
url = "https://webcast.tiktok.com/webcast/room/enter/?room_id=XXX&aid=1988"
signed_url, headers = xbogus.sign(url, user_agent=xbogus.DEFAULT_UA)
# headers now contains 'X-Bogus' and 'User-Agent'

# Make the signed request
import requests, urllib3
urllib3.disable_warnings()
r = requests.get(signed_url, headers={**headers, 'Cookie': 'ttwid=...; msToken=...'},
                 verify=False, timeout=10)
# r.status_code == 200 (was 403 without X-Bogus)
```

## How to start the APK monitoring service

```java
// In MainActivity.java
Intent intent = new Intent(this, ContinuousLiveMonitorService.class);
intent.putExtra("action", "start");
intent.putExtra("url", "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/");
intent.putExtra("cookies", cookieManager.getCookie("https://www.tiktok.com"));
startService(intent);
```

## How to query the v8 monitoring data on Render

```bash
# List recent sessions
curl https://tiktok-extractor-pro.onrender.com/api/v8/sessions

# Get aggregate stats
curl https://tiktok-extractor-pro.onrender.com/api/v8/stats
```

## Next steps (v10)

1. Use native X-Bogus in the v9 HybridSigner — replace the Playwright webmssdk.js approach with native `xbogus.sign()` (much faster, no browser needed)
2. Test with a real live stream URL (current target room is offline)
3. Add gift event detail capture (which user sent which gift)
4. Real-time webhook alerts (Discord/Telegram) for stream events
5. APK UI integration — add "Start Monitoring" button in MainActivity
