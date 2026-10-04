#!/usr/bin/env python3
"""Quick probe to verify the live stream is currently active with the new cookies."""
import requests, urllib3, json, re
urllib3.disable_warnings()

# The 16 new cookies from APK check session
NEW_COOKIES = {
    "tt_csrf_token": "0GoreJbo-W8cC5nqD6NTHm_jFCWQABAjK740",
    "x-web-secsdk-uid": "e998bcb1-954f-45c1-9a23-e89a8dc82e13",
    "tiktok_webapp_theme_source": "auto",
    "tiktok_webapp_theme": "dark",
    "delay_guest_mode_vid": "8",
    "g_state": '{"i_l":0,"i_ll":1789759268742,"i_b":"5rxWlbcqJNypZVyn3Dltn+jWlfEmxPg11M51TIBR1HE","i_e":{"enable_itp_optimization":24},"i_et":1789759268742}',
    "use_live_desktop_arch": "edenx3",
    "_tea_utm_cache_1988": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
    "ttwid": "1%7CtF6PjTiO3dE37p7IactYRUDWhGwmVspKDkUziSHJo9I%7C1790458009%7Cc81bafdf22f6b54fd62c63764f9ec5b1c6b8ecd5a197e003bcfd5e54ffd12f5c",
    "living_user_id": "887827915334",
    "_tea_utm_cache_345918": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
    "csrfToken": "N7no2T4b-DEoDiI4SEuOSXAARC3kx3tBwt3E",
    "tt_chain_token": "kdAgkGeQTrIACEXJfdBUDg==",
    "msToken": "83jG5-8Svzmmu0rQvOrj2yIac-SF5TOnA7NGfZwFdYCbEknxcAT-CXT5FIln1260fXJ6PeGok8ZwIhVjV67hb1ZHwrO-3d8Je1_6JpqCXH549iqpIj-AjKCeu1jYm2Hvg8trmjvwb0N2C08rPc5NVzL0wkuEPQ==",
    "odin_tt": "1f6e6c48e7522b2a7b10b9311980f3c9fd3993a34f85abdeb895eda5efba3ca88ad89badc4e1b42ae97feb638834cfd40915b911c884619ddfa4c3bf5cfd016e9e72f2b2450ed196658eb82ed37b0766",
    "_tea_utm_cache_1992": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
}

LIVE_URL = "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/"

UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

# Step 1: follow redirect to get the real /@username/live URL
print("=" * 70)
print("PROBE 1: Following redirect from short URL")
print("=" * 70)
session = requests.Session()
session.verify = False
resp = session.head(LIVE_URL, allow_redirects=True, timeout=10,
                    headers={"User-Agent": UA})
print(f"Final URL: {resp.url}")

# Extract unique_id
m = re.search(r'/@([^/]+)/live', resp.url)
unique_id = m.group(1) if m else None
print(f"Streamer unique_id: {unique_id}")
print(f"living_user_id cookie: {NEW_COOKIES['living_user_id']}")

# Step 2: try to find room_id by scraping the live page
print()
print("=" * 70)
print("PROBE 2: Fetching live page to extract room_id")
print("=" * 70)
headers = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Cookie": "; ".join(f"{k}={v}" for k, v in NEW_COOKIES.items()),
}
resp = session.get(resp.url, headers=headers, timeout=15, allow_redirects=False)
print(f"HTTP status: {resp.status_code}")
print(f"Response size: {len(resp.text)} bytes")

# Look for room_id / stream_id / liveRoomId in the HTML
for pattern_name, pattern in [
    ("room_id", r'"room_id"\s*:\s*"?(\d{15,25})"?'),
    ("liveRoomId", r'"liveRoomId"\s*:\s*"?(\d{15,25})"?'),
    ("stream_id", r'"stream_id"\s*:\s*"?(\d{15,25})"?'),
    ("LiveRoomId", r'"LiveRoomId"\s*:\s*"?(\d{15,25})"?'),
    ("roomId", r'"roomId"\s*:\s*"?(\d{15,25})"?'),
    ("owner_id", r'"owner_id"\s*:\s*"?(\d{10,25})"?'),
    ("owner_user_id", r'"user_id"\s*:\s*"?(\d{10,25})"?'),
    ("isLive", r'"isLive"\s*:\s*(true|false)'),
    ("status", r'"status"\s*:\s*(\d+)'),
]:
    m = re.search(pattern, resp.text)
    if m:
        print(f"  Found {pattern_name}: {m.group(1)}")

