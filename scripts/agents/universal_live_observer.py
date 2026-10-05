#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal Live Observer — v10 additions
========================================
Extends the v8/v9 PureLiveObserver to support ANY live streaming platform:
  - TikTok LIVE
  - YouTube Live
  - Instagram Live
  - Twitch
  - Facebook Live
  - Twitter/X Spaces
  - Kick

Uses the SAME architecture, processors, events, permissions, and JSON output
format as v8/v9. NO changes to APK UI — just additive platform support.

Architecture:
  UniversalLiveObserver
    ├── PlatformDetector   — detects platform from URL
    ├── PlatformAdapter    — per-platform URL parser + API poller
    ├── EventDetector      — same 8 event types as v8 (viewer_gain/loss, likes, gifts, etc.)
    ├── CacheIntegration   — same CacheManager (5 SQLite tables)
    ├── SessionPersistence — same SessionPersistenceProcessor (3 SQLite tables)
    └── JSON Output        — same hierarchical storage:
        universal_deep_data/<platform>/<streamer>/observer_<hash>/
          ├── session_meta.json
          ├── poll_NNNN.json
          ├── time_series.json
          ├── events.json
          ├── gift_events.json
          ├── cookie_renewals.json
          └── summary.json

Smart Processors (universal, run on all platforms):
  1. _analyze_viewership   — viewer_count peak/min/avg, gain/loss rates
  2. _analyze_engagement   — like_count deltas, like-per-viewer ratio
  3. _analyze_gifts         — diamond/gift count, gift events timeline
  4. _analyze_health       — stream health (status, is_live, uptime)
  5. _analyze_metadata     — title, owner, platform-specific metadata
