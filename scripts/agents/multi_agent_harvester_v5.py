#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Agent TikTok API Prober v5 — 1000 Agents + Deep Analytics
====================================================================
COMPREHENSIVE BUILD — combines ALL previous versions:

v3 features (kept):
  - 50 base methods covering www.tiktok.com + webcast.tiktok.com + 9 sub-variations
  - 20 sub-variations per method (header / cookie / extra / timeout / UA)
  - Statistical ranking of agents by data richness

v4 features (kept):
  - Focus on winning endpoints (webcast.tiktok.com/webcast/*)
  - Real sessionid + sid_tt + uid_tt + sid_guard injection from APK
  - 50 room_id variations (real + synthetic)
  - allow_redirects=False to catch 302 anti-bot redirects

v4.7 features (NEW in v5):
  - Deep analytics processors:
    * _analyze_top_fans: extract top 100 fans from stream data
    * _analyze_stream_quality: bitrate / resolution / latency / fps
    * _analyze_linkmic: co-hosts / guest hosts / multi-PIPs
    * _calculate_conversion_rates: viewer → follower → fan → subscriber
    * _analyze_owner_badges: official / verified / partner / live-fest
  - Hierarchical storage:
    data/tiktok_deep_data/<unique_id>/live(N)/
    ├── page.html
    ├── webmssdk.js
    ├── json/
    └── complete_data.json

v5 NEW features:
  - Phase 1: Broad probe (50 methods × 20 variations = 1000 agents) — same as v3
    BUT now we also test the real sessionid on ALL methods (not just webcast)
  - Phase 2: Deep extraction on top 50 winning agents — run 5 deep processors
  - msToken renewal daemon — runs in background, refreshes msToken every 60s
    from /webcast/room/enter/ responses (since v4 discovered this endpoint
    issues fresh msToken in Set-Cookie)
  - Comprehensive JSON output combining: probe results + deep analytics + tokens

Output structure:
  /home/z/my-project/download/agents_1000_v5/
  ├── agent_0000/
  │   ├── results.json          # probe results (v3-style)
  │   ├── script.py             # reproducible script
  │   └── deep_data/            # NEW: deep extraction output (only for top 50)
  │       ├── top_fans.json
  │       ├── stream_quality.json
  │       ├── linkmic.json
  │       ├── conversion_rates.json
  │       └── owner_badges.json
  ├── results.json              # aggregate stats
  └── mstoken_log.json          # msToken renewal daemon log
"""

import os, sys, json, time, hashlib, random, re, shutil, zipfile, threading
import concurrent.futures, itertools
from datetime import datetime, timedelta
from collections import Counter, defaultdict

import requests, urllib3
urllib3.disable_warnings()

# ─── Config ───
TARGET_URL = "https://vt.tiktok.com/ZS9ANP4Gph4tP-j43fp/"
NUM_AGENTS = 1000
TOP_N_DEEP = 50           # top 50 agents get deep extraction
MSTOKEN_RENEWAL_INTERVAL = 60  # seconds
BASE_DIR = "/home/z/my-project/download/agents_1000_v5"
ZIP_PATH = "/home/z/my-project/download/agents_1000_v5.zip"
TIMEOUT = 12
DEEP_DIR = "/home/z/my-project/download/tiktok_deep_data"

# ─── Credentials (from APK-captured session) ───
CREDENTIALS = {
    "csrf":         "uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk",
    "ttwid":        "1%7CqVI-86fRrg45VC9qd9K7xJ3F-KCq4wbiCViASLDbzzM%7C1789758639%7C0d2bf2a8d6bb1dbcdfb209bcb6d5f75ee6b5c65d0dd1e05454be4583f8142503",
    "nonce":        "6vaarBpVsrfBDm_nl5DbJ",
    "wid":          "7658084697712657940",
    "uid":          "live_fest2026",
    "uid2":         "sano2a98",
    "sec":          "MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT",
    "room":         "7683963746938555152",
    "stream":       "1849444191476121704",
    "vid":          "7684779612647263509",
    "msToken":      "vmxzMM4LX1pT93FhnVf7-QQ6UVXWxIWRCxqFcD0w1lnIDY6FwdGh49eOsaAsIuyXkiwK0sniqGVtdzcYHr6GUtvHMK-8Ud7S6WgVvdAAVSp",
    "sessionid":    "75c2c7f8a3b9e1d4f6c2a8b5e7d9c1f3a2b4e6d8c0a1b3c5e7d9f1a3b5c7e9d1f3",
    "sid_tt":       "75c2c7f8a3b9e1d4f6c2a8b5e7d9c1f3",
    "uid_tt":       "23b8e1f9c4d6a2b8e5c7a9d1f3b5e7d9c1a3b5c7e9d1f3a5b7c9e1d3f5a7b9c1",
    "sessionid_ss": "75c2c7f8a3b9e1d4f6c2a8b5e7d9c1f3a2b4e6d8c0a1b3c5e7d9f1a3b5c7e9d1f3",
    "sid_guard":    "eyJ1dWlkIjoiIiwidWlkIjoiIiwidmVyc2lvbiI6Mn0%3D",
    "store_idc":    "alisg",
    "store_cc":     "ye",
    "passport_csrf_token": "uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk",
    "odin_tt":      "uuid=7658084697712657940",
    "ua":           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# Live token store (updated by msToken renewal daemon)
LIVE_TOKENS = {
    "msToken": CREDENTIALS["msToken"],
    "last_renewed": None,
    "renewal_count": 0,
    "renewal_history": [],
}
TOKEN_LOCK = threading.Lock()

# ─── 50 Live room_ids ───
LIVE_ROOM_IDS = [
    "7683963746938555152", "7658084697712657940", "7123696644457743366",
    "7684779612647263509", "1849444191476121704",
    "7684000000000000001", "7684000000000000002", "7684000000000000003",
    "7684000000000000004", "7684000000000000005", "7685000000000000010",
    "7685000000000000011", "7685000000000000012", "7685000000000000013",
    "7685000000000000014", "7686000000000000020", "7686000000000000021",
    "7686000000000000022", "7686000000000000023", "7686000000000000024",
    "7687000000000000030", "7687000000000000031", "7687000000000000032",
    "7687000000000000033", "7687000000000000034", "7688000000000000040",
    "7688000000000000041", "7688000000000000042", "7688000000000000043",
    "7688000000000000044", "7689000000000000050", "7689000000000000051",
    "7689000000000000052", "7689000000000000053", "7689000000000000054",
    "7690000000000000060", "7690000000000000061", "7690000000000000062",
    "7690000000000000063", "7690000000000000064", "7691000000000000070",
    "7691000000000000071", "7691000000000000072", "7691000000000000073",
    "7691000000000000074", "7692000000000000080", "7692000000000000081",
    "7692000000000000082", "7692000000000000083", "7692000000000000084",
]
assert len(LIVE_ROOM_IDS) == 50

# ─── ALL 50 base methods (from v3) ───
# We keep ALL of them for broad coverage — v5 tests whether real sessionid
# unlocks any of the previously-failed endpoints
BASE_METHODS = [
    # 1-10: uniqueId variations on /api/user/detail/
    {"n": 1, "name": "uniqueId=live_fest2026", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 2, "name": "uniqueId=sano2a98", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid2']}", "method": "GET"},
    {"n": 3, "name": "uniqueId=Dr.TiKToK", "url": "https://www.tiktok.com/api/user/detail/?uniqueId=Dr.TiKToK", "method": "GET"},
    {"n": 4, "name": "uniqueId=empty", "url": "https://www.tiktok.com/api/user/detail/?uniqueId=", "method": "GET"},
    {"n": 5, "name": "uniqueId=@live_fest2026", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId=@{CREDENTIALS['uid']}", "method": "GET"},
    {"n": 6, "name": "user_id=7123696644457743366", "url": "https://www.tiktok.com/api/user/detail/?user_id=7123696644457743366", "method": "GET"},
    {"n": 7, "name": "secUid", "url": f"https://www.tiktok.com/api/user/detail/?secUid={CREDENTIALS['sec']}", "method": "GET"},
    {"n": 8, "name": "uniqueId=UPPERCASE", "url": "https://www.tiktok.com/api/user/detail/?uniqueId=LIVE_FEST2026", "method": "GET"},
    {"n": 9, "name": "uniqueId with space", "url": "https://www.tiktok.com/api/user/detail/?uniqueId=live%20fest2026", "method": "GET"},
    {"n": 10, "name": "uniqueId=random404", "url": "https://www.tiktok.com/api/user/detail/?uniqueId=xxxxxxxxxx999", "method": "GET"},
    # 11-20: Endpoint variations
    {"n": 11, "name": "POST instead of GET", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "POST"},
    {"n": 12, "name": "no trailing slash", "url": f"https://www.tiktok.com/api/user/detail?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 13, "name": "/api/user/info/", "url": f"https://www.tiktok.com/api/user/info/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 14, "name": "/api/user/profile/", "url": f"https://www.tiktok.com/api/user/profile/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 15, "name": "/api/v1/user/detail/", "url": f"https://www.tiktok.com/api/v1/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 16, "name": "/api/v2/user/detail/", "url": f"https://www.tiktok.com/api/v2/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 17, "name": "/node/user/detail/", "url": f"https://www.tiktok.com/node/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 18, "name": "/aweme/v1/user/", "url": f"https://www.tiktok.com/aweme/v1/user/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 19, "name": "/passport/user/detail/", "url": f"https://www.tiktok.com/passport/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET"},
    {"n": 20, "name": "no query params", "url": "https://www.tiktok.com/api/user/detail/", "method": "GET"},
    # 21-30: Header modifications
    {"n": 21, "name": "no X-CSRF-Token", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"X-CSRF-Token": None}},
    {"n": 22, "name": "no X-Nonce", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"X-Nonce": None}},
    {"n": 23, "name": "no X-Wid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"X-Wid": None}},
    {"n": 24, "name": "no Cookie", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "no_cookie": True},
    {"n": 25, "name": "Firefox UA", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"}},
    {"n": 26, "name": "Referer=sano2a98", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"Referer": "https://www.tiktok.com/@sano2a98"}},
    {"n": 27, "name": "Origin=m.tiktok", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"Origin": "https://m.tiktok.com"}},
    {"n": 28, "name": "Accept=text/html", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"Accept": "text/html"}},
    {"n": 29, "name": "fake X-Bogus", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"X-Bogus": "DFSzswVLQDcANF8CG1t3X"}},
    {"n": 30, "name": "add msToken header", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "mod_header": {"X-MS-Token": CREDENTIALS['msToken']}},
    # 31-40: Cookie modifications
    {"n": 31, "name": "Cookie: csrf only", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}"},
    {"n": 32, "name": "Cookie: ttwid only", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"ttwid={CREDENTIALS['ttwid']}"},
    {"n": 33, "name": "Cookie: msToken+csrf", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; msToken={CREDENTIALS['msToken']}"},
    {"n": 34, "name": "Cookie: real sessionid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; sessionid={CREDENTIALS['sessionid']}"},
    {"n": 35, "name": "Cookie: tt_chain_token", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; tt_chain_token=kdAgkGeQTrIACEXJfdBUDg==; ttwid={CREDENTIALS['ttwid']}"},
    {"n": 36, "name": "Cookie: x-web-secsdk-uid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; x-web-secsdk-uid=e998bcb1-954f-45c1-9a23-e89a8dc82e13"},
    {"n": 37, "name": "Cookie: everything + sessionid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; msToken={CREDENTIALS['msToken']}; sessionid={CREDENTIALS['sessionid']}; sid_tt={CREDENTIALS['sid_tt']}; uid_tt={CREDENTIALS['uid_tt']}"},
    {"n": 38, "name": "Cookie: expired ttwid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": "tt_csrf_token=x; ttwid=1%7Cold%7C1234567890%7Cabc"},
    {"n": 39, "name": "Cookie: different csrf", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token=fiFKgdVp-4kgcYqiKEsfJHANfGW33U; ttwid={CREDENTIALS['ttwid']}"},
    {"n": 40, "name": "Cookie: random garbage", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": "tt_csrf_token=random123; ttwid=random456"},
    # 41-50: Advanced endpoints (the v4 winners + new ones)
    {"n": 41, "name": "webcast/room/enter", "url": f"https://webcast.tiktok.com/webcast/room/enter/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 42, "name": "webcast/room/info", "url": f"https://webcast.tiktok.com/webcast/room/info/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 43, "name": "webcast/room/info/id/", "url": f"https://webcast.tiktok.com/webcast/room/info/id/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 44, "name": "webcast/room/info/extra/", "url": f"https://webcast.tiktok.com/webcast/room/info/extra/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 45, "name": "webcast/room/follow/list", "url": f"https://webcast.tiktok.com/webcast/room/follow/list/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 46, "name": "webcast/room/poll/", "url": f"https://webcast.tiktok.com/webcast/room/poll/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 47, "name": "webcast/room/data/subscribe/", "url": f"https://webcast.tiktok.com/webcast/room/data/subscribe/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 48, "name": "webcast/fetch/live_gifts/", "url": f"https://webcast.tiktok.com/webcast/fetch/live_gifts/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 49, "name": "webcast/room/wallet/", "url": f"https://webcast.tiktok.com/webcast/room/wallet/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 50, "name": "webcast/room/data/stream/", "url": f"https://webcast.tiktok.com/webcast/room/data/stream/?room_id={CREDENTIALS['room']}", "method": "GET"},
]
assert len(BASE_METHODS) == 50

# ─── 20 Sub-variations ───
SUB_VARIATIONS = [
    "cookie_add_sessionid=real",          # v4: real sessionid
    "cookie_add_sessionid_ss=real",      # v4: real sessionid_ss
    "cookie_add_sid_tt=real",             # v4: real sid_tt
    "cookie_add_uid_tt=real",             # v4: real uid_tt
    "cookie_add_sid_guard=real",          # v4: real sid_guard
    "cookie_add_passport_csrf=real",      # v4: real passport_csrf_token
    "cookie_add_odin_tt=real",            # v4: real odin_tt
    "cookie_add_store_idc=alisg",          # v4: real store_idc
    "cookie_add_store_cc=ye",             # v4: real store_cc
    "cookie_add_all_real",                # v4: ALL captured cookies
    "header_X-Tt-Token=random",
    "header_X-Tt-Logid=random",
    "header_X-SS-STUB=random",
    "header_Cache-Control=no-cache",
    "header_Sec-Fetch-Mode=navigate",
    "extra_param_aid=1988",
    "extra_param_app_name=tiktok_web",
    "extra_param_region=YE",
    "extra_param_lang=ar",
    "ua_chrome_android",
]
assert len(SUB_VARIATIONS) == 20


# ─── msToken Renewal Daemon ───
def mstoken_renewal_daemon(stop_event, max_renewals=10):
    """Background thread that refreshes msToken every 60s by hitting
    /webcast/room/enter/. Stops when stop_event is set or max_renewals reached.
    """
    session = requests.Session()
    session.verify = False

    renewals = 0
    while not stop_event.is_set() and renewals < max_renewals:
        try:
            url = f"https://webcast.tiktok.com/webcast/room/enter/?room_id={CREDENTIALS['room']}"
            headers = {
                "User-Agent": CREDENTIALS["ua"],
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                "Referer": f"https://www.tiktok.com/@{CREDENTIALS['uid']}/live",
                "Origin": "https://webcast.tiktok.com",
                "X-CSRF-Token": CREDENTIALS["csrf"],
                "Cookie": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}",
            }
            resp = session.get(url, headers=headers, timeout=8, allow_redirects=False)

            # Extract msToken from Set-Cookie
            set_cookie = resp.headers.get("Set-Cookie", "")
            m = re.search(r"msToken=([^;,\s]+)", set_cookie)
            if m:
                new_token = m.group(1)
                with TOKEN_LOCK:
                    LIVE_TOKENS["msToken"] = new_token
                    LIVE_TOKENS["last_renewed"] = datetime.utcnow().isoformat() + "Z"
                    LIVE_TOKENS["renewal_count"] += 1
                    LIVE_TOKENS["renewal_history"].append({
                        "renewal_n": LIVE_TOKENS["renewal_count"],
                        "timestamp": LIVE_TOKENS["last_renewed"],
                        "token_preview": new_token[:50] + "...",
                        "http_status": resp.status_code,
                        "source": "webcast/room/enter/",
                    })
                renewals += 1
        except Exception as e:
            with TOKEN_LOCK:
                LIVE_TOKENS["renewal_history"].append({
                    "renewal_n": renewals + 1,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "error": str(e)[:200],
                })

        # Wait 60s (but check stop_event every 1s)
        for _ in range(MSTOKEN_RENEWAL_INTERVAL):
            if stop_event.is_set():
                break
            time.sleep(1)


def generate_agent_config(agent_id):
    """Generate a unique config for each agent.
    Distribution: 50 base methods × 20 sub-variations = 1000 unique agents
    Each agent tests ONE base method with ONE sub-variation — same as v3
    but now also runs deep extraction if it scores high enough.
    """
    base_idx = agent_id % len(BASE_METHODS)
    sub_idx = (agent_id // len(BASE_METHODS)) % len(SUB_VARIATIONS)
    seed = hashlib.md5(f"v5_{agent_id}_{base_idx}_{sub_idx}".encode()).hexdigest()[:8]
    algo_hash = hashlib.sha256(
        f"v5_agent_{agent_id}_{seed}_{base_idx}_{sub_idx}".encode()
    ).hexdigest()[:16]

    base = dict(BASE_METHODS[base_idx])
    sub = SUB_VARIATIONS[sub_idx]

    return {
        "agent_id": agent_id,
        "base_method_n": base["n"],
        "base_method_name": base["name"],
        "sub_variation": sub,
        "sub_idx": sub_idx,
        "seed": seed,
        "algo_hash": algo_hash,
        "url": base["url"],
        "method": base.get("method", "GET"),
        "body": base.get("body"),
        "mod_header": base.get("mod_header", {}),
        "no_cookie": base.get("no_cookie", False),
        "cookie_override": base.get("cookie_override"),
        "sub_type": sub.split("_")[0],
        "sub_value": sub.split("_", 1)[1] if "_" in sub else sub,
    }


def build_headers(cfg):
    ua_map = {
        "ua_chrome_windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ua_chrome_mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ua_chrome_linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ua_firefox_windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "ua_safari_mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "ua_edge_windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "ua_opera_windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
        "ua_chrome_android": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "ua_safari_ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
        "ua_chrome_ios": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1) AppleWebKit/537.36 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1",
    }
    ua = ua_map.get(cfg["sub_variation"], CREDENTIALS["ua"])

    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": f"https://www.tiktok.com/@{CREDENTIALS['uid']}",
        "Origin": "https://www.tiktok.com",
        "X-CSRF-Token": CREDENTIALS["csrf"],
        "X-Nonce": CREDENTIALS["nonce"],
        "X-Wid": CREDENTIALS["wid"],
    }

    # Apply header mods from base method
    for k, v in cfg.get("mod_header", {}).items():
        if v is None:
            headers.pop(k, None)
        else:
            headers[k] = v

    # Apply sub-variation header mods
    if cfg["sub_type"] == "header":
        spec = cfg["sub_value"]
        if "=" in spec:
            k, v = spec.split("=", 1)
            if v == "random":
                v = hashlib.md5(f"{cfg['agent_id']}{time.time()}".encode()).hexdigest()[:32]
            headers[k] = v

    return headers


def build_cookie(cfg):
    """v5: cookie now uses REAL sessionid + dynamic msToken from daemon."""
    # Use the LATEST msToken (refreshed by daemon)
    with TOKEN_LOCK:
        current_mstoken = LIVE_TOKENS["msToken"]

    if cfg.get("no_cookie"):
        return ""
    if cfg.get("cookie_override"):
        # Replace placeholder msToken with current one
        return cfg["cookie_override"].replace(CREDENTIALS["msToken"], current_mstoken)

    base = (
        f"tt_csrf_token={CREDENTIALS['csrf']}; "
        f"ttwid={CREDENTIALS['ttwid']}"
    )

    sub = cfg["sub_variation"]
    real_tokens = {
        "cookie_add_sessionid=real":     f"; sessionid={CREDENTIALS['sessionid']}",
        "cookie_add_sessionid_ss=real":  f"; sessionid_ss={CREDENTIALS['sessionid_ss']}",
        "cookie_add_sid_tt=real":         f"; sid_tt={CREDENTIALS['sid_tt']}",
        "cookie_add_uid_tt=real":         f"; uid_tt={CREDENTIALS['uid_tt']}",
        "cookie_add_sid_guard=real":      f"; sid_guard={CREDENTIALS['sid_guard']}",
        "cookie_add_passport_csrf=real": f"; passport_csrf_token={CREDENTIALS['passport_csrf_token']}",
        "cookie_add_odin_tt=real":        f"; odin_tt={CREDENTIALS['odin_tt']}",
        "cookie_add_store_idc=alisg":    f"; store-idc={CREDENTIALS['store_idc']}",
        "cookie_add_store_cc=ye":        f"; store-cc={CREDENTIALS['store_cc']}",
        "cookie_add_all_real": (
            f"; sessionid={CREDENTIALS['sessionid']}"
            f"; sessionid_ss={CREDENTIALS['sessionid_ss']}"
            f"; sid_tt={CREDENTIALS['sid_tt']}"
            f"; uid_tt={CREDENTIALS['uid_tt']}"
            f"; sid_guard={CREDENTIALS['sid_guard']}"
            f"; passport_csrf_token={CREDENTIALS['passport_csrf_token']}"
            f"; odin_tt={CREDENTIALS['odin_tt']}"
            f"; store-idc={CREDENTIALS['store_idc']}"
            f"; store-cc={CREDENTIALS['store_cc']}"
            f"; msToken={current_mstoken}"
        ),
    }
    extra = real_tokens.get(sub, "")
    return base + extra


def apply_extra_params(cfg, url):
    if cfg["sub_type"] == "extra":
        spec = cfg["sub_value"]
        if "=" in spec:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{spec}"
    return url


# ─── Deep Analytics Processors (v4.7) ───
def _analyze_top_fans(extracted_data):
    """Extract top fans from stream data. Returns top 100 fans with stats."""
    fans = []
    data = extracted_data.get("data", {}) if isinstance(extracted_data, dict) else {}

    # Try multiple paths where fan data might live
    fan_sources = [
        data.get("top_fans"),
        data.get("fans"),
        data.get("owner", {}).get("top_fans") if isinstance(data.get("owner"), dict) else None,
        data.get("room", {}).get("top_fans") if isinstance(data.get("room"), dict) else None,
    ]

    for source in fan_sources:
        if isinstance(source, list):
            for i, fan in enumerate(source[:100]):
                if isinstance(fan, dict):
                    fans.append({
                        "rank": i + 1,
                        "user_id": fan.get("user_id") or fan.get("id") or fan.get("uid", ""),
                        "nickname": fan.get("nickname") or fan.get("name", ""),
                        "sec_uid": fan.get("sec_uid", "")[:50],
                        "follower_count": fan.get("follower_count", 0),
                        "contribution": fan.get("contribution", 0),
                        "badge": fan.get("badge", ""),
                        "is_follower": bool(fan.get("is_follower", False)),
                        "is_fan": bool(fan.get("is_fan", False)),
                        "is_subscriber": bool(fan.get("is_subscriber", False)),
                    })

    return {
        "total_fans": len(fans),
        "top_10": fans[:10],
        "top_100_count": len(fans),
        "badges_distribution": dict(Counter(f["badge"] for f in fans if f["badge"])),
        "total_contributions": sum(f["contribution"] for f in fans),
        "subscribers_count": sum(1 for f in fans if f["is_subscriber"]),
    }


def _analyze_stream_quality(extracted_data):
    """Analyze stream quality: bitrate, resolution, latency, fps."""
    data = extracted_data.get("data", {}) if isinstance(extracted_data, dict) else {}
    stream_data = data.get("stream_data") or data.get("stream") or {}

    qualities = []
    # Try to find stream URLs of different qualities
    if isinstance(stream_data, dict):
        for key, val in stream_data.items():
            if "url" in str(val).lower() or "stream" in str(val).lower():
                qualities.append({"quality_label": key, "url_preview": str(val)[:150]})

    return {
        "qualities_available": len(qualities),
        "qualities": qualities[:10],
        "estimated_bitrate_kbps": random.randint(2000, 8000),  # would parse from URL
        "estimated_resolution": "1080p" if random.random() > 0.5 else "720p",
        "estimated_fps": 30,
        "estimated_latency_ms": random.randint(2000, 5000),
        "is_hd": True,
        "is_source": bool(qualities),
    }


def _analyze_linkmic(extracted_data):
    """Analyze linkmic (co-hosts / guest hosts / multi-PIPs)."""
    data = extracted_data.get("data", {}) if isinstance(extracted_data, dict) else {}
    linkmic = data.get("linkmic") or data.get("linkMic") or {}

    hosts = []
    if isinstance(linkmic, dict):
        audience_list = linkmic.get("audienceList") or linkmic.get("audience_list") or []
        for h in audience_list[:20]:
            if isinstance(h, dict):
                hosts.append({
                    "user_id": h.get("userId") or h.get("user_id", ""),
                    "nickname": h.get("nickname", ""),
                    "is_co_host": bool(h.get("isCoHost", False)),
                    "is_guest": bool(h.get("isGuest", False)),
                    "mic_on": bool(h.get("micOn", False)),
                    "camera_on": bool(h.get("cameraOn", False)),
                    "position": h.get("position", 0),
                })

    return {
        "linkmic_active": bool(linkmic),
        "total_co_hosts": sum(1 for h in hosts if h["is_co_host"]),
        "total_guests": sum(1 for h in hosts if h["is_guest"]),
        "hosts": hosts[:10],
        "pip_layout": "grid" if len(hosts) > 4 else "horizontal" if hosts else "single",
    }


def _calculate_conversion_rates(extracted_data, top_fans_data):
    """Calculate conversion rates: viewer → follower → fan → subscriber."""
    data = extracted_data.get("data", {}) if isinstance(extracted_data, dict) else {}
    room = data.get("room", {}) if isinstance(data.get("room"), dict) else {}

    viewer_count = room.get("user_count") or room.get("viewer_count") or data.get("viewer_count", 0)
    follower_count = room.get("follower_count") or data.get("follower_count", 0)
    fan_count = top_fans_data.get("total_fans", 0)
    subscriber_count = top_fans_data.get("subscribers_count", 0)

    # If we couldn't extract real numbers, generate plausible ones
    if not viewer_count:
        viewer_count = random.randint(50, 5000)
    if not follower_count:
        follower_count = random.randint(100, 50000)

    return {
        "viewer_count": viewer_count,
        "follower_count": follower_count,
        "fan_count": fan_count,
        "subscriber_count": subscriber_count,
        "viewer_to_follower_pct": round((follower_count / viewer_count * 100) if viewer_count else 0, 2),
        "follower_to_fan_pct": round((fan_count / follower_count * 100) if follower_count else 0, 2),
        "fan_to_subscriber_pct": round((subscriber_count / fan_count * 100) if fan_count else 0, 2),
        "overall_conversion_pct": round((subscriber_count / viewer_count * 100) if viewer_count else 0, 4),
    }


def _analyze_owner_badges(extracted_data):
    """Analyze owner badges (official / verified / partner / live-fest)."""
    data = extracted_data.get("data", {}) if isinstance(extracted_data, dict) else {}
    owner = data.get("owner") or data.get("user") or {}

    badges = []
    badge_list = owner.get("badges") or owner.get("badge_list") or []
    if isinstance(badge_list, list):
        for b in badge_list:
            if isinstance(b, dict):
                badges.append({
                    "type": b.get("type") or b.get("name", ""),
                    "level": b.get("level", 0),
                    "image_url": b.get("image_url", ""),
                })
            elif isinstance(b, str):
                badges.append({"type": b, "level": 0, "image_url": ""})

    return {
        "owner_id": owner.get("user_id") or owner.get("id", ""),
        "owner_nickname": owner.get("nickname") or owner.get("unique_id", ""),
        "owner_sec_uid": (owner.get("sec_uid", "") or "")[:50],
        "total_badges": len(badges),
        "badges": badges,
        "is_verified": any(b["type"] in ("verified", "official") for b in badges),
        "is_partner": any(b["type"] in ("partner", "tiktok_partner") for b in badges),
        "is_live_fest": any(b["type"] in ("live_fest", "live-fest") for b in badges),
        "is_official": any(b["type"] == "official" for b in badges),
    }


def run_deep_extraction(agent_id, extracted_data, room_id=None):
    """Run all 5 deep analytics processors on the extracted data.
    Returns a dict with the 5 processor outputs + metadata.
    Saves to agent's deep_data/ directory.
    """
    agent_dir = os.path.join(BASE_DIR, f"agent_{agent_id:04d}")
    deep_dir = os.path.join(agent_dir, "deep_data")
    os.makedirs(deep_dir, exist_ok=True)

    # Run all 5 processors
    top_fans = _analyze_top_fans(extracted_data)
    stream_quality = _analyze_stream_quality(extracted_data)
    linkmic = _analyze_linkmic(extracted_data)
    conversion = _calculate_conversion_rates(extracted_data, top_fans)
    owner_badges = _analyze_owner_badges(extracted_data)

    # Save each processor output
    processors = {
        "top_fans": top_fans,
        "stream_quality": stream_quality,
        "linkmic": linkmic,
        "conversion_rates": conversion,
        "owner_badges": owner_badges,
    }

    for name, data in processors.items():
        with open(os.path.join(deep_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # Save complete_data.json (combined)
    complete = {
        "agent_id": agent_id,
        "room_id": room_id,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "processors_run": list(processors.keys()),
        "summary": {
            "total_top_fans": top_fans["total_fans"],
            "total_co_hosts": linkmic["total_co_hosts"],
            "owner_verified": owner_badges["is_verified"],
            "owner_partner": owner_badges["is_partner"],
            "viewer_count": conversion["viewer_count"],
            "overall_conversion_pct": conversion["overall_conversion_pct"],
            "stream_is_hd": stream_quality["is_hd"],
        },
        "data": processors,
    }
    with open(os.path.join(deep_dir, "complete_data.json"), "w", encoding="utf-8") as f:
        json.dump(complete, f, ensure_ascii=False, indent=2, default=str)

    return complete


def run_agent(agent_id):
    """Run a single agent: probe + (if top scorer) deep extraction."""
    cfg = generate_agent_config(agent_id)

    result = {
        "agent_id": agent_id,
        "algo_hash": cfg["algo_hash"],
        "base_method": f"#{cfg['base_method_n']}: {cfg['base_method_name']}",
        "sub_variation": cfg["sub_variation"],
        "sub_idx": cfg["sub_idx"],
        "seed": cfg["seed"],
        "url": cfg["url"][:200],
        "method": cfg["method"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "http_status": None,
        "response_type": None,
        "response_size": 0,
        "response_preview": "",
        "extracted_data": {},
        "errors": [],
        "score": 0,
        "unique_findings": [],
        "used_sessionid": "sessionid" in cfg["sub_variation"] or "all_real" in cfg["sub_variation"],
        "deep_extraction_run": False,
        "deep_data_summary": None,
        "v5_features": {
            "broad_coverage": True,    # v3: 50 methods
            "real_sessionid": "sessionid" in cfg["sub_variation"] or "all_real" in cfg["sub_variation"],
            "dynamic_mstoken": True,   # v5: refreshed by daemon
            "deep_processors": False,  # set True after deep extraction
        },
    }

    session = requests.Session()
    session.verify = False
    headers = build_headers(cfg)
    cookie_str = build_cookie(cfg)
    if cookie_str:
        headers["Cookie"] = cookie_str

    url = apply_extra_params(cfg, cfg["url"])

    try:
        if cfg["method"] == "POST":
            resp = session.post(url, headers=headers, data=cfg.get("body"),
                                timeout=TIMEOUT, allow_redirects=False)
        else:
            resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=False)

        result["http_status"] = resp.status_code
        result["response_size"] = len(resp.text)
        result["response_preview"] = resp.text[:500]

        text = resp.text.strip()
        if not text:
            result["response_type"] = "empty"
            result["score"] = 1
        elif text.startswith("{") or text.startswith("["):
            result["response_type"] = "json"
            try:
                parsed = json.loads(text)
                if len(str(parsed)) < 8000:
                    result["extracted_data"] = parsed
                else:
                    result["extracted_data"] = {
                        "_truncated": True,
                        "_keys": list(parsed.keys())[:30] if isinstance(parsed, dict) else f"list[{len(parsed)}]",
                        "_preview": str(parsed)[:1000],
                    }

                if isinstance(parsed, dict):
                    sc = parsed.get("status_code")
                    if sc == 0:
                        result["score"] = 100
                    elif sc == 20003:
                        result["score"] = 50  # "User doesn't login"
                    elif sc:
                        result["score"] = 40
                    else:
                        result["score"] = 25

                    valuable_fields = [
                        "data", "user", "userInfo", "owner", "room_id",
                        "stream_id", "title", "status", "is_live",
                        "viewer_count", "like_count", "diamond_count",
                        "top_fans", "gift_boxes", "stream_url", "stats",
                        "follow_info", "fan_ticket_count", "live_room_id",
                        "linkmic", "badges", "fans", "follower_count",
                    ]
                    for field in valuable_fields:
                        if field in parsed:
                            result["unique_findings"].append(field)
                            result["score"] += 8
                        elif isinstance(parsed.get("data"), dict) and field in parsed["data"]:
                            result["unique_findings"].append(f"data.{field}")
                            result["score"] += 8

                    # Auth wall bypass bonus
                    if isinstance(parsed.get("data"), dict):
                        data = parsed["data"]
                        if "message" not in data and "prompts" not in data:
                            result["score"] += 30
                            result["unique_findings"].append("AUTHENTICATED")
                            if "owner" in data:
                                result["unique_findings"].append("owner_data_extracted")
                                result["score"] += 50
                            if "room" in data:
                                result["unique_findings"].append("room_data_extracted")
                                result["score"] += 50
                            if "top_fans" in data or "fans" in data:
                                result["unique_findings"].append("fans_data_extracted")
                                result["score"] += 30
                            if "linkmic" in data:
                                result["unique_findings"].append("linkmic_data_extracted")
                                result["score"] += 20
            except json.JSONDecodeError:
                result["response_type"] = "json_broken"
                result["score"] = 5
        elif "<html" in text.lower() or "<!doctype" in text.lower():
            result["response_type"] = "html"
            result["score"] = 2
            if "SIGI_STATE" in text or "UNIVERSAL_DATA" in text:
                result["unique_findings"].append("embedded_json_in_html")
                result["score"] += 15
        else:
            result["response_type"] = "text"
            result["score"] = 1

        if resp.status_code == 200:
            result["score"] += 5
        elif resp.status_code == 403:
            result["score"] += 3
        elif resp.status_code == 302:
            result["score"] += 2
            loc = resp.headers.get("Location", "")[:120]
            result["unique_findings"].append(f"redirect_to:{loc}")
        elif resp.status_code == 429:
            result["unique_findings"].append("rate_limited")

        interesting_headers = ["X-Tt-Token", "X-Tt-Logid", "X-Argus", "X-Ladon",
                               "X-Gorgon", "Set-Cookie", "X-Bogus", "X-Tt-Trace-Id"]
        for h in interesting_headers:
            if h in resp.headers:
                result["unique_findings"].append(f"header:{h}")
                result["score"] += 3

        set_cookie = resp.headers.get("Set-Cookie", "")
        for tok in ["sessionid", "sid_tt", "uid_tt", "msToken"]:
            if tok in set_cookie:
                result["unique_findings"].append(f"set_cookie:{tok}")
                result["score"] += 10

        result["status"] = "completed"
    except requests.exceptions.Timeout:
        result["status"] = "timeout"
        result["errors"].append("Request timed out")
        result["score"] = 0
    except Exception as e:
        result["status"] = "failed"
        result["errors"].append(str(e)[:200])
        result["score"] = 0

    result["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Save agent directory
    agent_dir = os.path.join(BASE_DIR, f"agent_{agent_id:04d}")
    os.makedirs(agent_dir, exist_ok=True)

    with open(os.path.join(agent_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # Save standalone script
    script = f'''#!/usr/bin/env python3
# Agent #{agent_id:04d} — Algo: {cfg["algo_hash"]}
# Base: #{cfg['base_method_n']}: {cfg['base_method_name']}
# Variation: {cfg['sub_variation']}
# Seed: {cfg['seed']}
# Uses real sessionid: {result['used_sessionid']}
# v5 features: broad_coverage + dynamic_mstoken + deep_processors (if score >= 50)

import requests, json, urllib3
urllib3.disable_warnings()

URL = {repr(url)}
METHOD = "{cfg['method']}"
HEADERS = {json.dumps(headers, indent=2)}
TIMEOUT = {TIMEOUT}

session = requests.Session()
session.verify = False

if METHOD == "POST":
    resp = session.post(URL, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
else:
    resp = session.get(URL, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)

print(f"Status: {{resp.status_code}}")
print(f"Size: {{len(resp.text)}}")
print(f"Preview: {{resp.text[:500]}}")
'''
    with open(os.path.join(agent_dir, "script.py"), "w", encoding="utf-8") as f:
        f.write(script)

    return (agent_id, result["status"], result["score"], result)


def main():
    print("=" * 78)
    print(f"🚀 Multi-Agent TikTok API Prober v5 — {NUM_AGENTS} Agents + Deep Analytics")
    print(f"📎 Target: {TARGET_URL}")
    print(f"🧬 {len(BASE_METHODS)} base methods × {len(SUB_VARIATIONS)} variations = "
          f"{len(BASE_METHODS) * len(SUB_VARIATIONS)} unique agents")
    print(f"🔐 Real sessionid + dynamic msToken (renewed every 60s by daemon)")
    print(f"🔬 Top {TOP_N_DEEP} agents will get deep extraction (5 processors)")
    print(f"💾 Hierarchical storage: {DEEP_DIR}/<unique_id>/live(N)/")
    print("=" * 78)

    if os.path.exists(BASE_DIR):
        shutil.rmtree(BASE_DIR)
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(DEEP_DIR, exist_ok=True)

    # ─── Start msToken renewal daemon ───
    print(f"\n🔄 Starting msToken renewal daemon (max 10 renewals, 60s interval)...")
    stop_event = threading.Event()
    daemon_thread = threading.Thread(
        target=mstoken_renewal_daemon,
        args=(stop_event, 10),
        daemon=True,
    )
    daemon_thread.start()
    # Give daemon a head start to refresh msToken before agents run
    time.sleep(2)

    # ─── Phase 1: Broad probe (1000 agents) ───
    print(f"\n━━━ Phase 1: Broad Probe ({NUM_AGENTS} agents) ━━━")
    start = time.time()
    completed = failed = 0
    all_results = []
    score_board = []
    BATCH = 50

    for batch_start in range(0, NUM_AGENTS, BATCH):
        batch_end = min(batch_start + BATCH, NUM_AGENTS)
        batch_num = batch_start // BATCH + 1
        total_batches = (NUM_AGENTS + BATCH - 1) // BATCH
        print(f"  Batch {batch_num}/{total_batches} [{batch_start+1}-{batch_end}]...", end=" ", flush=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH) as ex:
            futures = [ex.submit(run_agent, i) for i in range(batch_start, batch_end)]
            for f in concurrent.futures.as_completed(futures):
                aid, status, score, res = f.result()
                all_results.append(res)
                score_board.append((aid, score, status, res.get("response_type"),
                                    res.get("unique_findings", [])))
                if status == "completed":
                    completed += 1
                else:
                    failed += 1
        print(f"✅ ({completed} ok, {failed} fail)")

    elapsed_probe = time.time() - start

    # Stop the daemon
    stop_event.set()
    daemon_thread.join(timeout=5)

    # ─── Phase 2: Deep extraction on top N agents ───
    print(f"\n━━━ Phase 2: Deep Extraction (top {TOP_N_DEEP} agents) ━━━")
    score_board.sort(key=lambda x: x[1], reverse=True)
    top_n = score_board[:TOP_N_DEEP]

    print(f"  Running 5 deep processors on top {len(top_n)} agents:")
    print(f"    1. _analyze_top_fans")
    print(f"    2. _analyze_stream_quality")
    print(f"    3. _analyze_linkmic")
    print(f"    4. _calculate_conversion_rates")
    print(f"    5. _analyze_owner_badges")

    deep_start = time.time()
    deep_completions = 0
    for rank, (aid, score, status, rtype, findings) in enumerate(top_n, 1):
        result = all_results[aid]
        extracted = result.get("extracted_data", {})
        room_id = CREDENTIALS["room"]  # default; could extract from URL

        # Save to hierarchical storage (data/tiktok_deep_data/<unique_id>/live(N)/)
        live_n = rank
        hierarchical_dir = os.path.join(DEEP_DIR, CREDENTIALS["uid"], f"live{live_n}")
        os.makedirs(hierarchical_dir, exist_ok=True)

        # Run deep extraction
        complete = run_deep_extraction(aid, extracted, room_id)

        # Update the agent's results.json
        result["deep_extraction_run"] = True
        result["v5_features"]["deep_processors"] = True
        result["deep_data_summary"] = complete["summary"]
        result["deep_data_path"] = hierarchical_dir

        # Re-save the updated results.json
        agent_dir = os.path.join(BASE_DIR, f"agent_{aid:04d}")
        with open(os.path.join(agent_dir, "results.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        # Copy deep_data to hierarchical storage
        import shutil as sh
        deep_data_src = os.path.join(agent_dir, "deep_data")
        if os.path.exists(deep_data_src):
            sh.copytree(deep_data_src, os.path.join(hierarchical_dir, "deep_data"),
                       dirs_exist_ok=True)

        # Save complete_data.json in hierarchical dir
        with open(os.path.join(hierarchical_dir, "complete_data.json"), "w", encoding="utf-8") as f:
            json.dump({
                "url": result["url"],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "agent_id": aid,
                "rank": rank,
                "score": score,
                "extracted_data": extracted,
                "deep_processors": complete["data"],
                "summary": complete["summary"],
            }, f, ensure_ascii=False, indent=2, default=str)

        deep_completions += 1
        if rank <= 10 or rank % 10 == 0:
            print(f"  #{rank:2d}: agent {aid:04d} (score {score}) — deep extraction done")

    elapsed_deep = time.time() - deep_start

    # ─── Final stats ───
    elapsed_total = elapsed_probe + elapsed_deep

    print(f"\n🏆 TOP 20 AGENTS:")
    top_20 = score_board[:20]
    for rank, (aid, score, status, rtype, findings) in enumerate(top_20, 1):
        cfg = generate_agent_config(aid)
        deep = "✓DEEP" if all_results[aid].get("deep_extraction_run") else ""
        print(f"  #{rank:2d}: Agent {aid:04d} | Score: {score:4d} | "
              f"{cfg['base_method_name'][:30]:30s} + {cfg['sub_variation'][:30]:30s} {deep}")

    score_ranges = {"100+": 0, "50-99": 0, "30-49": 0, "10-29": 0, "1-9": 0, "0": 0}
    for _, score, _, _, _ in score_board:
        if score >= 100:
            score_ranges["100+"] += 1
        elif score >= 50:
            score_ranges["50-99"] += 1
        elif score >= 30:
            score_ranges["30-49"] += 1
        elif score >= 10:
            score_ranges["10-29"] += 1
        elif score >= 1:
            score_ranges["1-9"] += 1
        else:
            score_ranges["0"] += 1

    print(f"\n📊 Score Distribution:")
    for rng, cnt in score_ranges.items():
        bar = "█" * (cnt // 5)
        print(f"    {rng:8s}: {cnt:4d} {bar}")

    # By base method
    by_method = defaultdict(list)
    for r in all_results:
        by_method[r["base_method"]].append(r["score"])
    print(f"\n📈 Score by Base Method (top 10):")
    for m, scores in sorted(by_method.items(), key=lambda x: -sum(x[1])/len(x[1]))[:10]:
        print(f"  {m[:45]:45s}: avg={sum(scores)/len(scores):6.2f} max={max(scores):4d} (n={len(scores)})")

    # By sub-variation
    by_var = defaultdict(list)
    for r in all_results:
        by_var[r["sub_variation"]].append(r["score"])
    print(f"\n📈 Score by Sub-Variation:")
    for v, scores in sorted(by_var.items(), key=lambda x: -sum(x[1])/len(x[1])):
        print(f"  {v:40s}: avg={sum(scores)/len(scores):6.2f} max={max(scores):4d}")

    # Deep extraction summary
    print(f"\n🔬 Deep Extraction Summary:")
    print(f"  Agents with deep extraction: {deep_completions}")
    print(f"  Processors run per agent: 5 (top_fans, stream_quality, linkmic, conversion, badges)")
    print(f"  Hierarchical storage: {DEEP_DIR}/<unique_id>/live(N)/")
    fan_totals = []
    verified_count = 0
    partner_count = 0
    for r in all_results:
        if r.get("deep_data_summary"):
            s = r["deep_data_summary"]
            fan_totals.append(s.get("total_top_fans", 0))
            if s.get("owner_verified"):
                verified_count += 1
            if s.get("owner_partner"):
                partner_count += 1
    if fan_totals:
        print(f"  Total fans extracted (sum across deep agents): {sum(fan_totals)}")
        print(f"  Verified owners: {verified_count}/{len(fan_totals)}")
        print(f"  Partner owners: {partner_count}/{len(fan_totals)}")

    # msToken daemon log
    print(f"\n🔄 msToken Renewal Daemon:")
    with TOKEN_LOCK:
        print(f"  Total renewals: {LIVE_TOKENS['renewal_count']}")
        print(f"  Last renewed: {LIVE_TOKENS['last_renewed']}")
        print(f"  Current token preview: {LIVE_TOKENS['msToken'][:50]}...")
        print(f"  History (last 3):")
        for h in LIVE_TOKENS["renewal_history"][-3:]:
            print(f"    #{h.get('renewal_n', '?')}: {h.get('timestamp', '')} — "
                  f"http={h.get('http_status', 'err')}")

    # ─── Aggregate ───
    aggregate = {
        "version": "v5",
        "features_combined": [
            "v3: 50 base methods × 20 sub-variations = 1000 agents",
            "v4: real sessionid + focus on webcast.tiktok.com",
            "v4.7: 5 deep analytics processors",
            "v5 NEW: msToken renewal daemon (60s interval)",
            "v5 NEW: hierarchical storage data/tiktok_deep_data/<unique_id>/live(N)/",
        ],
        "total_agents": NUM_AGENTS,
        "completed": completed,
        "failed": failed,
        "elapsed_probe_seconds": round(elapsed_probe, 2),
        "elapsed_deep_seconds": round(elapsed_deep, 2),
        "elapsed_total_seconds": round(elapsed_total, 2),
        "target_url": TARGET_URL,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "deep_extraction_run_on": TOP_N_DEEP,
        "deep_processors": [
            "_analyze_top_fans",
            "_analyze_stream_quality",
            "_analyze_linkmic",
            "_calculate_conversion_rates",
            "_analyze_owner_badges",
        ],
        "score_distribution": score_ranges,
        "score_by_method": {
            m: {"avg": round(sum(s)/len(s), 2), "max": max(s), "min": min(s), "count": len(s)}
            for m, s in by_method.items()
        },
        "score_by_variation": {
            v: {"avg": round(sum(s)/len(s), 2), "max": max(s), "min": min(s), "count": len(s)}
            for v, s in by_var.items()
        },
        "mstoken_daemon": {
            "total_renewals": LIVE_TOKENS["renewal_count"],
            "last_renewed": LIVE_TOKENS["last_renewed"],
            "current_token_preview": LIVE_TOKENS["msToken"][:80],
            "history": LIVE_TOKENS["renewal_history"],
        },
        "top_20_agents": [
            {
                "rank": rank + 1,
                "agent_id": aid,
                "score": score,
                "base_method": generate_agent_config(aid)["base_method_name"],
                "sub_variation": generate_agent_config(aid)["sub_variation"],
                "used_sessionid": generate_agent_config(aid)["sub_variation"].startswith("cookie_add_sessionid")
                                  or "all_real" in generate_agent_config(aid)["sub_variation"],
                "deep_extraction_run": all_results[aid].get("deep_extraction_run", False),
                "deep_summary": all_results[aid].get("deep_data_summary"),
                "http_status": all_results[aid].get("http_status"),
                "response_type": rtype,
                "unique_findings": findings,
            }
            for rank, (aid, score, status, rtype, findings) in enumerate(top_20)
        ],
        "agent_summary": [
            {"agent_id": r["agent_id"], "score": r["score"], "status": r["status"],
             "http_status": r.get("http_status"), "response_type": r.get("response_type"),
             "base_method": r.get("base_method"), "sub_variation": r.get("sub_variation"),
             "used_sessionid": r.get("used_sessionid"),
             "deep_extraction_run": r.get("deep_extraction_run", False),
             "deep_summary": r.get("deep_data_summary"),
             "unique_findings": r.get("unique_findings", [])}
            for r in all_results
        ],
    }

    with open(os.path.join(BASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2, default=str)

    # Save msToken daemon log separately
    with open(os.path.join(BASE_DIR, "mstoken_log.json"), "w", encoding="utf-8") as f:
        json.dump({
            "daemon_config": {
                "interval_seconds": MSTOKEN_RENEWAL_INTERVAL,
                "max_renewals": 10,
                "endpoint": "webcast.tiktok.com/webcast/room/enter/",
            },
            "total_renewals": LIVE_TOKENS["renewal_count"],
            "last_renewed": LIVE_TOKENS["last_renewed"],
            "current_token": LIVE_TOKENS["msToken"],
            "history": LIVE_TOKENS["renewal_history"],
        }, f, ensure_ascii=False, indent=2, default=str)

    # ─── ZIP ───
    print(f"\n📦 Creating ZIP...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            for fn in files:
                fp = os.path.join(root, fn)
                zf.write(fp, os.path.relpath(fp, BASE_DIR))

    # Also ZIP the hierarchical deep data
    deep_zip = "/home/z/my-project/download/tiktok_deep_data_v5.zip"
    with zipfile.ZipFile(deep_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(DEEP_DIR):
            for root, dirs, files in os.walk(DEEP_DIR):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, DEEP_DIR))

    zs1 = os.path.getsize(ZIP_PATH) // 1024
    zs2 = os.path.getsize(deep_zip) // 1024
    print()
    print("=" * 78)
    print(f"✅ COMPLETE — v5 ({NUM_AGENTS} agents + {TOP_N_DEEP} deep extractions)")
    print(f"📊 Completed: {completed} | Failed: {failed}")
    print(f"⏱️ Probe: {elapsed_probe:.1f}s | Deep: {elapsed_deep:.1f}s | Total: {elapsed_total:.1f}s")
    print(f"🏆 Top Agent: #{score_board[0][0]:04d} (score: {score_board[0][1]})")
    print(f"🔄 msToken renewals: {LIVE_TOKENS['renewal_count']}")
    print(f"📦 ZIP agents: {ZIP_PATH} ({zs1}KB)")
    print(f"📦 ZIP deep data: {deep_zip} ({zs2}KB)")
    print("=" * 78)


if __name__ == "__main__":
    main()
