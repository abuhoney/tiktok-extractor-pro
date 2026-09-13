#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onlinetiktok.py — موحّد استخراج TikTok Deep Data v5.0
====================================================================
يدمج جميع الدوال الفريدة من 5 سكربتات:
  - onlinetiktok.py (TikTokSearchEngine class-based extraction)
  - 2tikhtml.py (HTML parsing functions)
  - 3htmlboxtik.py (Gift boxes analysis)
  - tiktokjson.py (URL expansion + live details)
  - mhmdz1.py (TikTokSessionManager + WebSocket data)

يحفظ النتائج في: data/tiktok_deep_data/<unique_id>/live(N)/
  ├── page.html
  ├── webmssdk.js
  ├── json/
  │   ├── sigi_state.json
  │   ├── universal_data.json
  │   ├── pumbaa_rule.json
  │   ├── slardar_config.json
  │   └── api_domains.json
  ├── complete_data.json
  ├── gift_boxes.json (إذا وُجدت)
  └── js_files.txt

لا يكرّر الدوال الموجودة في extractor.py — يعمل بالتكامل معه.
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import logging
import hashlib
import random
import socket
import shutil
import base64
import tempfile
from datetime import datetime
from functools import wraps
from typing import Optional, Tuple, Dict, Any, List, Set
from urllib.parse import urlparse, parse_qs, urljoin, unquote, quote
from dataclasses import dataclass, field, asdict
from html import unescape
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("onlinetiktok")

# ────────────────────────────────────────────────────────────────────────────
#  الإعدادات
# ────────────────────────────────────────────────────────────────────────────

