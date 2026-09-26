#!/usr/bin/env python3
"""v8 probe v3: discover the actual live room_id from the living_user_id cookie.

The living_user_id=887827915334 is the streamer's numeric ID. We need to:
1. Get the user's sec_uid from /api/user/detail/?user_id=887827915334
2. Use the sec_uid to find their currently-active live room_id
3. Poll that room_id for live stats

Note: without sessionid, /api/user/detail/ returns 302. But /webcast/room/info/
with owner_user_id parameter might work with just ttwid.
"""
import requests, urllib3, json, re, time
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

# Try webcast endpoints with owner_user_id (the streamer's user_id)
endpoints = [
    # webcast room info with owner_user_id
    ("webcast/room/info/ by owner_user_id",
     f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}&aid=1988&app_name=tiktok_web&device_platform=web"),
    # webcast room enter with owner_user_id
    ("webcast/room/enter/ by owner_user_id",
     f"https://webcast.tiktok.com/webcast/room/enter/?owner_user_id={LIVING_USER_ID}&aid=1988&app_name=tiktok_web&device_platform=web"),
    # webcast live info by user_id
    ("webcast/live/info/ by user_id",
     f"https://webcast.tiktok.com/webcast/live/info/?user_id={LIVING_USER_ID}&aid=1988"),
    # webcast live info by living_user_id
    ("webcast/live/info/ by living_user_id",
     f"https://webcast.tiktok.com/webcast/live/info/?living_user_id={LIVING_USER_ID}&aid=1988"),
    # webcast room info by user_id
    ("webcast/room/info/ by user_id",
     f"https://webcast.tiktok.com/webcast/room/info/?user_id={LIVING_USER_ID}&aid=1988"),
    # webcast room enter by user_id
    ("webcast/room/enter/ by user_id",
     f"https://webcast.tiktok.com/webcast/room/enter/?user_id={LIVING_USER_ID}&aid=1988"),
    # Try with all required params
    ("webcast/room/info full params",
     f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}&aid=1988&app_name=tiktok_web&device_platform=web&channel=tiktok_web&from_page=live"),
    # Try a different path
    ("webcast/live/room/info",
     f"https://webcast.tiktok.com/webcast/live/room/info/?owner_user_id={LIVING_USER_ID}"),
    # Try api/user/live
    ("api/user/live",
     f"https://www.tiktok.com/api/user/live/?user_id={LIVING_USER_ID}"),
    # Try with sec_uid from cookies (we have it from v6)
    ("webcast/room/info/ by sec_uid",
     "https://webcast.tiktok.com/webcast/room/info/?sec_uid=MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT&aid=1988"),
]

found_room_id = None
found_response = None

for name, url in endpoints:
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
        text = r.text[:400] if r.text else ""
        print(f"\n[{name}]")
        print(f"  HTTP {r.status_code}, size {len(r.text)}")
        if r.status_code == 200 and r.text.startswith("{"):
            d = r.json()
            sc = d.get("status_code")
            print(f"  status_code: {sc}")
            data = d.get("data", {}) if isinstance(d, dict) else {}
            if isinstance(data, dict):
                if "room" in data:
                    room = data["room"]
                    if isinstance(room, dict):
                        rid = room.get("room_id")
                        viewer = room.get("user_count", 0)
                        status = room.get("status")
                        title = room.get("title", "")[:80]
                        print(f"  room_id: {rid}")
                        print(f"  viewer_count: {viewer}")
                        print(f"  status: {status} (2=live)")
                        print(f"  title: {title}")
                        if rid and (status == 2 or viewer > 0):
                            print(f"\n  ✅ LIVE STREAM FOUND: room_id={rid}")
                            found_room_id = rid
                            found_response = d
                            break
                elif "owner" in data:
                    owner = data["owner"]
                    if isinstance(owner, dict):
                        print(f"  owner.nickname: {owner.get('nickname', '')}")
                        print(f"  owner.user_id: {owner.get('user_id', '')}")
                        print(f"  owner.sec_uid: {owner.get('sec_uid', '')[:60]}")
                elif "message" in data:
                    print(f"  data.message: {data.get('message')}")
                else:
                    print(f"  data keys: {list(data.keys())[:10] if isinstance(data, dict) else 'N/A'}")
                    print(f"  full response preview: {json.dumps(d)[:400]}")
            else:
                print(f"  full preview: {text}")
        elif r.status_code == 302:
            loc = r.headers.get("Location", "")[:80]
            print(f"  redirect: {loc}")
        elif r.status_code == 400:
            print(f"  400 Bad Request — endpoint exists but missing required param")
        else:
            print(f"  preview: {text[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

if found_room_id:
    print(f"\n✅ Found live room_id: {found_room_id}")
    print(f"   Saving to /tmp/v8_live_match.json")
    with open("/tmp/v8_live_match.json", "w") as f:
        json.dump(found_response, f, indent=2, default=str, ensure_ascii=False)
else:
    print(f"\n⚠️ No live stream detected. The stream may have ended or requires sessionid.")
    print(f"   Last known room_id from v6 was 7683963746938555152 — it returned 20003 (not live).")

print()
print("=" * 70)
print(f"PROBE v3 COMPLETE")
print("=" * 70)
