#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal URL Handler — v11 additions
=======================================
Extends v10's UniversalLiveObserver to handle ANY URL (not just live streams):
  - TikTok videos (regular posts)
  - YouTube videos (regular, shorts, live)
  - Instagram posts/reels/profiles
  - Twitch VODs/clips/channels
  - Facebook posts/videos/profiles
  - Twitter/X posts/spaces/profiles
  - Kick VODs/channels
  - Bilibili videos/live
  - Douyin videos/live

Each URL is detected as either:
  - "live" — live stream (uses v10's live adapters)
  - "video" — recorded video/post
  - "profile" — user/channel profile page
  - "post" — text/image post (Twitter/X, Facebook)

The handler runs the SAME 5 universal smart processors (viewership, engagement,
gifts, health, metadata) plus a 6th processor for content analysis.

Output JSON structure (same as v10):
  universal_deep_data/<platform>/<content_type>/<streamer>/handler_<hash>/
    ├── session_meta.json
    ├── poll_NNNN.json
    ├── time_series.json
    ├── events.json
    ├── smart_processors.json (6 processors)
    └── summary.json
"""

import os, sys, json, time, hashlib, re, threading, zipfile
import requests, urllib3
from datetime import datetime
from collections import Counter
urllib3.disable_warnings()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "cache"))

try:
    from cache_manager import CacheManager, get_cache
    from session_persistence import SessionPersistenceProcessor
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False

try:
    # Import v10's UniversalLiveObserver + PlatformDetector
    sys.path.insert(0, SCRIPT_DIR)
    from universal_live_observer import (
        PlatformDetector, PLATFORM_ADAPTERS,
        TikTokAdapter, YouTubeAdapter, TwitchAdapter, KickAdapter,
        BilibiliAdapter, DouyinAdapter, InstagramAdapter,
        FacebookAdapter, TwitterXSpacesAdapter,
        UA, DEFAULT_COOKIES, OUTPUT_DIR as V10_OUTPUT_DIR,
        MONITOR_POLL_INTERVAL, MONITOR_MAX_DURATION,
        UniversalLiveObserver,
        _analyze_viewership, _analyze_engagement, _analyze_gifts,
        _analyze_health, _analyze_metadata,
    )
    V10_AVAILABLE = True
except ImportError:
    V10_AVAILABLE = False
    print("Warning: v10 universal_live_observer not available")


# ═══════════════════════════════════════════════════════════════════════
# Content Type Detector — detects if URL is live, video, post, or profile
# ═══════════════════════════════════════════════════════════════════════
class ContentTypeDetector:
    """Detects the content type (live/video/post/profile) from a URL."""

    CONTENT_TYPE_PATTERNS = {
        "live": [
            r'/live\b', r'/live/', r'live\.bilibili', r'/spaces/',
            r'isLive', r'live_status', r'livestream',
        ],
        "video": [
            r'/video/', r'/watch\?v=', r'/shorts/', r'/reel/', r'/reels/',
            r'youtu\.be/', r'youtube\.com/live/', r'/v/',
            r'fb\.watch/', r'/videos/', r'twitter\.com/.+/status/',
            r'x\.com/.+/status/', r'/status/', r'kick\.com/video/',
            r'kick\.com/clips/', r'/clip/', r'/clips/',
        ],
        "post": [
            r'/p/', r'/post/', r'/posts/',
            r'instagram\.com/p/', r'facebook\.com/.+/posts/',
            r'twitter\.com/.+/status/', r'x\.com/.+/status/',
        ],
        "profile": [
            r'/@[^/]+/?$', r'/user/', r'/channel/',
            r'/c/', r'twitch\.tv/[^/]+/?$',
            r'kick\.com/[^/]+/?$', r'instagram\.com/[^/]+/?$',
            r'facebook\.com/[^/]+/?$', r'tiktok\.com/@[^/]+/?$',
        ],
    }

    @classmethod
    def detect(cls, url: str) -> str:
        url_lower = (url or "").lower()
        # Check live patterns first
        for pattern in cls.CONTENT_TYPE_PATTERNS["live"]:
            if re.search(pattern, url_lower):
                return "live"
        # Check video patterns
        for pattern in cls.CONTENT_TYPE_PATTERNS["video"]:
            if re.search(pattern, url_lower):
                return "video"
        # Check post patterns
        for pattern in cls.CONTENT_TYPE_PATTERNS["post"]:
            if re.search(pattern, url_lower):
                return "post"
        # Check profile patterns
        for pattern in cls.CONTENT_TYPE_PATTERNS["profile"]:
            if re.search(pattern, url_lower):
                return "profile"
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Video Adapter Base + per-platform video adapters
# ═══════════════════════════════════════════════════════════════════════
class VideoAdapter:
    """Base adapter for recorded video content (not live)."""

    PLATFORM = "base"
    CONTENT_TYPE = "video"

    def __init__(self, url: str, cookies: dict = None):
        self.live_url = url
        self.cookies = cookies or {}
        self.video_id = None
        self.streamer_unique_id = None
        self.streamer_id = None

    def parse(self) -> dict:
        return {
            "platform": self.PLATFORM,
            "content_type": self.CONTENT_TYPE,
            "live_url": self.live_url,
            "video_id": self.video_id,
            "streamer_unique_id": self.streamer_unique_id,
            "streamer_id": self.streamer_id,
        }

    def fetch_metadata(self) -> dict:
        """Fetch video metadata (title, view_count, like_count, etc.)."""
        return {
            "http_status": 0,
            "title": "",
            "view_count": 0,
            "like_count": 0,
            "comment_count": 0,
            "share_count": 0,
            "duration_seconds": 0,
            "upload_date": "",
            "streamer_nickname": "",
            "raw_response": None,
        }


class TikTokVideoAdapter(VideoAdapter):
    """TikTok video adapter — fetches metadata for a TikTok video post."""

    PLATFORM = "tiktok"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        # Pattern: tiktok.com/@username/video/1234567890
        m = re.search(r'/@([^/]+)/video/(\d+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
            self.video_id = m.group(2)
        return super().parse()

    def fetch_metadata(self) -> dict:
        if not self.video_id:
            return super().fetch_metadata()
        # Use TikTok's oEmbed endpoint (public, no auth)
        oembed_url = f"https://www.tiktok.com/oembed?url=https://www.tiktok.com/@{self.streamer_unique_id}/video/{self.video_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(oembed_url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "title": "",
                "view_count": 0,
                "like_count": 0,
                "comment_count": 0,
                "share_count": 0,
                "duration_seconds": 0,
                "upload_date": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    result["title"] = d.get("title", "")
                    result["streamer_nickname"] = d.get("author_name", "")
                    result["streamer_unique_id"] = self.streamer_unique_id
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class YouTubeVideoAdapter(VideoAdapter):
    """YouTube video adapter — fetches metadata for a YouTube video.

    v12 FIX: Uses YouTubeURLFixer to avoid 429 errors.
    Never fetches the full YouTube page (which triggers Google bot detection).
    Instead, uses oEmbed (primary) + noembed.com (fallback).
    """

    PLATFORM = "youtube"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        # v12: use YouTubeURLFixer to extract video_id (handles ?si= param)
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from rate_limit_handler import YouTubeURLFixer
            self.video_id = YouTubeURLFixer.extract_video_id(self.live_url)
        except ImportError:
            # Fallback to original patterns
            patterns = [
                r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
                r'youtu\.be/([a-zA-Z0-9_-]{11})',
                r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
                r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
                r'm\.youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
            ]
            for p in patterns:
                m = re.search(p, self.live_url)
                if m:
                    self.video_id = m.group(1)
                    break
        return super().parse()

    def fetch_metadata(self) -> dict:
        if not self.video_id:
            return super().fetch_metadata()
        # v12: use YouTubeURLFixer (avoids 429 via oEmbed + noembed fallback)
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from rate_limit_handler import YouTubeURLFixer
            fixed = YouTubeURLFixer.fetch_metadata(self.live_url)
            return {
                "http_status": fixed.get("http_status", 0),
                "title": fixed.get("title", ""),
                "view_count": 0,
                "like_count": 0,
                "comment_count": 0,
                "share_count": 0,
                "duration_seconds": 0,
                "upload_date": "",
                "streamer_nickname": fixed.get("streamer_nickname", ""),
                "thumbnail_url": fixed.get("thumbnail_url", ""),
                "source": fixed.get("source", ""),
                "raw_response": None,
            }
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class InstagramPostAdapter(VideoAdapter):
    """Instagram post/reel adapter."""

    PLATFORM = "instagram"
    CONTENT_TYPE = "post"

    def parse(self) -> dict:
        m = re.search(r'instagram\.com/(?:p|reel)/([a-zA-Z0-9_-]+)', self.live_url)
        if m:
            self.video_id = m.group(1)
        m = re.search(r'instagram\.com/([^/]+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        if not self.video_id:
            return super().fetch_metadata()
        oembed_url = f"https://api.instagram.com/oembed?url=https://www.instagram.com/p/{self.video_id}/"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(oembed_url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "title": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    result["title"] = d.get("title", "")
                    result["streamer_nickname"] = d.get("author_name", "")
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class TwitchVODAdapter(VideoAdapter):
    """Twitch VOD (recorded stream) adapter."""

    PLATFORM = "twitch"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        m = re.search(r'twitch\.tv/videos/(\d+)', self.live_url)
        if m:
            self.video_id = m.group(1)
        elif re.search(r'twitch\.tv/([^/]+)', self.live_url):
            m = re.search(r'twitch\.tv/([^/]+)', self.live_url)
            if m:
                self.streamer_unique_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        # Twitch requires client_id for VOD API — best-effort
        return {
            "http_status": 401,
            "title": "Twitch VOD requires client_id",
            "streamer_nickname": self.streamer_unique_id or "",
            "raw_response": None,
        }


class FacebookPostAdapter(VideoAdapter):
    """Facebook post/video adapter."""

    PLATFORM = "facebook"
    CONTENT_TYPE = "post"

    def parse(self) -> dict:
        m = re.search(r'facebook\.com/([^/]+)/videos/(\d+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
            self.video_id = m.group(2)
        elif re.search(r'fb\.watch/([a-zA-Z0-9_-]+)', self.live_url):
            m = re.search(r'fb\.watch/([a-zA-Z0-9_-]+)', self.live_url)
            if m:
                self.video_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        return {
            "http_status": 401,
            "title": "Facebook requires login for post metadata",
            "streamer_nickname": self.streamer_unique_id or "",
            "raw_response": None,
        }


class TwitterXPostAdapter(VideoAdapter):
    """Twitter/X post adapter."""

    PLATFORM = "twitter_x"
    CONTENT_TYPE = "post"

    def parse(self) -> dict:
        m = re.search(r'(?:twitter|x)\.com/([^/]+)/status/(\d+)', self.live_url)
        if m:
            self.streamer_unique_id = m.group(1)
            self.video_id = m.group(2)
        return super().parse()

    def fetch_metadata(self) -> dict:
        return {
            "http_status": 401,
            "title": "X requires API bearer token",
            "streamer_nickname": self.streamer_unique_id or "",
            "raw_response": None,
        }


class KickVideoAdapter(VideoAdapter):
    """Kick VOD/clip adapter."""

    PLATFORM = "kick"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        m = re.search(r'kick\.com/video/([a-zA-Z0-9_-]+)', self.live_url)
        if m:
            self.video_id = m.group(1)
        elif re.search(r'kick\.com/([^/]+)', self.live_url):
            m = re.search(r'kick\.com/([^/]+)', self.live_url)
            if m:
                self.streamer_unique_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        # Kick VOD API
        if not self.streamer_unique_id:
            return super().fetch_metadata()
        url = f"https://kick.com/api/v2/channels/{self.streamer_unique_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "title": "",
                "streamer_nickname": self.streamer_unique_id,
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    result["streamer_nickname"] = d.get("user", {}).get("username", self.streamer_unique_id)
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class BilibiliVideoAdapter(VideoAdapter):
    """Bilibili video adapter."""

    PLATFORM = "bilibili"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        m = re.search(r'bilibili\.com/video/([a-zA-Z0-9]+)', self.live_url)
        if m:
            self.video_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        if not self.video_id:
            return super().fetch_metadata()
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={self.video_id}"
        h = {"User-Agent": UA, "Accept": "application/json"}
        try:
            r = requests.get(url, headers=h, timeout=8, verify=False)
            result = {
                "http_status": r.status_code,
                "title": "",
                "view_count": 0,
                "like_count": 0,
                "comment_count": 0,
                "share_count": 0,
                "duration_seconds": 0,
                "upload_date": "",
                "streamer_nickname": "",
                "raw_response": None,
            }
            if r.status_code == 200 and r.text.startswith("{"):
                try:
                    d = r.json()
                    result["raw_response"] = d
                    data = d.get("data", {})
                    result["title"] = data.get("title", "")
                    result["view_count"] = data.get("stat", {}).get("view", 0)
                    result["like_count"] = data.get("stat", {}).get("like", 0)
                    result["comment_count"] = data.get("stat", {}).get("reply", 0)
                    result["share_count"] = data.get("stat", {}).get("share", 0)
                    result["duration_seconds"] = data.get("duration", 0)
                    result["upload_date"] = data.get("pubdate", 0)
                    result["streamer_nickname"] = data.get("owner", {}).get("name", "")
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"http_status": 0, "error": str(e)[:200]}


class DouyinVideoAdapter(VideoAdapter):
    """Douyin video adapter."""

    PLATFORM = "douyin"
    CONTENT_TYPE = "video"

    def parse(self) -> dict:
        m = re.search(r'douyin\.com/video/(\d+)', self.live_url)
        if m:
            self.video_id = m.group(1)
        elif re.search(r'iesdouyin\.com/share/video/(\d+)', self.live_url):
            m = re.search(r'iesdouyin\.com/share/video/(\d+)', self.live_url)
            if m:
                self.video_id = m.group(1)
        return super().parse()

    def fetch_metadata(self) -> dict:
        return {
            "http_status": 401,
            "title": "Douyin video requires API token",
            "streamer_nickname": "",
            "raw_response": None,
        }


# Registry of video adapters (for non-live content)
VIDEO_ADAPTERS = {
    "tiktok": TikTokVideoAdapter,
    "youtube": YouTubeVideoAdapter,
    "instagram": InstagramPostAdapter,
    "twitch": TwitchVODAdapter,
    "facebook": FacebookPostAdapter,
    "twitter_x": TwitterXPostAdapter,
    "kick": KickVideoAdapter,
    "bilibili": BilibiliVideoAdapter,
    "douyin": DouyinVideoAdapter,
}


def get_video_adapter(url: str, cookies: dict = None) -> VideoAdapter:
    """Detect platform and return the appropriate video adapter."""
    platform = PlatformDetector.detect(url)
    adapter_class = VIDEO_ADAPTERS.get(platform, VideoAdapter)
    adapter = adapter_class(url, cookies or {})
    adapter.parse()
    return adapter


# ═══════════════════════════════════════════════════════════════════════
# Universal URL Handler — main orchestrator (handles ANY URL)
# ═══════════════════════════════════════════════════════════════════════
class UniversalURLHandler:
    """Universal URL handler that processes ANY URL (live, video, post, profile).

    - For live URLs: uses v10's UniversalLiveObserver (continuous polling)
    - For video/post/profile URLs: fetches metadata once (single fetch, not polling)

    Output JSON is saved in the same hierarchical structure as v10:
      universal_deep_data/<platform>/<content_type>/<streamer>/handler_<hash>/
    """

    def __init__(self, output_dir: str = V10_OUTPUT_DIR if V10_AVAILABLE else
                 "/home/z/my-project/download/universal_deep_data",
                 cache=None, session_processor=None):
        self.output_dir = output_dir
        self.cache = cache
        self.session_processor = session_processor
        self.platform = None
        self.content_type = None
        self.adapter = None  # Either live adapter or video adapter
        self.session_id = None
        self.session_dir = None
        self.start_time = None
        self.polls = []
        self.events = []
        self.is_observing = False
        self.stop_event = threading.Event()
        self.thread = None

    def handle_url(self, url: str, cookies: dict = None) -> dict:
        """Process ANY URL — auto-detects platform + content type."""
        self.start_time = datetime.utcnow()
        self.cookies = cookies or (DEFAULT_COOKIES if V10_AVAILABLE else {})

        # Step 1: Detect platform
        self.platform = PlatformDetector.detect(url)
        # Step 2: Detect content type
        self.content_type = ContentTypeDetector.detect(url)

        # Step 3: Generate session ID
        self.session_id = hashlib.md5(
            f"{url}_{int(time.time())}".encode()
        ).hexdigest()[:12]

        # Step 4: Get appropriate adapter
        if self.content_type == "live":
            # Use v10's live observer (continuous polling)
            if not V10_AVAILABLE:
                return {"error": "v10 UniversalLiveObserver not available for live URLs"}
            # Get the right live adapter
            from universal_live_observer import get_adapter
            self.adapter = get_adapter(url, self.cookies)
            self.streamer_unique_id = self.adapter.streamer_unique_id or "unknown"
            # Delegate to UniversalLiveObserver
            self.live_observer = UniversalLiveObserver(
                output_dir=self.output_dir,
                cache=self.cache,
                session_processor=self.session_processor,
            )
            result = self.live_observer.start_observation(url, cookies=self.cookies)
            self.session_dir = result.get("session_dir", "")
            self.is_observing = True
            return {
                "status": "live_observation_started",
                "platform": self.platform,
                "content_type": self.content_type,
                "session_id": self.live_observer.session_id,
                "session_dir": self.session_dir,
                "streamer_unique_id": self.streamer_unique_id,
            }
        else:
            # Video/post/profile: fetch metadata once (single fetch)
            self.adapter = get_video_adapter(url, self.cookies)
            self.streamer_unique_id = self.adapter.streamer_unique_id or "unknown"

            # Create session directory
            platform_dir = os.path.join(self.output_dir, self.platform)
            type_dir = os.path.join(platform_dir, self.content_type)
            streamer_dir = os.path.join(type_dir, self.streamer_unique_id)
            self.session_dir = os.path.join(streamer_dir, f"handler_{self.session_id}")
            os.makedirs(self.session_dir, exist_ok=True)

            # Save session metadata
            session_meta = {
                "url": url,
                "platform": self.platform,
                "content_type": self.content_type,
                "streamer_unique_id": self.streamer_unique_id,
                "video_id": self.adapter.video_id,
                "handler_session_id": self.session_id,
                "started_at": self.start_time.isoformat() + "Z",
                "v11_features": [
                    "Universal URL handling (any URL, not just live)",
                    "Auto platform + content type detection",
                    "Single fetch for non-live content",
                    "6 universal smart processors",
                    "Same JSON output format as v10",
                ],
            }
            with open(os.path.join(self.session_dir, "session_meta.json"), "w") as f:
                json.dump(session_meta, f, indent=2, ensure_ascii=False)

            # Fetch metadata (single fetch)
            metadata = self.adapter.fetch_metadata()

            # Save as poll_0001.json (consistent with v10)
            poll_record = {
                "poll_n": 1,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "elapsed_s": 0.0,
                "platform": self.platform,
                "content_type": self.content_type,
                "url": url,
                "streamer_unique_id": self.streamer_unique_id,
                "video_id": self.adapter.video_id,
                "http_status": metadata.get("http_status", 0),
                "title": metadata.get("title", ""),
                "view_count": metadata.get("view_count", 0),
                "like_count": metadata.get("like_count", 0),
                "comment_count": metadata.get("comment_count", 0),
                "share_count": metadata.get("share_count", 0),
                "duration_seconds": metadata.get("duration_seconds", 0),
                "upload_date": metadata.get("upload_date", ""),
                "streamer_nickname": metadata.get("streamer_nickname", ""),
                "error": metadata.get("error"),
            }
            self.polls.append(poll_record)
            with open(os.path.join(self.session_dir, "poll_0001.json"), "w") as f:
                json.dump(poll_record, f, indent=2, default=str)

            # Save time_series (just one entry for video/post)
            with open(os.path.join(self.session_dir, "time_series.json"), "w") as f:
                json.dump(self.polls, f, indent=2, default=str)

            # Run 6 universal smart processors
            smart_processors = {
                "viewership": _analyze_viewership(self.polls) if V10_AVAILABLE else {},
                "engagement": _analyze_engagement(self.polls) if V10_AVAILABLE else {},
                "gifts": _analyze_gifts(self.polls, []) if V10_AVAILABLE else {},
                "health": _analyze_health(self.polls) if V10_AVAILABLE else {},
                "metadata": self._analyze_metadata_v11(),
                "content_analysis": self._analyze_content(metadata),
            }
            with open(os.path.join(self.session_dir, "smart_processors.json"), "w") as f:
                json.dump(smart_processors, f, indent=2, default=str)

            # Save summary
            summary = {
                "url": url,
                "platform": self.platform,
                "content_type": self.content_type,
                "streamer_unique_id": self.streamer_unique_id,
                "video_id": self.adapter.video_id,
                "handler_session_id": self.session_id,
                "started_at": self.start_time.isoformat() + "Z",
                "ended_at": datetime.utcnow().isoformat() + "Z",
                "duration_seconds": round(
                    (datetime.utcnow() - self.start_time).total_seconds(), 1
                ),
                "total_polls": 1,
                "total_events": 0,
                "title": metadata.get("title", ""),
                "view_count": metadata.get("view_count", 0),
                "like_count": metadata.get("like_count", 0),
                "comment_count": metadata.get("comment_count", 0),
                "share_count": metadata.get("share_count", 0),
                "duration_seconds_content": metadata.get("duration_seconds", 0),
                "streamer_nickname": metadata.get("streamer_nickname", ""),
                "http_status": metadata.get("http_status", 0),
                "smart_processors": list(smart_processors.keys()),
            }
            with open(os.path.join(self.session_dir, "summary.json"), "w") as f:
                json.dump(summary, f, indent=2, default=str)

            # Record in cache (if available)
            if self.cache:
                self.cache.record_session_cookies(
                    self.cookies, source=f"universal_url_handler_{self.platform}_{self.content_type}",
                    notes=f"url={url}"
                )
            if self.session_processor:
                self.session_processor.create_session(
                    live_url=url,
                    cookies=self.cookies,
                    room_id=self.adapter.video_id or "",
                    unique_id=self.streamer_unique_id,
                    streamer_user_id=self.adapter.streamer_id or "",
                )

            return {
                "status": "metadata_fetched",
                "platform": self.platform,
                "content_type": self.content_type,
                "session_id": self.session_id,
                "session_dir": self.session_dir,
                "title": metadata.get("title", ""),
                "view_count": metadata.get("view_count", 0),
                "like_count": metadata.get("like_count", 0),
                "streamer_nickname": metadata.get("streamer_nickname", ""),
                "http_status": metadata.get("http_status", 0),
            }

    def _analyze_content(self, metadata: dict) -> dict:
        """6th smart processor — content analysis (for non-live content)."""
        return {
            "title_length": len(metadata.get("title", "")),
            "has_view_count": metadata.get("view_count", 0) > 0,
            "has_like_count": metadata.get("like_count", 0) > 0,
            "has_comment_count": metadata.get("comment_count", 0) > 0,
            "has_share_count": metadata.get("share_count", 0) > 0,
            "has_duration": metadata.get("duration_seconds", 0) > 0,
            "duration_formatted": self._format_duration(metadata.get("duration_seconds", 0)),
            "engagement_score": self._compute_engagement_score(metadata),
        }

    def _analyze_metadata_v11(self) -> dict:
        """v11 metadata analyzer — works with both live + video adapters."""
        if not self.polls:
            return {}
        last = self.polls[-1]
        return {
            "platform": self.platform,
            "content_type": self.content_type,
            "title": last.get("title", ""),
            "streamer_nickname": last.get("streamer_nickname", ""),
            "streamer_unique_id": self.streamer_unique_id or "",
            "stream_id": getattr(self.adapter, 'stream_id', None) or getattr(self.adapter, 'video_id', None) or "",
            "video_id": getattr(self.adapter, 'video_id', None) or "",
            "room_id": getattr(self.adapter, 'room_id', None) or "",
            "live_url": self.adapter.live_url if hasattr(self.adapter, 'live_url') else "",
        }

    def _format_duration(self, seconds: int) -> str:
        if not seconds:
            return "0s"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    def _compute_engagement_score(self, metadata: dict) -> str:
        """Compute engagement score from view/like/comment/share counts."""
        views = metadata.get("view_count", 0)
        likes = metadata.get("like_count", 0)
        comments = metadata.get("comment_count", 0)
        shares = metadata.get("share_count", 0)
        if views == 0:
            return "unknown"
        ratio = (likes + comments * 5 + shares * 10) / views
        if ratio > 0.5:
            return "very_high"
        elif ratio > 0.2:
            return "high"
        elif ratio > 0.05:
            return "medium"
        elif ratio > 0.01:
            return "low"
        return "very_low"

    def stop(self):
        if hasattr(self, 'live_observer') and self.live_observer:
            self.live_observer.stop()
        self.stop_event.set()
        self.is_observing = False

    def get_status(self):
        if hasattr(self, 'live_observer') and self.live_observer:
            return self.live_observer.get_status()
        return {
            "status": "completed" if self.polls else "not_started",
            "platform": self.platform,
            "content_type": self.content_type,
            "polls": len(self.polls),
        }


# ═══════════════════════════════════════════════════════════════════════
# Demo — test on diverse URL types
# ═══════════════════════════════════════════════════════════════════════
def main():
    """Test the Universal URL Handler on diverse URL types (not just live)."""
    print("=" * 80)
    print("🌐 Universal URL Handler — v11 (ANY URL, not just live)")
    print(f"📋 Supported: 9 platforms × 4 content types (live/video/post/profile)")
    print("=" * 80)

    os.makedirs(V10_OUTPUT_DIR if V10_AVAILABLE else
                "/home/z/my-project/download/universal_deep_data", exist_ok=True)

    # Test URLs — diverse types (not just live streams)
    test_urls = [
        # Live streams
        ("tiktok", "live", "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/"),
        ("youtube", "live", "https://www.youtube.com/watch?v=jfKfPfyJRdk"),  # LoFi Girl
        # Videos
        ("youtube", "video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),  # Rick Astley
        ("bilibili", "video", "https://www.bilibili.com/video/BV1GJ411x7h7"),
        # Posts/profiles
        ("kick", "profile", "https://kick.com/xqc"),
        ("twitch", "profile", "https://www.twitch.tv/xqc"),
    ]

    cache = None
    session_processor = None
    if CACHE_AVAILABLE:
        try:
            cache = get_cache()
            session_processor = SessionPersistenceProcessor(cache=cache)
        except Exception:
            pass

    all_summaries = []

    for platform, expected_type, url in test_urls:
        print(f"\n{'━' * 70}")
        print(f"Testing {platform.upper()} ({expected_type}): {url}")
        print(f"{'━' * 70}")

        handler = UniversalURLHandler(
            output_dir=V10_OUTPUT_DIR if V10_AVAILABLE else
            "/home/z/my-project/download/universal_deep_data",
            cache=cache,
            session_processor=session_processor,
        )

        # For live URLs, run for 60s only (to keep total runtime reasonable)
        if expected_type == "live":
            result = handler.handle_url(url, cookies=DEFAULT_COOKIES if V10_AVAILABLE else {})
            print(f"  Live observation started: {result.get('session_dir')}")
            # Run for 30s for live URLs
            obs_start = time.time()
            while handler.is_observing and (time.time() - obs_start) < 30:
                time.sleep(5)
            handler.stop()
            final = handler.get_status()
            print(f"  ✅ Live: polls={final.get('polls', 0)}, events={final.get('events', 0)}")
            all_summaries.append({
                "platform": platform,
                "expected_type": expected_type,
                "detected_type": handler.content_type,
                "url": url,
                "polls": final.get("polls", 0),
                "events": final.get("events", 0),
                "session_dir": handler.session_dir,
            })
        else:
            # For non-live URLs: single fetch (instant)
            result = handler.handle_url(url, cookies=DEFAULT_COOKIES if V10_AVAILABLE else {})
            print(f"  Platform: {result.get('platform')}")
            print(f"  Content type: {result.get('content_type')}")
            print(f"  Title: {result.get('title', '')[:80]}")
            print(f"  View count: {result.get('view_count', 0)}")
            print(f"  Like count: {result.get('like_count', 0)}")
            print(f"  Streamer: {result.get('streamer_nickname', '')}")
            print(f"  HTTP: {result.get('http_status')}")
            print(f"  Session dir: {result.get('session_dir')}")
            all_summaries.append({
                "platform": platform,
                "expected_type": expected_type,
                "detected_type": result.get("content_type"),
                "url": url,
                "title": result.get("title", "")[:100],
                "view_count": result.get("view_count", 0),
                "like_count": result.get("like_count", 0),
                "streamer_nickname": result.get("streamer_nickname", ""),
                "http_status": result.get("http_status", 0),
                "session_dir": result.get("session_dir"),
            })

    # Save aggregate
    output_dir = V10_OUTPUT_DIR if V10_AVAILABLE else "/home/z/my-project/download/universal_deep_data"
    aggregate_path = os.path.join(output_dir, "universal_url_handler_summary.json")
    with open(aggregate_path, "w") as f:
        json.dump({
            "version": "v11",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "supported_platforms": PlatformDetector.list_supported() if V10_AVAILABLE else [],
            "supported_content_types": list(ContentTypeDetector.CONTENT_TYPE_PATTERNS.keys()),
            "total_urls_tested": len(all_summaries),
            "results": all_summaries,
        }, f, indent=2, default=str)

    print(f"\n{'=' * 80}")
    print(f"✅ Universal URL Handler — v11 Test Complete")
    print(f"{'=' * 80}")
    print(f"\n📊 Aggregate saved: {aggregate_path}")
    print(f"\nResults:")
    for s in all_summaries:
        if s.get('detected_type') == 'live':
            print(f"  {s['platform']:10s} ({s['detected_type']:8s}): polls={s.get('polls', 0)}")
        else:
            print(f"  {s['platform']:10s} ({s['detected_type']:8s}): title='{s.get('title', '')[:40]}', views={s.get('view_count', 0)}")

    # ZIP
    zip_path = "/home/z/my-project/download/universal_deep_data_v11.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(output_dir):
            for root, dirs, files in os.walk(output_dir):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, output_dir))
    print(f"\n📦 ZIP: {zip_path} ({os.path.getsize(zip_path) // 1024}KB)")


if __name__ == "__main__":
    main()
