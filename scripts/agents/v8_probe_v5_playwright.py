#!/usr/bin/env python3
"""v8 probe v5: Use Playwright to load the live page and extract room_id from SIGI_STATE.

The page contains embedded JSON (SIGI_STATE / __UNIVERSAL_DATA_FOR_REHYDRATION__)
which has the room_id. We can then poll /webcast/room/enter/?room_id=XXX with the
cookies to get live stats.
"""
import asyncio, json, re, time
from playwright.async_api import async_playwright

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

async def main():
    print("=" * 80)
    print("Playwright probe: load live page and extract room_id")
    print("=" * 80)
    print(f"URL: {LIVE_URL}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Riyadh",
        )

        # Add cookies
        cookies_for_context = []
        for name, value in NEW_COOKIES.items():
            cookies_for_context.append({
                "name": name,
                "value": value,
                "domain": ".tiktok.com",
                "path": "/",
            })
        await context.add_cookies(cookies_for_context)

        page = await context.new_page()

        print("\n[1] Navigating to live URL (following redirect)...")
        try:
            await page.goto(LIVE_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  goto error: {e}")

        # Wait for content
        await asyncio.sleep(3)

        # Get final URL
        final_url = page.url
        print(f"  Final URL: {final_url}")
        print(f"  Title: {await page.title()}")

        # Get page HTML
        html = await page.content()
        print(f"  HTML size: {len(html)} bytes")

        # Look for embedded JSON patterns
        print("\n[2] Searching for room_id / stream_id in HTML...")

        patterns = [
            ("room_id (numeric)", r'"room_id"\s*:\s*"?(\d{15,25})"?'),
            ("room_id_str", r'"room_id_str"\s*:\s*"(\d{15,25})"'),
            ("liveRoomId", r'"liveRoomId"\s*:\s*"?(\d{15,25})"?'),
            ("LiveRoomId", r'"LiveRoomId"\s*:\s*"?(\d{15,25})"?'),
            ("roomId (capital)", r'"roomId"\s*:\s*"?(\d{15,25})"?'),
            ("roomIdStr", r'"roomIdStr"\s*:\s*"?(\d{15,25})"?'),
            ("streamId", r'"streamId"\s*:\s*"?(\d{15,25})"?'),
            ("stream_id_str", r'"stream_id_str"\s*:\s*"?(\d{15,25})"?'),
            ("owner_user_id", r'"owner_user_id"\s*:\s*"?(\d{10,25})"?'),
            ("user_id (in liveRoom)", r'"user_id"\s*:\s*"?(\d{10,25})"?'),
            ("uniqueId", r'"uniqueId"\s*:\s*"([^"]+)"'),
            ("nickname", r'"nickname"\s*:\s*"([^"]+)"'),
            ("isLive", r'"isLive"\s*:\s*(true|false)'),
            ("webcast.room_id", r'webcast[^"]*room_id["\s:=]+(\d{15,25})'),
            ("status: 2", r'"status"\s*:\s*2\b'),
        ]

        found_room_id = None
        for name, pattern in patterns:
            matches = re.findall(pattern, html)
            if matches:
                # Dedupe
                unique = list(dict.fromkeys(matches))
                print(f"  {name}: {unique[:5]}")
                if "room_id" in name.lower() and not found_room_id:
                    found_room_id = unique[0]

        # Try to extract from SIGI_STATE
        print("\n[3] Looking for SIGI_STATE / UNIVERSAL_DATA JSON blocks...")
        for json_pattern in [
            r'<script[^>]*id="SIGI_STATE"[^>]*>([^<]+)</script>',
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)</script>',
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>([^<]+)</script>',
            r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*</script>',
            r'window\["SIGI_STATE"\]\s*=\s*({.+?})\s*</script>',
        ]:
            m = re.search(json_pattern, html)
            if m:
                json_text = m.group(1)
                print(f"  Found JSON block ({len(json_text)} chars)")
                try:
                    d = json.loads(json_text)
                    # Search recursively for room_id
                    def find_room_ids(obj, path=""):
                        results = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k in ("room_id", "liveRoomId", "roomId", "roomIdStr", "room_id_str") and isinstance(v, (str, int)):
                                    rid = str(v) if not isinstance(v, str) else v
                                    if len(rid) >= 15 and rid.isdigit():
                                        results.append((f"{path}.{k}", rid))
                                results.extend(find_room_ids(v, f"{path}.{k}"))
                        elif isinstance(obj, list):
                            for i, item in enumerate(obj):
                                results.extend(find_room_ids(item, f"{path}[{i}]"))
                        return results

                    rids = find_room_ids(d)
                    if rids:
                        print(f"  Found room_ids in JSON:")
                        for path, rid in rids[:10]:
                            print(f"    {path} = {rid}")
                        if not found_room_id and rids:
                            found_room_id = rids[0][1]
                except Exception as e:
                    print(f"  JSON parse error: {e}")

        # Also try evaluate window state
        print("\n[4] Trying page.evaluate for window state...")
        try:
            sigi = await page.evaluate("() => window.SIGI_STATE || null")
            if sigi:
                print(f"  SIGI_STATE found, keys: {list(sigi.keys())[:10]}")
                # Save it
                with open("/tmp/v8_sigi_state.json", "w") as f:
                    json.dump(sigi, f, indent=2, default=str, ensure_ascii=False)
                # Find room_id
                def find_rids(obj, path=""):
                    r = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("room_id", "liveRoomId", "roomId") and isinstance(v, (str, int)):
                                rid = str(v)
                                if len(rid) >= 15 and rid.isdigit():
                                    r.append((f"{path}.{k}", rid))
                            r.extend(find_rids(v, f"{path}.{k}"))
                    return r
                rids = find_rids(sigi)
                if rids:
                    print(f"  Found {len(rids)} room_ids in SIGI_STATE:")
                    for p, rid in rids[:5]:
                        print(f"    {p} = {rid}")
                    if not found_room_id:
                        found_room_id = rids[0][1]
        except Exception as e:
            print(f"  page.evaluate error: {e}")

        # Save HTML for inspection
        with open("/tmp/v8_live_page.html", "w", encoding="utf-8") as f:
            f.write(html[:200000])
        print(f"\n  HTML saved: /tmp/v8_live_page.html")

        if found_room_id:
            print(f"\n🎉 Found room_id: {found_room_id}")
            with open("/tmp/v8_room_id.json", "w") as f:
                json.dump({"room_id": found_room_id, "found_via": "playwright"}, f, indent=2)
        else:
            print(f"\n⚠️ No room_id found in the page HTML.")
            print(f"   The page may have redirected to /hk/about (geo-blocked).")

        await browser.close()

asyncio.run(main())