# Step 3: try /webcast/room/enter/ with various candidate room_ids
# From the URL hash ZS9AGo6U7ML we may extract one; try also living_user_id
print()
print("=" * 70)
print("PROBE 3: Polling /webcast/room/enter/ with various candidate room_ids")
print("=" * 70)

# Get more candidate room_ids from the HTML
candidate_ids = set(re.findall(r'(\d{18,20})', resp.text))
print(f"  Found {len(candidate_ids)} candidate 18-20 digit IDs in HTML")
print(f"  Sample: {list(candidate_ids)[:5]}")

# Try the most promising ones — fetch each one
test_ids = list(candidate_ids)[:10] if candidate_ids else []
# Also add the living_user_id (it's 12 digits, too short for room_id, but try)
# Try the SIGI_STATE / UNIVERSAL_DATA for the proper live room ID
m = re.search(r'"roomId"\s*:\s*"?(\d{15,25})"?', resp.text) or \
    re.search(r'"room_id_str"\s*:\s*"?(\d{15,25})"?', resp.text) or \
    re.search(r'"roomIdStr"\s*:\s*"?(\d{15,25})"?', resp.text)
if m:
    test_ids.insert(0, m.group(1))
    print(f"  Added primary room_id candidate: {m.group(1)}")

# Try each candidate
for rid in test_ids[:8]:
    url = f"https://webcast.tiktok.com/webcast/room/enter/?room_id={rid}"
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": resp.url,
        "Origin": "https://www.tiktok.com",
        "Cookie": "; ".join(f"{k}={v}" for k, v in NEW_COOKIES.items()),
    }
    try:
        r = session.get(url, headers=h, timeout=8, allow_redirects=False)
        try:
            d = r.json()
            sc = d.get("status_code")
            data = d.get("data", {}) if isinstance(d, dict) else {}
            room = data.get("room", {}) if isinstance(data, dict) else {}
            owner = data.get("owner", {}) if isinstance(data, dict) else {}
            viewer_count = room.get("user_count", 0) if isinstance(room, dict) else 0
            title = room.get("title", "")[:80] if isinstance(room, dict) else ""
            is_live = (room.get("status") == 2) if isinstance(room, dict) else False
            print(f"  room_id={rid}: status_code={sc}, http={r.status_code}, "
                  f"viewer_count={viewer_count}, is_live={is_live}, title='{title}'")
            if is_live or viewer_count > 0 or (title and "doesn't login" not in str(data)):
                print(f"    ✅ LIVE stream found!")
                print(f"    Owner: {owner.get('nickname', '')} (id={owner.get('user_id', '')})")
                print(f"    Title: {room.get('title', '')}")
                print(f"    Viewer count: {room.get('user_count', 0)}")
                print(f"    Like count: {room.get('like_count', 0)}")
                print(f"    Diamond count: {room.get('diamond_count', 0)}")
                print(f"    Total fans: {room.get('total_fans', 0)}")
                print(f"    Stream status: {room.get('status')} (2=live)")
                print(f"    Stream ID: {room.get('stream_id', '')}")
                print(f"    Room ID: {room.get('room_id', '')}")
                print(f"    Title: {room.get('title', '')}")
                print(f"    Cover URL: {room.get('cover_url', '')[:100]}")
                # Dump full response
                with open("/tmp/live_probe_match.json", "w") as f:
                    json.dump(d, f, indent=2, default=str, ensure_ascii=False)
                print(f"\n  Full response saved: /tmp/live_probe_match.json")
                break
        except Exception as e:
            print(f"  room_id={rid}: parse error {e}, text[:200]={r.text[:200]}")
    except Exception as e:
        print(f"  room_id={rid}: error {e}")

print()
print("=" * 70)
print("PROBE COMPLETE")
print("=" * 70)