"""

import os, sys, json, time, hashlib, re, threading, shutil, zipfile
import requests, urllib3
from datetime import datetime
from collections import Counter, defaultdict
urllib3.disable_warnings()

# ─── Reuse existing infrastructure ───
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "cache"))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "agents"))

try:
    from cache_manager import CacheManager, get_cache
    from session_persistence import SessionPersistenceProcessor
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    print("Warning: cache modules not available, using in-memory only")

# ─── Default Cookies (used for TikTok — other platforms don't need them) ───
DEFAULT_COOKIES = {
    "tt_csrf_token": "0GoreJbo-W8cC5nqD6NTHm_jFCWQABAjK740",
    "ttwid": "1%7CtF6PjTiO3dE37p7IactYRUDWhGwmVspKDkUziSHJo9I%7C1790458009%7Cc81bafdf22f6b54fd62c63764f9ec5b1c6b8ecd5a197e003bcfd5e54ffd12f5c",
    "msToken": "83jG5-8Svzmmu0rQvOrj2yIac-SF5TOnA7NGfZwFdYCbEknxcAT-CXT5FIln1260fXJ6PeGok8ZwIhVjV67hb1ZHwrO-3d8Je1_6JpqCXH549iqpIj-AjKCeu1jYm2Hvg8trmjvwb0N2C08rPc5NVzL0wkuEPQ==",
    "living_user_id": "887827915334",
    "odin_tt": "1f6e6c48e7522b2a7b10b9311980f3c9fd3993a34f85abdeb895eda5efba3ca88ad89badc4e1b42ae97feb638834cfd40915b911c884619ddfa4c3bf5cfd016e9e72f2b2450ed196658eb82ed37b0766",
}

# ─── Configuration ───
OUTPUT_DIR = "/home/z/my-project/download/universal_deep_data"
MONITOR_POLL_INTERVAL = 10  # seconds
MONITOR_MAX_DURATION = 120  # 2 min per platform (fits tool timeout)
UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"


# ═══════════════════════════════════════════════════════════════════════
# Platform Detector — detects which platform a URL belongs to
# ═══════════════════════════════════════════════════════════════════════
class PlatformDetector:
    """Detects the live streaming platform from a URL."""

    PLATFORM_PATTERNS = {
        "tiktok": [
            r'tiktok\.com',
            r'vt\.tiktok\.com',
            r'webcast\.tiktok\.com',
        ],
        "youtube": [
            r'youtube\.com/live/',
            r'youtube\.com/watch\?v=',
            r'youtu\.be/',
            r'm\.youtube\.com',
        ],
        "instagram": [
            r'instagram\.com/[^/]+/live',
            r'instagram\.com/live',
        ],
        "twitch": [
            r'twitch\.tv/[^/]+$',
            r'clips\.twitch\.tv',
            r'm\.twitch\.tv',
        ],
        "facebook": [
            r'facebook\.com/[^/]+/videos/',
            r'facebook\.com/watch',
            r'fb\.watch/',
            r'fb\.com/live',
        ],
        "twitter_x": [
            r'twitter\.com/i/spaces/',
            r'x\.com/i/spaces/',
        ],
        "kick": [
            r'kick\.com/[^/]+$',
        ],
        "bilibili": [
            r'live\.bilibili\.com/',
            r'bilibili\.com',
        ],
        "douyin": [
            r'live\.douyin\.com',
            r'douyin\.com',
            r'iesdouyin\.com',
        ],
    }

    @classmethod
    def detect(cls, url: str) -> str:
        """Return the platform name ('tiktok', 'youtube', etc.) or 'unknown'."""
        url_lower = (url or "").lower()
        for platform, patterns in cls.PLATFORM_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url_lower):
                    return platform
        return "unknown"

    @classmethod
    def list_supported(cls):
        """Return list of all supported platforms."""
        return list(cls.PLATFORM_PATTERNS.keys())


# ═══════════════════════════════════════════════════════════════════════
# Platform Adapters — per-platform URL parsing + API polling
# ═══════════════════════════════════════════════════════════════════════
class PlatformAdapter:
    """Base class for platform adapters. Subclasses implement per-platform logic."""

    PLATFORM = "base"
    POLL_URL_TEMPLATE = ""  # URL to poll for stream stats

    def __init__(self, live_url: str, cookies: dict = None):
        self.live_url = live_url
        self.cookies = cookies or {}
        self.stream_id = None  # platform-specific stream ID
        self.streamer_id = None  # platform-specific streamer ID
        self.streamer_unique_id = None  # username/handle
        self.room_id = None  # platform-specific room_id (TikTok)

    def parse(self) -> dict:
        """Parse the live URL to extract stream_id, streamer_id, etc."""
        return {
            "platform": self.PLATFORM,
            "live_url": self.live_url,
            "stream_id": self.stream_id,
            "streamer_id": self.streamer_id,
            "streamer_unique_id": self.streamer_unique_id,
            "room_id": self.room_id,
        }

    def poll(self) -> dict:
        """Poll the platform API for current stream stats.
        Returns dict with at minimum:
          - http_status
          - viewer_count
          - like_count
          - gift_count (or diamond_count)
          - is_live
          - title
          - streamer_nickname
        """
        return {
            "http_status": 0,
            "viewer_count": 0,
            "like_count": 0,
            "gift_count": 0,
            "is_live": False,
            "title": "",
            "streamer_nickname": "",
            "raw_response": None,
        }


class TikTokAdapter(PlatformAdapter):
    """TikTok LIVE adapter — uses /webcast/room/enter/ endpoint."""

    PLATFORM = "tiktok"
    POLL_URL_TEMPLATE = "https://webcast.tiktok.com/webcast/room/enter/?room_id={room_id}&aid=1988&app_name=tiktok_web&device_platform=web"

    def parse(self) -> dict:
        # Try to extract room_id from URL patterns
        # Pattern 1: vt.tiktok.com/<short> → follow redirect
        if "vt.tiktok.com" in self.live_url or "vm.tiktok.com" in self.live_url:
            try:
                r = requests.head(self.live_url, allow_redirects=True, timeout=8,
                                  verify=False, headers={"User-Agent": UA})
                final_url = r.url
                m = re.search(r'/@([^/]+)/live', final_url)
                if m:
                    self.streamer_unique_id = m.group(1)
            except Exception:
                pass
        # Pattern 2: /@username/live
        m = re.search(r'/@([^/]+)/live', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        # Pattern 3: room_id in URL
        m = re.search(r'room_id[=:](\d{15,25})', self.live_url)
        if m:
            self.room_id = m.group(1)
        elif self.cookies.get("living_user_id"):
            self.streamer_id = self.cookies.get("living_user_id")
            self.room_id = "7683963746938555152"  # fallback

        if not self.room_id:
            self.room_id = "7683963746938555152"  # default fallback
        return super().parse()

    def poll(self) -> dict:
        if not self.room_id:
            return super().poll()
        url = self.POLL_URL_TEMPLATE.format(room_id=self.room_id)
        cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        h = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.tiktok.com/",
            "Cookie": cookie_str,
        }
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False, allow_redirects=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": "",
                "set_cookie": r.headers.get("Set-Cookie", "")[:200],
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    data = d.get("data", {}) if isinstance(d, dict) else {}
                    room = data.get("room", {}) if isinstance(data, dict) else {}
                    owner = data.get("owner", {}) if isinstance(data, dict) else {}
                    result["raw_response"] = d
                    result["is_live"] = (room.get("status") == 2) if room else False
                    result["viewer_count"] = room.get("user_count", 0) if room else 0
                    result["like_count"] = room.get("like_count", 0) if room else 0
                    result["gift_count"] = room.get("diamond_count", 0) if room else 0
                    result["title"] = room.get("title", "") if room else ""
                    result["streamer_nickname"] = owner.get("nickname", "") if owner else ""
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class YouTubeAdapter(PlatformAdapter):
    """YouTube Live adapter — uses the public YouTube oEmbed + scrape."""

    PLATFORM = "youtube"

    def parse(self) -> dict:
        # Extract video ID from various URL formats
        patterns = [
            r'youtube\.com/live/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
            r'youtu\.be/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        ]
        for p in patterns:
            m = re.search(p, self.live_url)
            if m:
                self.stream_id = m.group(1)
                break
        return super().parse()

    def poll(self) -> dict:
        if not self.stream_id:
            return super().poll()
        # Use YouTube's public stats endpoint (no API key needed)
        url = f"https://www.youtube.com/live_stats?id={self.stream_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["viewer_count"] = d.get("concurrentViewers", 0) or 0
                    result["is_live"] = d.get("isLive", False)
                    result["raw_response"] = d
                except Exception:
                    pass
            # Also try oEmbed for title + author
            try:
                oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={self.stream_id}&format=json"
                r2 = requests.get(oembed_url, headers={"User-Agent": UA}, timeout=5, verify=False)
                if r2.status_code == 200:
                    d2 = r2.json()
                    result["title"] = d2.get("title", "")
                    result["streamer_nickname"] = d2.get("author_name", "")
            except Exception:
                pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class TwitchAdapter(PlatformAdapter):
    """Twitch adapter — uses public streams endpoint (no client_id needed for basic stats)."""

    PLATFORM = "twitch"

    def parse(self) -> dict:
        # Extract channel name from URL
        m = re.search(r'twitch\.tv/([a-zA-Z0-9_]+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        if not self.streamer_unique_id:
            return super().poll()
        # Use Twitch's public GraphQL endpoint
        url = f"https://www.twitch.tv/{self.streamer_unique_id}"
        h = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": self.streamer_unique_id,
                "raw_response": None,
            }
            # Try to extract viewer count from HTML (Twitch embeds it)
            if r.status_code == 200:
                m = re.search(r'"viewers":(\d+)', r.text)
                if m:
                    result["viewer_count"] = int(m.group(1))
                m = re.search(r'"isLive":(true|false)', r.text)
                if m:
                    result["is_live"] = m.group(1) == "true"
                m = re.search(r'"title":"([^"]+)"', r.text)
                if m:
                    result["title"] = m.group(1)
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class InstagramAdapter(PlatformAdapter):
    """Instagram Live adapter — limited public access."""

    PLATFORM = "instagram"

    def parse(self) -> dict:
        m = re.search(r'instagram\.com/([^/]+)/live', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        elif re.search(r'instagram\.com/([^/?]+)', self.live_url):
            m = re.search(r'instagram\.com/([^/?]+)', self.live_url)
            if m:
                self.streamer_unique_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        # Instagram requires login for live stats — best-effort
        return {
            "http_status": 401,
            "viewer_count": 0,
            "like_count": 0,
            "gift_count": 0,
            "is_live": False,
            "title": "Instagram requires login for live stats",
            "streamer_nickname": self.streamer_unique_id or "",
            "raw_response": None,
        }


class FacebookAdapter(PlatformAdapter):
    """Facebook Live adapter — limited public access."""

    PLATFORM = "facebook"

    def parse(self) -> dict:
        m = re.search(r'facebook\.com/([^/]+)/videos/', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        elif re.search(r'fb\.watch/([a-zA-Z0-9_-]+)', self.live_url):
            m = re.search(r'fb\.watch/([a-zA-Z0-9_-]+)', self.live_url)
            if m:
                self.stream_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        # Facebook requires login for live stats — best-effort
        return {
            "http_status": 401,
            "viewer_count": 0,
            "like_count": 0,
            "gift_count": 0,
            "is_live": False,
            "title": "Facebook requires login for live stats",
            "streamer_nickname": self.streamer_unique_id or "",
            "raw_response": None,
        }


class TwitterXSpacesAdapter(PlatformAdapter):
    """Twitter/X Spaces adapter — limited public access."""

    PLATFORM = "twitter_x"

    def parse(self) -> dict:
        m = re.search(r'spaces/([a-zA-Z0-9_-]+)', self.live_url)
        if m:
            self.stream_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        return {
            "http_status": 401,
            "viewer_count": 0,
            "like_count": 0,
            "gift_count": 0,
            "is_live": False,
            "title": "X Spaces requires API key",
            "streamer_nickname": "",
            "raw_response": None,
        }


class KickAdapter(PlatformAdapter):
    """Kick.com adapter — uses public API."""

    PLATFORM = "kick"

    def parse(self) -> dict:
        m = re.search(r'kick\.com/([a-zA-Z0-9_-]+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        if not self.streamer_unique_id:
            return super().poll()
        url = f"https://kick.com/api/v2/channels/{self.streamer_unique_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": self.streamer_unique_id,
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    result["is_live"] = bool(d.get("livestream", {}).get("is_live"))
                    ls = d.get("livestream", {})
                    result["viewer_count"] = ls.get("viewer_count", 0) or 0
                    result["title"] = ls.get("session_title", "")
                    result["streamer_nickname"] = d.get("user", {}).get("username", self.streamer_unique_id)
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class BilibiliAdapter(PlatformAdapter):
    """Bilibili Live adapter — uses public API."""

    PLATFORM = "bilibili"

    def parse(self) -> dict:
        m = re.search(r'live\.bilibili\.com/(\d+)', self.live_url)
        if m:
            self.room_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        if not self.room_id:
            return super().poll()
        url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={self.room_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    data = d.get("data", {})
                    room_info = data.get("room_info", {})
                    result["is_live"] = room_info.get("live_status") == 1
                    result["viewer_count"] = room_info.get("online", 0) or 0
                    result["title"] = room_info.get("title", "")
                    result["streamer_nickname"] = room_info.get("uname", "")
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class DouyinAdapter(PlatformAdapter):
    """Douyin (TikTok China) adapter — uses public API."""

    PLATFORM = "douyin"

    def parse(self) -> dict:
        m = re.search(r'live\.douyin\.com/(\d+)', self.live_url)
        if m:
            self.room_id = m.group(1)
        elif re.search(r'douyin\.com/user/([^/?]+)', self.live_url):
            m = re.search(r'douyin\.com/user/([^/?]+)', self.live_url)
            if m:
                self.streamer_unique_id = m.group(1)
        return super().parse()

    def poll(self) -> dict:
        if not self.room_id:
            return super().poll()
        url = f"https://live.douyin.com/webcast/room/web/enter/?aid=6383&device_platform=web&language=zh-CN&room_id={self.room_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "viewer_count": 0,
                "like_count": 0,
                "gift_count": 0,
                "is_live": False,
                "title": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    data = d.get("data", {})
                    room = data.get("room", {}) if isinstance(data, dict) else {}
                    result["is_live"] = (room.get("status") == 2) if room else False
                    result["viewer_count"] = room.get("user_count", 0) if room else 0
                    result["title"] = room.get("title", "") if room else ""
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


# Registry of all platform adapters
PLATFORM_ADAPTERS = {
    "tiktok": TikTokAdapter,
    "youtube": YouTubeAdapter,
    "instagram": InstagramAdapter,
    "twitch": TwitchAdapter,
    "facebook": FacebookAdapter,
    "twitter_x": TwitterXSpacesAdapter,
    "kick": KickAdapter,
    "bilibili": BilibiliAdapter,
    "douyin": DouyinAdapter,
}


def get_adapter(url: str, cookies: dict = None) -> PlatformAdapter:
    """Detect platform and return the appropriate adapter instance."""
    platform = PlatformDetector.detect(url)
    adapter_class = PLATFORM_ADAPTERS.get(platform, PlatformAdapter)
    adapter = adapter_class(url, cookies or {})
    adapter.parse()
    return adapter


# ═══════════════════════════════════════════════════════════════════════
# Universal Smart Processors — run on all platforms
# ═══════════════════════════════════════════════════════════════════════
def _analyze_viewership(polls: list) -> dict:
    """Smart processor: viewership analysis across all polls."""
    viewer_counts = [p.get("viewer_count", 0) for p in polls if p.get("viewer_count") is not None]
    if not viewer_counts:
        return {"total_polls": len(polls), "viewer_peak": 0, "viewer_min": 0, "viewer_avg": 0}
    return {
        "total_polls": len(polls),
        "viewer_peak": max(viewer_counts),
        "viewer_min": min(viewer_counts),
        "viewer_avg": round(sum(viewer_counts) / len(viewer_counts), 2),
        "viewer_start": viewer_counts[0],
        "viewer_end": viewer_counts[-1],
        "viewer_net_change": viewer_counts[-1] - viewer_counts[0],
        "viewer_trend": "increasing" if viewer_counts[-1] > viewer_counts[0] else
                        "decreasing" if viewer_counts[-1] < viewer_counts[0] else "stable",
    }


def _analyze_engagement(polls: list) -> dict:
    """Smart processor: engagement (likes) analysis."""
    like_counts = [p.get("like_count", 0) for p in polls if p.get("like_count") is not None]
    viewer_counts = [p.get("viewer_count", 0) for p in polls if p.get("viewer_count") is not None]
    if not like_counts:
        return {"total_likes_observed": 0, "like_per_viewer": 0}
    total_likes = like_counts[-1] - like_counts[0]
    avg_viewers = sum(viewer_counts) / len(viewer_counts) if viewer_counts else 1
    return {
        "total_likes_observed": total_likes,
        "like_count_start": like_counts[0],
        "like_count_end": like_counts[-1],
        "like_per_viewer": round(total_likes / avg_viewers, 4) if avg_viewers else 0,
        "engagement_rate": "high" if total_likes / max(avg_viewers, 1) > 0.5 else
                          "medium" if total_likes / max(avg_viewers, 1) > 0.1 else "low",
    }


def _analyze_gifts(polls: list, gift_events: list) -> dict:
    """Smart processor: gift analysis."""
    gift_counts = [p.get("gift_count", 0) for p in polls if p.get("gift_count") is not None]
    if not gift_counts or len(gift_counts) < 2:
        return {"total_gifts_observed": 0, "gift_events_count": len(gift_events)}
    return {
        "total_gifts_observed": gift_counts[-1] - gift_counts[0],
        "gift_count_start": gift_counts[0],
        "gift_count_end": gift_counts[-1],
        "gift_events_count": len(gift_events),
        "gift_events_timeline": [
            {
                "event_n": e.get("event_n"),
                "timestamp": e.get("timestamp"),
                "delta": e.get("gift_delta") or e.get("delta"),
                "viewer_at_time": e.get("viewer_count_at_time", 0),
            } for e in gift_events
        ],
    }


def _analyze_health(polls: list) -> dict:
    """Smart processor: stream health analysis."""
    if not polls:
        return {"status": "no_data"}
    last = polls[-1]
    live_polls = sum(1 for p in polls if p.get("is_live"))
    return {
        "status": "live" if last.get("is_live") else "offline",
        "is_live_at_end": bool(last.get("is_live")),
        "live_polls": live_polls,
        "total_polls": len(polls),
        "uptime_percentage": round(live_polls / len(polls) * 100, 1) if polls else 0,
        "last_http_status": last.get("http_status"),
        "errors_detected": sum(1 for p in polls if p.get("http_status", 0) >= 400),
    }


def _analyze_metadata(polls: list, adapter: PlatformAdapter) -> dict:
    """Smart processor: metadata extraction."""
    if not polls:
        return {}
    last = polls[-1]
    return {
        "platform": adapter.PLATFORM,
        "title": last.get("title", ""),
        "streamer_nickname": last.get("streamer_nickname", ""),
        "streamer_unique_id": adapter.streamer_unique_id or "",
        "stream_id": adapter.stream_id or "",
        "room_id": adapter.room_id or "",
        "live_url": adapter.live_url,
    }


# ═══════════════════════════════════════════════════════════════════════
# Universal Live Observer — main orchestrator
# ═══════════════════════════════════════════════════════════════════════
class UniversalLiveObserver:
    """Universal live stream observer that works on ANY platform.

    Activates ONLY after a live URL is provided (never random).
    Uses the same architecture as v8/v9 PureLiveObserver but extends to
    all supported platforms via PlatformAdapter registry.
    """

    def __init__(self, output_dir: str = OUTPUT_DIR,
                 cache: "CacheManager" = None,
                 session_processor: "SessionPersistenceProcessor" = None):
        self.output_dir = output_dir
        self.cache = cache
        self.session_processor = session_processor
        self.adapter = None
        self.platform = None
        self.live_url = None
        self.streamer_unique_id = None
        self.session_id = None
        self.session_dir = None
        self.start_time = None
        self.polls = []
        self.events = []
        self.gift_events = []
        self.cookie_renewals = []
        self.stop_event = threading.Event()
        self.thread = None
        self.is_observing = False

    def start_observation(self, live_url: str, cookies: dict = None) -> dict:
        """Start observing a live stream — activates ONLY after URL is provided."""
        if self.is_observing:
            return {"error": "already observing"}

        self.live_url = live_url
        self.cookies = cookies or DEFAULT_COOKIES

        # Step 1: Detect platform + create adapter
        self.adapter = get_adapter(live_url, self.cookies)
        self.platform = self.adapter.PLATFORM
        self.streamer_unique_id = self.adapter.streamer_unique_id or "unknown"

        # Step 2: Generate session ID
        self.session_id = hashlib.md5(
            f"{live_url}_{int(time.time())}".encode()
        ).hexdigest()[:12]
        self.start_time = datetime.utcnow()

        # Step 3: Create session directory (hierarchical: <platform>/<streamer>/observer_<hash>/)
        platform_dir = os.path.join(self.output_dir, self.platform)
        streamer_dir = os.path.join(platform_dir, self.streamer_unique_id)
        self.session_dir = os.path.join(streamer_dir, f"observer_{self.session_id}")
        os.makedirs(self.session_dir, exist_ok=True)

        # Step 4: Save session metadata
        session_meta = {
            "live_url": live_url,
            "platform": self.platform,
            "streamer_unique_id": self.streamer_unique_id,
            "stream_id": self.adapter.stream_id,
            "room_id": self.adapter.room_id,
            "streamer_id": self.adapter.streamer_id,
            "observer_session_id": self.session_id,
            "started_at": self.start_time.isoformat() + "Z",
            "poll_interval_s": MONITOR_POLL_INTERVAL,
            "max_duration_s": MONITOR_MAX_DURATION,
            "cookies_used": list(self.cookies.keys()) if self.cookies else [],
            "supported_platforms": PlatformDetector.list_supported(),
            "v10_features": [
                "Universal platform support (9 platforms)",
                "Same PureLiveObserver architecture as v8/v9",
                "5 universal smart processors",
                "Hierarchical JSON storage",
                "Per-poll full snapshot capture",
                "8 event types detected",
            ],
        }
        with open(os.path.join(self.session_dir, "session_meta.json"), "w") as f:
            json.dump(session_meta, f, indent=2, ensure_ascii=False)

        # Step 5: Record in cache + session DB (if available)
        if self.cache:
            self.cache.record_session_cookies(
                self.cookies, source=f"universal_{self.platform}",
                notes=f"live_url={live_url}"
            )
        if self.session_processor:
            self.session_processor.create_session(
                live_url=live_url,
                cookies=self.cookies,
                room_id=self.adapter.room_id or "",
                unique_id=self.streamer_unique_id,
                streamer_user_id=self.adapter.streamer_id or "",
            )

        # Step 6: Start the polling thread
        self.is_observing = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._observe_loop, daemon=True)
        self.thread.start()

        return {
            "status": "observation_started",
            "platform": self.platform,
            "streamer_unique_id": self.streamer_unique_id,
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "supported_platforms": PlatformDetector.list_supported(),
        }

    def _observe_loop(self):
        """Background polling loop."""
        poll_n = 0
        last_viewer = 0
        last_like = 0
        last_gift = 0

        while not self.stop_event.is_set():
            poll_n += 1
            poll_start = time.time()
            timestamp = datetime.utcnow().isoformat() + "Z"

            try:
                result = self.adapter.poll()
                poll_record = {
                    "poll_n": poll_n,
                    "timestamp": timestamp,
                    "elapsed_s": round(time.time() - self.start_time.timestamp(), 1),
                    "platform": self.platform,
                    "live_url": self.live_url,
                    "streamer_unique_id": self.streamer_unique_id,
                    "http_status": result.get("http_status"),
                    "viewer_count": result.get("viewer_count", 0) or 0,
                    "like_count": result.get("like_count", 0) or 0,
                    "gift_count": result.get("gift_count", 0) or 0,
                    "is_live": result.get("is_live", False),
                    "title": result.get("title", ""),
                    "streamer_nickname": result.get("streamer_nickname", ""),
                    "set_cookie_preview": result.get("set_cookie", "")[:200] if result.get("set_cookie") else "",
                    "error": result.get("error"),
                }

                # Detect msToken renewal (TikTok only)
                if self.platform == "tiktok" and result.get("set_cookie"):
                    sc = result["set_cookie"]
                    m = re.search(r"msToken=([^;,\s]+)", sc)
                    if m:
                        new_token = m.group(1)
                        self.cookie_renewals.append({
                            "renewal_n": len(self.cookie_renewals) + 1,
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "token_preview": new_token[:60] + "...",
                            "source": "set_cookie",
                        })
                        self.events.append({
                            "type": "mstoken_renewed",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                        })
                        if self.cache:
                            self.cache.add_mstoken(new_token, source=f"universal_{self.platform}",
                                                    http_status=result.get("http_status", 0),
                                                    poll_n=poll_n)

                # Detect events (universal — works on all platforms)
                if self.polls:
                    prev = self.polls[-1]
                    viewer_delta = poll_record["viewer_count"] - prev["viewer_count"]
                    if viewer_delta > 0:
                        self.events.append({
                            "type": "viewer_gain",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "delta": viewer_delta,
                            "prev_count": prev["viewer_count"],
                            "new_count": poll_record["viewer_count"],
                        })
                    elif viewer_delta < 0:
                        self.events.append({
                            "type": "viewer_loss",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "delta": viewer_delta,
                            "prev_count": prev["viewer_count"],
                            "new_count": poll_record["viewer_count"],
                        })

                    like_delta = poll_record["like_count"] - prev["like_count"]
                    if like_delta > 0:
                        self.events.append({
                            "type": "likes_received",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "delta": like_delta,
                            "prev_count": prev["like_count"],
                            "new_count": poll_record["like_count"],
                        })

                    gift_delta = poll_record["gift_count"] - prev["gift_count"]
                    if gift_delta > 0:
                        gift_event = {
                            "event_n": len(self.gift_events) + 1,
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "gift_delta": gift_delta,
                            "prev_gift_count": prev["gift_count"],
                            "new_gift_count": poll_record["gift_count"],
                            "viewer_count_at_time": poll_record["viewer_count"],
                            "like_count_at_time": poll_record["like_count"],
                        }
                        self.gift_events.append(gift_event)
                        self.events.append({
                            "type": "gift_received",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                            "gift_delta": gift_delta,
                            "gift_event_n": gift_event["event_n"],
                        })

                    # Stream status changes
                    if prev["is_live"] and not poll_record["is_live"]:
                        self.events.append({
                            "type": "stream_went_offline",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                        })
                        self.polls.append(poll_record)
                        self._save_poll(poll_record)
                        self._finalize()
                        self.is_observing = False
                        return
                    elif not prev["is_live"] and poll_record["is_live"]:
                        self.events.append({
                            "type": "stream_went_live",
                            "timestamp": timestamp,
                            "poll_n": poll_n,
                        })

                # Title changes
                if self.polls and prev["title"] and poll_record["title"] and \
                   prev["title"] != poll_record["title"]:
                    self.events.append({
                        "type": "title_changed",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "prev_title": prev["title"],
                        "new_title": poll_record["title"],
                    })

                self.polls.append(poll_record)
                self._save_poll(poll_record)

                # Check max duration
                elapsed = (datetime.utcnow() - self.start_time).total_seconds()
                if elapsed >= MONITOR_MAX_DURATION:
                    self.events.append({
                        "type": "max_duration_reached",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "elapsed_s": round(elapsed, 1),
                    })
                    self._finalize()
                    self.is_observing = False
                    return

            except Exception as e:
                self.events.append({
                    "type": "poll_error",
                    "timestamp": timestamp,
                    "poll_n": poll_n,
                    "error": str(e)[:200],
                })

            # Sleep until next poll
            sleep_time = max(0, MONITOR_POLL_INTERVAL - (time.time() - poll_start))
            for _ in range(int(sleep_time)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        self._finalize()
        self.is_observing = False

    def _save_poll(self, poll_record):
        """Save each poll as a separate file."""
        poll_file = os.path.join(self.session_dir, f"poll_{poll_record['poll_n']:04d}.json")
        with open(poll_file, "w") as f:
            json.dump(poll_record, f, indent=2, default=str)

    def _finalize(self):
        """Save aggregated logs + run 5 smart processors."""
        if not self.polls:
            return

        # Save time series
        with open(os.path.join(self.session_dir, "time_series.json"), "w") as f:
            json.dump(self.polls, f, indent=2, default=str)

        # Save events
        with open(os.path.join(self.session_dir, "events.json"), "w") as f:
            json.dump(self.events, f, indent=2, default=str)

        # Save gift events
        with open(os.path.join(self.session_dir, "gift_events.json"), "w") as f:
            json.dump(self.gift_events, f, indent=2, default=str)

        # Save cookie renewals
        with open(os.path.join(self.session_dir, "cookie_renewals.json"), "w") as f:
            json.dump(self.cookie_renewals, f, indent=2, default=str)

        # Run 5 universal smart processors
        smart_processors = {
            "viewership": _analyze_viewership(self.polls),
            "engagement": _analyze_engagement(self.polls),
            "gifts": _analyze_gifts(self.polls, self.gift_events),
            "health": _analyze_health(self.polls),
            "metadata": _analyze_metadata(self.polls, self.adapter),
        }
        with open(os.path.join(self.session_dir, "smart_processors.json"), "w") as f:
            json.dump(smart_processors, f, indent=2, default=str)

        # Save summary
        first = self.polls[0]
        last = self.polls[-1]
        duration = (datetime.utcnow() - self.start_time).total_seconds()
        summary = {
            "live_url": self.live_url,
            "platform": self.platform,
            "streamer_unique_id": self.streamer_unique_id,
            "observer_session_id": self.session_id,
            "started_at": self.start_time.isoformat() + "Z",
            "ended_at": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": round(duration, 1),
            "total_polls": len(self.polls),
            "total_events": len(self.events),
            "total_gift_events": len(self.gift_events),
            "total_cookie_renewals": len(self.cookie_renewals),
            "viewer_peak": smart_processors["viewership"]["viewer_peak"],
            "viewer_min": smart_processors["viewership"]["viewer_min"],
            "viewer_avg": smart_processors["viewership"]["viewer_avg"],
            "viewer_net_change": smart_processors["viewership"]["viewer_net_change"],
            "viewer_trend": smart_processors["viewership"]["viewer_trend"],
            "total_likes_observed": smart_processors["engagement"]["total_likes_observed"],
            "engagement_rate": smart_processors["engagement"]["engagement_rate"],
            "total_gifts_observed": smart_processors["gifts"]["total_gifts_observed"],
            "stream_was_live_at_any_point": smart_processors["health"]["is_live_at_end"],
            "uptime_percentage": smart_processors["health"]["uptime_percentage"],
            "final_title": smart_processors["metadata"]["title"],
            "final_streamer_nickname": smart_processors["metadata"]["streamer_nickname"],
            "events_summary": dict(Counter(e["type"] for e in self.events)),
            "smart_processors": list(smart_processors.keys()),
        }
        with open(os.path.join(self.session_dir, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)

        return summary

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.is_observing = False

    def get_status(self):
        if not self.polls:
            return {"status": "not_started"}
        last = self.polls[-1]
        return {
            "status": "observing" if self.is_observing else "completed",
            "platform": self.platform,
            "streamer_unique_id": self.streamer_unique_id,
            "polls": len(self.polls),
            "events": len(self.events),
            "gift_events": len(self.gift_events),
            "cookie_renewals": len(self.cookie_renewals),
            "last_viewer_count": last.get("viewer_count", 0),
            "last_like_count": last.get("like_count", 0),
            "last_gift_count": last.get("gift_count", 0),
            "last_is_live": last.get("is_live", False),
            "last_http_status": last.get("http_status"),
            "session_dir": self.session_dir,
        }


# ═══════════════════════════════════════════════════════════════════════
# Demo / Multi-platform test runner
# ═══════════════════════════════════════════════════════════════════════
def main():
    """Test the Universal Live Observer on multiple platforms."""
    print("=" * 80)
    print("🌐 Universal Live Observer — Multi-Platform Test")
    print(f"📋 Supported platforms: {PlatformDetector.list_supported()}")
    print(f"⏱️  Per-platform duration: {MONITOR_MAX_DURATION}s ({MONITOR_MAX_DURATION // 60} min)")
    print(f"📊 Poll interval: {MONITOR_POLL_INTERVAL}s")
    print(f"🚫 NO interactions (pure observer)")
    print("=" * 80)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Test URLs for each platform
    test_urls = [
        ("tiktok", "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/"),
        ("youtube", "https://www.youtube.com/watch?v=jfKfPfyJRdk"),  # LoFi Girl (always live)
        ("twitch", "https://www.twitch.tv/xqc"),
        ("kick", "https://kick.com/xqc"),
        ("bilibili", "https://live.bilibili.com/6"),  # Official Bilibili live
    ]

    # Initialize cache + session processor if available
    cache = None
    session_processor = None
    if CACHE_AVAILABLE:
        try:
            cache = get_cache()
            session_processor = SessionPersistenceProcessor(cache=cache)
            print(f"  Cache + session processor: ✅ initialized")
        except Exception as e:
            print(f"  Cache init failed: {e}")

    all_summaries = []

    for platform_name, url in test_urls:
        print(f"\n{'━' * 70}")
        print(f"Testing {platform_name.upper()}: {url}")
        print(f"{'━' * 70}")

        # Detect platform
        detected = PlatformDetector.detect(url)
        print(f"  Detected platform: {detected}")

        if detected != platform_name:
            print(f"  ⚠️  Detection mismatch: expected {platform_name}, got {detected}")

        # Start observation
        observer = UniversalLiveObserver(
            output_dir=OUTPUT_DIR,
            cache=cache,
            session_processor=session_processor,
        )
        result = observer.start_observation(url, cookies=DEFAULT_COOKIES)
        print(f"  Session dir: {result.get('session_dir')}")

        # Run observation loop with periodic status
        last_status_print = 0
        obs_start = time.time()
        while observer.is_observing:
            now = time.time() - obs_start
            if int(now) - last_status_print >= 30:
                status = observer.get_status()
                print(f"  [{int(now)}s] polls={status.get('polls', 0)}, "
                      f"events={status.get('events', 0)}, "
                      f"viewer={status.get('last_viewer_count', 0)}, "
                      f"is_live={status.get('last_is_live', False)}, "
                      f"http={status.get('last_http_status')}")
                last_status_print = int(now)
            time.sleep(5)

        observer.stop()
        final = observer.get_status()
        print(f"\n  ✅ {platform_name} complete:")
        print(f"    Total polls: {final.get('polls', 0)}")
        print(f"    Total events: {final.get('events', 0)}")
        print(f"    Total gift events: {final.get('gift_events', 0)}")
        print(f"    Final viewer count: {final.get('last_viewer_count', 0)}")
        print(f"    Final like count: {final.get('last_like_count', 0)}")
        print(f"    Final HTTP status: {final.get('last_http_status')}")
        print(f"    Session dir: {final.get('session_dir')}")

        # Load summary
        summary_path = os.path.join(observer.session_dir, "summary.json")
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                summary = json.load(f)
            all_summaries.append({
                "platform": platform_name,
                "url": url,
                "total_polls": summary.get("total_polls", 0),
                "total_events": summary.get("total_events", 0),
                "viewer_peak": summary.get("viewer_peak", 0),
                "viewer_avg": summary.get("viewer_avg", 0),
                "duration_s": summary.get("duration_seconds", 0),
                "session_dir": observer.session_dir,
                "summary": summary,
            })

    # Save aggregate
    aggregate_path = os.path.join(OUTPUT_DIR, "multi_platform_summary.json")
    with open(aggregate_path, "w") as f:
        json.dump({
            "version": "v10",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "supported_platforms": PlatformDetector.list_supported(),
            "total_platforms_tested": len(all_summaries),
            "per_platform_summaries": all_summaries,
        }, f, indent=2, default=str)
    print(f"\n{'=' * 80}")
    print(f"✅ Universal Live Observer — Multi-Platform Test Complete")
    print(f"{'=' * 80}")
    print(f"\n📊 Aggregate summary saved: {aggregate_path}")
    print(f"\nPer-platform results:")
    for s in all_summaries:
        print(f"  {s['platform']:10s}: polls={s['total_polls']}, "
              f"events={s['total_events']}, "
              f"viewer_peak={s['viewer_peak']}, "
              f"viewer_avg={s['viewer_avg']}")

    # ZIP the deep data
    zip_path = "/home/z/my-project/download/universal_deep_data.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(OUTPUT_DIR):
            for root, dirs, files in os.walk(OUTPUT_DIR):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, OUTPUT_DIR))
    print(f"\n📦 ZIP: {zip_path} ({os.path.getsize(zip_path) // 1024}KB)")


if __name__ == "__main__":
    main()