DATA_ROOT = os.environ.get("DATA_ROOT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
TIKTOK_DEEP_DIR = os.path.join(DATA_ROOT, "tiktok_deep_data")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ════════════════════════════════════════════════════════════════════════════
#  PART 1: TikTokSessionManager (من mhmdz1.py)
# ════════════════════════════════════════════════════════════════════════════

class TikTokSessionManager:
    """مدير جلسة HTTP موحّد (مدمج من mhmdz1.py) — يوفّر:
      - User-Agent rotation
      - Retry strategy مع backoff
      - Cookie persistence
      - Proxy support
    """

    _instance: Optional["TikTokSessionManager"] = None

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._init_session()
        self._init_user_agent()

    @classmethod
    def get_instance(cls) -> "TikTokSessionManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_session(self):
        s = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.verify = False
        # دعم البروكسي من البيئة
        proxy = os.environ.get("TIKTOK_PROXY") or os.environ.get("HTTPS_PROXY")
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
            logger.info(f"Using proxy: {proxy[:30]}...")
        self._session = s

    def _init_user_agent(self):
        self._user_agents = USER_AGENTS

    def _get_user_agent(self) -> str:
        return random.choice(self._user_agents)

    def _update_headers(self, additional_headers: dict = None):
        self._session.headers.update(DEFAULT_HEADERS)
        self._session.headers["User-Agent"] = self._get_user_agent()
        if additional_headers:
            self._session.headers.update(additional_headers)

    def get(self, url, **kwargs):
        self._update_headers(kwargs.pop("headers", None))
        return self._session.get(url, **kwargs)

    def post(self, url, **kwargs):
        self._update_headers(kwargs.pop("headers", None))
        return self._session.post(url, **kwargs)


def get_session() -> TikTokSessionManager:
    """Returns the singleton TikTokSessionManager instance."""
    return TikTokSessionManager.get_instance()


# ════════════════════════════════════════════════════════════════════════════
#  PART 2: HTML Extraction Functions (مدمجة من 2tikhtml.py + 3htmlboxtik.py + tiktokjson.py)
# ════════════════════════════════════════════════════════════════════════════

def is_tiktok_url(url: str) -> bool:
    """يتحقق إذا كان الرابط من TikTok."""
    if not url or not isinstance(url, str):
        return False
    return bool(re.search(r'tiktok\.com|tiktokv\.com|vm\.tiktok|vt\.tiktok', url, re.IGNORECASE))


def get_headers() -> dict:
    """يُرجع headers قياسية لطلبات TikTok."""
    return {**DEFAULT_HEADERS, "User-Agent": random.choice(USER_AGENTS)}


def extract_room_id_from_html(html: str) -> Optional[str]:
    """يستخرج room_id من HTML — يحاول عدة أنماط."""
    if not html:
        return None
    patterns = [
        r'"roomId"\s*:\s*"?(\d{15,25})',
        r'"room_id"\s*:\s*"?(\d{15,25})',
        r'"id_str"\s*:\s*"?(\d{15,25})["\s,]',
        r'"liveRoomID"\s*:\s*"?(\d{15,25})',
        r'"webcastRoomId"\s*:\s*"?(\d{15,25})',
        r'"streamId"\s*:\s*"?(\d{15,25})',
        r'/live/(\d{15,25})',
        r'room_id=(\d{15,25})',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def extract_username_from_html(html: str) -> Optional[str]:
    """يستخرج username من HTML."""
    if not html:
        return None
    patterns = [
        r'"uniqueId"\s*:\s*"([^"]+)"',
        r'"unique_id"\s*:\s*"([^"]+)"',
        r'"username"\s*:\s*"([^"]+)"',
        r'"screen_name"\s*:\s*"([^"]+)"',
        r'"nickname"\s*:\s*"([^"]+)"',
        r'@([A-Za-z0-9_.]+)/live',
        r'@([A-Za-z0-9_.]+)"',
        r'<title[^>]*>[@＠]?([A-Za-z0-9_.]+)\s',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            username = m.group(1).strip()
            if username and not username.isdigit() and len(username) <= 40:
                return username
    return None


def extract_user_id_from_html(html: str) -> Optional[str]:
    """يستخرج user_id من HTML."""
    if not html:
        return None
    patterns = [
        r'"userId"\s*:\s*"?(\d{15,25})',
        r'"user_id"\s*:\s*"?(\d{15,25})',
        r'"id_str"\s*:\s*"?(\d{15,25})["\s,]',
        r'"uid"\s*:\s*"?(\d{15,25})',
        r'"owner_user_id"\s*:\s*"?(\d{15,25})',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def extract_sec_uid_from_html(html: str) -> Optional[str]:
    """يستخرج sec_uid من HTML."""
    if not html:
        return None
    patterns = [
        r'"secUid"\s*:\s*"([A-Za-z0-9_-]{40,})"',
        r'"sec_uid"\s*:\s*"([A-Za-z0-9_-]{40,})"',
        r'"secUserId"\s*:\s*"([A-Za-z0-9_-]{40,})"',
        r'sec_user_id=([A-Za-z0-9_-]{40,})',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def extract_country_from_html(html: str) -> Optional[str]:
    """يستخرج region/country من HTML."""
    if not html:
        return None
    patterns = [
        r'"region"\s*:\s*"([A-Z]{2})"',
        r'"country"\s*:\s*"([A-Z]{2})"',
        r'"shareRegion"\s*:\s*"([A-Z]{2})"',
        r'"countryCode"\s*:\s*"([A-Z]{2})"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def extract_followers_from_html(html: str) -> int:
    """يستخرج follower_count من HTML."""
    if not html:
        return 0
    patterns = [
        r'"followerCount"\s*:\s*(\d+)',
        r'"follower_count"\s*:\s*(\d+)',
        r'"fans"\s*:\s*(\d+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return 0


def extract_total_likes_from_html(html: str) -> int:
    """يستخرج total likes من HTML."""
    if not html:
        return 0
    patterns = [
        r'"heart"\s*:\s*(\d+)',
        r'"totalLikes"\s*:\s*(\d+)',
        r'"total_likes"\s*:\s*(\d+)',
        r'"likeCount"\s*:\s*(\d+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return 0


def extract_bio_from_html(html: str) -> str:
    """يستخرج bio/signature من HTML."""
    if not html:
        return ""
    patterns = [
        r'"signature"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"bio_description"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"userBio"\s*:\s*"((?:[^"\\]|\\.)*)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return unescape(m.group(1).encode().decode('unicode_escape', errors='replace'))
    return ""


def extract_avatar_from_html(html: str) -> str:
    """يستخرج avatar URL من HTML."""
    if not html:
        return ""
    patterns = [
        r'"avatarThumb"\s*:\s*\{[^}]*"urlList"\s*:\s*\["([^"]+)"',
        r'"avatarUrl"\s*:\s*"([^"]+)"',
        r'"avatar_url"\s*:\s*"([^"]+)"',
        r'"avatarUri"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1).replace("\\u002F", "/").replace("\\/", "/")
    return ""


def extract_start_time_from_html(html: str) -> int:
    """يستخرج start_time من HTML."""
    if not html:
        return 0
    patterns = [
        r'"startTime"\s*:\s*(\d{10,11})',
        r'"start_time"\s*:\s*(\d{10,11})',
        r'"create_time"\s*:\s*(\d{10,11})',
        r'"liveStartTime"\s*:\s*(\d{10,11})',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return 0


def extract_cover_from_html(html: str) -> str:
    """يستخرج cover image URL من HTML."""
    if not html:
        return ""
    patterns = [
        r'"coverUrl"\s*:\s*"([^"]+)"',
        r'"live_cover"\s*:\s*"([^"]+)"',
        r'"cover"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1).replace("\\u002F", "/").replace("\\/", "/")
    return ""


def extract_rank_from_html(html: str) -> str:
    """يستخرج rank_text من HTML."""
    if not html:
        return ""
    patterns = [
        r'"rankText"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"rank_text"\s*:\s*"((?:[^"\\]|\\.)*)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return ""


def extract_total_views_from_html(html: str) -> int:
    """يستخرج total views من HTML."""
    if not html:
        return 0
    patterns = [
        r'"userCount"\s*:\s*(\d+)',
        r'"viewer_count"\s*:\s*(\d+)',
        r'"total_viewers"\s*:\s*(\d+)',
        r'"playCount"\s*:\s*(\d+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return 0


def extract_extra_live_data_from_html(html: str) -> Dict[str, Any]:
    """يستخرج بيانات إضافية من HTML — enter_count, replay_viewers, etc."""
    result = {}
    if not html:
        return result

    extraction_map = {
        "enter_count": [r'"enterCount"\s*:\s*(\d+)', r'"enter_count"\s*:\s*(\d+)'],
        "replay_viewers": [r'"replayViewers"\s*:\s*(\d+)', r'"replay_viewers"\s*:\s*(\d+)'],
        "like_count": [r'"likeCount"\s*:\s*(\d+)', r'"like_count"\s*:\s*(\d+)'],
        "follow_count": [r'"followCount"\s*:\s*(\d+)', r'"follow_count"\s*:\s*(\d+)'],
        "share_count": [r'"shareCount"\s*:\s*(\d+)', r'"share_count"\s*:\s*(\d+)'],
        "comment_count": [r'"commentCount"\s*:\s*(\d+)', r'"comment_count"\s*:\s*(\d+)'],
        "diamond_count": [r'"diamondCount"\s*:\s*(\d+)', r'"diamond_count"\s*:\s*(\d+)'],
        "gift_uv_count": [r'"giftUvCount"\s*:\s*(\d+)', r'"gift_uv_count"\s*:\s*(\d+)'],
        "room_id": [r'"roomId"\s*:\s*"?(\d{15,25})', r'"room_id"\s*:\s*"?(\d{15,25})'],
        "stream_id": [r'"streamId"\s*:\s*"?(\d{15,25})', r'"stream_id"\s*:\s*"?(\d{15,25})'],
        "live_status": [r'"liveStatus"\s*:\s*"([^"]+)"', r'"live_status"\s*:\s*"([^"]+)"'],
        "title": [r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"'],
        "with_linkmic": [r'"withLinkmic"\s*:\s*(true|false)'],
    }

    for key, patterns in extraction_map.items():
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                val = m.group(1)
                if val in ("true", "false"):
                    result[key] = val == "true"
                elif val.isdigit():
                    result[key] = int(val)
                else:
                    result[key] = val
                break
    return result


def extract_user_info_from_html(html: str) -> Dict[str, Any]:
    """يستخرج معلومات المستخدم الكاملة من HTML (مدمجة من tiktokjson.py)."""
    return {
        "username": extract_username_from_html(html),
        "user_id": extract_user_id_from_html(html),
        "sec_uid": extract_sec_uid_from_html(html),
        "country": extract_country_from_html(html),
        "followers": extract_followers_from_html(html),
        "total_likes": extract_total_likes_from_html(html),
        "bio": extract_bio_from_html(html),
        "avatar": extract_avatar_from_html(html),
    }


def extract_live_details_from_html(html: str) -> Dict[str, Any]:
    """يستخرج تفاصيل البث الكاملة (مدمجة من tiktokjson.py)."""
    return {
        "room_id": extract_room_id_from_html(html),
        "stream_id": None,
        "start_time": extract_start_time_from_html(html),
        "cover": extract_cover_from_html(html),
        "rank_text": extract_rank_from_html(html),
        "total_views": extract_total_views_from_html(html),
        **extract_extra_live_data_from_html(html),
    }


def extract_all_params_from_url(url: str) -> Dict[str, Any]:
    """يستخرج جميع البارامترات من رابط TikTok المختصر أو الكامل."""
    result = {
        "url": url,
        "url_type": "unknown",
        "user_id": None,
        "sec_user_id": None,
        "room_id": None,
        "video_id": None,
        "checksum": None,
        "share_link_id": None,
        "share_region": None,
        "timestamp": None,
        "utm_source": None,
    }
    if not url:
        return result

    parsed = parse_qs(urlparse(url).query)
    if "user_id" in parsed:
        result["user_id"] = parsed["user_id"][0]
    if "sec_user_id" in parsed:
        result["sec_user_id"] = parsed["sec_user_id"][0]
    if "room_id" in parsed:
        result["room_id"] = parsed["room_id"][0]
    if "checksum" in parsed:
        result["checksum"] = parsed["checksum"][0]
    if "share_link_id" in parsed:
        result["share_link_id"] = parsed["share_link_id"][0]
    if "share_region" in parsed:
        result["share_region"] = parsed["share_region"][0]
    if "timestamp" in parsed:
        try:
            result["timestamp"] = int(parsed["timestamp"][0])
        except ValueError:
            pass
    if "utm_source" in parsed:
        result["utm_source"] = parsed["utm_source"][0]

    # تحديد النوع
    if "/live" in url or "ugbiz_name=LIVE" in url:
        result["url_type"] = "live"
    elif "/video/" in url:
        result["url_type"] = "video"
    elif "/photo/" in url:
        result["url_type"] = "photo"
    elif re.search(r'@\w+$', urlparse(url).path):
        result["url_type"] = "profile"

    return result


def expand_tiktok_url(url: str) -> Tuple[str, Optional[str], Optional[str], Dict[str, Any]]:
    """يحلّ رابط TikTok المختصر ويُرجع (final_url, room_id, sec_user_id, all_params).
    مدمجة من tiktokjson.py.
    """
    if not url:
        return url, None, None, {}

    session = get_session()
    final_url = url
    try:
        resp = session.get(url, timeout=30, allow_redirects=True)
        final_url = resp.url
        # حفظ HTML لاستخراج البيانات منه
        html = resp.text
    except Exception as e:
        logger.warning(f"expand_tiktok_url failed: {e}")
        html = ""

    params = extract_all_params_from_url(final_url)
    room_id = params.get("room_id") or extract_room_id_from_html(html)
    sec_user_id = params.get("sec_user_id") or extract_sec_uid_from_html(html)

    return final_url, room_id, sec_user_id, params


# ════════════════════════════════════════════════════════════════════════════
#  PART 3: Gift Boxes Analysis (مدمجة من 3htmlboxtik.py)
# ════════════════════════════════════════════════════════════════════════════

def extract_gift_boxes_from_html(html: str) -> List[Dict[str, Any]]:
    """يستخرج صناديق الهدايا من HTML."""
    if not html:
        return []
    boxes = []
    pattern = r'"giftBoxes"\s*:\s*\[(.*?)\]'
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        return boxes
    try:
        # محاولة تحليل JSON للصناديق
        boxes_str = "[" + m.group(1) + "]"
        boxes = json.loads(boxes_str)
    except Exception:
        # محاولة بديلة عبر regex
        box_pattern = r'\{[^{}]*"gift_id"[^{}]*\}'
        for bm in re.finditer(box_pattern, m.group(1)):
            try:
                box = json.loads(bm.group(0))
                boxes.append(box)
            except Exception:
                continue
    return boxes


def analyze_gift_box(box: Dict[str, Any]) -> Dict[str, Any]:
    """يحلل صندوق هدايا واحد ويستخرج معلوماته."""
    if not isinstance(box, dict):
        return {}
    return {
        "gift_id": box.get("gift_id") or box.get("giftId"),
        "gift_count": box.get("gift_count") or box.get("giftCount") or 0,
        "level": box.get("level") or box.get("giftLevel"),
        "rarity": box.get("rarity") or box.get("gift_type") or "unknown",
        "expire_time": box.get("expire_time") or box.get("expireTime"),
        "user_count": box.get("user_count") or box.get("userCount"),
        "diamond_count": box.get("diamond_count") or box.get("diamondCount") or 0,
        "name": box.get("name") or box.get("giftName"),
        "description": box.get("description"),
        "icon_url": box.get("icon_url") or box.get("iconUrl"),
    }


def extract_all_gift_boxes(html: str, webcast_data: Dict[str, Any]) -> Dict[str, Any]:
    """يستخرج كل صناديق الهدايا من HTML + Webcast data."""
    html_boxes = extract_gift_boxes_from_html(html)
    webcast_boxes = []
    if isinstance(webcast_data, dict):
        gift_boxes = webcast_data.get("gift_boxes") or webcast_data.get("giftBoxes")
        if isinstance(gift_boxes, list):
            webcast_boxes = gift_boxes

    all_boxes = html_boxes + webcast_boxes
    analyzed = [analyze_gift_box(b) for b in all_boxes if isinstance(b, dict)]

    return {
        "total_boxes": len(analyzed),
        "boxes": analyzed,
        "by_rarity": _group_by_field(analyzed, "rarity"),
        "by_level": _group_by_field(analyzed, "level"),
        "highest_level": max((b.get("level") or 0 for b in analyzed), default=0),
        "total_diamond_value": sum(b.get("diamond_count") or 0 for b in analyzed),
    }


def _group_by_field(items: List[Dict], field: str) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for item in items:
        val = str(item.get(field, "unknown"))
        result[val] = result.get(val, 0) + 1
    return result


# ════════════════════════════════════════════════════════════════════════════
#  PART 4: HTML JSON Extraction (مدمجة من onlinetiktok.py TikTokSearchEngine)
# ════════════════════════════════════════════════════════════════════════════

def extract_json_blocks_from_html(html: str) -> Dict[str, Any]:
    """يستخرج جميع JSON blocks المضمّنة في HTML:
      - __UNIVERSAL_DATA_FOR_REHYDRATION__
      - __SIGI_STATE__ / _SIGI_STATE
      - pumbaa_rule
      - slardar_config
      - api_domains
    """
    if not html:
        return {}

    result = {}

    # __UNIVERSAL_DATA_FOR_REHYDRATION__
    m = re.search(
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if m:
        try:
            result["universal_data"] = json.loads(m.group(1).strip())
        except Exception:
            result["universal_data"] = None

    # __SIGI_STATE__
    m = re.search(
        r'<script[^>]*id="__SIGI_STATE__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not m:
        m = re.search(r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            result["sigi_state"] = json.loads(m.group(1).strip())
        except Exception:
            result["sigi_state"] = None

    # pumbaa_rule
    m = re.search(r'<script[^>]*id="pumbaa_rule"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            result["pumbaa_rule"] = json.loads(m.group(1).strip())
        except Exception:
            result["pumbaa_rule"] = None

    # slardar_config
    m = re.search(r'<script[^>]*id="slardar_config"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        m = re.search(r'"slardarConfig"\s*:\s*(\{.*?\})\s*[,}]', html, re.DOTALL)
    if m:
        try:
            result["slardar_config"] = json.loads(m.group(1).strip())
        except Exception:
            result["slardar_config"] = None

    # api_domains
    m = re.search(r'"apiDomains"\s*:\s*(\{[^}]+\})', html)
    if m:
        try:
            result["api_domains"] = json.loads(m.group(1))
        except Exception:
            result["api_domains"] = None

    return result


def extract_js_files_from_html(html: str, base_url: str = "https://www.tiktok.com") -> List[str]:
    """يستخرج كل روابط <script src="*.js"> من HTML — بما فيها webmssdk.js."""
    if not html:
        return []
    js_files: Set[str] = set()
    pattern = r'src=["\']([^"\']*\.js[^"\']*)["\']'
    for match in re.finditer(pattern, html, re.IGNORECASE):
        url = match.group(1)
        # تحويل المسار النسبي لمطلق
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = urljoin(base_url, url)
        elif not url.startswith("http"):
            url = urljoin(base_url, url)
        js_files.add(url)
    return sorted(js_files)


def extract_css_files_from_html(html: str, base_url: str = "https://www.tiktok.com") -> List[str]:
    """يستخرج كل روابط <link rel="stylesheet" href="*.css">."""
    if not html:
        return []
    css_files: Set[str] = set()
    pattern = r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']'
    for match in re.finditer(pattern, html, re.IGNORECASE):
        url = match.group(1)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = urljoin(base_url, url)
        elif not url.startswith("http"):
            url = urljoin(base_url, url)
        css_files.add(url)
    return sorted(css_files)


def extract_images_from_html(html: str, base_url: str = "https://www.tiktok.com") -> List[str]:
    """يستخرج روابط الصور من HTML."""
    if not html:
        return []
    images: Set[str] = set()
    pattern = r'<img[^>]+src=["\']([^"\']+)["\']'
    for match in re.finditer(pattern, html, re.IGNORECASE):
        url = match.group(1)
        if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']):
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = urljoin(base_url, url)
            elif not url.startswith("http"):
                url = urljoin(base_url, url)
            images.add(url)
    return sorted(images)


# ════════════════════════════════════════════════════════════════════════════
#  PART 5: Utilities (مدمجة من 2tikhtml.py)
# ════════════════════════════════════════════════════════════════════════════

def calculate_duration(start_time: int) -> str:
    """يحسب مدة البث من start_time (Unix timestamp)."""
    if not start_time:
        return "—"
    now = int(time.time())
    diff = max(0, now - start_time)
    hours = diff // 3600
    minutes = (diff % 3600) // 60
    seconds = diff % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_number(num: int) -> str:
    """تنسيق الأرقام (1.2K, 3.4M, etc)."""
    if num is None:
        return "0"
    num = int(num)
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def format_duration(timestamp: int) -> str:
    """يحوّل Unix timestamp إلى تاريخ مقروء."""
    if not timestamp:
        return "—"
    try:
        return datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "—"


def split_text(text: str, max_length: int = 4000) -> List[str]:
    """يُقسّم نص طويل إلى أجزاء (مدمجة من mhmdz1.py)."""
    if not text:
        return []
    if len(text) <= max_length:
        return [text]
    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    return parts


# ════════════════════════════════════════════════════════════════════════════
#  PART 6: Deep Storage System — data/tiktok_deep_data/<unique_id>/live(N)/
# ════════════════════════════════════════════════════════════════════════════

class DeepDataStorage:
    """يدير التخزين العميق للبيانات المستخرجة.

    البنية:
      data/tiktok_deep_data/
        ├── <unique_id>/
        │   ├── live1/
        │   │   ├── page.html
        │   │   ├── webmssdk.js
        │   │   ├── json/
        │   │   │   ├── sigi_state.json
        │   │   │   ├── universal_data.json
        │   │   │   ├── pumbaa_rule.json
        │   │   │   ├── slardar_config.json
        │   │   │   └── api_domains.json
        │   │   ├── complete_data.json
        │   │   ├── gift_boxes.json
        │   │   └── js_files.txt
        │   ├── live2/
        │   │   └── ...
        │   └── ...
    """

    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or TIKTOK_DEEP_DIR
        os.makedirs(self.base_dir, exist_ok=True)

    def _sanitize_id(self, uid: str) -> str:
        """يُنظّف unique_id للاستخدام كاسم مجلد."""
        if not uid:
            return "unknown"
        return re.sub(r'[^a-zA-Z0-9_.\-]', '_', str(uid))[:80]

    def get_or_create_live_dir(self, unique_id: str) -> Tuple[str, int]:
        """يُرجع مسار مجلد live(N) التالي لهذا المستخدم.

        Returns:
            (live_dir_path, live_number)
        """
        uid_safe = self._sanitize_id(unique_id)
        user_dir = os.path.join(self.base_dir, uid_safe)
        os.makedirs(user_dir, exist_ok=True)

        # ابحث عن next available live number
        existing = []
        for entry in os.listdir(user_dir):
            m = re.match(r'^live(\d+)$', entry)
            if m:
                existing.append(int(m.group(1)))

        next_num = (max(existing) + 1) if existing else 1
        live_dir = os.path.join(user_dir, f"live{next_num}")
        os.makedirs(live_dir, exist_ok=True)
        os.makedirs(os.path.join(live_dir, "json"), exist_ok=True)
        return live_dir, next_num

    def list_user_extractions(self, unique_id: str) -> List[Dict[str, Any]]:
        """يُرجع قائمة بكل استخراجات المستخدم."""
        uid_safe = self._sanitize_id(unique_id)
        user_dir = os.path.join(self.base_dir, uid_safe)
        if not os.path.exists(user_dir):
            return []
        extractions = []
        for entry in sorted(os.listdir(user_dir)):
            m = re.match(r'^live(\d+)$', entry)
            if not m:
                continue
            live_num = int(m.group(1))
            live_path = os.path.join(user_dir, entry)
            files = os.listdir(live_path) if os.path.isdir(live_path) else []
            complete_data_path = os.path.join(live_path, "complete_data.json")
            saved_at = None
            if os.path.exists(complete_data_path):
                try:
                    with open(complete_data_path, "r", encoding="utf-8") as f:
                        cd = json.load(f)
                    saved_at = cd.get("saved_at")
                except Exception:
                    pass
            extractions.append({
                "live_number": live_num,
                "directory": entry,
                "path": live_path,
                "files": files,
                "saved_at": saved_at,
            })
        return extractions

    def list_all_users(self) -> List[Dict[str, Any]]:
        """يُرجع قائمة بكل المستخدمين الذين لديهم بيانات محفوظة."""
        if not os.path.exists(self.base_dir):
            return []
        users = []
        for entry in sorted(os.listdir(self.base_dir)):
            user_path = os.path.join(self.base_dir, entry)
            if not os.path.isdir(user_path):
                continue
            live_count = sum(1 for x in os.listdir(user_path) if re.match(r'^live\d+$', x))
            users.append({
                "unique_id": entry,
                "live_count": live_count,
                "last_extraction": self._get_last_extraction_time(os.path.join(user_path, f"live{live_count}"))
                    if live_count > 0 else None,
            })
        return users

    def _get_last_extraction_time(self, live_dir: str) -> Optional[str]:
        """يقرأ saved_at من complete_data.json."""
        cd_path = os.path.join(live_dir, "complete_data.json")
        if not os.path.exists(cd_path):
            return None
        try:
            with open(cd_path, "r", encoding="utf-8") as f:
                cd = json.load(f)
            return cd.get("saved_at")
        except Exception:
            return None

    def save_html(self, live_dir: str, html: str) -> str:
        """يحفظ page.html."""
        path = os.path.join(live_dir, "page.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def save_json_files(self, live_dir: str, json_blocks: Dict[str, Any]) -> List[str]:
        """يحفظ جميع JSON blocks المُستخرجة في json/."""
        saved_paths = []
        json_dir = os.path.join(live_dir, "json")
        os.makedirs(json_dir, exist_ok=True)
        name_map = {
            "sigi_state": "sigi_state.json",
            "universal_data": "universal_data.json",
            "pumbaa_rule": "pumbaa_rule.json",
            "slardar_config": "slardar_config.json",
            "api_domains": "api_domains.json",
        }
        for key, filename in name_map.items():
            data = json_blocks.get(key)
            if data is not None:
                path = os.path.join(json_dir, filename)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                saved_paths.append(path)
        return saved_paths

    def save_js_files_list(self, live_dir: str, js_files: List[str]) -> str:
        """يحفظ قائمة روابط JS في js_files.txt."""
        path = os.path.join(live_dir, "js_files.txt")
        with open(path, "w", encoding="utf-8") as f:
            for js in js_files:
                f.write(js + "\n")
        return path

    def save_gift_boxes(self, live_dir: str, gift_data: Dict[str, Any]) -> str:
        """يحفظ gift_boxes.json."""
        path = os.path.join(live_dir, "gift_boxes.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gift_data, f, ensure_ascii=False, indent=2, default=str)
        return path

    def save_complete_data(self, live_dir: str, data: Dict[str, Any]) -> str:
        """يحفظ complete_data.json — التقرير الشامل."""
        path = os.path.join(live_dir, "complete_data.json")
        data.setdefault("saved_at", datetime.utcnow().isoformat() + "Z")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return path


# ════════════════════════════════════════════════════════════════════════════
#  PART 7: webmssdk.js Downloader
# ════════════════════════════════════════════════════════════════════════════

def download_webmssdk_from_html(html: str, live_dir: str) -> Dict[str, Any]:
    """يبحث عن webmssdk.js في HTML ويُنزّله إلى مجلد البث.

    Returns: dict with success, path, version, url.
    """
    if not html:
        return {"success": False, "error": "No HTML provided"}

    # ابحث عن روابط webmssdk
    pattern = r'(https?://[^"\'\s]*webmssdk/[^"\'\s]*\.js)'
    matches = re.findall(pattern, html)
    if not matches:
        return {"success": False, "error": "No webmssdk.js URL found in HTML"}

    # اختر أحدث إصدار (الأعلى رقم)
    def extract_version(url):
        m = re.search(r'webmssdk/(\d+\.\d+\.\d+\.\d+)/', url)
        return tuple(int(x) for x in m.group(1).split('.')) if m else (0, 0, 0, 0)

    matches.sort(key=extract_version, reverse=True)
    best_url = matches[0]
    version_str = re.search(r'webmssdk/(\d+\.\d+\.\d+\.\d+)/', best_url)
    version = version_str.group(1) if version_str else "unknown"

    # نزّل الملف
    session = get_session()
    try:
        resp = session.get(best_url, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}", "url": best_url}
        path = os.path.join(live_dir, "webmssdk.js")
        with open(path, "wb") as f:
            f.write(resp.content)
        logger.info(f"📥 Downloaded webmssdk.js v{version} ({len(resp.content)} bytes) → {path}")
        return {
            "success": True,
            "path": path,
            "version": version,
            "url": best_url,
            "size_bytes": len(resp.content),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "url": best_url}


# ════════════════════════════════════════════════════════════════════════════
#  PART 8: Main Deep Extractor — يوحّد كل ما سبق
# ════════════════════════════════════════════════════════════════════════════

def deep_extract(url: str, html: str = None, webcast_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """يستخرج كل البيانات من رابط TikTok ويحفظها في البنية العميقة.

    Args:
        url: رابط TikTok (مختصر أو كامل)
        html: HTML الكامل (اختياري — يُجلب إن لم يُمرَّر)
        webcast_data: بيانات Webcast API (اختياري)

    Returns:
        {
            "success": true,
            "unique_id": "soumiyalabihi",
            "live_number": 3,
            "live_dir": "data/tiktok_deep_data/soumiyalabihi/live3",
            "files_saved": [...],
            "user_info": {...},
            "live_details": {...},
            "gift_boxes": {...},
            "webmssdk": {...},
        }
    """
    if not url:
        return {"success": False, "error": "URL is required"}

    storage = DeepDataStorage()
    session = get_session()

    # 1) جلب HTML إن لم يُمرَّر
    if not html:
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            html = resp.text
            url = resp.url  # final URL after redirects
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch HTML: {e}"}

    # 2) استخراج unique_id من HTML
    unique_id = extract_username_from_html(html) or "unknown"

    # 3) إنشاء مجلد live(N) جديد
    live_dir, live_number = storage.get_or_create_live_dir(unique_id)

    # 4) استخراج جميع البيانات
    user_info = extract_user_info_from_html(html)
    live_details = extract_live_details_from_html(html)
    json_blocks = extract_json_blocks_from_html(html)
    js_files = extract_js_files_from_html(html)
    css_files = extract_css_files_from_html(html)
    images = extract_images_from_html(html)
    gift_data = extract_all_gift_boxes(html, webcast_data or {})
    url_params = extract_all_params_from_url(url)
    webmssdk_result = download_webmssdk_from_html(html, live_dir)

    # 5) حفظ الملفات
    saved_files = []
    saved_files.append(storage.save_html(live_dir, html))
    saved_files.extend(storage.save_json_files(live_dir, json_blocks))
    saved_files.append(storage.save_js_files_list(live_dir, js_files))
    if gift_data.get("total_boxes", 0) > 0:
        saved_files.append(storage.save_gift_boxes(live_dir, gift_data))

    # 6) بناء complete_data.json
    complete_data = {
        "url": url,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "unique_id": unique_id,
        "live_number": live_number,
        "live_dir": live_dir,
        "user_info": user_info,
        "live_details": live_details,
        "url_params": url_params,
        "extracted_files": {
            "js_files": js_files,
            "css_files": css_files,
            "images": images[:20],
        },
        "json_blocks_summary": {
            "has_universal_data": json_blocks.get("universal_data") is not None,
            "has_sigi_state": json_blocks.get("sigi_state") is not None,
            "has_pumbaa_rule": json_blocks.get("pumbaa_rule") is not None,
            "has_slardar_config": json_blocks.get("slardar_config") is not None,
            "has_api_domains": json_blocks.get("api_domains") is not None,
        },
        "gift_boxes_summary": {
            "total_boxes": gift_data.get("total_boxes", 0),
            "highest_level": gift_data.get("highest_level", 0),
            "total_diamond_value": gift_data.get("total_diamond_value", 0),
        },
        "webmssdk": webmssdk_result,
        "saved_files": saved_files,
    }
    saved_files.append(storage.save_complete_data(live_dir, complete_data))

    logger.info(f"✅ Deep extract complete: @{unique_id} live{live_number} → {live_dir} ({len(saved_files)} files)")

    return {
        "success": True,
        "unique_id": unique_id,
        "live_number": live_number,
        "live_dir": live_dir,
        "files_saved": saved_files,
        "user_info": user_info,
        "live_details": live_details,
        "gift_boxes": gift_data,
        "webmssdk": webmssdk_result,
        "complete_data": complete_data,
    }


# ════════════════════════════════════════════════════════════════════════════
#  PART 9: API Functions (تُستدعى من app.py)
# ════════════════════════════════════════════════════════════════════════════

def list_deep_users() -> Dict[str, Any]:
    """يُرجع قائمة بكل المستخدمين الذين لديهم بيانات عميقة محفوظة."""
    storage = DeepDataStorage()
    users = storage.list_all_users()
    return {"success": True, "total_users": len(users), "users": users, "base_dir": storage.base_dir}


def list_user_extractions(unique_id: str) -> Dict[str, Any]:
    """يُرجع قائمة بكل استخراجات مستخدم محدد."""
    storage = DeepDataStorage()
    extractions = storage.list_user_extractions(unique_id)
    if not extractions:
        return {"success": False, "error": "No extractions found", "unique_id": unique_id}
    return {
        "success": True,
        "unique_id": unique_id,
        "total_extractions": len(extractions),
        "extractions": extractions,
    }


def get_extraction_files(unique_id: str, live_number: int) -> Dict[str, Any]:
    """يُرجع محتوى ملفات استخراج محدد."""
    storage = DeepDataStorage()
    uid_safe = storage._sanitize_id(unique_id)
    live_dir = os.path.join(storage.base_dir, uid_safe, f"live{live_number}")
    if not os.path.exists(live_dir):
        return {"success": False, "error": "Extraction not found"}

    files = {}
    for entry in os.listdir(live_dir):
        path = os.path.join(live_dir, entry)
        if os.path.isfile(path):
            try:
                if entry.endswith(".json"):
                    with open(path, "r", encoding="utf-8") as f:
                        files[entry] = json.load(f)
                elif entry.endswith(".txt"):
                    with open(path, "r", encoding="utf-8") as f:
                        files[entry] = f.read().splitlines()
                elif entry.endswith(".js"):
                    size = os.path.getsize(path)
                    files[entry] = {"size_bytes": size, "preview": "(JS file - too large to display)"}
                elif entry == "page.html":
                    size = os.path.getsize(path)
                    files[entry] = {"size_bytes": size, "preview": "(HTML file - too large to display)"}
                else:
                    files[entry] = {"size_bytes": os.path.getsize(path)}
            except Exception as e:
                files[entry] = {"error": str(e)}

    # محتوى مجلد json/
    json_dir = os.path.join(live_dir, "json")
    if os.path.exists(json_dir):
        json_files = {}
        for entry in os.listdir(json_dir):
            path = os.path.join(json_dir, entry)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json_files[entry] = json.load(f)
            except Exception as e:
                json_files[entry] = {"error": str(e)}
        files["json/"] = json_files

    return {
        "success": True,
        "unique_id": unique_id,
        "live_number": live_number,
        "live_dir": live_dir,
        "files": files,
    }


def download_file(unique_id: str, live_number: int, filename: str) -> Optional[Tuple[str, bytes]]:
    """يُرجع (filename, content) لملف محدد. None إن لم يوجد."""
    storage = DeepDataStorage()
    uid_safe = storage._sanitize_id(unique_id)
    # اسم الملف قد يحتوي على /
    if "/" in filename:
        parts = filename.split("/")
        path = os.path.join(storage.base_dir, uid_safe, f"live{live_number}", *parts)
    else:
        path = os.path.join(storage.base_dir, uid_safe, f"live{live_number}", filename)
    if not os.path.exists(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            content = f.read()
        return (os.path.basename(path), content)
    except Exception as e:
        logger.error(f"download_file failed: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python onlinetiktok.py <tiktok_url>")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    result = deep_extract(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])
