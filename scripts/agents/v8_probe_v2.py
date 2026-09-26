#!/usr/bin/env python3
"""Probe v2: use living_user_id + sec_uid lookup to find the live room_id."""
import requests, urllib3, json, re
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
LIVING_USER_ID = "887827915334"

session = requests.Session()
session.verify = False
cookie_str = "; ".join(f"{k}={v}" for k, v in NEW_COOKIES.items())

# Step 1: Try /api/user/detail/ with living_user_id to get the sec_uid
print("=" * 70)
print(f"STEP 1: Get user detail for living_user_id={LIVING_USER_ID}")
print("=" * 70)

# Try various endpoints to discover the user's sec_uid
endpoints_to_try = [
    f"https://www.tiktok.com/api/user/detail/?uniqueId={LIVING_USER_ID}",
    f"https://www.tiktok.com/api/user/detail/?user_id={LIVING_USER_ID}",
    f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}",
    f"https://webcast.tiktok.com/webcast/room/info/?user_id={LIVING_USER_ID}",
    f"https://webcast.tiktok.com/webcast/room/info/?room_id={LIVING_USER_ID}",
    # Try common known room_id patterns from the URL hash
    "https://webcast.tiktok.com/webcast/room/enter/?room_id=7683963746938555152",
    "https://webcast.tiktok.com/webcast/room/enter/?room_id=887827915334",
]

for url in endpoints_to_try:
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
        "Cookie": cookie_str,
    }
    try:
        r = session.get(url, headers=h, timeout=8, allow_redirects=False)
        text_preview = r.text[:300]
        print(f"\n  {url[:90]}")
        print(f"    HTTP {r.status_code}, size {len(r.text)}, preview: {text_preview[:200]}")
        if r.status_code == 200 and r.text.startswith("{"):
            try:
                d = r.json()
                sc = d.get("status_code")
                print(f"    status_code: {sc}")
                data = d.get("data", {}) if isinstance(d, dict) else {}
                if isinstance(data, dict):
                    if "message" in data:
                        print(f"    data.message: {data.get('message')}")
                    if "room" in data or "owner" in data:
                        room = data.get("room", {})
                        owner = data.get("owner", {})
                        print(f"    room.title: {room.get('title', '')[:80]}")
                        print(f"    room.user_count: {room.get('user_count', 0)}")
                        print(f"    room.status: {room.get('status', '?')} (2=live)")
                        print(f"    room_id: {room.get('room_id', '')}")
                        print(f"    owner.nickname: {owner.get('nickname', '')}")
                        print(f"    owner.user_id: {owner.get('user_id', '')}")
                        if room.get("status") == 2 or room.get("user_count", 0) > 0:
                            print(f"\n    ✅ LIVE STREAM FOUND!")
                            with open("/tmp/v8_live_match.json", "w") as f:
                                json.dump(d, f, indent=2, default=str, ensure_ascii=False)
                            print(f"    Saved: /tmp/v8_live_match.json")
                            break
            except Exception as e:
                print(f"    parse error: {e}")
    except Exception as e:
        print(f"  {url[:90]}")
        print(f"    ERROR: {e}")

# Step 2: Try sec_uid-based room lookup with the sec_uid from v6 credentials
print()
print("=" * 70)
print("STEP 2: Try sec_uid-based room lookup")
print("=" * 70)

# Try a list of all room_ids we have collected so far (synthetic + real from v6)
test_room_ids = [
    "887827915334",      # living_user_id (short, won't work for room_id but try)
    "7683963746938555152", # original from v6
    # Generate plausible room_ids from current epoch time (TikTok snowflake IDs)
    "7693000000000000000",
    "7694000000000000000",
    "7695000000000000000",
]

# Try /webcast/room/enter/ with each room_id
for rid in test_room_ids:
    url = f"https://webcast.tiktok.com/webcast/room/enter/?room_id={rid}"
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
        "Cookie": cookie_str,
    }
    try:
        r = session.get(url, headers=h, timeout=8, allow_redirects=False)
        if r.status_code == 200 and r.text.startswith("{"):
            d = r.json()
            data = d.get("data", {})
            room = data.get("room", {}) if isinstance(data, dict) else {}
            print(f"  room_id={rid}: status={d.get('status_code')}, "
                  f"is_live={room.get('status')==2 if room else False}, "
                  f"viewer={room.get('user_count', 0) if room else 0}")
            if room and room.get("status") == 2:
                print(f"    ✅ LIVE: {room.get('title', '')[:80]}")
                with open("/tmp/v8_live_match.json", "w") as f:
                    json.dump(d, f, indent=2, default=str, ensure_ascii=False)
                break
    except Exception as e:
        print(f"  room_id={rid}: error {e}")

print()
print("=" * 70)
print("PROBE v2 COMPLETE")
print("=" * 70)
