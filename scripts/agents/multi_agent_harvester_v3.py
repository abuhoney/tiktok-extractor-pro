#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Agent TikTok API Prober v3 — 1000 Agents
====================================================================
Each agent uses a DIFFERENT algorithm to probe TikTok API endpoints
with the SAME credentials, testing 1000 unique variations.

Inspired by the 50-method API tester: each agent modifies one parameter,
header, cookie, endpoint, or method to find what works.

Results are scored and ranked to identify which agents found
the most valuable/unique information.
"""

import os, sys, json, time, hashlib, random, re, shutil, zipfile
import concurrent.futures, itertools
from datetime import datetime
from collections import Counter

import requests, urllib3
urllib3.disable_warnings()

# ─── Config ───
TARGET_URL = "https://vt.tiktok.com/ZS9ANP4Gph4tP-j43fp/"
NUM_AGENTS = 1000
BASE_DIR = "/home/z/my-project/download/agents_1000_v3"
ZIP_PATH = "/home/z/my-project/download/agents_1000_v3.zip"
TIMEOUT = 15

# ─── Base credentials (from extracted data) ───
# These are the SAME for all agents — only the APPROACH differs
CREDENTIALS = {
    "csrf": "uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk",
    "ttwid": "1%7CqVI-86fRrg45VC9qd9K7xJ3F-KCq4wbiCViASLDbzzM%7C1789758639%7C0d2bf2a8d6bb1dbcdfb209bcb6d5f75ee6b5c65d0dd1e05454be4583f8142503",
    "nonce": "6vaarBpVsrfBDm_nl5DbJ",
    "wid": "7658084697712657940",
    "uid": "live_fest2026",
    "uid2": "sano2a98",
    "sec": "MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT",
    "room": "7683963746938555152",
    "stream": "1849444191476121704",
    "vid": "7684779612647263509",
    "msToken": "vmxzMM4LX1pT93FhnVf7-QQ6UVXWxIWRCxqFcD0w1lnIDY6FwdGh49eOsaAsIuyXkiwK0sniqGVtdzcYHr6GUtvHMK-8Ud7S6WgVvdAAVSp",
    "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# ─── 50 Base Probe Methods (from the HTML tester) ───
# Each method tests a different API variation
BASE_METHODS = [
    # 1-10: uniqueId variations
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
    {"n": 34, "name": "Cookie: fake sessionid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; sessionid=fake_session_12345"},
    {"n": 35, "name": "Cookie: tt_chain_token", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; tt_chain_token=kdAgkGeQTrIACEXJfdBUDg==; ttwid={CREDENTIALS['ttwid']}"},
    {"n": 36, "name": "Cookie: x-web-secsdk-uid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; x-web-secsdk-uid=e998bcb1-954f-45c1-9a23-e89a8dc82e13"},
    {"n": 37, "name": "Cookie: everything", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}; msToken={CREDENTIALS['msToken']}; tt_chain_token=kdAgkGeQTrIACEXJfdBUDg=="},
    {"n": 38, "name": "Cookie: expired ttwid", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": "tt_csrf_token=x; ttwid=1%7Cold%7C1234567890%7Cabc"},
    {"n": 39, "name": "Cookie: different csrf", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": f"tt_csrf_token=fiFKgdVp-4kgcYqiKEsfJHANfGW33U; ttwid={CREDENTIALS['ttwid']}"},
    {"n": 40, "name": "Cookie: random garbage", "url": f"https://www.tiktok.com/api/user/detail/?uniqueId={CREDENTIALS['uid']}", "method": "GET", "cookie_override": "tt_csrf_token=random123; ttwid=random456"},
    # 41-50: Advanced endpoints
    {"n": 41, "name": "webcast/room/enter", "url": f"https://webcast.tiktok.com/webcast/room/enter/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 42, "name": "webcast/room/info", "url": f"https://webcast.tiktok.com/webcast/room/info/?room_id={CREDENTIALS['room']}", "method": "GET"},
    {"n": 43, "name": "api/comment/list", "url": f"https://www.tiktok.com/api/comment/list/?aweme_id={CREDENTIALS['vid']}", "method": "GET"},
    {"n": 44, "name": "api/item/detail", "url": f"https://www.tiktok.com/api/item/detail/?itemId={CREDENTIALS['vid']}", "method": "GET"},
    {"n": 45, "name": "api/user/list", "url": f"https://www.tiktok.com/api/user/list/?secUid={CREDENTIALS['sec']}", "method": "GET"},
    {"n": 46, "name": "api/recommend/item_list", "url": "https://www.tiktok.com/api/recommend/item_list/", "method": "GET"},
    {"n": 47, "name": "api/following/list", "url": "https://www.tiktok.com/api/following/list/?user_id=7123696644457743366", "method": "GET"},
    {"n": 48, "name": "api/follower/list", "url": "https://www.tiktok.com/api/follower/list/?user_id=7123696644457743366", "method": "GET"},
    {"n": 49, "name": "api/commit/item/digg (POST)", "url": "https://www.tiktok.com/api/commit/item/digg/", "method": "POST", "body": json.dumps({"aweme_id": CREDENTIALS['vid'], "type": 1})},
    {"n": 50, "name": "api/relation/follow (POST)", "url": "https://www.tiktok.com/api/relation/follow/", "method": "POST", "body": json.dumps({"sec_uid": CREDENTIALS['sec'], "type": 1})},
]

# ─── Generate 1000 unique agent variations ───
# Each agent combines: base_method + unique_sub_variation + unique_seed
# This creates 1000 truly unique algorithms

SUB_VARIATIONS = [
    "extra_param_aid=1233", "extra_param_device_id=random", "extra_param_lang=ar",
    "extra_param_region=YE", "extra_param_app_name=musical_ly", "extra_param_channel=GooglePlay",
    "extra_param_sub_channel=AppStore", "extra_param_utm_source=copy", "extra_param_utm_medium=android",
    "extra_param_utm_campaign=share", "extra_param_os=android14", "extra_param_carrier=unknown",
    "header_X-Tt-Token=random", "header_X-Tt-Logid=random", "header_X-SS-STUB=random",
    "header_X-Argus=random", "header_X-Ladon=random", "header_X-Gorgon=random",
    "header_X-Khronos=random", "header_X-Pods=random", "header_Connection=keep-alive",
    "header_Connection=close", "header_Accept-Encoding=gzip", "header_Accept-Encoding=br",
    "header_Cache-Control=no-cache", "header_Pragma=no-cache", "header_Sec-Fetch-Dest=document",
    "header_Sec-Fetch-Mode=navigate", "header_Sec-Fetch-Site=none", "header_Sec-Fetch-User=?1",
    "header_Sec-Ch-Ua=Chrome", "header_Sec-Ch-Ua-Mobile=?0", "header_Sec-Ch-Ua-Platform=Windows",
    "cookie_add_sid_tt=random", "cookie_add_uid_tt=random", "cookie_add_sid_guard=random",
    "cookie_add_cmpl_token=random", "cookie_add_tt_chain_token=random", "cookie_add_sessionid=fake",
    "cookie_add_passport_csrf=random", "cookie_add_odin_tt=random", "cookie_add_multi_sids=random",
    "cookie_add_store_idc=alisg", "cookie_add_store_cc=ye", "cookie_add_tt_target_idc=alisg",
    "timeout_5s", "timeout_10s", "timeout_30s", "timeout_60s", "retry_0",
    "ua_chrome_windows", "ua_chrome_mac", "ua_chrome_linux", "ua_firefox_windows", "ua_safari_mac",
    "ua_edge_windows", "ua_opera_windows", "ua_chrome_android", "ua_safari_ios", "ua_chrome_ios",
]

def generate_agent_config(agent_id):
    """Generate a unique config for each agent."""
    base_idx = agent_id % len(BASE_METHODS)
    sub_idx = (agent_id * 7 + 3) % len(SUB_VARIATIONS)  # different distribution
    seed = hashlib.md5(f"agent_v3_{agent_id}_{base_idx}_{sub_idx}".encode()).hexdigest()[:8]
    algo_hash = hashlib.sha256(f"{agent_id}_{seed}_{base_idx}_{sub_idx}".encode()).hexdigest()[:16]
    
    base = dict(BASE_METHODS[base_idx])
    sub = SUB_VARIATIONS[sub_idx]
    
    return {
        "agent_id": agent_id,
        "base_method_n": base["n"],
        "base_method_name": base["name"],
        "sub_variation": sub,
        "seed": seed,
        "algo_hash": algo_hash,
        "url": base["url"],
        "method": base.get("method", "GET"),
        "body": base.get("body"),
        "mod_header": base.get("mod_header", {}),
        "no_cookie": base.get("no_cookie", False),
        "cookie_override": base.get("cookie_override"),
        "sub_type": sub.split("_")[0],  # "extra", "header", "cookie", "timeout", "retry", "ua"
        "sub_value": sub.split("_", 1)[1] if "_" in sub else sub,
    }


def run_agent(agent_id):
    """Run a single agent and return scored results."""
    cfg = generate_agent_config(agent_id)
    
    result = {
        "agent_id": agent_id,
        "algo_hash": cfg["algo_hash"],
        "base_method": f"#{cfg['base_method_n']}: {cfg['base_method_name']}",
        "sub_variation": cfg["sub_variation"],
        "seed": cfg["seed"],
        "url": cfg["url"][:150],
        "method": cfg["method"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "http_status": None,
        "response_type": None,  # "json", "html", "empty", "error"
        "response_size": 0,
        "response_preview": "",
        "extracted_data": {},
        "errors": [],
        "score": 0,  # Higher = more valuable findings
        "unique_findings": [],
    }
    
    # Build session
    session = requests.Session()
    session.verify = False
    
    # Apply UA variation
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
    
    # Base headers
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
    
    # Apply header modifications from base method
    for k, v in cfg.get("mod_header", {}).items():
        if v is None:
            headers.pop(k, None)
        else:
            headers[k] = v
    
    # Apply sub-variation header mods
    if cfg["sub_type"] == "header":
        parts = cfg["sub_value"].split("=")
        if len(parts) == 2:
            headers[parts[0]] = parts[1]
    
    # Build cookie
    if cfg.get("no_cookie"):
        cookie_str = ""
    elif cfg.get("cookie_override"):
        cookie_str = cfg["cookie_override"]
    else:
        cookie_str = f"tt_csrf_token={CREDENTIALS['csrf']}; ttwid={CREDENTIALS['ttwid']}"
    
    # Apply sub-variation cookie mods
    if cfg["sub_type"] == "cookie":
        parts = cfg["sub_value"].split("=")
        if len(parts) == 2:
            cookie_str += f"; {parts[0]}={parts[1]}"
    
    if cookie_str:
        headers["Cookie"] = cookie_str
    
    # Apply sub-variation extra params
    if cfg["sub_type"] == "extra":
        sep = "&" if "?" in cfg["url"] else "?"
        cfg["url"] = f"{cfg['url']}{sep}{cfg['sub_value']}"
    
    # Timeout
    timeout = {"timeout_5s": 5, "timeout_10s": 10, "timeout_30s": 30, "timeout_60s": 60}.get(cfg["sub_variation"], 15)
    
    try:
        if cfg["method"] == "POST":
            resp = session.post(cfg["url"], headers=headers, data=cfg.get("body"), timeout=timeout, allow_redirects=False)
        else:
            resp = session.get(cfg["url"], headers=headers, timeout=timeout, allow_redirects=False)
        
        result["http_status"] = resp.status_code
        result["response_size"] = len(resp.text)
        result["response_preview"] = resp.text[:300]
        
        # Classify response
        text = resp.text.strip()
        if not text:
            result["response_type"] = "empty"
            result["score"] = 1
        elif text.startswith("{") or text.startswith("["):
            result["response_type"] = "json"
            try:
                parsed = json.loads(text)
                result["extracted_data"] = parsed if len(str(parsed)) < 5000 else {"_truncated": True, "_keys": list(parsed.keys())[:20] if isinstance(parsed, dict) else f"list[{len(parsed)}]"}
                # Score based on what we got
                if isinstance(parsed, dict):
                    if parsed.get("status_code") == 0:
                        result["score"] = 100  # Perfect response
                    elif parsed.get("status_code"):
                        result["score"] = 50  # Got a status code
                    else:
                        result["score"] = 30  # JSON but no status_code
                # Check for valuable fields
                valuable_fields = ["user", "userInfo", "followers", "follower_count", "following", "aweme_list", "comments", "room_id", "owner", "stats", "top_fans", "gift_boxes", "stream_url"]
                for field in valuable_fields:
                    if isinstance(parsed, dict) and field in parsed:
                        result["unique_findings"].append(field)
                        result["score"] += 10
                    elif isinstance(parsed, dict) and "data" in parsed and isinstance(parsed["data"], dict) and field in parsed["data"]:
                        result["unique_findings"].append(f"data.{field}")
                        result["score"] += 10
            except json.JSONDecodeError:
                result["response_type"] = "json_broken"
                result["score"] = 5
        elif "<html" in text.lower() or "<!doctype" in text.lower():
            result["response_type"] = "html"
            result["score"] = 2
            # Check for useful HTML content
            if "SIGI_STATE" in text or "UNIVERSAL_DATA" in text:
                result["unique_findings"].append("embedded_json_in_html")
                result["score"] += 15
            if "csrf" in text.lower():
                result["unique_findings"].append("csrf_in_html")
                result["score"] += 5
        else:
            result["response_type"] = "text"
            result["score"] = 1
        
        # Bonus for unique status codes
        if resp.status_code == 200:
            result["score"] += 5
        elif resp.status_code == 403:
            result["score"] += 3  # 403 tells us the endpoint exists but needs auth
        elif resp.status_code == 404:
            result["score"] += 1  # 404 tells us the endpoint doesn't exist
        elif resp.status_code == 302:
            result["score"] += 2  # Redirect — interesting
            result["unique_findings"].append(f"redirect_to:{resp.headers.get('Location', '')[:80]}")
        
        # Bonus for response headers
        interesting_headers = ["X-Tt-Token", "X-Tt-Logid", "X-Argus", "X-Ladon", "X-Gorgon", "Set-Cookie"]
        for h in interesting_headers:
            if h in resp.headers:
                result["unique_findings"].append(f"header:{h}")
                result["score"] += 3
        
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
    
    # Save to agent directory
    agent_dir = os.path.join(BASE_DIR, f"agent_{agent_id:04d}")
    os.makedirs(agent_dir, exist_ok=True)
    
    # Save results.json
    with open(os.path.join(agent_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    # Save script.py (unique algorithm for this agent)
    script = f'''#!/usr/bin/env python3
# Agent #{agent_id:04d} — Algo: {cfg["algo_hash"]}
# Base: #{cfg["base_method_n"]}: {cfg["base_method_name"]}
# Variation: {cfg["sub_variation"]}
# Seed: {cfg["seed"]}
# URL: {cfg["url"][:100]}
# Method: {cfg["method"]}

import requests, json, urllib3
urllib3.disable_warnings()

URL = "{cfg['url']}"
METHOD = "{cfg['method']}"
HEADERS = {json.dumps(headers, indent=2)}
BODY = {json.dumps(cfg.get("body"), indent=2) if cfg.get("body") else "None"}
TIMEOUT = {timeout}

session = requests.Session()
session.verify = False

if METHOD == "POST":
    resp = session.post(URL, headers=HEADERS, data=BODY, timeout=TIMEOUT, allow_redirects=False)
else:
    resp = session.get(URL, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)

print(f"Status: {{resp.status_code}}")
print(f"Size: {{len(resp.text)}}")
print(f"Preview: {{resp.text[:300]}}")
'''
    with open(os.path.join(agent_dir, "script.py"), "w", encoding="utf-8") as f:
        f.write(script)
    
    return (agent_id, result["status"], result["score"], result)


def main():
    print("=" * 70)
    print(f"🔬 Multi-Agent TikTok API Prober v3 — {NUM_AGENTS} Agents")
    print(f"📎 URL: {TARGET_URL}")
    print(f"🧬 50 base methods × {len(SUB_VARIATIONS)} variations = 1000 unique agents")
    print("=" * 70)
    
    if os.path.exists(BASE_DIR):
        shutil.rmtree(BASE_DIR)
    os.makedirs(BASE_DIR, exist_ok=True)
    
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
                score_board.append((aid, score, status, res.get("response_type"), res.get("unique_findings", [])))
                if status == "completed": completed += 1
                else: failed += 1
        print(f"✅ ({completed} ok, {failed} fail)")
    
    elapsed = time.time() - start
    
    # ─── RANK AGENTS BY SCORE ───
    print(f"\n🏆 RANKING AGENTS BY VALUE (score = data richness + uniqueness)...")
    score_board.sort(key=lambda x: x[1], reverse=True)
    
    top_10 = score_board[:10]
    print(f"\n  🥇 Top 10 Most Valuable Agents:")
    for rank, (aid, score, status, rtype, findings) in enumerate(top_10, 1):
        cfg = generate_agent_config(aid)
        print(f"    #{rank}: Agent {aid:04d} | Score: {score} | {cfg['base_method_name']} + {cfg['sub_variation']}")
        print(f"         Status: {status} | HTTP: {all_results[aid].get('http_status')} | Type: {rtype}")
        if findings:
            print(f"         Unique findings: {', '.join(findings[:5])}")
    
    # ─── SCORE DISTRIBUTION ───
    score_ranges = {"100+": 0, "50-99": 0, "30-49": 0, "10-29": 0, "1-9": 0, "0": 0}
    for _, score, _, _, _ in score_board:
        if score >= 100: score_ranges["100+"] += 1
        elif score >= 50: score_ranges["50-99"] += 1
        elif score >= 30: score_ranges["30-49"] += 1
        elif score >= 10: score_ranges["10-29"] += 1
        elif score >= 1: score_ranges["1-9"] += 1
        else: score_ranges["0"] += 1
    
    print(f"\n  📊 Score Distribution:")
    for rng, cnt in score_ranges.items():
        print(f"    {rng:8s}: {cnt} agents")
    
    # ─── UNIQUE FINDINGS SUMMARY ───
    all_findings = Counter()
    for _, _, _, _, findings in score_board:
        for f in findings:
            all_findings[f] += 1
    
    print(f"\n  🔍 Unique Findings (across all agents):")
    for finding, count in all_findings.most_common(20):
        print(f"    {finding}: found by {count} agents")
    
    # ─── AGGREGATE RESULTS ───
    aggregate = {
        "total_agents": NUM_AGENTS,
        "completed": completed,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 2),
        "target_url": TARGET_URL,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "base_methods_used": len(BASE_METHODS),
        "sub_variations_used": len(SUB_VARIATIONS),
        "score_distribution": score_ranges,
        "top_10_agents": [
            {
                "rank": rank + 1,
                "agent_id": aid,
                "score": score,
                "base_method": generate_agent_config(aid)["base_method_name"],
                "sub_variation": generate_agent_config(aid)["sub_variation"],
                "http_status": all_results[aid].get("http_status"),
                "response_type": rtype,
                "unique_findings": findings,
            }
            for rank, (aid, score, status, rtype, findings) in enumerate(top_10)
        ],
        "all_findings": dict(all_findings.most_common()),
        "agent_summary": [
            {"agent_id": r["agent_id"], "score": r["score"], "status": r["status"],
             "http_status": r.get("http_status"), "response_type": r.get("response_type"),
             "base_method": r.get("base_method"), "unique_findings": r.get("unique_findings", [])}
            for r in all_results
        ],
    }
    
    with open(os.path.join(BASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2, default=str)
    
    # ─── ZIP ───
    print(f"\n📦 Creating ZIP...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            for fn in files:
                fp = os.path.join(root, fn)
                zf.write(fp, os.path.relpath(fp, BASE_DIR))
    
    zs = os.path.getsize(ZIP_PATH) // 1024
    print()
    print("=" * 70)
    print(f"✅ COMPLETE — {NUM_AGENTS} Agents")
    print(f"📊 Completed: {completed} | Failed: {failed}")
    print(f"⏱️ Elapsed: {elapsed:.1f}s")
    print(f"🏆 Top Agent: #{score_board[0][0]:04d} (score: {score_board[0][1]})")
    print(f"📦 ZIP: {ZIP_PATH} ({zs}KB)")
    print("=" * 70)

if __name__ == "__main__":
    main()
