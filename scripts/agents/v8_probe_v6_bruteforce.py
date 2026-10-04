#!/usr/bin/env python3
"""v8 probe v6: brute-force candidate room_ids derived from user_id and time.

TikTok snowflake IDs encode a timestamp. living_user_id=887827915334 was
created around 2026-XX. Room IDs for currently-live streams would be in
the 76XX-77XX range (2026 timestamps).

Strategy: try a wide range of plausible room_ids (19-digit snowflake IDs
in the right timestamp range) and find the one that returns viewer_count > 0
or status_code == 0 (live).
"""
import requests, urllib3, json, time, concurrent.futures
urllib3.disable_warnings()

NEW_COOKIES = {
    "tt_csrf_token": "0GoreJbo-W8cC5nqD6NTHm_jFCWQABAjK740",
    "x-web-secsdk-uid": "e998bcb1-954f-45c1-9a23-e89a8dc82e13",
    "ttwid": "1%7CtF6PjTiO3dE37p7IactYRUDWhGwmVspKDkUziSHJo9I%7C1790458009%7Cc81bafdf22f6b54fd62c63764f9ec5b1c6b8ecd5a197e003bcfd5e54ffd12f5c",
    "living_user_id": "887827915334",
    "csrfToken": "N7no2T4b-DEoDiI4SEuOSXAARC3kx3tBwt3E",
    "tt_chain_token": "kdAgkGeQTrIACEXJfdBUDg==",
    "msToken": "83jG5-8Svzmmu0rQvOrj2yIac-SF5TOnA7NGfZwFdYCbEknxcAT-CXT5FIln1260fXJ6PeGok8ZwIhVjV67hb1ZHwrO-3d8Je1_6JpqCXH549iqpIj-AjKCeu1jYm2Hvg8trmjvwb0N2C08rPc5NVzL0wkuEPQ==",
    "odin_tt": "1f6e6c48e7522b2a7b10b9311980f3c9fd3993a34f85abdeb895eda5efba3ca88ad89badc4e1b42ae97feb638834cfd40915b911c884619ddfa4c3bf5cfd016e9e72f2b2450ed196658eb82ed37b0766",
}

UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

cookie_str = "; ".join(f"{k}={v}" for k, v in NEW_COOKIES.items())

# Generate candidate room_ids in the right range
# TikTok snowflake IDs for 2026 are ~7693XXXX (current as of Sep 2026)
# Try a few hundred candidates spread across the likely range
def generate_candidates():
    candidates = []
    # Base patterns: 7 + 18 digits, ranging from 7693000... to 7696000...
    for prefix_int in range(76930, 76961, 5):
        for middle in range(0, 100, 10):
            for suffix in [0, 1, 5, 9]:
                rid = f"{prefix_int}00000{middle:03d}{suffix:013d}"
                candidates.append(rid)
    return candidates

candidates = generate_candidates()
print(f"Generated {len(candidates)} candidate room_ids")
print(f"Sample: {candidates[:3]}")
print()

# Also try the original 50 from v6
fallback_ids = [
    "7683963746938555152", "7658084697712657940", "7123696644457743366",
    "7684779612647263509", "1849444191476121704",
    # Try recent ones — last 4 hours would be ~7693-7696 range
    "7693000000000000000", "7693500000000000000", "7694000000000000000",
    "7694500000000000000", "7695000000000000000", "7695500000000000000",
    "7696000000000000000", "7696500000000000000",
]
candidates = fallback_ids + candidates[:50]
print(f"Total candidates to test: {len(candidates)}")

session = requests.Session()
session.verify = False

def test_room_id(rid):
    url = f"https://webcast.tiktok.com/webcast/room/enter/?room_id={rid}&aid=1988"
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tiktok.com/",
        "Cookie": cookie_str,
    }
    try:
        r = session.get(url, headers=h, timeout=5, allow_redirects=False)
        if r.status_code == 200 and r.text.startswith("{"):
            d = r.json()
            sc = d.get("status_code")
            data = d.get("data", {})
            if isinstance(data, dict) and "room" in data:
                room = data["room"]
                if isinstance(room, dict):
                    status = room.get("status")
                    viewer = room.get("user_count", 0)
                    title = room.get("title", "")[:60]
                    if status == 2 or viewer > 0:
                        return (rid, d, "LIVE", viewer, status, title)
                    elif sc == 0:
                        return (rid, d, "OK", viewer, status, title)
            # If status_code is 10011, this room_id doesn't exist
            # If status_code is 20003, room exists but we need sessionid
            return None
    except Exception:
        return None

found_live = None
tested = 0
start = time.time()

print(f"\nBrute-force testing {len(candidates)} candidates...")
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    futures = {ex.submit(test_room_id, rid): rid for rid in candidates}
    for future in concurrent.futures.as_completed(futures):
        rid = futures[future]
        tested += 1
        try:
            result = future.result()
            if result:
                rid_match, d, status_label, viewer, status, title = result
                print(f"\n  [{tested}/{len(candidates)}] ✅ {status_label}: room_id={rid_match}")
                print(f"    viewer_count: {viewer}")
                print(f"    status: {status}")
                print(f"    title: {title}")
                if status_label == "LIVE":
                    print(f"\n  🎉 LIVE STREAM FOUND!")
                    with open("/tmp/v8_live_match.json", "w") as f:
                        json.dump(d, f, indent=2, default=str, ensure_ascii=False)
                    print(f"  Saved: /tmp/v8_live_match.json")
                    found_live = rid_match
                    # Don't break — keep checking for other live rooms
        except Exception as e:
            pass

        if tested % 20 == 0:
            print(f"  [{tested}/{len(candidates)}] tested, elapsed={time.time()-start:.1f}s", end="\r")

print()
print("=" * 70)
if found_live:
    print(f"FOUND LIVE STREAM: room_id={found_live}")
else:
    print(f"No live stream found in {tested} candidates.")
    print(f"The stream may have ended, or the room_id is outside our test range.")
print(f"Total elapsed: {time.time()-start:.1f}s")
print("=" * 70)
