#!/usr/bin/env python3
"""v8 probe v4: discover the live room_id using webcast endpoints with proper app params.

Based on TikTok web app's actual webcast.tiktok.com params.
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

# Try webcast with full app params (used by TikTok web SDK)
print("=" * 80)
print("TRY 1: webcast/room/info/ with proper TikTok web app params")
print("=" * 80)

# TikTok web app uses these params:
APP_PARAMS = {
    "aid": "1988",
    "app_name": "tiktok_web",
    "device_platform": "web",
    "channel": "tiktok_web",
    "webcast_sdk_version": "1.0.0-beta.0.417",
    "region": "YE",
    "prefetch_addr": "",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "en-US",
    "browser_platform": "Linux x86_64",
    "browser_name": "Mozilla",
    "browser_version": "5.0",
    "web_rid": "",  # webcast room id, empty for room/info
    "room_id": "",
    "enter_source": "",
    "is_need_multi_stream": "true",
    "linkmic_version": "1.0.7-beta.0",
    "identity": "audience",
}

endpoints_to_try = [
    # 1. webcast/room/info with owner_user_id (the streamer)
    f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 2. webcast/room/info with user_id_str (newer param name)
    f"https://webcast.tiktok.com/webcast/room/info/?user_id_str={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 3. webcast/room/info with enter_from
    f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}&enter_from=web_live&a=1&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 4. webcast/room/info/ with enter_source=web_live
    f"https://webcast.tiktok.com/webcast/room/info/?owner_user_id={LIVING_USER_ID}&enter_source=web_live&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 5. webcast/room/enter/ with owner_user_id (might give 403 without proper X-Bogus, but try)
    f"https://webcast.tiktok.com/webcast/room/enter/?owner_user_id={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 6. /webcast/room/web/enter/ (alternate path)
    f"https://webcast.tiktok.com/webcast/room/web/enter/?owner_user_id={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 7. /webcast/room/web/info/
    f"https://webcast.tiktok.com/webcast/room/web/info/?owner_user_id={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 8. /api-live/user/info/
    f"https://api-live.tiktok.com/api-live/user/info/?user_id={LIVING_USER_ID}",
    # 9. /webcast/live/info/ with all params
    f"https://webcast.tiktok.com/webcast/live/info/?owner_user_id={LIVING_USER_ID}&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
    # 10. Try sec_uid from earlier captures
    "https://webcast.tiktok.com/webcast/room/info/?sec_uid=MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT&" + "&".join(f"{k}={v}" for k, v in APP_PARAMS.items()),
]

found = False
for i, url in enumerate(endpoints_to_try, 1):
    short = url[:120]
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
        "Cookie": cookie_str,
        "X-CSRF-Token": NEW_COOKIES["tt_csrf_token"],
        "X-Wid": LIVING_USER_ID,
    }
    try:
        r = session.get(url, headers=h, timeout=8, allow_redirects=False)
        text = r.text[:500] if r.text else ""
        print(f"\n[{i}] {short}...")
        print(f"  HTTP {r.status_code}, size {len(r.text)}")

        if r.status_code == 200 and r.text.startswith("{"):
            d = r.json()
            sc = d.get("status_code")
            print(f"  status_code: {sc}")
            if isinstance(d.get("data"), dict):
                data = d["data"]
                print(f"  data keys: {list(data.keys())[:15]}")
                if "room" in data:
                    room = data["room"]
                    if isinstance(room, dict):
                        rid = room.get("room_id")
                        viewer = room.get("user_count", 0)
                        status = room.get("status")
                        title = room.get("title", "")[:80]
                        print(f"  ✅ room_id: {rid}")
                        print(f"     viewer_count: {viewer}")
                        print(f"     status: {status} (2=live)")
                        print(f"     title: {title}")
                        if rid and (status == 2 or viewer > 0):
                            print(f"\n  🎉 LIVE STREAM FOUND!")
                            with open("/tmp/v8_live_match.json", "w") as f:
                                json.dump(d, f, indent=2, default=str, ensure_ascii=False)
                            print(f"  Full response saved: /tmp/v8_live_match.json")
                            found = True
                            break
                if "owner" in data:
                    owner = data["owner"]
                    if isinstance(owner, dict):
                        print(f"  owner.nickname: {owner.get('nickname', '')}")
                        print(f"  owner.user_id: {owner.get('user_id', '')}")
                        print(f"  owner.sec_uid: {owner.get('sec_uid', '')[:60]}")
                if "message" in data:
                    print(f"  data.message: {data.get('message')}")
            else:
                print(f"  preview: {text[:300]}")
        elif r.status_code == 302:
            print(f"  redirect: {r.headers.get('Location', '')[:100]}")
        elif r.status_code == 403:
            print(f"  403 Forbidden — X-Bogus required")
        elif r.status_code == 400:
            print(f"  400 Bad Request")
        else:
            print(f"  preview: {text[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

if not found:
    print("\n" + "=" * 80)
    print("No live stream found via any endpoint.")
    print("The stream may have ended, or it requires X-Bogus signature.")
    print("=" * 80)
