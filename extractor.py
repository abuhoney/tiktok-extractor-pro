#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok Universal Extractor — Core Engine v3.0 (REAL MODE, NO DEMO)
====================================================================

يدعم جميع صيغ روابط TikTok:
  - https://www.tiktok.com/@username/video/1234567890
  - https://vm.tiktok.com/ZSJxxxxxxx/   (short)
  - https://vt.tiktok.com/ZSRxxxxxxx/   (short)
  - https://m.tiktok.com/v/1234567890.html
  - https://www.tiktok.com/@username
  - https://www.tiktok.com/@username/live
  - https://www.tiktok.com/t/PRxxxxxx/
  - tiktok.com/@username/video/ID (بدون https)
  - @username (mention مباشر)

يستخدم 7 استراتيجيات حقيقية متتالية (بدون وضع تجريبي):
  1. yt-dlp                                  (الأساسية — تعمل من أي IP، تستخرج الفيديو/الصوت/الغلاف/الإحصائيات)
  2. __UNIVERSAL_DATA_FOR_REHYDRATION__      (من HTML — تعطي sec_uid, csrf, wid, nonce, إلخ)
  3. SIGI_STATE / _SIGI_STATE                (الكلاسيكية)
  4. Webcast API + X-Bogus (Playwright)      (للبث المباشر — يستخدم webmssdk.js الحقيقي)
  5. oEmbed API                              (الرسمية — تعطي title + author + thumbnail)
  6. Meta Tags (og:*, twitter:*)             (الاحتياطية)
  7. DOM Fallback                             (الأخيرة)

المتغيرات البيئية المدعومة:
  - TIKTOK_PROXY:       بروكسي لتجاوز الحظر الجغرافي (http:// أو socks5://)
  - ENABLE_PLAYWRIGHT:  "true" لتفعيل Playwright + X-Bogus الحقيقي (يستهلك ذاكرة أكبر)
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import logging
import socket
import hashlib
import subprocess
import shutil
import tempfile
import base64
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse, quote, urlencode, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("tiktok_extractor")


# ────────────────────────────────────────────────────────────────────────────
#  جلسة HTTP موحّدة مع إعادة المحاولة + دعم البروكسي
# ────────────────────────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]


def build_session(proxy: str = None) -> requests.Session:
    """يبني جلسة HTTP مع Retry/Backoff لإمكانية الوصول العالية.

    يدعم البروكسي عبر المعامل `proxy` أو متغيرات البيئة:
      - TIKTOK_PROXY   أو  HTTPS_PROXY   أو  ALL_PROXY   أو  SOCKS5_PROXY
      - صيغ مدعومة: http://host:port, https://host:port,
                    socks5://host:port, socks5h://host:port (DNS عبر البروكسي)
    """
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
    s.headers.update({
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })

    # إعداد البروكسي إن وُجد
    proxy_url = proxy or os.environ.get("TIKTOK_PROXY") \
                    or os.environ.get("HTTPS_PROXY") \
                    or os.environ.get("ALL_PROXY") \
                    or os.environ.get("SOCKS5_PROXY")
    if proxy_url:
        if proxy_url.startswith("socks"):
            try:
                import socks  # noqa: F401 — ensures PySocks is installed
            except ImportError:
                logger.warning(
                    "SOCKS proxy requested but PySocks not installed; "
                    "pip install pysocks"
                )
            else:
                s.proxies = {"http": proxy_url, "https": proxy_url}
                s.trust_env = False
                logger.info(f"build_session: using SOCKS proxy {proxy_url}")
        else:
            s.proxies = {"http": proxy_url, "https": proxy_url}
            s.trust_env = False
            logger.info(f"build_session: using HTTP proxy {proxy_url}")
    else:
        logger.debug("build_session: no proxy (direct connection)")

    return s


# ────────────────────────────────────────────────────────────────────────────
#  أنماط الروابط - تدعم كل صيغ TikTok المعروفة
# ────────────────────────────────────────────────────────────────────────────
SHORT_DOMAINS = {
    "vm.tiktok.com", "vt.tiktok.com", "tiktok.com",
    "m.tiktok.com", "vt.tiktokv.com", "vm.tiktokv.com",
}

USERNAME_RE = re.compile(r"@?([A-Za-z0-9_.]+)")
VIDEO_ID_RE = re.compile(r"/video/(\d+)")
PHOTO_ID_RE = re.compile(r"/photo/(\d+)")
NOTE_ID_RE = re.compile(r"/note/(\d+)")
LIVE_PATH_RE = re.compile(r"/live(?:/|$|\?)", re.I)


@dataclass
class ParsedLink:
    """نتيجة تحليل رابط TikTok إلى مكوّناته الأساسية."""
    raw: str
    normalized: str
    kind: str  # 'video' | 'photo' | 'note' | 'live' | 'profile' | 'unknown'
    username: Optional[str] = None
    content_id: Optional[str] = None
    is_short: bool = False
    final_url: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def normalize_input(raw: str) -> str:
    """يطهّر المدخلات: يزيل المسافات، يحوّل @username إلى رابط كامل."""
    if not raw:
        return ""
    s = raw.strip().strip('"').strip("'").strip()
    s = re.sub(r"^(?:url|link|رابط|الرابط)\s*[:：]\s*", "", s, flags=re.I)
    if re.fullmatch(r"@?[A-Za-z0-9_.]{2,32}", s) and "." not in s and "/" not in s:
        s = f"https://www.tiktok.com/@{s.lstrip('@')}"
    if not s.startswith(("http://", "https://")):
        if s.startswith("tiktok.com") or s.startswith("@"):
            s = "https://www." + s.lstrip("@") if not s.startswith("@") else "https://www.tiktok.com/" + s
            if "www." not in s:
                s = "https://www." + s
        else:
            s = "https://" + s
    return s


def parse_link(raw: str) -> ParsedLink:
    """يحلّل أي رابط TikTok إلى نوعه ومكوّناته دون اتصال شبكي."""
    normalized = normalize_input(raw)
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    result = ParsedLink(raw=raw, normalized=normalized, kind="unknown")

    if host in SHORT_DOMAINS and host not in ("www.tiktok.com", "tiktok.com"):
        result.is_short = True
        result.kind = "short"
        return result

    m_user = re.search(r"/@([A-Za-z0-9_.]+)/?", path)
    if m_user:
        result.username = m_user.group(1)

    m_vid = VIDEO_ID_RE.search(path)
    if m_vid:
        result.content_id = m_vid.group(1)
        result.kind = "video"
        return result

    m_photo = PHOTO_ID_RE.search(path)
    if m_photo:
        result.content_id = m_photo.group(1)
        result.kind = "photo"
        return result

    m_note = NOTE_ID_RE.search(path)
    if m_note:
        result.content_id = m_note.group(1)
        result.kind = "note"
        return result

    if LIVE_PATH_RE.search(path):
        result.kind = "live"
        return result

    if result.username:
        result.kind = "profile"
        return result

    return result


def resolve_short_url(session: requests.Session, url: str,
                      timeout: int = 15) -> Optional[str]:
    """يحلّ رابطاً مختصراً إلى الرابط الكامل النهائي.

    مهم: نريد أول URL يحتوي على `tiktok.com/@username/...` وليس
    الرابط النهائي بعد الحظر الجغرافي (مثل /hk/about). لذلك نوقف
    متابعة التحويلات بعد أول خطوة تصل إلى www.tiktok.com.
    """
    try:
        # First request: don't follow redirects, just inspect the Location header
        r = session.get(url, timeout=timeout, allow_redirects=False, stream=True)
        r.close()

        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("Location", "")
            if not location:
                return None

            # If location is /hk/about (geo-block), abort — we lost the username
            if re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", location, re.I):
                logger.warning(f"resolve_short_url: geo-blocked (first hop → {location})")
                return None

            # If location is on tiktok.com with @username, we're done
            if "tiktok.com" in location and "/@" in location:
                logger.info(f"resolve_short_url: {url} → {location}")
                return location

            # Otherwise, follow one more hop
            try:
                r2 = session.get(location, timeout=timeout, allow_redirects=False, stream=True)
                r2.close()
                if r2.status_code in (301, 302, 303, 307, 308):
                    final_loc = r2.headers.get("Location", "")
                    if final_loc and "tiktok.com" in final_loc and "/@" in final_loc:
                        logger.info(f"resolve_short_url: {url} → {location} → {final_loc}")
                        return final_loc
                    # If second hop is /hk/about but first had @username, use first
                    if "tiktok.com" in location and "/@" in location:
                        return location
            except Exception:
                pass

            # Single-hop fallback: location is the final URL
            return location

        # No redirect — return as-is
        return r.url
    except requests.exceptions.SSLError:
        try:
            r = session.get(url, timeout=timeout, allow_redirects=False,
                            stream=True, verify=False)
            r.close()
            if r.status_code in (301, 302, 303, 307, 308):
                return r.headers.get("Location", "")
            return r.url
        except Exception as e:
            logger.warning(f"resolve_short_url SSL fallback failed: {e}")
            return None
    except Exception as e:
        logger.warning(f"resolve_short_url failed: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────────
#  بنية البيانات المستخرجة
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class Author:
    unique_id: Optional[str] = None
    nickname: Optional[str] = None
    user_id: Optional[str] = None
    sec_uid: Optional[str] = None
    avatar: Optional[str] = None
    signature: Optional[str] = None
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    like_count: Optional[int] = None
    video_count: Optional[int] = None
    verified: Optional[bool] = None


@dataclass
class Stats:
    play_count: Optional[int] = None
    digg_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None


@dataclass
class Video:
    play_url: Optional[str] = None
    download_url: Optional[str] = None
    cover: Optional[str] = None
    dynamic_cover: Optional[str] = None
    origin_cover: Optional[str] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    ratio: Optional[str] = None
    format: Optional[str] = None


@dataclass
class Music:
    id: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    play_url: Optional[str] = None
    cover: Optional[str] = None
    duration: Optional[int] = None


@dataclass
class ExtractionResult:
    success: bool
    url: str = ""
    final_url: Optional[str] = None
    kind: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    content_id: Optional[str] = None
    create_time: Optional[int] = None
    author: Author = field(default_factory=Author)
    stats: Stats = field(default_factory=Stats)
    video: Video = field(default_factory=Video)
    music: Music = field(default_factory=Music)
    images: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    live: Optional[Dict[str, Any]] = None
    all_ids: Dict[str, Any] = field(default_factory=dict)
    json_files: Dict[str, Any] = field(default_factory=dict)
    meta_tags: Dict[str, str] = field(default_factory=dict)
    raw_keys: List[str] = field(default_factory=list)
    raw_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    extracted_at: str = ""
    # ─── v3.1: New enrichment fields ─────────────────────────────────
    decoded_tokens: Dict[str, Any] = field(default_factory=dict)        # decoded _d, expire, sign, checksum, timestamp
    security_analysis: Dict[str, Any] = field(default_factory=dict)    # sensitivity classification per token
    cdn_metadata: Dict[str, Any] = field(default_factory=dict)        # CDN datacenter, quality, region from stream URLs
    avatar_metadata: Dict[str, Any] = field(default_factory=dict)      # avatar URL params (x-expires, refresh_token, idc)
    stream_access: Dict[str, Any] = field(default_factory=dict)        # all signed stream URLs + expire datetime + access level
    gift_list: List[Dict[str, Any]] = field(default_factory=list)     # available gifts in room (type, level, rarity, value)
    donor_rankings: List[Dict[str, Any]] = field(default_factory=list) # top donors with user + amount
    http_headers: Dict[str, str] = field(default_factory=dict)         # required HTTP headers for stream access
    # ─── v3.2: Deep analysis fields ───────────────────────────────────
    url_analysis: Dict[str, Any] = field(default_factory=dict)          # deep URL decomposition (original + final)
    json_analysis: Dict[str, Any] = field(default_factory=dict)          # flattened JSON with all IDs/timestamps/URLs classified
    html_analysis: Dict[str, Any] = field(default_factory=dict)         # scripts, meta tags, data-* attrs, inline JS, API endpoints
    deep_token_analysis: Dict[str, Any] = field(default_factory=dict)   # multi-decode attempts for each token
    stream_url_analysis: Dict[str, Any] = field(default_factory=dict)   # per-format full URL decomposition + sign comparison
    # ─── v3.3: Advanced processor fields (from tiktokjson/tikhtml/mhmdz1/Interactiontik) ───
    security_credentials: Dict[str, Any] = field(default_factory=dict)  # csrf_token, wid, nonce, encrypted_webid, request_id, sessionid
    advanced_json_files: Dict[str, Any] = field(default_factory=dict)   # 8 embedded JSON: sigi, universal, pumbaa, slardar, api_domains, etc.
    api_domains: Dict[str, Any] = field(default_factory=dict)           # rootApi, webcastApi, imApi, mTApi, slardar, starling, etc.
    live_room_entered: Dict[str, Any] = field(default_factory=dict)     # from enter_live_room() — actual room entry data
    full_gift_rank: Dict[str, Any] = field(default_factory=dict)        # complete gift rank from /api/live/gift/rank/
    full_gift_list: Dict[str, Any] = field(default_factory=dict)        # complete gift list from /api/live/gift/list/
    user_detail_full: Dict[str, Any] = field(default_factory=dict)       # from /api/v1/user/detail/ — follower_count, following_count, etc.
    live_detail_full: Dict[str, Any] = field(default_factory=dict)      # from /api/live/detail/ — live stream details by user_id
    advanced_ids: Dict[str, Any] = field(default_factory=dict)          # AdvancedIDExtractor: user, live, security, app, location
    saved_html_path: Optional[str] = None                               # path to saved HTML file (if save_html enabled)
    # ─── v3.6: Interaction analysis + webcast surfacing ──────────────────
    interaction_analysis: Dict[str, Any] = field(default_factory=dict)   # what's available vs missing for auto-interaction
    webcast_full_data: Dict[str, Any] = field(default_factory=dict)      # raw_json.webcast_room_info.data surfaced to top-level
    interaction_targets: List[Dict[str, Any]] = field(default_factory=list)  # list of users that can be interacted with (top_fans, owner)
    # ─── v4.2: Deep analytics fields ──────────────────────────────────────
    engagement_quality: Dict[str, Any] = field(default_factory=dict)     # engagement rate, velocity, density, ratios
    gift_economy: Dict[str, Any] = field(default_factory=dict)           # gift economy analysis (coins, value distribution, rarity)
    stream_health: Dict[str, Any] = field(default_factory=dict)          # stream URL quality, codec, bitrate, stability
    influence_score: Dict[str, Any] = field(default_factory=dict)        # weighted influence scoring (0-100)
    audience_profile: Dict[str, Any] = field(default_factory=dict)       # audience demographics hints (lang, region)
    temporal_profile: Dict[str, Any] = field(default_factory=dict)       # snapshot for temporal tracking
    account_risk: Dict[str, Any] = field(default_factory=dict)           # account risk assessment (verification, age, etc.)
    commerce_data: Dict[str, Any] = field(default_factory=dict)           # shop links, product tags
    deep_analytics: Dict[str, Any] = field(default_factory=dict)         # aggregated v4.2 analytics summary

    def __post_init__(self):
        if not self.extracted_at:
            self.extracted_at = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 1: yt-dlp (الأساسية — تعمل من أي IP)
# ────────────────────────────────────────────────────────────────────────────
def _extract_with_ytdlp(url: str, parsed: ParsedLink,
                        session: requests.Session,
                        timeout: int = 60) -> Optional[ExtractionResult]:
    """يستخدم yt-dlp لاستخراج بيانات TikTok. يعمل من أي IP بما في ذلك
    خوادم Render المتأثرة بالحظر الجغرافي، لأن yt-dlp يستخدم بروتوكولات
    TikTok الداخلية (مع X-Bogus مضمن) ولا يعتمد على HTML scraping.

    يدعم: video / photo / live / profile
    """
    if not shutil.which("yt-dlp") and not os.environ.get("YT_DLP_PATH"):
        # try the Python module
        try:
            import yt_dlp
            return _extract_with_ytdlp_module(url, parsed, session, timeout, yt_dlp)
        except ImportError:
            logger.debug("yt-dlp not installed")
            return None

    # Use the CLI version
    ytdlp_bin = os.environ.get("YT_DLP_PATH", "yt-dlp")
    cmd = [
        ytdlp_bin,
        "--skip-download",  # metadata only, don't actually download
        "--dump-json",      # output single JSON object
        "--no-warnings",
        "--no-check-certificate",
        "--user-agent", USER_AGENTS[0],
        "--no-playlist",
        url,
    ]

    # Pass proxy through env if set
    env = os.environ.copy()
    proxy = os.environ.get("TIKTOK_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy

    try:
        logger.info(f"yt-dlp: extracting {url}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env,
        )
        if result.returncode != 0:
            # Extract the user-friendly error message from yt-dlp's stderr
            err_msg = result.stderr.strip()
            logger.warning(f"yt-dlp exited {result.returncode}: {err_msg[:300]}")
            # Return a result with error set (so caller can propagate it)
            return ExtractionResult(
                success=False,
                url=url,
                kind=parsed.kind or "unknown",
                error=f"yt-dlp: {err_msg}",
            )

        # yt-dlp may emit multiple JSON lines (e.g. for photo carousels)
        lines = [l for l in result.stdout.splitlines() if l.strip().startswith("{")]
        if not lines:
            logger.warning("yt-dlp: no JSON output")
            return ExtractionResult(
                success=False,
                url=url,
                kind=parsed.kind or "unknown",
                error="yt-dlp: لا يوجد JSON output (الرابط قد يكون غير صالح)",
            )

        # Use the first JSON object as the main entry
        info = json.loads(lines[0])
        extra_images = []
        if len(lines) > 1:
            for l in lines[1:]:
                try:
                    sub = json.loads(l)
                    if sub.get("_type") == "image" and sub.get("url"):
                        extra_images.append(sub["url"])
                except Exception:
                    pass

        return _from_ytdlp(info, parsed, extra_images)
    except subprocess.TimeoutExpired:
        logger.warning(f"yt-dlp timed out after {timeout}s")
        return None
    except Exception as e:
        logger.exception(f"yt-dlp crashed: {e}")
        return None


def _extract_with_ytdlp_module(url: str, parsed: ParsedLink,
                                session: requests.Session,
                                timeout: int, yt_dlp) -> Optional[ExtractionResult]:
    """يستخدم yt-dlp كـ Python module (أسرع، بدون subprocess)."""
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "extract_flat": False,
        "socket_timeout": timeout,
    }
    proxy = os.environ.get("TIKTOK_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        opts["proxy"] = proxy

    try:
        logger.info(f"yt-dlp (module): extracting {url}")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            extra_images = []
            if info.get("_type") == "playlist" and info.get("entries"):
                for e in info["entries"]:
                    if e and e.get("url") and (e.get("_type") == "image" or e.get("ext") in ("jpg", "png", "webp")):
                        extra_images.append(e["url"])
                info = info["entries"][0] if info["entries"] else info
            return _from_ytdlp(info, parsed, extra_images)
    except Exception as e:
        logger.warning(f"yt-dlp module failed: {e}")
        return None


def _from_ytdlp(info: dict, parsed: ParsedLink,
                extra_images: List[str] = None) -> ExtractionResult:
    """يحوّل استجابة yt-dlp إلى ExtractionResult — يجمع كل الحقول المتاحة."""
    extra_images = extra_images or []

    # Determine the kind based on yt-dlp's extractor + URL
    kind = parsed.kind or "video"
    if info.get("is_live") or info.get("live_status") == "is_live":
        kind = "live"
    elif info.get("_type") == "image" or extra_images:
        kind = "photo"

    res = ExtractionResult(
        success=True,
        url=parsed.final_url or parsed.normalized,
        final_url=parsed.final_url or info.get("webpage_url") or parsed.normalized,
        kind=kind,
        title=info.get("title") or info.get("fulltitle") or "",
        description=info.get("description") or info.get("title") or "",
        content_id=str(info.get("id") or parsed.content_id or ""),
        create_time=int(info.get("timestamp") or 0) if info.get("timestamp") else None,
    )

    # ─── Author (enhanced) ────────────────────────────────────────────
    # yt-dlp uses "creator" for the display name and "uploader" for the @handle
    uploader = info.get("uploader") or info.get("channel") or parsed.username
    creator = info.get("creator") or (info.get("creators") or [None])[0]
    res.author.unique_id = uploader
    res.author.nickname = creator or info.get("uploader") or info.get("channel") or uploader
    res.author.user_id = str(info.get("uploader_id") or info.get("channel_id") or "")
    res.author.avatar = info.get("uploader_avatar") or info.get("channel_avatar") or info.get("thumbnails", [{}])[0].get("url") if info.get("thumbnails") else None
    res.author.signature = info.get("uploader_description") or info.get("channel_description")
    res.author.follower_count = info.get("uploader_follower_count") or info.get("channel_follower_count") or info.get("channel_follower_count")
    res.author.following_count = info.get("uploader_following_count") or info.get("channel_following_count")
    res.author.like_count = info.get("uploader_like_count") or info.get("channel_like_count") or info.get("uploader_heart_count")
    res.author.video_count = info.get("uploader_video_count") or info.get("channel_video_count")
    res.author.verified = bool(info.get("uploader_verified") or info.get("channel_verified"))

    # ─── Stats (enhanced) ────────────────────────────────────────────
    # For live: concurrent_view_count is the live viewer count
    if kind == "live":
        res.stats.play_count = info.get("concurrent_view_count") or info.get("view_count") or 0
    else:
        res.stats.play_count = info.get("view_count") or info.get("play_count")
    res.stats.digg_count = info.get("like_count")
    res.stats.comment_count = info.get("comment_count")
    res.stats.share_count = info.get("repost_count") or info.get("share_count")
    res.stats.collect_count = info.get("collect_count") or info.get("favorite_count")

    # ─── Video (enhanced) ────────────────────────────────────────────
    if info.get("url"):
        res.video.play_url = info["url"]
        res.video.download_url = info["url"]

    # Cover: try multiple sources
    thumb = info.get("thumbnail")
    thumbs_list = info.get("thumbnails") or []
    if not thumb and thumbs_list:
        thumb = thumbs_list[-1].get("url") if isinstance(thumbs_list[-1], dict) else None
    res.video.cover = thumb
    res.video.dynamic_cover = info.get("dynamic_cover") or info.get("preview_url")
    res.video.origin_cover = thumb
    res.video.duration = int(info.get("duration") or 0) or None
    res.video.format = info.get("ext") or "mp4"
    res.video.ratio = info.get("aspect_ratio")

    # Parse resolution string like "480x864" → width/height
    resolution = info.get("resolution") or ""
    if resolution and "x" in resolution:
        parts = resolution.lower().split("x")
        if len(parts) == 2:
            try:
                res.video.width = int(parts[0])
                res.video.height = int(parts[1])
            except (ValueError, TypeError):
                pass
    # Fallback to direct width/height fields
    if not res.video.width:
        res.video.width = info.get("width")
    if not res.video.height:
        res.video.height = info.get("height")

    # ─── Music ────────────────────────────────────────────────────────
    if info.get("track") or info.get("artist"):
        res.music.title = info.get("track")
        res.music.author = info.get("artist")
        res.music.duration = res.video.duration
    track_info = info.get("music") or {}
    if isinstance(track_info, dict):
        res.music.id = str(track_info.get("id") or "")
        res.music.title = res.music.title or track_info.get("title")
        res.music.author = res.music.author or track_info.get("author")
        res.music.play_url = track_info.get("playUrl") or track_info.get("play_url")
        res.music.cover = track_info.get("coverThumb") or track_info.get("cover_large", {}).get("url_list", [None])[0] if isinstance(track_info.get("cover_large"), dict) else None
        res.music.duration = track_info.get("duration") or res.music.duration

    # ─── Images (for photo carousels) ─────────────────────────────────
    if extra_images:
        res.images = extra_images
    elif info.get("_type") == "image" and info.get("url"):
        res.images = [info["url"]]

    # ─── Hashtags & mentions ──────────────────────────────────────────
    res.hashtags = [t.lstrip("#") for t in (info.get("tags") or []) if t]
    res.mentions = re.findall(r"@([A-Za-z0-9_.]+)",
                              info.get("description") or info.get("title") or "")

    # ─── Live-specific fields (enhanced) ──────────────────────────────
    if kind == "live":
        # Collect all stream URLs from formats array
        stream_urls = {}
        formats = info.get("formats") or []
        for fmt in formats:
            fmt_id = fmt.get("format_id") or fmt.get("format") or "unknown"
            stream_urls[fmt_id] = {
                "url": fmt.get("url"),
                "protocol": fmt.get("protocol"),
                "ext": fmt.get("ext"),
                "vcodec": fmt.get("vcodec"),
                "resolution": fmt.get("resolution"),
                "tbr": fmt.get("tbr"),
                "quality": fmt.get("quality"),
                "format": fmt.get("format"),
            }

        res.live = {
            "is_live": True,
            "live_status": info.get("live_status") or "is_live",
            "was_live": info.get("was_live", False),
            "room_id": str(info.get("id") or ""),
            "stream_id": str(info.get("stream_id") or ""),
            "viewer_count": info.get("concurrent_view_count") or info.get("view_count") or 0,
            "title": info.get("title") or info.get("fulltitle") or "",
            "cover": thumb or "",
            "duration": "",
            "stream_urls": stream_urls,
            "primary_url": info.get("url"),
            "release_year": info.get("release_year"),
            "protocol": info.get("protocol"),
            "vcodec": info.get("vcodec"),
            "tbr": info.get("tbr"),
            "dynamic_range": info.get("dynamic_range"),
            "resolution": info.get("resolution"),
            "format": info.get("format"),
            "format_id": info.get("format_id"),
            "creators": info.get("creators") or [],
            "extracted_at_epoch": info.get("epoch"),
            # Placeholders for Webcast API enrichment (filled later)
            "like_count": 0,
            "diamond_count": 0,
            "start_time": 0,
            "rank_text": "",
            "enter_count": None,
            "unique_viewers": None,
            "follow_status": None,
            "gift_boxes": [],
            "top_donors": [],
            "recent_donors": [],
        }

        # Also populate stats for live
        if not res.stats.play_count:
            res.stats.play_count = res.live["viewer_count"]

    res.raw_keys.append("yt_dlp")
    res.raw_json = {"yt_dlp": info}

    # ─── Extract sec_uid from uploader_url if present ────────────────
    uploader_url = info.get("uploader_url") or ""
    m_sec = re.search(r"sec_uid=([A-Za-z0-9_-]+)", uploader_url)
    if m_sec:
        res.author.sec_uid = m_sec.group(1)

    # ─── All IDs from yt-dlp ──────────────────────────────────────────
    res.all_ids = {
        "user_id": res.author.user_id,
        "video_id": res.content_id,
        "uploader_id": info.get("uploader_id"),
        "channel_id": info.get("channel_id"),
        "music_id": res.music.id if res.music.id else None,
        "room_id": res.live.get("room_id") if res.live else None,
    }

    return res


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 2: __UNIVERSAL_DATA_FOR_REHYDRATION__
# ────────────────────────────────────────────────────────────────────────────
def extract_universal_data(soup: BeautifulSoup) -> Optional[dict]:
    tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not tag:
        return None
    try:
        return json.loads(tag.string)
    except Exception as e:
        logger.warning(f"universal_data parse failed: {e}")
        return None


def extract_sigi_state(soup: BeautifulSoup) -> Optional[dict]:
    """يستخرج SIGI_STATE من الصفحة (الكلاسيكية)."""
    tag = soup.find("script", id="SIGI_STATE")
    if tag:
        try:
            return json.loads(tag.string)
        except Exception:
            pass
    # Fallback: regex over the whole HTML
    text = soup.find("script", string=re.compile(r"SIGI_STATE"))
    if text:
        m = re.search(r"window\[['\"]?SIGI_STATE['\"]?\]\s*=\s*(\{.*?\})\s*;", text.string or "")
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return None


def _from_universal(data: dict) -> Optional[ExtractionResult]:
    """يحوّل __UNIVERSAL_DATA_FOR_REHYDRATION__ إلى ExtractionResult."""
    try:
        ds = data.get("__DEFAULT_SCOPE__", {})

        # Try live-detail first (LIVE streams)
        live_detail = ds.get("webapp.live-detail", {})
        if live_detail:
            return _from_universal_live(live_detail, data)

        # Then video-detail (videos)
        video_detail = ds.get("webapp.video-detail", {})
        if video_detail:
            return _from_universal_video(video_detail, data)

        # Then user-detail (profiles)
        user_detail = ds.get("webapp.user-detail", {})
        if user_detail:
            return _from_universal_user(user_detail, data)

        return None
    except Exception as e:
        logger.exception(f"_from_universal parse failed: {e}")
        return None


def _from_universal_video(video_detail: dict, full_data: dict) -> ExtractionResult:
    """يحوّل webapp.video-detail إلى ExtractionResult."""
    item_info = video_detail.get("itemInfo") or video_detail.get("itemInfo", {})
    item_struct = item_info.get("itemStruct") or {}
    video_stats = item_struct.get("stats") or {}
    author_struct = item_struct.get("author") or {}
    music_struct = item_struct.get("music") or {}
    video_struct = item_struct.get("video") or {}

    res = ExtractionResult(
        success=True,
        kind="video",
        title=item_struct.get("desc") or "",
        description=item_struct.get("desc") or "",
        content_id=str(item_struct.get("id") or ""),
        create_time=item_struct.get("createTime") or 0,
    )

    # Author
    res.author.unique_id = author_struct.get("uniqueId")
    res.author.nickname = author_struct.get("nickname")
    res.author.user_id = str(author_struct.get("id") or "")
    res.author.sec_uid = author_struct.get("secUid")
    res.author.signature = author_struct.get("signature")
    res.author.follower_count = author_struct.get("followerCount")
    res.author.following_count = author_struct.get("followingCount")
    res.author.like_count = author_struct.get("heart") or author_struct.get("likeCount")
    res.author.video_count = author_struct.get("videoCount")
    res.author.verified = bool(author_struct.get("verified"))
    if author_struct.get("avatarLarger"):
        urls = author_struct["avatarLarger"].get("url_list") or []
        res.author.avatar = urls[0] if urls else None

    # Stats
    res.stats.play_count = video_stats.get("playCount")
    res.stats.digg_count = video_stats.get("diggCount")
    res.stats.comment_count = video_stats.get("commentCount")
    res.stats.share_count = video_stats.get("shareCount")
    res.stats.collect_count = video_stats.get("collectCount")

    # Video
    res.video.play_url = video_struct.get("playAddr") or ""
    if isinstance(res.video.play_url, list):
        urls = res.video.play_url
        res.video.play_url = urls[0] if urls else ""
    res.video.download_url = video_struct.get("downloadAddr") or res.video.play_url
    res.video.cover = video_struct.get("cover") or video_struct.get("originCover")
    res.video.dynamic_cover = video_struct.get("dynamicCover")
    res.video.origin_cover = video_struct.get("originCover")
    res.video.duration = video_struct.get("duration")
    res.video.width = video_struct.get("width")
    res.video.height = video_struct.get("height")
    res.video.ratio = video_struct.get("ratio")
    res.video.format = video_struct.get("format") or "mp4"

    # Music
    res.music.id = str(music_struct.get("id") or "")
    res.music.title = music_struct.get("title")
    res.music.author = music_struct.get("authorName")
    res.music.play_url = music_struct.get("playUrl")
    res.music.cover = music_struct.get("coverLarge")
    res.music.duration = music_struct.get("duration")

    # Images (for photo carousels)
    if item_struct.get("images"):
        res.images = [img.get("urlList", [None])[0] if isinstance(img, dict) else None
                       for img in item_struct["images"] if img]
        res.images = [u for u in res.images if u]
        if res.images:
            res.kind = "photo"

    # Hashtags
    challenges = item_struct.get("challenges") or item_struct.get("textExtra") or []
    res.hashtags = [c.get("hashtagName") or c.get("hashtag_name") for c in challenges
                     if c.get("hashtagName") or c.get("hashtag_name")]
    res.hashtags = [h for h in res.hashtags if h]

    # Mentions
    desc = item_struct.get("desc") or ""
    res.mentions = re.findall(r"@([A-Za-z0-9_.]+)", desc)

    # All IDs (raw)
    res.all_ids = {
        "sec_uid": author_struct.get("secUid"),
        "user_id": author_struct.get("id"),
        "video_id": item_struct.get("id"),
        "music_id": music_struct.get("id"),
    }

    # App context (extract csrf, wid, nonce, requestId)
    app_ctx = (full_data.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.app-context", {})
    if app_ctx:
        res.all_ids.update({
            "csrf_token": app_ctx.get("csrfToken"),
            "wid": app_ctx.get("wid"),
            "nonce": app_ctx.get("nonce"),
            "request_id": app_ctx.get("requestId"),
            "encrypted_webid": app_ctx.get("encryptedWebid"),
            "region": app_ctx.get("region"),
            "cluster_region": app_ctx.get("clusterRegion"),
            "ab_test_version": app_ctx.get("abTestVersion", {}).get("versionName") if isinstance(app_ctx.get("abTestVersion"), dict) else None,
        })

    res.raw_keys.append("universal_data")
    res.raw_json = full_data

    return res


def _from_universal_live(live_detail: dict, full_data: dict) -> ExtractionResult:
    """يحوّل webapp.live-detail إلى ExtractionResult."""
    live_info = live_detail.get("liveRoom") or live_detail.get("liveInfo") or live_detail
    owner = live_info.get("owner") or {}
    stats = live_info.get("stats") or {}
    room = live_info.get("roomInfo") or live_info

    res = ExtractionResult(
        success=True,
        kind="live",
        title=room.get("title") or live_info.get("title") or "",
        content_id=str(room.get("roomId") or live_info.get("roomId") or ""),
        create_time=room.get("createTime") or live_info.get("createTime") or 0,
    )

    # Author (owner of the live stream)
    res.author.unique_id = owner.get("uniqueId") or owner.get("displayId")
    res.author.nickname = owner.get("nickname")
    res.author.user_id = str(owner.get("id") or "")
    res.author.sec_uid = owner.get("secUid") or owner.get("secUID")
    res.author.signature = owner.get("signature")
    res.author.follower_count = owner.get("followerCount")
    res.author.following_count = owner.get("followingCount")
    res.author.like_count = owner.get("heartCount") or owner.get("likeCount")
    res.author.video_count = owner.get("videoCount") or owner.get("roomEventCnt")
    res.author.verified = bool(owner.get("verified"))
    if owner.get("avatarThumb"):
        urls = owner["avatarThumb"].get("url_list") or []
        res.author.avatar = urls[0] if urls else None

    # Live stats
    res.stats.play_count = stats.get("totalUser") or stats.get("total_user_desp") or live_info.get("userCount")
    res.stats.digg_count = stats.get("likeCount") or stats.get("likeCountStr")
    res.stats.comment_count = stats.get("commentCount")
    res.stats.share_count = stats.get("shareCount")

    # Live-specific data
    res.live = {
        "is_live": True,
        "room_id": str(room.get("roomId") or ""),
        "stream_id": str(room.get("streamId") or ""),
        "title": room.get("title") or "",
        "viewer_count": stats.get("totalUser") or 0,
        "like_count": stats.get("likeCount") or 0,
        "diamond_count": stats.get("diamondCount") or 0,
        "start_time": room.get("createTime") or 0,
        "cover": room.get("coverUrl") or live_info.get("coverUrl"),
        "duration": "",  # calculated by frontend from start_time
        "stream_urls": room.get("streamUrl") or {},
        "rank_text": room.get("rank") or "",
        "enter_count": live_info.get("enterCount"),
        "unique_viewers": stats.get("totalUserDesp"),
        "follow_status": live_info.get("followStatus"),
        "gift_boxes": live_info.get("giftBoxes") or [],
        "top_donors": live_info.get("topDonors") or [],
    }

    # IDs
    res.all_ids = {
        "sec_uid": owner.get("secUid") or owner.get("secUID"),
        "user_id": owner.get("id"),
        "room_id": room.get("roomId"),
        "stream_id": room.get("streamId"),
    }

    # App context
    app_ctx = (full_data.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.app-context", {})
    if app_ctx:
        res.all_ids.update({
            "csrf_token": app_ctx.get("csrfToken"),
            "wid": app_ctx.get("wid"),
            "nonce": app_ctx.get("nonce"),
            "request_id": app_ctx.get("requestId"),
            "encrypted_webid": app_ctx.get("encryptedWebid"),
            "region": app_ctx.get("region"),
        })

    res.raw_keys.append("universal_data_live")
    res.raw_json = full_data

    return res


def _from_universal_user(user_detail: dict, full_data: dict) -> ExtractionResult:
    """يحوّل webapp.user-detail إلى ExtractionResult (ملف شخصي)."""
    user_info = user_detail.get("userInfo") or user_detail.get("user") or {}
    user = user_info.get("user") or user_info
    stats = user_info.get("stats") or {}

    res = ExtractionResult(
        success=True,
        kind="profile",
        title=user.get("nickname") or user.get("uniqueId") or "",
        description=user.get("signature") or "",
        content_id=str(user.get("id") or ""),
    )

    res.author.unique_id = user.get("uniqueId")
    res.author.nickname = user.get("nickname")
    res.author.user_id = str(user.get("id") or "")
    res.author.sec_uid = user.get("secUid")
    res.author.signature = user.get("signature")
    res.author.follower_count = stats.get("followerCount")
    res.author.following_count = stats.get("followingCount")
    res.author.like_count = stats.get("heart") or stats.get("likeCount")
    res.author.video_count = stats.get("videoCount")
    res.author.verified = bool(user.get("verified"))
    if user.get("avatarLarger"):
        urls = user["avatarLarger"].get("url_list") or []
        res.author.avatar = urls[0] if urls else None

    res.stats.play_count = stats.get("playCount") or stats.get("totalFavorited")
    res.stats.digg_count = stats.get("likeCount") or stats.get("diggCount")

    res.all_ids = {
        "sec_uid": user.get("secUid"),
        "user_id": user.get("id"),
    }

    app_ctx = (full_data.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.app-context", {})
    if app_ctx:
        res.all_ids.update({
            "csrf_token": app_ctx.get("csrfToken"),
            "wid": app_ctx.get("wid"),
            "nonce": app_ctx.get("nonce"),
            "request_id": app_ctx.get("requestId"),
        })

    res.raw_keys.append("universal_data_user")
    res.raw_json = full_data
    return res


def _from_sigi(sigi: dict) -> Optional[ExtractionResult]:
    """يحوّل SIGI_STATE الكلاسيكية إلى ExtractionResult (fallback قديم)."""
    try:
        # Try ItemModule (video page)
        item_module = sigi.get("ItemModule", {})
        if item_module:
            item = list(item_module.values())[0] if item_module else {}
            user_module = sigi.get("UserModule", {}).get("users", {})
            author = list(user_module.values())[0] if user_module else {}

            res = ExtractionResult(
                success=True,
                kind="video",
                title=item.get("desc") or "",
                description=item.get("desc") or "",
                content_id=str(item.get("id") or ""),
                create_time=item.get("createTime") or 0,
            )

            res.author.unique_id = author.get("uniqueId")
            res.author.nickname = author.get("nickname")
            res.author.user_id = str(author.get("id") or "")
            res.author.sec_uid = author.get("secUid")
            res.author.signature = author.get("signature")
            res.author.follower_count = (sigi.get("UserModule", {}).get("stats", {}).get("followerCount"))
            res.author.verified = bool(author.get("verified"))

            stats = item.get("stats") or {}
            res.stats.play_count = stats.get("playCount")
            res.stats.digg_count = stats.get("diggCount")
            res.stats.comment_count = stats.get("commentCount")
            res.stats.share_count = stats.get("shareCount")

            video = item.get("video") or {}
            res.video.play_url = (video.get("playAddr") or [None])[0]
            res.video.cover = video.get("cover")
            res.video.duration = video.get("duration")
            res.video.width = video.get("width")
            res.video.height = video.get("height")

            res.raw_keys.append("sigi_state")
            res.raw_json = sigi
            return res

        # Try LiveRoom
        live_module = sigi.get("LiveRoomModule", {}).get("liveRoomUserInfo", {})
        if live_module:
            user = live_module.get("user") or {}
            room = live_module.get("liveRoom") or {}

            res = ExtractionResult(
                success=True,
                kind="live",
                title=room.get("title") or "",
                content_id=str(room.get("roomId") or ""),
            )

            res.author.unique_id = user.get("uniqueId")
            res.author.nickname = user.get("nickname")
            res.author.user_id = str(user.get("id") or "")
            res.author.sec_uid = user.get("secUid")
            res.author.follower_count = user.get("followerCount")
            res.author.verified = bool(user.get("verified"))

            res.live = {
                "is_live": True,
                "room_id": str(room.get("roomId") or ""),
                "stream_id": str(room.get("streamId") or ""),
                "viewer_count": room.get("userCount") or 0,
                "title": room.get("title") or "",
                "cover": room.get("coverUrl"),
            }

            res.raw_keys.append("sigi_state_live")
            res.raw_json = sigi
            return res

        return None
    except Exception as e:
        logger.exception(f"_from_sigi failed: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 5: oEmbed API (الرسمية — تعطي title + author + thumbnail)
# ────────────────────────────────────────────────────────────────────────────
def extract_oembed(session: requests.Session,
                   url: str, ua: str) -> Optional[dict]:
    """يستخدم oEmbed API الرسمي من TikTok للحصول على بيانات أساسية."""
    try:
        api = "https://www.tiktok.com/oembed"
        r = session.get(api, params={"url": url}, timeout=15,
                       headers={"User-Agent": ua})
        if r.status_code != 200:
            logger.debug(f"oembed HTTP {r.status_code}")
            return None
        data = r.json()
        if "error" in data:
            logger.debug(f"oembed error: {data}")
            return None
        return data
    except Exception as e:
        logger.debug(f"oembed failed: {e}")
        return None


def _from_oembed(oem: dict, parsed: ParsedLink) -> ExtractionResult:
    """يحوّل بيانات oEmbed إلى ExtractionResult جزئي."""
    res = ExtractionResult(
        success=True,
        url=parsed.final_url or parsed.normalized,
        final_url=parsed.final_url or parsed.normalized,
        kind=parsed.kind or "video",
        title=oem.get("title") or "",
    )
    res.author.unique_id = oem.get("author_name")
    res.author.nickname = oem.get("author_name")
    res.video.cover = oem.get("thumbnail_url")
    res.video.duration = oem.get("duration") or oem.get("embed_product_id")
    res.raw_keys.append("oembed")
    res.raw_json = {"oembed": oem}
    return res


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 6 + 7: Meta Tags + DOM Fallback
# ────────────────────────────────────────────────────────────────────────────
def _extract_meta_tags(soup: BeautifulSoup) -> Dict[str, str]:
    """يستخرج جميع وسوم meta (og:*, twitter:*, article:*)."""
    meta = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property") or tag.get("name") or tag.get("itemprop")
        content = tag.get("content")
        if prop and content:
            meta[prop] = content
    return meta


def _extract_dom(soup: BeautifulSoup) -> Dict[str, Any]:
    """يستخرج بيانات إضافية من DOM مباشرة (fallback أخير)."""
    dom = {}
    title_tag = soup.find("title")
    if title_tag:
        dom["title"] = title_tag.string
    # Avatar
    avatar_img = soup.find("img", attrs={"alt": re.compile(r"avatar", re.I)})
    if avatar_img:
        dom["avatar"] = avatar_img.get("src")
    return dom


def _from_meta(meta: Dict[str, str], dom: Dict[str, Any],
               parsed: ParsedLink) -> ExtractionResult:
    res = ExtractionResult(
        success=True,
        url=parsed.final_url or parsed.normalized,
        final_url=parsed.final_url or parsed.normalized,
        kind=parsed.kind or "unknown",
        title=meta.get("og:title") or dom.get("title") or "",
        description=meta.get("og:description") or meta.get("description") or "",
    )

    # Author from og:video:tag or twitter:creator
    res.author.unique_id = parsed.username
    res.author.nickname = parsed.username

    # Cover from og:image
    res.video.cover = meta.get("og:image") or meta.get("twitter:image")
    res.video.play_url = meta.get("og:video") or meta.get("og:video:url") or meta.get("og:video:secure_url")

    # Author avatar from DOM
    if dom.get("avatar"):
        res.author.avatar = dom["avatar"]

    res.meta_tags = meta
    res.raw_keys.append("meta_tags")
    res.raw_json = {"meta": meta, "dom": dom}
    return res


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 4: Webcast API + X-Bogus (Playwright — للبث المباشر)
# ────────────────────────────────────────────────────────────────────────────
def _try_webcast_api(session: requests.Session,
                     parsed: ParsedLink) -> Optional[ExtractionResult]:
    """يستدعي Webcast API لاستخراج بيانات البث المباشر.

    يتطلب X-Bogus صالح؛ يستخدم Playwright مع webmssdk.js الحقيقي للتوقيع.
    يعمل فقط إذا كان ENABLE_PLAYWRIGHT=true.
    """
    if not parsed.username:
        return None

    if os.environ.get("ENABLE_PLAYWRIGHT", "").lower() != "true":
        logger.debug("Webcast API skipped: ENABLE_PLAYWRIGHT not 'true'")
        return None

    ua = USER_AGENTS[0]
    base = "https://webcast.tiktok.com/webcast/room/page/info/"
    params = {
        "unique_id": parsed.username,
        "device_platform": "web",
        "aid": "1988",
        "channel": "channel_unknown",
        "app_language": "en",
        "web_rhd": "1",
    }
    url = base + "?" + urlencode(params)

    signed_url = url
    headers = {"User-Agent": ua}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xbogus_playwright import sign_with_playwright
        signed_url, headers = sign_with_playwright(url, user_agent=ua)
        if "X-Bogus" not in headers:
            logger.warning("Webcast API: X-Bogus signing failed; aborting")
            return None
        logger.info(f"Webcast API: signed OK, X-Bogus={headers['X-Bogus'][:24]}...")
    except Exception as e:
        logger.warning(f"Webcast API: signer unavailable ({e})")
        return None

    try:
        r = session.get(signed_url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status_code") != 0:
            logger.warning(f"Webcast API: status_code={data.get('status_code')} "
                          f"msg={data.get('status_msg')}")
            return None
        return _from_webcast(data, parsed)
    except Exception as e:
        logger.exception(f"Webcast API crashed: {e}")
        return None


def _from_webcast(data: dict, parsed: ParsedLink) -> Optional[ExtractionResult]:
    """يحوّل استجابة Webcast API إلى ExtractionResult."""
    try:
        room_data = data.get("data") or {}
        if not room_data:
            return None
        owner = room_data.get("owner") or {}
        stats = room_data.get("stats") or {}
        live_room = room_data.get("live_room") or room_data

        res = ExtractionResult(
            success=True,
            url=parsed.final_url or parsed.normalized,
            final_url=parsed.final_url or parsed.normalized,
            kind="live",
            title=live_room.get("title") or "",
            content_id=str(live_room.get("room_id") or ""),
            create_time=live_room.get("create_time") or 0,
        )
        res.author.unique_id = owner.get("unique_id") or parsed.username
        res.author.nickname = owner.get("nickname") or ""
        res.author.user_id = str(owner.get("id") or "")
        res.author.sec_uid = owner.get("sec_uid") or ""
        if owner.get("avatar_thumb"):
            urls = owner["avatar_thumb"].get("url_list", [None])
            res.author.avatar = urls[0] if urls else None
        res.author.signature = owner.get("signature") or ""
        res.author.follower_count = owner.get("follower_count") or 0
        res.author.following_count = owner.get("following_count") or 0
        res.author.verified = bool(owner.get("verified"))

        res.stats.play_count = stats.get("total_user") or stats.get("total_user_desp") or 0
        res.stats.digg_count = stats.get("like_count") or 0
        res.stats.comment_count = stats.get("comment_count") or 0
        res.stats.share_count = stats.get("share_count") or 0

        res.live = {
            "is_live": True,
            "room_id": str(live_room.get("room_id") or ""),
            "stream_id": str(live_room.get("stream_id") or ""),
            "viewer_count": stats.get("total_user") or 0,
            "like_count": stats.get("like_count") or 0,
            "diamond_count": stats.get("diamond_count") or 0,
            "title": live_room.get("title") or "",
            "cover": live_room.get("cover_url") or "",
            "start_time": live_room.get("create_time") or 0,
        }
        res.raw_keys.append("webcast_api")
        res.raw_json = {"webcast": data}
        return res
    except Exception as e:
        logger.exception(f"_from_webcast failed: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────────
#  نقطة الدخول الرئيسية
# ────────────────────────────────────────────────────────────────────────────
def extract(raw_url: str, timeout: int = 30) -> ExtractionResult:
    """يستخرج بيانات أي رابط TikTok باستخدام كل الاستراتيجيات بالترتيب.

    الترتيب:
      1) yt-dlp (الأقوى — يعمل من أي IP)
      2) حلّ الرابط المختصر
      3) جلب HTML
      4) UNIVERSAL_DATA
      5) SIGI_STATE
      6) Webcast API + X-Bogus (للبث المباشر)
      7) oEmbed API
      8) Meta Tags + DOM fallback
    """
    session = build_session()
    parsed = parse_link(raw_url)

    # ─── 1) حلّ الرابط المختصر أولاً ──────────────────────────────────
    # نحتاج الـ username قبل أن نستطيع استدعاء yt-dlp بفعالية، لأن
    # yt-dlp على الرابط المختصر يتبع التحويل وينتهي عند /hk/about.
    if parsed.is_short or parsed.kind == "short":
        final = resolve_short_url(session, parsed.normalized, timeout=timeout)
        if not final:
            return ExtractionResult(
                success=False,
                url=parsed.normalized,
                error="تعذّر حلّ الرابط المختصر - قد يكون محذوفاً",
            )
        parsed = parse_link(final)
        parsed.final_url = final
        parsed.notes.append("resolved_short")

    # ─── 2) yt-dlp على الرابط المحلول (الأقوى) ───────────────────────
    # yt-dlp يعرف كيف يتعامل مع: /@user/video/ID, /@user/live, /@user/photo/ID
    target_url = parsed.final_url or parsed.normalized
    ytdlp_result = _extract_with_ytdlp(target_url, parsed, session, timeout=timeout)
    if ytdlp_result and ytdlp_result.success:
        logger.info(f"✅ yt-dlp succeeded: kind={ytdlp_result.kind} "
                    f"author=@{ytdlp_result.author.unique_id} "
                    f"viewer_count={ytdlp_result.live.get('viewer_count') if ytdlp_result.live else 'N/A'}")

        # ─── Enrichment: collect ALL remaining data ──────────────────
        # 1) Share-link params (sec_uid, checksum, share_link_id, etc.)
        _enrich_with_share_params(ytdlp_result, target_url)
        # 2) Webcast API direct (no X-Bogus needed for some rooms)
        _enrich_with_webcast(ytdlp_result, session, parsed)
        # 3) HTML UNIVERSAL_DATA (csrf, wid, nonce, requestId, region)
        _enrich_with_html(ytdlp_result, session, parsed, timeout)
        # 4) v3.1: Webcast gifts + donations + rankings
        _enrich_with_webcast_gifts(ytdlp_result, session, parsed)
        # 5) v3.1: User detail API (follower_count, following_count, like_count, video_count)
        _enrich_with_user_detail_api(ytdlp_result, session, parsed)
        # 6) v3.1: Decode sensitive tokens (_d, expire, sign, checksum, timestamp)
        _decode_sensitive_tokens(ytdlp_result, target_url)
        # 7) v3.1: CDN metadata (datacenter, quality, region, protocol)
        _extract_cdn_metadata(ytdlp_result)
        # 8) v3.1: Avatar metadata (x-expires, refresh_token, idc, tos_bucket)
        _extract_avatar_metadata(ytdlp_result)
        # 9) v3.1: Stream access analysis (all signed URLs + expiry + access level)
        _build_stream_access_analysis(ytdlp_result)
        # 10) v3.2: Deep analysis — URL, JSON, HTML, tokens, stream URLs
        _analyze_url_deeply(ytdlp_result, raw_url)
        _analyze_json_files(ytdlp_result)
        _analyze_html_content(ytdlp_result, session, parsed)
        _deep_decode_tokens(ytdlp_result)
        _analyze_stream_urls_deeply(ytdlp_result)
        # 11) v3.3: Advanced processors (from tiktokjson/tikhtml/mhmdz1)
        # Fetch HTML once for all HTML-based processors
        html_content = ""
        try:
            r_html = session.get(target_url, timeout=15, headers={"User-Agent": USER_AGENTS[0]})
            if r_html.status_code == 200 and not re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", r_html.url or "", re.I):
                html_content = r_html.text or ""
        except Exception:
            pass
        _enrich_with_security_credentials(ytdlp_result, html_content)
        _enrich_with_advanced_json_files(ytdlp_result, html_content)
        _enrich_with_advanced_id_extraction(ytdlp_result, html_content)
        _enrich_with_advanced_api(ytdlp_result, session, parsed)
        _enrich_with_enter_live_room(ytdlp_result, session)
        if html_content:
            _save_html_file(ytdlp_result, html_content, parsed)
        # ─── v3.6: Interaction analysis + webcast surfacing ───
        _surface_webcast_data(ytdlp_result)
        _build_interaction_analysis(ytdlp_result)
        # ─── v3.7: Preloaded accounts + ready payloads ───
        _enrich_with_preloaded_accounts(ytdlp_result)
        # ─── v4.2: Deep token extraction + fingerprint ───
        _extract_tea_analytics(session, ytdlp_result)
        _extract_slardar_data(ytdlp_result)
        _extract_argus_token(ytdlp_result)
        _deep_webcast_analysis(ytdlp_result)
        _build_interaction_fingerprint(ytdlp_result)
        return ytdlp_result

    target = parsed.final_url or parsed.normalized

    # ─── 3) جلب HTML ────────────────────────────────────────────────
    ua = USER_AGENTS[hash(target) % len(USER_AGENTS)]
    try:
        r = session.get(target, timeout=timeout, headers={"User-Agent": ua})
    except requests.exceptions.SSLError:
        r = session.get(target, timeout=timeout, headers={"User-Agent": ua}, verify=False)
    except (requests.exceptions.ConnectionError, socket.timeout) as e:
        return ExtractionResult(success=False, url=target,
                                error=f"فشل الاتصال: {e}")
    except Exception as e:
        return ExtractionResult(success=False, url=target,
                                error=f"خطأ غير متوقع: {e}")

    if r.status_code >= 400:
        return ExtractionResult(success=False, url=target,
                                error=f"استجابة HTTP {r.status_code}")

    # كشف إعادة التوجيه الجغرافي
    final = r.url or ""
    if re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", final, re.I) \
       and "video" not in final and "@@" not in final:
        # جرّب Webcast API قبل أن نعلن الفشل
        logger.info("geo-block detected, trying Webcast API fallback")
        wc_result = _try_webcast_api(session, parsed)
        if wc_result and wc_result.success:
            return wc_result

        # جرّب yt-dlp على رابط /@user/live مباشرة (يعمل من أي IP بدون HTML)
        if parsed.username and parsed.kind == "live":
            logger.info(f"Trying yt-dlp on https://www.tiktok.com/@{parsed.username}/live")
            alt_parsed = ParsedLink(
                raw=raw_url, normalized=f"https://www.tiktok.com/@{parsed.username}/live",
                kind="live", username=parsed.username,
                final_url=f"https://www.tiktok.com/@{parsed.username}/live",
            )
            alt_result = _extract_with_ytdlp(alt_parsed.normalized, alt_parsed, session, timeout=timeout)
            if alt_result and alt_result.success:
                _enrich_with_html(alt_result, session, alt_parsed, timeout)
                return alt_result
            elif alt_result and alt_result.error:
                # yt-dlp returned a clear error (e.g. "channel not live")
                # — but try share-link params first before giving up
                share_data = _extract_share_link_params(target)
                if share_data:
                    share_result = _from_share_link(share_data, parsed, target)
                    share_result.error = alt_result.error  # attach yt-dlp error for transparency
                    share_result.raw_keys.append("ytdlp_error_fallback")
                    # ─── v3.1: Run enrichment even on share-link fallback ───
                    _enrich_with_share_params(share_result, target)
                    _enrich_with_user_detail_api(share_result, session, parsed)
                    _decode_sensitive_tokens(share_result, target)
                    _enrich_with_webcast_gifts(share_result, session, parsed)
                    _extract_avatar_metadata(share_result)
                    _build_stream_access_analysis(share_result)
                    # ─── v3.2: Deep analysis on share-link fallback ───
                    _analyze_url_deeply(share_result, target)
                    _analyze_json_files(share_result)
                    _deep_decode_tokens(share_result)
                    _analyze_stream_urls_deeply(share_result)
                    return share_result
                # No share data — propagate the yt-dlp error
                return alt_result
            elif not alt_result:
                # yt-dlp itself failed (subprocess error) — try share-link params
                share_data = _extract_share_link_params(target)
                if share_data:
                    share_result = _from_share_link(share_data, parsed, target)
                    share_result.raw_keys.append("ytdlp_subprocess_failed")
                    # ─── v3.1: Run enrichment even on share-link fallback ───
                    _enrich_with_share_params(share_result, target)
                    _enrich_with_user_detail_api(share_result, session, parsed)
                    _decode_sensitive_tokens(share_result, target)
                    _enrich_with_webcast_gifts(share_result, session, parsed)
                    _extract_avatar_metadata(share_result)
                    _build_stream_access_analysis(share_result)
                    # ─── v3.2: Deep analysis on share-link fallback ───
                    _analyze_url_deeply(share_result, target)
                    _analyze_json_files(share_result)
                    _deep_decode_tokens(share_result)
                    _analyze_stream_urls_deeply(share_result)
                    return share_result

                return ExtractionResult(
                    success=False,
                    url=target,
                    final_url=final,
                    kind=parsed.kind or "unknown",
                    error=f"yt-dlp لم يتمكن من استخراج بيانات البث المباشر من @{parsed.username}. "
                          "قد يكون البث قد انتهى أو لم يبدأ بعد، أو أن المستخدم غير موجود.",
                )

        # حتى عند الحظر الجغرافي، نحاول استخراج المُعرّفات من رابط المشاركة
        share_data = _extract_share_link_params(target)
        if share_data:
            logger.info("Falling back to share-link params extraction")
            share_result = _from_share_link(share_data, parsed, target)
            # ─── v3.1: Run enrichment even on share-link fallback ───
            _enrich_with_share_params(share_result, target)
            _enrich_with_user_detail_api(share_result, session, parsed)
            _decode_sensitive_tokens(share_result, target)
            _enrich_with_webcast_gifts(share_result, session, parsed)
            _extract_avatar_metadata(share_result)
            _build_stream_access_analysis(share_result)
            # ─── v3.2: Deep analysis on share-link fallback ───
            _analyze_url_deeply(share_result, target)
            _analyze_json_files(share_result)
            _deep_decode_tokens(share_result)
            _analyze_stream_urls_deeply(share_result)
            return share_result

        return ExtractionResult(
            success=False,
            url=target,
            final_url=final,
            kind=parsed.kind or "unknown",
            error=f"الخادم محجوب جغرافياً من TikTok (تم التحويل إلى {final}). "
                  "yt-dlp فشل أيضاً — تأكد أن البث لا يزال مباشراً، أو عوّم "
                  "TIKTOK_PROXY ببروكسي سكني صالح، أو فعّل ENABLE_PLAYWRIGHT=true.",
        )

    html = r.text or ""
    soup = BeautifulSoup(html, "lxml")

    # ─── 4) UNIVERSAL_DATA ────────────────────────────────────────────
    result: Optional[ExtractionResult] = None

    uni = extract_universal_data(soup)
    if uni:
        try:
            result = _from_universal(uni)
        except Exception as e:
            logger.exception(f"universal parse failed: {e}")

    # ─── 5) SIGI_STATE ────────────────────────────────────────────────
    if not result or not result.success:
        sigi = extract_sigi_state(soup)
        if sigi:
            try:
                result = _from_sigi(sigi)
            except Exception as e:
                logger.exception(f"sigi parse failed: {e}")

    # ─── 6) Webcast API + X-Bogus (للبث المباشر) ─────────────────────
    if (not result or not result.success) and parsed.kind == "live":
        wc_result = _try_webcast_api(session, parsed)
        if wc_result and wc_result.success:
            result = wc_result

    # ─── 7) oEmbed (للفيديوهات فقط) ─────────────────────────────────
    if (not result or not result.success) and parsed.kind in ("video", "photo"):
        oem = extract_oembed(session, target, ua)
        if oem:
            result = _from_oembed(oem, parsed)

    # ─── 8) Meta Tags + DOM Fallback ─────────────────────────────────
    if not result or not result.success:
        meta = _extract_meta_tags(soup)
        dom = _extract_dom(soup)
        if meta or dom:
            result = _from_meta(meta, dom, parsed)

    # ─── 9) Share-link params fallback ────────────────────────────────
    # استخراج المُعرّفات من رابط المشاركة نفسه (sec_user_id، user_id، إلخ)
    # يعمل دائماً لأن البيانات مضمّنة في الـ URL نفسه
    if not result or not result.success:
        share_data = _extract_share_link_params(target)
        if share_data:
            result = _from_share_link(share_data, parsed, target)

    # ─── النتيجة النهائية ──────────────────────────────────────────
    if result:
        result.url = target
        result.final_url = final or target
        # ─── v3.1: Final enrichment pass (runs on ALL successful results) ───
        # These are non-destructive — only fill empty fields
        _enrich_with_share_params(result, target)
        _enrich_with_user_detail_api(result, session, parsed)
        _decode_sensitive_tokens(result, target)
        _enrich_with_webcast_gifts(result, session, parsed)
        _extract_cdn_metadata(result)
        _extract_avatar_metadata(result)
        _build_stream_access_analysis(result)
        # ─── v3.2: Deep analysis pass ────────────────────────────────
        _analyze_url_deeply(result, raw_url)  # pass the ORIGINAL url
        _analyze_json_files(result)
        _analyze_html_content(result, session, parsed)
        _deep_decode_tokens(result)
        _analyze_stream_urls_deeply(result)
        # ─── v3.3: Advanced processors (from tiktokjson/tikhtml/mhmdz1) ───
        # Fetch HTML once for all HTML-based processors
        html_content = ""
        try:
            r_html = session.get(target, timeout=15, headers={"User-Agent": USER_AGENTS[0]})
            if r_html.status_code == 200 and not re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", r_html.url or "", re.I):
                html_content = r_html.text or ""
        except Exception:
            pass
        _enrich_with_security_credentials(result, html_content)
        _enrich_with_advanced_json_files(result, html_content)
        _enrich_with_advanced_id_extraction(result, html_content)
        _enrich_with_advanced_api(result, session, parsed)
        _enrich_with_enter_live_room(result, session)
        if html_content:
            _save_html_file(result, html_content, parsed)
        # ─── v3.6: Interaction analysis + webcast surfacing ───
        _surface_webcast_data(result)
        _build_interaction_analysis(result)
        # ─── v3.7: Preloaded accounts + ready payloads ───
        _enrich_with_preloaded_accounts(result)
        # ─── v4.2: Deep token extraction + fingerprint ───
        _extract_tea_analytics(session, result)
        _extract_slardar_data(result)
        _extract_argus_token(result)
        _deep_webcast_analysis(result)
        _build_interaction_fingerprint(result)
        # ─── v4.3: Deep analytics processors ───
        _analyze_engagement_quality(result)
        _analyze_gift_economy(result)
        _detect_stream_health(result)
        _compute_influence_score(result)
        _build_audience_profile(result)
        _build_temporal_profile(result)
        _assess_account_risk(result)
        _extract_commerce_data(result)
        _aggregate_deep_analytics(result)
        # ─── v4.4: Persist user data to JSON DB ───
        try:
            _persist_user_data(result)
        except Exception as e:
            logger.warning(f"persist_user_data failed (non-fatal): {e}")
        return result

    return ExtractionResult(
        success=False,
        url=target,
        final_url=final or target,
        kind=parsed.kind or "unknown",
        error="فشلت جميع استراتيجيات الاستخراج. تأكد من صحة الرابط أو "
              "فعّل TIKTOK_PROXY أو ENABLE_PLAYWRIGHT=true.",
    )


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية 9: استخراج المُعرّفات من رابط المشاركة نفسه
# ────────────────────────────────────────────────────────────────────────────
def _extract_share_link_params(url: str) -> Optional[Dict[str, Any]]:
    """يستخرج المُعرّفات المضمّنة في روابط مشاركة TikTok مباشرة من الـ URL.

    روابط مشاركة TikTok تحتوي على معطيات قيّمة في query string:
      - sec_user_id:   المُعرّف المشفّر للمستخدم
      - user_id:       المُعرّف الرقمي للمستخدم
      - share_from_user_id: مُعرّف مُرسل المشاركة (قد يكون مختلفاً)
      - share_region:  منطقة المشاركة (YE، SA، EG، إلخ)
      - share_app_id:  معرّف التطبيق المُشارِك (عادةً 1233 = TikTok Android)
      - share_link_id: مُعرّف فريد للمشاركة
      - timestamp:     وقت إنشاء المشاركة
      - ugbiz_name:    نوع المحتوى (LIVE، VIDEO، إلخ)
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        share_data = {}
        for k in ("sec_user_id", "user_id", "share_from_user_id",
                  "share_region", "share_app_id", "share_link_id",
                  "share_enter_from", "social_share_type", "source",
                  "timestamp", "ugbiz_name", "utm_campaign", "utm_medium",
                  "utm_source", "checksum", "_d", "_r", "_svg",
                  "enter_from_merge", "enter_method"):
            if params.get(k):
                share_data[k] = params[k][0]

        # Only return if we have at least one useful identifier
        if share_data.get("sec_user_id") or share_data.get("user_id"):
            return share_data
        return None
    except Exception as e:
        logger.debug(f"_extract_share_link_params failed: {e}")
        return None


def _from_share_link(share_data: Dict[str, Any], parsed: ParsedLink,
                      url: str) -> ExtractionResult:
    """يبني ExtractionResult من بيانات رابط المشاركة فقط."""
    is_live = share_data.get("ugbiz_name") == "LIVE"
    kind = "live" if is_live else (parsed.kind or "video")

    res = ExtractionResult(
        success=True,
        url=url,
        final_url=url,
        kind=kind,
        title=f"رابط مشاركة TikTok ({kind})",
        description=f"تم استخراج المُعرّفات من رابط المشاركة مباشرةً.",
    )

    # Author info from share params
    res.author.unique_id = parsed.username
    res.author.nickname = parsed.username
    res.author.user_id = share_data.get("user_id") or share_data.get("share_from_user_id")
    res.author.sec_uid = share_data.get("sec_user_id")

    # All extracted IDs (permissions/keys)
    res.all_ids = {
        "sec_uid": share_data.get("sec_user_id"),
        "user_id": share_data.get("user_id"),
        "share_from_user_id": share_data.get("share_from_user_id"),
        "share_region": share_data.get("share_region"),
        "share_app_id": share_data.get("share_app_id"),
        "share_link_id": share_data.get("share_link_id"),
        "share_enter_from": share_data.get("share_enter_from"),
        "social_share_type": share_data.get("social_share_type"),
        "source": share_data.get("source"),
        "timestamp": share_data.get("timestamp"),
        "ugbiz_name": share_data.get("ugbiz_name"),
        "utm_campaign": share_data.get("utm_campaign"),
        "utm_medium": share_data.get("utm_medium"),
        "utm_source": share_data.get("utm_source"),
        "checksum": share_data.get("checksum"),
    }

    # Live-specific data (if applicable)
    if is_live:
        res.live = {
            "is_live": True,  # share link was for a LIVE broadcast
            "title": f"بث مباشر من @{parsed.username}",
            "viewer_count": 0,  # unknown without API call
            "room_id": "",  # unknown without Webcast API
            "stream_id": "",
            "share_link_id": share_data.get("share_link_id"),
            "share_region": share_data.get("share_region"),
            "shared_at_timestamp": share_data.get("timestamp"),
        }

    res.raw_keys.append("share_link_params")
    res.raw_json = {"share_data": share_data}
    return res


def _enrich_with_html(result: ExtractionResult, session: requests.Session,
                       parsed: ParsedLink, timeout: int = 15) -> None:
    """يضيف بيانات sec_uid / csrf_token / wid من HTML إن أمكن.
    لا يستبدل البيانات الحالية، فقط يملأ الفراغات."""
    if result.all_ids.get("sec_uid") and result.all_ids.get("csrf_token"):
        return  # already enriched

    target = parsed.final_url or parsed.normalized
    try:
        ua = USER_AGENTS[0]
        r = session.get(target, timeout=timeout, headers={"User-Agent": ua})
        if r.status_code >= 400:
            return
        # Skip if geo-blocked
        if re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", r.url or "", re.I):
            return

        soup = BeautifulSoup(r.text or "", "lxml")
        uni = extract_universal_data(soup)
        if not uni:
            return
        app_ctx = (uni.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.app-context", {})
        if app_ctx:
            if not result.all_ids.get("csrf_token"):
                result.all_ids["csrf_token"] = app_ctx.get("csrfToken")
            if not result.all_ids.get("wid"):
                result.all_ids["wid"] = app_ctx.get("wid")
            if not result.all_ids.get("nonce"):
                result.all_ids["nonce"] = app_ctx.get("nonce")
            if not result.all_ids.get("request_id"):
                result.all_ids["request_id"] = app_ctx.get("requestId")
            if not result.all_ids.get("encrypted_webid"):
                result.all_ids["encrypted_webid"] = app_ctx.get("encryptedWebid")
            if not result.all_ids.get("region"):
                result.all_ids["region"] = app_ctx.get("region")
            result.raw_keys.append("enriched_from_html")
    except Exception as e:
        logger.debug(f"enrich_with_html failed (non-fatal): {e}")


# ────────────────────────────────────────────────────────────────────────────
#  دالة الإثراء 1: استخراج معطيات رابط المشاركة (تعمل دائماً)
# ────────────────────────────────────────────────────────────────────────────
def _enrich_with_share_params(result: ExtractionResult, url: str) -> None:
    """يضيف مُعرّفات رابط المشاركة إلى result.all_ids حتى لو نجح yt-dlp.

    يستخرج: sec_user_id, user_id, share_from_user_id, share_region,
    share_app_id, share_link_id, share_enter_from, social_share_type,
    source, timestamp, ugbiz_name, utm_*, checksum.
    """
    try:
        share_data = _extract_share_link_params(url)
        if not share_data:
            return

        # sec_uid from URL params (often the most critical ID)
        if share_data.get("sec_user_id") and not result.author.sec_uid:
            result.author.sec_uid = share_data["sec_user_id"]

        # Merge all share params into all_ids
        for k, v in share_data.items():
            if v and not result.all_ids.get(k):
                result.all_ids[k] = v

        # Also add sec_uid explicitly
        if share_data.get("sec_user_id"):
            result.all_ids["sec_uid"] = share_data["sec_user_id"]

        result.raw_keys.append("share_link_params")
        logger.info(f"share-link enrichment: +{len(share_data)} params "
                    f"(sec_uid={'✓' if share_data.get('sec_user_id') else '✗'}, "
                    f"region={share_data.get('share_region', '?')})")
    except Exception as e:
        logger.debug(f"_enrich_with_share_params failed (non-fatal): {e}")


# ────────────────────────────────────────────────────────────────────────────
#  دالة الإثراء 2: Webcast API مباشرة (بدون X-Bogus)
# ────────────────────────────────────────────────────────────────────────────
def _enrich_with_webcast(result: ExtractionResult, session: requests.Session,
                         parsed: ParsedLink) -> None:
    """يحاول استدعاء Webcast API مباشرةً (بدون X-Bogus) لإثراء بيانات البث.

    يملأ: stream_id, viewer_count (real-time), like_count, diamond_count,
    owner.follower_count, owner.following_count, top_donors, gift_boxes.

    KEY INSIGHT: webcast.tiktok.com/webcast/room/info/?room_id=XXX works
    from any IP (no geo-block, no X-Bogus needed). The /room/page/info/
    endpoint with unique_id returns 10013 — we use room_id instead.
    """
    ua = USER_AGENTS[0]

    # Build candidate endpoints — room_id endpoint is proven to work (yt-dlp uses it)
    endpoints = []

    # PRIORITY 1: room/info with room_id (proven working — yt-dlp uses this)
    room_id = result.all_ids.get("room_id") or (result.live.get("room_id") if result.live else None)
    if room_id:
        endpoints.append((
            "https://webcast.tiktok.com/webcast/room/info/",
            {"room_id": room_id, "aid": "1988"},
        ))

    # PRIORITY 2: room/page/info with unique_id (often returns 10013 but try anyway)
    if parsed.username:
        endpoints.append((
            "https://webcast.tiktok.com/webcast/room/page/info/",
            {"unique_id": parsed.username, "device_platform": "web",
             "aid": "1988", "channel": "channel_unknown",
             "app_language": "en", "web_rhd": "1"},
        ))

    # PRIORITY 3: room/info with user_id (sometimes works)
    user_id = result.author.user_id or result.all_ids.get("user_id")
    if user_id:
        endpoints.append((
            "https://webcast.tiktok.com/webcast/room/info/",
            {"user_id": user_id, "aid": "1988"},
        ))

    for base_url, params in endpoints:
        try:
            url = base_url + "?" + urlencode(params)
            r = session.get(url, headers={"User-Agent": ua}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("status_code") != 0:
                logger.debug(f"Webcast direct {base_url.split('/')[-2]}: "
                             f"status_code={data.get('status_code')}")
                continue

            # Success! Enrich the result
            # The room/info endpoint returns data directly (owner, stats, stream_url)
            # The room/page/info endpoint returns nested (owner, stats, live_room)
            room_data = data.get("data") or {}
            owner = room_data.get("owner") or {}
            stats = room_data.get("stats") or {}
            # For room/info endpoint, the room data IS the top-level data
            # For room/page/info, it's nested under live_room
            live_room = room_data.get("live_room") or room_data

            # Author enrichment
            if owner.get("unique_id") or owner.get("display_id"):
                if not result.author.unique_id:
                    result.author.unique_id = owner.get("unique_id") or owner.get("display_id")
            if owner.get("nickname") and (not result.author.nickname or result.author.nickname == result.author.unique_id):
                result.author.nickname = owner.get("nickname")
            if owner.get("id") and not result.author.user_id:
                result.author.user_id = str(owner.get("id"))
            if owner.get("sec_uid") and not result.author.sec_uid:
                result.author.sec_uid = owner.get("sec_uid")
            if owner.get("signature") and not result.author.signature:
                result.author.signature = owner.get("signature")
            if owner.get("follower_count"):
                result.author.follower_count = owner.get("follower_count")
            if owner.get("following_count"):
                result.author.following_count = owner.get("following_count")
            if owner.get("verified") is not None:
                result.author.verified = bool(owner.get("verified"))

            # Avatar — try all sizes
            for avatar_key in ("avatar_thumb", "avatar_medium", "avatar_large"):
                avatar = owner.get(avatar_key)
                if avatar and isinstance(avatar, dict):
                    urls = avatar.get("url_list") or []
                    if urls and not result.author.avatar:
                        result.author.avatar = urls[0]
                        break

            # Live enrichment
            if result.live:
                # Stream ID
                if live_room.get("stream_id"):
                    result.live["stream_id"] = str(live_room.get("stream_id"))
                elif room_data.get("stream_id"):
                    result.live["stream_id"] = str(room_data.get("stream_id"))

                # Stats from the stats object
                if stats.get("total_user"):
                    result.live["viewer_count"] = stats.get("total_user")
                if stats.get("like_count") is not None:
                    result.live["like_count"] = stats.get("like_count")
                if stats.get("digg_count") is not None:
                    result.live["digg_count"] = stats.get("digg_count")
                if stats.get("enter_count"):
                    result.live["enter_count"] = stats.get("enter_count")
                if stats.get("gift_uv_count") is not None:
                    result.live["gift_uv_count"] = stats.get("gift_uv_count")
                if stats.get("share_count") is not None:
                    result.live["share_count"] = stats.get("share_count")
                if stats.get("room_follow_count") is not None:
                    result.live["room_follow_count"] = stats.get("room_follow_count")
                if stats.get("replay_viewers") is not None:
                    result.live["replay_viewers"] = stats.get("replay_viewers")
                if stats.get("total_user_desp"):
                    result.live["unique_viewers"] = stats.get("total_user_desp")
                if stats.get("user_count_composition"):
                    result.live["user_count_composition"] = stats.get("user_count_composition")

                # Room metadata
                if live_room.get("create_time") or room_data.get("create_time"):
                    result.live["start_time"] = live_room.get("create_time") or room_data.get("create_time")
                if live_room.get("title") or room_data.get("title"):
                    result.live["title"] = live_room.get("title") or room_data.get("title")
                if live_room.get("cover") or room_data.get("cover"):
                    cover = live_room.get("cover") or room_data.get("cover")
                    if isinstance(cover, dict):
                        urls = cover.get("url_list") or []
                        if urls:
                            result.live["cover"] = urls[0]
                if live_room.get("status") is not None:
                    result.live["status_code"] = live_room.get("status")
                if live_room.get("rank") or room_data.get("rank"):
                    result.live["rank_text"] = str(live_room.get("rank") or room_data.get("rank"))
                if room_data.get("follow_status") is not None:
                    result.live["follow_status"] = room_data.get("follow_status")
                if room_data.get("gift_boxes"):
                    result.live["gift_boxes"] = room_data.get("gift_boxes")
                if room_data.get("top_donors"):
                    result.live["top_donors"] = room_data.get("top_donors")
                if room_data.get("recent_donors"):
                    result.live["recent_donors"] = room_data.get("recent_donors")
                if room_data.get("link_mic"):
                    result.live["link_mic"] = room_data.get("link_mic")
                if room_data.get("decoration"):
                    result.live["decoration"] = room_data.get("decoration")
                if room_data.get("commerce_info"):
                    result.live["commerce_info"] = room_data.get("commerce_info")
                if room_data.get("content_tag"):
                    result.live["content_tag"] = room_data.get("content_tag")

                # Stream URLs (full structure from room/info)
                stream_url = live_room.get("stream_url") or room_data.get("stream_url")
                if stream_url and isinstance(stream_url, dict):
                    # Extract all stream URL types
                    stream_urls_enriched = {}
                    # HLS pull URL
                    hls_url = stream_url.get("hls_pull_url")
                    if hls_url:
                        stream_urls_enriched["hls_pull_url"] = hls_url
                    # RTMP pull URL
                    rtmp_url = stream_url.get("rtmp_pull_url")
                    if rtmp_url:
                        stream_urls_enriched["rtmp_pull_url"] = rtmp_url
                    # FLV pull URLs
                    flv_urls = stream_url.get("flv_pull_url") or {}
                    if isinstance(flv_urls, dict):
                        for fmt_id, url in flv_urls.items():
                            stream_urls_enriched[f"flv_{fmt_id.lower()}"] = url
                    # Complete push URLs
                    if stream_url.get("complete_push_urls"):
                        stream_urls_enriched["complete_push_urls"] = stream_url.get("complete_push_urls")
                    # Live core SDK data
                    if stream_url.get("live_core_sdk_data"):
                        stream_urls_enriched["live_core_sdk_data"] = stream_url.get("live_core_sdk_data")
                    # Resolution info
                    for res_key in ("default_resolution", "candidate_resolution", "push_resolution", "resolution_name"):
                        if stream_url.get(res_key):
                            stream_urls_enriched[res_key] = stream_url.get(res_key)
                    # Stream dimensions
                    for dim_key in ("stream_size_width", "stream_size_height"):
                        if stream_url.get(dim_key) is not None:
                            stream_urls_enriched[dim_key] = stream_url.get(dim_key)

                    result.live["webcast_stream_urls"] = stream_urls_enriched
                    result.live["stream_url_full"] = stream_url

                # Update stats
                result.stats.play_count = result.live.get("viewer_count") or result.stats.play_count
                if stats.get("like_count") is not None:
                    result.stats.digg_count = stats.get("like_count")
                if stats.get("comment_count") is not None:
                    result.stats.comment_count = stats.get("comment_count")
                if stats.get("share_count") is not None:
                    result.stats.share_count = stats.get("share_count")
                if stats.get("digg_count") is not None:
                    result.stats.digg_count = stats.get("digg_count")

            # Store the full webcast response for reference
            result.raw_json = result.raw_json or {}
            result.raw_json["webcast_room_info"] = data

            # Merge IDs
            if room_data.get("room_id") or live_room.get("room_id"):
                result.all_ids["room_id"] = str(room_data.get("room_id") or live_room.get("room_id"))
            if live_room.get("stream_id") or room_data.get("stream_id"):
                result.all_ids["stream_id"] = str(live_room.get("stream_id") or room_data.get("stream_id"))

            # Extract owner stats — Webcast API nests these under follow_info
            follow_info = owner.get("follow_info") or {}
            if follow_info.get("follower_count") is not None:
                result.author.follower_count = follow_info.get("follower_count")
            elif owner.get("follower_count") is not None:
                result.author.follower_count = owner.get("follower_count")
            if follow_info.get("following_count") is not None:
                result.author.following_count = follow_info.get("following_count")
            elif owner.get("following_count") is not None:
                result.author.following_count = owner.get("following_count")

            # Extract owner bio_description (signature)
            if owner.get("bio_description") and not result.author.signature:
                result.author.signature = owner.get("bio_description")

            # Extract owner's own_room (room_ids list)
            own_room = owner.get("own_room") or {}
            if own_room.get("room_ids_str"):
                result.all_ids["owner_room_ids"] = own_room.get("room_ids_str")

            # Extract top_fans from data (NOT from owner — top_fans is at data level)
            top_fans = room_data.get("top_fans") or []
            if top_fans:
                result.donor_rankings = [{
                    "rank": i + 1,
                    "user_id": str(fan.get("user", {}).get("id") or fan.get("user_id") or ""),
                    "nickname": fan.get("user", {}).get("nickname"),
                    "unique_id": fan.get("user", {}).get("display_id") or fan.get("user", {}).get("unique_id"),
                    "sec_uid": fan.get("user", {}).get("sec_uid"),
                    "fan_ticket_count": fan.get("user", {}).get("fan_ticket_count"),
                    "follower_count": (fan.get("user", {}).get("follow_info") or {}).get("follower_count"),
                    "avatar": ((fan.get("user", {}).get("avatar_thumb") or {}).get("url_list") or [None])[0],
                } for i, fan in enumerate(top_fans)]
                if result.live:
                    result.live["top_donors"] = result.donor_rankings
                    result.live["top_fans"] = top_fans  # full raw data

            # Extract pay_grade (subscriber level info)
            pay_grade = owner.get("pay_grade") or {}
            if pay_grade:
                if result.live:
                    result.live["owner_pay_grade"] = {
                        "level": pay_grade.get("level"),
                        "name": pay_grade.get("grade_name"),
                        "icon": ((pay_grade.get("icon") or {}).get("url_list") or [None])[0],
                    }

            # Extract link_mic data
            link_mic = room_data.get("link_mic") or {}
            if link_mic and result.live:
                result.live["link_mic"] = {
                    "channel_id": link_mic.get("channel_id"),
                    "followed_count": link_mic.get("followed_count"),
                    "battle_settings": link_mic.get("battle_settings"),
                    "cohost_anchors_hash": link_mic.get("cohost_anchors_hash"),
                }

            # Extract commerce_info
            commerce_info = room_data.get("commerce_info") or {}
            if commerce_info and result.live:
                result.live["commerce_info"] = commerce_info

            # Extract share_url
            share_url = room_data.get("share_url")
            if share_url:
                result.all_ids["share_url"] = share_url

            # Extract feed_room_label
            feed_label = room_data.get("feed_room_label") or {}
            if feed_label and isinstance(feed_label, dict):
                urls = feed_label.get("url_list") or []
                if urls and result.live:
                    result.live["feed_room_label_url"] = urls[0]

            result.raw_keys.append("webcast_api_direct")
            logger.info(f"✅ Webcast API enrichment succeeded: "
                        f"viewer_count={result.live.get('viewer_count') if result.live else 'N/A'}, "
                        f"like_count={result.live.get('like_count') if result.live else 'N/A'}, "
                        f"enter_count={result.live.get('enter_count') if result.live else 'N/A'}, "
                        f"owner.followers={result.author.follower_count}, "
                        f"top_fans={len(top_fans)}")
            return  # Success, don't try the next endpoint

        except Exception as e:
            logger.debug(f"Webcast direct endpoint failed: {e}")
            continue

    logger.debug("Webcast API direct enrichment: all endpoints failed")


# ════════════════════════════════════════════════════════════════════════════
#  v3.1 Enhanced Enrichment: Gifts, Rankings, Donations, User Detail,
#  Token Decoding, CDN/Avatar Metadata, Security Analysis
# ════════════════════════════════════════════════════════════════════════════

def _enrich_with_webcast_gifts(result: ExtractionResult, session: requests.Session,
                                parsed: ParsedLink) -> None:
    """يستدعي Webcast API لقائمة الهدايا المتاحة في الغرفة.

    يملأ: result.gift_list[] بكل هدية (type, level, rarity, diamond_count, icon_url)
    """
    if not result.live or not result.live.get("room_id"):
        return

    ua = USER_AGENTS[0]
    room_id = result.live["room_id"]

    endpoints = [
        # Gift list endpoint
        ("https://webcast.tiktok.com/webcast/gift/list/",
         {"room_id": room_id, "aid": "1988", "device_platform": "web", "app_language": "en"}),
        # Donation list endpoint (recent donations)
        ("https://webcast.tiktok.com/webcast/donation/list/",
         {"room_id": room_id, "aid": "1988", "device_platform": "web", "app_language": "en", "count": "50"}),
        # Rank list endpoint (top donors)
        ("https://webcast.tiktok.com/webcast/rank/list/",
         {"room_id": room_id, "aid": "1988", "device_platform": "web", "app_language": "en", "rank_type": "1"}),
    ]

    for base_url, params in endpoints:
        try:
            url = base_url + "?" + urlencode(params)
            r = session.get(url, headers={"User-Agent": ua}, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("status_code") != 0:
                continue

            endpoint_name = base_url.split("/")[-2]

            if endpoint_name == "gift":
                gifts = data.get("data", {}).get("gifts") or []
                if gifts:
                    result.gift_list = [{
                        "id": g.get("id"),
                        "name": g.get("name") or g.get("describe"),
                        "diamond_count": g.get("diamond_count"),
                        "type": g.get("type"),
                        "level": g.get("level"),
                        "rarity": g.get("rarity"),
                        "icon_url": (g.get("icon", {}).get("url_list") or [None])[0] if isinstance(g.get("icon"), dict) else g.get("icon_url"),
                    } for g in gifts]
                    result.raw_keys.append("webcast_gifts")
                    logger.info(f"✅ Gifts enrichment: {len(result.gift_list)} gifts found")

            elif endpoint_name == "donation":
                donations = data.get("data", {}).get("donations") or []
                if donations and result.live:
                    result.live["recent_donors"] = [{
                        "user_id": d.get("user_id"),
                        "nickname": d.get("nickname"),
                        "unique_id": d.get("unique_id"),
                        "gift_id": d.get("gift_id"),
                        "gift_name": d.get("gift_name"),
                        "diamond_count": d.get("diamond_count"),
                        "timestamp": d.get("create_time"),
                    } for d in donations]
                    result.raw_keys.append("webcast_donations")
                    logger.info(f"✅ Donations enrichment: {len(result.live['recent_donors'])} recent donors")

            elif endpoint_name == "rank":
                ranks = data.get("data", {}).get("ranks") or data.get("data", {}).get("user_list") or []
                if ranks:
                    result.donor_rankings = [{
                        "rank": r.get("rank") or i + 1,
                        "user_id": r.get("user_id"),
                        "nickname": r.get("nickname"),
                        "unique_id": r.get("unique_id"),
                        "score": r.get("score"),
                        "diamond_count": r.get("diamond_count") or r.get("score"),
                        "avatar": (r.get("avatar", {}).get("url_list") or [None])[0] if isinstance(r.get("avatar"), dict) else r.get("avatar_url"),
                    } for i, r in enumerate(ranks)]
                    if result.live:
                        result.live["top_donors"] = result.donor_rankings
                    result.raw_keys.append("webcast_rank")
                    logger.info(f"✅ Rank enrichment: {len(result.donor_rankings)} top donors")

        except Exception as e:
            logger.debug(f"Webcast gifts/rank endpoint failed: {e}")
            continue


def _enrich_with_user_detail_api(result: ExtractionResult, session: requests.Session,
                                  parsed: ParsedLink) -> None:
    """يستدعي /api/v1/user/detail/ للحصول على follower_count, following_count, إلخ.

    يحاول عدة صيغ: unique_id, sec_user_id, user_id
    """
    ua = USER_AGENTS[0]
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id")
    user_id = result.author.user_id or result.all_ids.get("user_id")
    unique_id = result.author.unique_id or parsed.username

    # Build candidate API URLs
    candidates = []
    if sec_uid:
        candidates.append(f"https://www.tiktok.com/api/v1/user/detail/?sec_user_id={sec_uid}&aid=1988&device_platform=web")
    if user_id:
        candidates.append(f"https://www.tiktok.com/api/v1/user/detail/?user_id={user_id}&aid=1988&device_platform=web")
    if unique_id:
        candidates.append(f"https://www.tiktok.com/api/v1/user/detail/?unique_id={unique_id}&aid=1988&device_platform=web")
    # Also try the m.tiktok.com mobile API (often less restricted)
    if sec_uid:
        candidates.append(f"https://m.tiktok.com/api/user/detail/?sec_uid={sec_uid}&aid=1988")

    for url in candidates:
        try:
            r = session.get(url, headers={
                "User-Agent": ua,
                "Referer": f"https://www.tiktok.com/@{unique_id}" if unique_id else "https://www.tiktok.com/",
            }, timeout=15)
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue

            # TikTok user detail API returns: {"userInfo": {"user": {...}, "stats": {...}}}
            user_info = data.get("userInfo") or data.get("user_info") or data
            user = user_info.get("user") or user_info
            stats = user_info.get("stats") or {}

            if not user and not stats:
                continue

            # Enrich author
            if user.get("uniqueId") and not result.author.unique_id:
                result.author.unique_id = user.get("uniqueId")
            if user.get("nickname") and not result.author.nickname:
                result.author.nickname = user.get("nickname")
            if user.get("id") and not result.author.user_id:
                result.author.user_id = str(user.get("id"))
            if user.get("secUid") and not result.author.sec_uid:
                result.author.sec_uid = user.get("secUid")
            if user.get("signature") and not result.author.signature:
                result.author.signature = user.get("signature")
            if user.get("verified") is not None:
                result.author.verified = bool(user.get("verified"))
            if user.get("avatarLarger"):
                avatar = user.get("avatarLarger")
                if isinstance(avatar, dict):
                    urls = avatar.get("url_list") or []
                    if urls and not result.author.avatar:
                        result.author.avatar = urls[0]
                elif isinstance(avatar, str) and not result.author.avatar:
                    result.author.avatar = avatar

            # Enrich stats
            if stats.get("followerCount"):
                result.author.follower_count = stats.get("followerCount")
            if stats.get("followingCount"):
                result.author.following_count = stats.get("followingCount")
            if stats.get("heart") or stats.get("likeCount"):
                result.author.like_count = stats.get("heart") or stats.get("likeCount")
            if stats.get("videoCount"):
                result.author.video_count = stats.get("videoCount")

            result.raw_keys.append("user_detail_api")
            logger.info(f"✅ User detail enrichment: followers={result.author.follower_count}, "
                        f"following={result.author.following_count}, likes={result.author.like_count}, "
                        f"videos={result.author.video_count}")
            return  # Success

        except Exception as e:
            logger.debug(f"User detail API failed: {e}")
            continue


def _decode_sensitive_tokens(result: ExtractionResult, url: str) -> None:
    """يفك تشفير التوكنات الحساسة ويصنفها أمنياً.

    يفك:
    - _d: base64-encoded session token
    - expire: Unix epoch → human-readable datetime
    - sign: MD5 signature hash
    - checksum: SHA-256 hash
    - timestamp: Unix epoch → datetime
    - x-expires (from avatar): epoch → datetime
    - x-signature (from avatar): signature token
    """
    import base64 as b64mod  # noqa: F811
    from datetime import datetime as dt  # noqa: F811

    decoded = {}
    security = {}

    # ─── 1. _d token (session encryption) ────────────────────────────
    d_token = result.all_ids.get("_d") or ""
    if d_token:
        decoded["_d"] = {
            "raw": d_token[:50] + "..." if len(d_token) > 50 else d_token,
            "raw_length": len(d_token),
        }
        # Try base64 decode (URL-decode first)
        try:
            from urllib.parse import unquote  # noqa: F811
            url_decoded = unquote(d_token)
            b64_decoded = b64mod.b64decode(url_decoded)
            decoded["_d"]["base64_decoded_hex"] = b64_decoded.hex()[:100] + "..."
            decoded["_d"]["base64_decoded_length"] = len(b64_decoded)
            # Look for readable strings inside
            readable = re.findall(rb'[\x20-\x7e]{4,}', b64_decoded)
            if readable:
                decoded["_d"]["readable_strings"] = [r.decode('ascii', errors='replace') for r in readable[:5]]
        except Exception:
            decoded["_d"]["base64_decode"] = "failed (not valid base64)"

        security["_d"] = {
            "sensitivity": "critical",
            "classification": "session_encryption_token",
            "description": "توكن جلسة مشفّر تستخدمه TikTok للتحقق من أن الطلب جاء من مشاركة رسمية. "
                           "يمكن استخدامه لفتح نفس الرابط كأنه نفس المستخدم الأصلي.",
            "risk": "identity_impersonation + tracking",
            "expires": "لا ينتهي (مرتبط بالجلسة)",
        }

    # ─── 2. expire tokens (from stream URLs) ──────────────────────────
    expire_times = {}
    if result.live and result.live.get("stream_urls"):
        for fmt_id, fmt_data in result.live["stream_urls"].items():
            stream_url = fmt_data.get("url") or ""
            m = re.search(r'[?&]expire=(\d+)', stream_url)
            if m:
                expire_epoch = int(m.group(1))
                expire_dt = dt.utcfromtimestamp(expire_epoch).isoformat() + "Z"
                expire_times[fmt_id] = {
                    "epoch": expire_epoch,
                    "datetime": expire_dt,
                    "remaining_seconds": expire_epoch - int(time.time()),
                }
    if result.live and result.live.get("primary_url"):
        m = re.search(r'[?&]expire=(\d+)', result.live["primary_url"])
        if m:
            expire_epoch = int(m.group(1))
            decoded["stream_expire"] = {
                "epoch": expire_epoch,
                "datetime": dt.utcfromtimestamp(expire_epoch).isoformat() + "Z",
                "remaining_seconds": expire_epoch - int(time.time()),
                "remaining_human": f"{(expire_epoch - int(time.time())) // 3600}h {((expire_epoch - int(time.time())) % 3600) // 60}m",
            }

    if expire_times:
        decoded["stream_expires_per_format"] = expire_times
        security["stream_expire"] = {
            "sensitivity": "high",
            "classification": "stream_access_expiry",
            "description": "وقت انتهاء صلاحية روابط البث. بعد هذا الوقت، الرابط يبطل ولا يمكن الوصول للبث.",
            "risk": "stream_access (time-limited)",
        }

    # ─── 3. sign tokens (from stream URLs) ────────────────────────────
    sign_hashes = {}
    if result.live and result.live.get("stream_urls"):
        for fmt_id, fmt_data in result.live["stream_urls"].items():
            stream_url = fmt_data.get("url") or ""
            m = re.search(r'[?&]sign=([a-f0-9]+)', stream_url)
            if m:
                sign_hashes[fmt_id] = m.group(1)
    if sign_hashes:
        decoded["stream_signs"] = sign_hashes
        security["stream_sign"] = {
            "sensitivity": "critical",
            "classification": "stream_access_signature",
            "description": "توقيع MD5 لروابط البث. أي حد معاه الرابط + التوقيع يقدر يدخل اللايف أو يحمله "
                           "حتى لو اللايف خاص. البث المباشر يمكن سحبه كاملاً ونشره.",
            "risk": "stream_theft + unauthorized_download",
            "hash_type": "MD5 (32 hex chars)",
        }

    # ─── 4. checksum ──────────────────────────────────────────────────
    checksum = result.all_ids.get("checksum") or ""
    if checksum:
        decoded["checksum"] = {
            "raw": checksum,
            "length": len(checksum),
            "hash_type": "SHA-256" if len(checksum) == 64 else "unknown",
        }
        security["checksum"] = {
            "sensitivity": "medium",
            "classification": "integrity_verification_hash",
            "description": "هاش SHA-256 يستخدم للتحقق من سلامة الرابط. لو تغير حرف واحد الرابط يخرب. "
                           "وجوده يكشف أن المحتوى من مشاركة TikTok رسمية.",
            "risk": "metadata_leak (confirms share origin)",
        }

    # ─── 5. timestamp (share creation time) ──────────────────────────
    ts = result.all_ids.get("timestamp") or ""
    if ts:
        try:
            ts_int = int(ts)
            decoded["share_timestamp"] = {
                "epoch": ts_int,
                "datetime": dt.utcfromtimestamp(ts_int).isoformat() + "Z",
                "age_seconds": int(time.time()) - ts_int,
                "age_human": f"{(int(time.time()) - ts_int) // 3600}h {((int(time.time()) - ts_int) % 3600) // 60}m ago",
            }
        except (ValueError, TypeError):
            pass

    # ─── 6. sec_uid / sec_user_id ────────────────────────────────────
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id")
    if sec_uid:
        decoded["sec_uid"] = {
            "raw": sec_uid,
            "length": len(sec_uid),
            "prefix": sec_uid[:20] + "...",
        }
        security["sec_uid"] = {
            "sensitivity": "critical",
            "classification": "identity_tracking_token",
            "description": "بطاقة الهوية المشفّرة للحساب. يستخدمه TikTok داخلياً لتتبع الحساب. "
                           "لو تسرب = كشف الهوية. يمكن استخدامه لجلب كل بيانات الحساب عبر API.",
            "risk": "identity_tracking + impersonation + cross_account_linking",
            "format": "MS4wLjAB... (base64-encoded user reference)",
        }

    # ─── 7. room_id + stream_id ──────────────────────────────────────
    room_id = result.all_ids.get("room_id")
    stream_id = result.all_ids.get("stream_id")
    if room_id and stream_id:
        security["room_stream_ids"] = {
            "sensitivity": "high",
            "classification": "direct_room_access",
            "description": "عنوان الغرفة في سيرفرات TikTok. بهما يمكن الوصول للبث مباشرةً عبر Webcast API "
                           "بدون المرور بواجهة TikTok.",
            "risk": "direct_stream_access (bypasses UI restrictions)",
            "room_id": room_id,
            "stream_id": stream_id,
        }

    # ─── 8. share_link_id ───────────────────────────────────────────
    share_link_id = result.all_ids.get("share_link_id")
    if share_link_id:
        security["share_link_id"] = {
            "sensitivity": "medium",
            "classification": "share_tracking_uuid",
            "description": "مُعرّف فريد لكل مشاركة (UUID v4). يكشف مَن شارك الرابط ومن أين (share_region).",
            "risk": "share_attribution + geographic_tracking",
            "uuid": share_link_id,
            "share_region": result.all_ids.get("share_region"),
        }

    result.decoded_tokens = decoded
    result.security_analysis = security
    if decoded or security:
        result.raw_keys.append("decoded_tokens")
        logger.info(f"✅ Token decoding: {len(decoded)} tokens decoded, "
                    f"{len(security)} classified by sensitivity")


def _extract_cdn_metadata(result: ExtractionResult) -> None:
    """يستخرج بيانات CDN من روابط البث (datacenter, quality, region, protocol).

    يحلل: pull-hls-q5-sg01.tiktokcdn.com/stage/stream-XXX_hd/index.m3u8
    - pull / pull-hls: protocol (FLV vs HLS)
    - q5: quality tier
    - sg01: datacenter (Singapore 01)
    - stage: staging area
    - stream-XXX: stream ID
    - _hd: HD variant
    - .flv / .m3u8: container format
    """
    if not result.live or not result.live.get("stream_urls"):
        return

    cdn = {}
    primary_url = result.live.get("primary_url") or ""
    if not primary_url:
        return

    parsed = urlparse(primary_url)
    host = parsed.hostname or ""
    path = parsed.path or ""

    cdn["primary_host"] = host
    cdn["primary_path"] = path

    # Parse host: pull-hls-q5-sg01.tiktokcdn.com
    # Parts: [pull, hls, q5, sg01] or [pull, q5, sg01]
    host_parts = host.split(".")[0].split("-")  # ["pull", "hls", "q5", "sg01"]
    cdn["host_parts"] = host_parts

    if "hls" in host_parts:
        cdn["protocol"] = "HLS (m3u8)"
    elif "flv" in host_parts:
        cdn["protocol"] = "FLV (RTMP over HTTPS)"
    elif "pull" in host_parts:
        cdn["protocol"] = "pull (HTTPS)"

    # Quality tier (q5, q1, etc.)
    for part in host_parts:
        if re.match(r'^q\d+$', part):
            cdn["quality_tier"] = part
            break

    # Datacenter
    for part in host_parts:
        if re.match(r'^[a-z]{2}\d+$', part):
            dc_map = {"sg": "Singapore", "va": "Virginia (US East)",
                      "use": "US East", "usw": "US West",
                      "eu": "Europe", "my": "Malaysia", "jp": "Japan"}
            region_code = re.match(r'^([a-z]+)', part).group(1)
            cdn["datacenter_code"] = part
            cdn["datacenter_region"] = dc_map.get(region_code, f"Unknown ({region_code})")
            break

    # Path analysis: /stage/stream-XXX_hd/index.m3u8
    if "/stage/" in path:
        cdn["staging"] = True
    if "_hd" in path:
        cdn["quality_variant"] = "HD"
    elif "_sd" in path:
        cdn["quality_variant"] = "SD"
    else:
        cdn["quality_variant"] = "standard"

    # Stream ID from path
    m = re.search(r'stream-(\d+)', path)
    if m:
        cdn["cdn_stream_id"] = m.group(1)

    # Container format from extension
    if path.endswith(".m3u8"):
        cdn["container_format"] = "HLS (m3u8 playlist)"
    elif path.endswith(".flv"):
        cdn["container_format"] = "FLV"
    elif ".mp4" in path:
        cdn["container_format"] = "MP4"

    # Count available formats
    cdn["total_formats_available"] = len(result.live.get("stream_urls") or {})

    # Best quality format
    best = None
    best_quality = -999
    for fmt_id, fmt_data in (result.live.get("stream_urls") or {}).items():
        q = fmt_data.get("quality") or 0
        if q > best_quality:
            best_quality = q
            best = fmt_id
    if best:
        cdn["best_quality_format"] = best

    # Audio-only format
    audio_fmts = [fmt_id for fmt_id, fmt_data in (result.live.get("stream_urls") or {}).items()
                  if "only_audio=1" in (fmt_data.get("url") or "")]
    if audio_fmts:
        cdn["audio_only_formats"] = audio_fmts

    # Collect all unique sign + expire pairs
    signs = set()
    expires = set()
    for fmt_data in (result.live.get("stream_urls") or {}).values():
        url = fmt_data.get("url") or ""
        m_sign = re.search(r'sign=([a-f0-9]+)', url)
        m_expire = re.search(r'expire=(\d+)', url)
        if m_sign:
            signs.add(m_sign.group(1))
        if m_expire:
            expires.add(m_expire.group(1))
    cdn["unique_signs"] = list(signs)
    cdn["unique_expires"] = list(expires)

    result.cdn_metadata = cdn
    result.raw_keys.append("cdn_metadata")
    logger.info(f"✅ CDN metadata: datacenter={cdn.get('datacenter_code','?')}, "
                f"quality_tier={cdn.get('quality_tier','?')}, "
                f"formats={cdn.get('total_formats_available')}")


def _extract_avatar_metadata(result: ExtractionResult) -> None:
    """يستخرج بيانات من رابط الصورة الرمزية (x-expires, x-signature, refresh_token, idc).

    يحلل: p16-common-sign.tiktokcdn.com/...?dr=14579&refresh_token=...&x-expires=...&x-signature=...
    """
    avatar_url = result.author.avatar or ""
    if not avatar_url:
        return

    parsed = urlparse(avatar_url)
    params = parse_qs(parsed.query)
    host = parsed.hostname or ""
    path = parsed.path or ""

    avatar_meta = {
        "host": host,
        "cdn_domain": host.split(".")[0] if "." in host else host,
        "path": path,
    }

    # Parse query params
    for k in ("dr", "refresh_token", "x-expires", "x-signature", "t", "ps", "shp", "shcp", "idc"):
        if params.get(k):
            avatar_meta[k] = params[k][0]

    # Decode x-expires (epoch → datetime)
    if avatar_meta.get("x-expires"):
        try:
            from datetime import datetime as dt  # noqa: F811
            exp_epoch = int(avatar_meta["x-expires"])
            avatar_meta["x-expires_datetime"] = dt.utcfromtimestamp(exp_epoch).isoformat() + "Z"
            avatar_meta["x-expires_remaining"] = f"{(exp_epoch - int(time.time())) // 86400}d"
        except (ValueError, TypeError):
            pass

    # IDC (data center)
    if avatar_meta.get("idc"):
        idc_map = {"my": "Malaysia", "sg": "Singapore", "va": "Virginia",
                   "use": "US East", "usw": "US West"}
        avatar_meta["idc_region"] = idc_map.get(avatar_meta["idc"], f"Unknown ({avatar_meta['idc']})")

    # Parse dr (display resolution?)
    if avatar_meta.get("dr"):
        avatar_meta["dr_note"] = "display_resolution_token"

    # CDN host classification
    if "common-sign" in host:
        avatar_meta["cdn_type"] = "common-sign (signed CDN)"
    elif "common" in host:
        avatar_meta["cdn_type"] = "common (unsigned CDN)"

    # Extract the avatar key from path (tos-alisg-avt-0068/...)
    m = re.search(r'/tos-([a-z0-9-]+)/', path)
    if m:
        avatar_meta["tos_bucket"] = m.group(1)
        # alisg = Alibaba Singapore
        bucket_map = {"alisg": "Alibaba Singapore", "alius": "Alibaba US",
                      "alie": "Alibaba Europe", "alimy": "Alibaba Malaysia"}
        bucket_prefix = m.group(1)[:5]
        avatar_meta["tos_bucket_region"] = bucket_map.get(bucket_prefix, f"Unknown ({m.group(1)})")

    result.avatar_metadata = avatar_meta
    result.raw_keys.append("avatar_metadata")
    logger.info(f"✅ Avatar metadata: idc={avatar_meta.get('idc','?')}, "
                f"expires={avatar_meta.get('x-expires_datetime','?')}")


def _build_stream_access_analysis(result: ExtractionResult) -> None:
    """يبني تحليلاً كاملاً لروابط البث — كل رابط + صلاحيته + مستوى الوصول."""
    if not result.live or not result.live.get("stream_urls"):
        return

    from datetime import datetime as dt  # noqa: F811
    access = {
        "total_streams": len(result.live["stream_urls"]),
        "streams": [],
        "best_quality_stream": None,
        "audio_only_stream": None,
        "all_signs_valid_until": None,
    }

    best_quality = -999
    for fmt_id, fmt_data in result.live["stream_urls"].items():
        url = fmt_data.get("url") or ""
        stream = {
            "format_id": fmt_id,
            "format": fmt_data.get("format"),
            "protocol": fmt_data.get("protocol"),
            "ext": fmt_data.get("ext"),
            "quality": fmt_data.get("quality"),
            "resolution": fmt_data.get("resolution"),
            "tbr": fmt_data.get("tbr"),
            "vcodec": fmt_data.get("vcodec"),
            "url": url,
            "is_audio_only": "only_audio=1" in url,
        }

        # Extract sign + expire
        m_sign = re.search(r'sign=([a-f0-9]+)', url)
        m_expire = re.search(r'expire=(\d+)', url)
        if m_sign:
            stream["sign"] = m_sign.group(1)
        if m_expire:
            exp_epoch = int(m_expire.group(1))
            stream["expire_epoch"] = exp_epoch
            stream["expire_datetime"] = dt.utcfromtimestamp(exp_epoch).isoformat() + "Z"
            stream["remaining"] = exp_epoch - int(time.time())
            stream["remaining_human"] = f"{(exp_epoch - int(time.time())) // 3600}h {((exp_epoch - int(time.time())) % 3600) // 60}m"

        access["streams"].append(stream)

        # Track best quality
        q = fmt_data.get("quality") or 0
        if q > best_quality and not stream["is_audio_only"]:
            best_quality = q
            access["best_quality_stream"] = stream

        # Track audio-only
        if stream["is_audio_only"] and not access["audio_only_stream"]:
            access["audio_only_stream"] = stream

    # Extract HTTP headers required for stream access (from yt-dlp format data)
    if result.raw_json and result.raw_json.get("yt_dlp"):
        ytdlp = result.raw_json["yt_dlp"]
        formats = ytdlp.get("formats") or []
        if formats:
            first_format = formats[0]
            http_headers = first_format.get("http_headers") or {}
            if http_headers:
                result.http_headers = http_headers
                access["required_http_headers"] = http_headers

    result.stream_access = access
    result.raw_keys.append("stream_access_analysis")
    logger.info(f"✅ Stream access analysis: {access['total_streams']} streams, "
                f"best={access['best_quality_stream']['format_id'] if access['best_quality_stream'] else 'N/A'}")


# ════════════════════════════════════════════════════════════════════════════
#  v3.2 Deep Analysis: URL, JSON, HTML, Tokens, Stream URLs
# ════════════════════════════════════════════════════════════════════════════

def _analyze_url_deeply(result: ExtractionResult, original_url: str) -> None:
    """تحليل عميق لـ original_url و final_url — يفكك كل param ويصنّفه.

    لكل URL يستخرج:
    - scheme, host, path, query string
    - كل param: مفتاح + قيمة + نوع (id/token/tracking/utm/flag)
    - decoding attempts: URL-decode, base64, hex
    - مقارنة original_url vs final_url (ما الذي تغيّر؟)
    """
    analysis = {
        "original_url": original_url,
        "final_url": result.final_url or original_url,
        "original_url_parts": {},
        "final_url_parts": {},
        "url_diff": {},
        "all_params_classified": {},
    }

    def decompose_url(url):
        if not url:
            return {}
        try:
            parsed = urlparse(url)
            parts = {
                "scheme": parsed.scheme,
                "host": parsed.netloc,
                "path": parsed.path,
                "path_parts": [p for p in parsed.path.split("/") if p],
                "query": parsed.query,
                "fragment": parsed.fragment,
                "params": {},
            }
            # Parse each query param
            for k, v_list in parse_qs(parsed.query).items():
                v = v_list[0] if v_list else ""
                param_info = {
                    "value": v,
                    "value_length": len(v),
                    "url_decoded": unquote(v) if "%" in v else v,
                }
                # Classify the param
                param_info["type"] = _classify_url_param(k, v)
                # Try to decode if it looks encoded
                if v:
                    decoded_attempts = _try_decode_value(v)
                    if decoded_attempts:
                        param_info["decode_attempts"] = decoded_attempts
                parts["params"][k] = param_info
            return parts
        except Exception as e:
            return {"error": str(e)}

    analysis["original_url_parts"] = decompose_url(original_url)
    analysis["final_url_parts"] = decompose_url(result.final_url or original_url)

    # Compare original vs final
    orig_params = analysis["original_url_parts"].get("params", {})
    final_params = analysis["final_url_parts"].get("params", {})
    diff = {
        "params_only_in_original": [k for k in orig_params if k not in final_params],
        "params_only_in_final": [k for k in final_params if k not in orig_params],
        "params_with_different_values": {
            k: {"original": orig_params[k]["value"], "final": final_params[k]["value"]}
            for k in orig_params
            if k in final_params and orig_params[k]["value"] != final_params[k]["value"]
        },
    }
    analysis["url_diff"] = diff

    # Classify all params by type
    all_params = {}
    for k, v in {**orig_params, **final_params}.items():
        all_params[k] = v.get("type", "unknown")
    analysis["all_params_classified"] = all_params

    result.url_analysis = analysis
    result.raw_keys.append("url_analysis")
    logger.info(f"✅ URL analysis: {len(all_params)} params classified, "
                f"{len(diff['params_only_in_original'])} only in original, "
                f"{len(diff['params_only_in_final'])} only in final")


def _classify_url_param(key: str, value: str) -> str:
    """يصنّف param URL حسب نوعه."""
    key_lower = key.lower()

    # ID-type params
    if key in ("user_id", "share_from_user_id", "uploader_id", "channel_id",
                "room_id", "stream_id", "video_id", "music_id"):
        return "numeric_id"
    if key in ("sec_uid", "sec_user_id"):
        return "encrypted_user_id"
    if key == "share_link_id":
        return "uuid_v4"
    if key == "checksum":
        return "sha256_hash"
    if key == "_d":
        return "session_encryption_token"
    if key == "sign":
        return "md5_signature"
    if key == "expire":
        return "unix_timestamp_expiry"
    if key == "timestamp":
        return "unix_timestamp"
    if key == "share_region":
        return "country_code"
    if key == "share_app_id":
        return "app_id"
    if key in ("utm_campaign", "utm_medium", "utm_source"):
        return "utm_tracking"
    if key in ("enter_from_merge", "enter_method", "share_enter_from",
                "social_share_type", "source", "ugbiz_name"):
        return "metadata_flag"
    if key in ("_r", "_svg"):
        return "internal_flag"
    if key == "ug_btm":
        return "ab_test_bucket"
    return "unknown"


def _try_decode_value(value: str) -> Dict[str, str]:
    """يحاول فك تشفير قيمة بطرق متعددة."""
    attempts = {}
    # URL-decode
    if "%" in value:
        try:
            decoded = unquote(value)
            if decoded != value:
                attempts["url_decoded"] = decoded[:200]
        except Exception:
            pass
    # Base64 decode
    try:
        # Add padding if needed
        padded = value + "=" * (4 - len(value) % 4) if len(value) % 4 else value
        b64_decoded = base64.b64decode(padded, validate=False)
        if b64_decoded and all(b < 128 for b in b64_decoded[:20]):
            attempts["base64_decoded"] = b64_decoded.decode('utf-8', errors='replace')[:200]
            attempts["base64_hex"] = b64_decoded.hex()[:200]
    except Exception:
        pass
    # Hex decode
    if re.match(r'^[0-9a-fA-F]+$', value) and len(value) % 2 == 0 and len(value) >= 8:
        try:
            hex_decoded = bytes.fromhex(value)
            if all(b < 128 for b in hex_decoded[:20]):
                attempts["hex_decoded"] = hex_decoded.decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
    return attempts


def _analyze_json_files(result: ExtractionResult) -> None:
    """يحلّل كل JSON مستخرج — يطبّع كل المفاتيح ويصنّف القيم.

    لكل JSON source (yt_dlp, webcast, universal_data):
    - يطبّع nested JSON إلى dot-notation keys
    - يستخرج كل IDs (حقول تنتهي بـ _id, Id, ID)
    - يستخرج كل timestamps (epoch values)
    - يستخرج كل URLs
    - يستخرج كل counts/stats
    - يصنّف كل حقل بالحساسية
    """
    analysis = {
        "sources": {},
        "all_ids_found": {},
        "all_timestamps_found": {},
        "all_urls_found": [],
        "all_counts_found": {},
        "total_fields": 0,
        "sensitive_fields": [],
    }

    raw_json = result.raw_json or {}

    def flatten_json(obj, prefix=""):
        """يطبّع JSON إلى dict من dot-notation keys."""
        items = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    items.update(flatten_json(v, new_key))
                elif isinstance(v, list):
                    items[new_key] = f"[list of {len(v)} items]"
                    for i, item in enumerate(v[:3]):  # First 3 items
                        if isinstance(item, dict):
                            items.update(flatten_json(item, f"{new_key}[{i}]"))
                        else:
                            items[f"{new_key}[{i}]"] = item
                else:
                    items[new_key] = v
        return items

    for source_name, source_data in raw_json.items():
        if not isinstance(source_data, (dict, list)):
            continue
        flat = flatten_json(source_data)
        analysis["sources"][source_name] = {
            "total_fields": len(flat),
            "fields": {},
        }
        analysis["total_fields"] += len(flat)

        for k, v in flat.items():
            field_info = {"value": str(v)[:200] if v is not None else None}
            # Classify
            k_lower = k.lower()
            if k_lower.endswith("_id") or k_lower.endswith("id") or k_lower.endswith("_id]"):
                field_info["type"] = "id"
                analysis["all_ids_found"][k] = v
                # Check if it's a numeric ID
                if isinstance(v, (int, str)) and str(v).isdigit():
                    analysis["sensitive_fields"].append({
                        "field": k,
                        "value": str(v)[:80],
                        "sensitivity": "high",
                        "reason": "numeric_user_or_content_id",
                    })
            elif k_lower.endswith("timestamp") or k_lower.endswith("time") or k_lower.endswith("_at"):
                field_info["type"] = "timestamp"
                if isinstance(v, (int, float)) and v > 1_000_000_000:
                    try:
                        dt_val = datetime.utcfromtimestamp(int(v)).isoformat() + "Z"
                        field_info["datetime"] = dt_val
                        analysis["all_timestamps_found"][k] = {"epoch": v, "datetime": dt_val}
                    except Exception:
                        pass
            elif isinstance(v, str) and v.startswith("http"):
                field_info["type"] = "url"
                analysis["all_urls_found"].append({"field": k, "url": v[:200]})
            elif k_lower.endswith("count") or k_lower.endswith("_count"):
                field_info["type"] = "count"
                analysis["all_counts_found"][k] = v
            elif k_lower in ("sec_uid", "secuserid", "sec_user_id"):
                field_info["type"] = "encrypted_id"
                analysis["sensitive_fields"].append({
                    "field": k,
                    "value": str(v)[:80],
                    "sensitivity": "critical",
                    "reason": "encrypted_user_identity",
                })
            elif k_lower in ("_d", "sign", "checksum", "x-signature"):
                field_info["type"] = "token"
                analysis["sensitive_fields"].append({
                    "field": k,
                    "value": str(v)[:80],
                    "sensitivity": "critical",
                    "reason": "security_token",
                })

            analysis["sources"][source_name]["fields"][k] = field_info

    result.json_analysis = analysis
    result.raw_keys.append("json_analysis")
    logger.info(f"✅ JSON analysis: {analysis['total_fields']} fields across "
                f"{len(analysis['sources'])} sources, "
                f"{len(analysis['all_ids_found'])} IDs, "
                f"{len(analysis['all_timestamps_found'])} timestamps, "
                f"{len(analysis['all_urls_found'])} URLs, "
                f"{len(analysis['sensitive_fields'])} sensitive")


def _analyze_html_content(result: ExtractionResult, session: requests.Session,
                          parsed: ParsedLink) -> None:
    """يحلّل محتوى HTML إذا تمكنّا من جلبه — يستخرج كل المعلومات المخفية.

    يستخرج:
    - جميع وسوم <script> ومحتواها
    - جميع وسوم <meta>
    - جميع سمات data-*
    - متغيرات JavaScript المضمّنة (window.X = ...)
    - نقاط نهاية API المُشار إليها في HTML
    - روابط CDN
    - بكسلات التتبع / beacons
    """
    target = parsed.final_url or parsed.normalized
    ua = USER_AGENTS[0]

    analysis = {
        "html_fetched": False,
        "html_size": 0,
        "scripts": [],
        "meta_tags": {},
        "data_attributes": {},
        "inline_js_vars": {},
        "api_endpoints": [],
        "cdn_urls": [],
        "tracking_pixels": [],
        "universal_data_present": False,
        "sigi_state_present": False,
    }

    try:
        r = session.get(target, timeout=15, headers={"User-Agent": ua})
        if r.status_code != 200:
            result.html_analysis = analysis
            return
        html = r.text or ""
        analysis["html_fetched"] = True
        analysis["html_size"] = len(html)

        # Check if geo-blocked
        if re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", r.url or "", re.I):
            analysis["geo_blocked"] = True
            result.html_analysis = analysis
            return

        soup = BeautifulSoup(html, "lxml")

        # 1. Extract all <script> tags
        for script in soup.find_all("script"):
            src = script.get("src") or ""
            content = (script.string or script.text or "").strip()
            script_id = script.get("id") or ""
            script_info = {
                "id": script_id,
                "src": src[:200] if src else None,
                "type": script.get("type"),
                "content_length": len(content),
                "content_preview": content[:300] if content else None,
            }
            if script_id == "__UNIVERSAL_DATA_FOR_REHYDRATION__":
                script_info["is_universal_data"] = True
                analysis["universal_data_present"] = True
            elif script_id == "SIGI_STATE":
                script_info["is_sigi_state"] = True
                analysis["sigi_state_present"] = True
            analysis["scripts"].append(script_info)

        # 2. Extract all <meta> tags
        for meta in soup.find_all("meta"):
            prop = meta.get("property") or meta.get("name") or meta.get("itemprop") or meta.get("http-equiv")
            content = meta.get("content")
            if prop and content:
                analysis["meta_tags"][prop] = content[:200]

        # 3. Extract all data-* attributes from all elements
        for tag in soup.find_all(attrs=True):
            for attr_name, attr_value in tag.attrs.items():
                if attr_name.startswith("data-"):
                    if attr_name not in analysis["data_attributes"]:
                        analysis["data_attributes"][attr_name] = []
                    val = attr_value[0] if isinstance(attr_value, list) else attr_value
                    if val and val not in analysis["data_attributes"][attr_name]:
                        analysis["data_attributes"][attr_name].append(str(val)[:100])

        # 4. Extract inline JavaScript variables (window.X = ...)
        inline_js_pattern = re.compile(r'window\.([A-Za-z_$][\w$]*)\s*=\s*([^;]{1,200})')
        for match in inline_js_pattern.finditer(html):
            var_name = match.group(1)
            var_value = match.group(2).strip()[:200]
            if var_name not in analysis["inline_js_vars"]:
                analysis["inline_js_vars"][var_name] = var_value

        # 5. Find API endpoints referenced in HTML
        api_patterns = [
            r'https?://[a-z0-9-]+\.tiktok(?:v)?\.com/api/[^\s"\'<>]+',
            r'https?://[a-z0-9-]+\.tiktok(?:cdn)?\.com/[^\s"\'<>]+',
            r'https?://webcast\.tiktok\.com/[^\s"\'<>]+',
            r'https?://[a-z0-9-]+\.ttwstatic\.com/[^\s"\'<>]+',
        ]
        for pattern in api_patterns:
            for m in re.finditer(pattern, html, re.I):
                url = m.group(0)[:300]
                if url not in [a["url"] for a in analysis["api_endpoints"]]:
                    endpoint_type = "api" if "/api/" in url else "cdn" if "cdn" in url else "other"
                    analysis["api_endpoints"].append({
                        "url": url,
                        "type": endpoint_type,
                    })

        # 6. Find CDN URLs (distinct from API endpoints)
        cdn_pattern = re.compile(r'https?://[a-z0-9-]+\.tiktokcdn(?:-[^.]+)?\.com/[^\s"\'<>]+', re.I)
        for m in cdn_pattern.finditer(html):
            url = m.group(0)[:300]
            if url not in analysis["cdn_urls"]:
                analysis["cdn_urls"].append(url)

        # 7. Find tracking pixels / beacons
        tracking_patterns = [
            r'<img[^>]+src=["\']https?://[^"\']*track[^"\']*["\']',
            r'<img[^>]+src=["\']https?://[^"\']*beacon[^"\']*["\']',
            r'<img[^>]+src=["\']https?://[^"\']*analytics[^"\']*["\']',
        ]
        for pattern in tracking_patterns:
            for m in re.finditer(pattern, html, re.I):
                analysis["tracking_pixels"].append(m.group(0)[:300])

        result.html_analysis = analysis
        result.raw_keys.append("html_analysis")
        logger.info(f"✅ HTML analysis: {len(analysis['scripts'])} scripts, "
                    f"{len(analysis['meta_tags'])} meta tags, "
                    f"{len(analysis['data_attributes'])} data-* attrs, "
                    f"{len(analysis['inline_js_vars'])} inline JS vars, "
                    f"{len(analysis['api_endpoints'])} API endpoints")

    except Exception as e:
        analysis["error"] = str(e)
        result.html_analysis = analysis
        logger.debug(f"HTML analysis failed (non-fatal): {e}")


def _deep_decode_tokens(result: ExtractionResult) -> None:
    """محاولات فك تشفير متعددة لكل توكن — يتجاوز base64 البسيط.

    لكل توكن يحاول:
    - _d: base64, base64url, protobuf-like, hex
    - sec_uid: base64 (payload only), extract embedded user_id
    - sign: hash type identification (MD5/SHA-1/SHA-256)
    - share_link_id: UUID version + variant
    - checksum: hash type + length validation
    - room_id/stream_id: numeric structure analysis
    """
    analysis = {}

    # ─── _d token deep analysis ─────────────────────────────────────
    d_token = result.all_ids.get("_d") or ""
    if d_token:
        d_analysis = {"raw_length": len(d_token)}
        # Try multiple decodings
        try:
            url_decoded = unquote(d_token)
            d_analysis["url_decoded"] = url_decoded[:100]
            d_analysis["url_decoded_length"] = len(url_decoded)
        except Exception:
            pass

        # Try base64 (standard + URL-safe)
        for b64_variant in ["standard", "urlsafe"]:
            try:
                if b64_variant == "standard":
                    decoded = base64.b64decode(url_decoded, validate=False)
                else:
                    decoded = base64.urlsafe_b64decode(url_decoded + "==")
                d_analysis[f"base64_{b64_variant}_length"] = len(decoded)
                d_analysis[f"base64_{b64_variant}_hex"] = decoded.hex()[:200]
                # Look for readable strings
                readable = re.findall(rb'[\x20-\x7e]{6,}', decoded)
                if readable:
                    d_analysis[f"base64_{b64_variant}_readable_strings"] = [
                        r.decode('ascii', errors='replace') for r in readable[:10]
                    ]
                # Check for protobuf-like structure (field tags)
                if decoded and (decoded[0] in (0x08, 0x10, 0x18, 0x0a, 0x12)):
                    d_analysis["looks_like_protobuf"] = True
                    # Extract varint fields
                    protobuf_fields = []
                    i = 0
                    while i < len(decoded) and i < 50:
                        tag = decoded[i]
                        field_num = tag >> 3
                        wire_type = tag & 0x7
                        protobuf_fields.append({
                            "offset": i,
                            "tag": hex(tag),
                            "field_number": field_num,
                            "wire_type": wire_type,
                        })
                        i += 1
                        # Skip value (simplified)
                        if wire_type == 0:  # varint
                            while i < len(decoded) and decoded[i] & 0x80:
                                i += 1
                            i += 1
                        elif wire_type == 2:  # length-delimited
                            if i < len(decoded):
                                length = decoded[i]
                                i += 1 + length
                        elif wire_type == 5:  # 32-bit
                            i += 4
                        elif wire_type == 1:  # 64-bit
                            i += 8
                        else:
                            break
                    d_analysis["protobuf_fields"] = protobuf_fields[:10]
                break
            except Exception as e:
                d_analysis[f"base64_{b64_variant}_error"] = str(e)[:100]
        analysis["_d"] = d_analysis

    # ─── sec_uid deep analysis ──────────────────────────────────────
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id") or ""
    if sec_uid:
        sec_analysis = {
            "raw_length": len(sec_uid),
            "prefix": sec_uid[:20],
        }
        # sec_uid format: MS4wLjABAAAA... — base64-encoded protobuf
        # Try to decode the payload
        try:
            # Remove potential prefix markers
            payload = sec_uid
            # Try base64 decode
            decoded = base64.urlsafe_b64decode(payload + "==")
            sec_analysis["decoded_length"] = len(decoded)
            sec_analysis["decoded_hex"] = decoded.hex()[:200]
            # Look for user_id as varint (protobuf field 1, wire type 0 → tag 0x08)
            m = re.search(rb'\x08([\x00-\xff]{4,12})', decoded)
            if m:
                # Decode varint
                varint_bytes = m.group(1)
                user_id = 0
                shift = 0
                for b in varint_bytes:
                    user_id |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                if user_id > 1_000_000_000_000_000:  # Looks like a TikTok user_id
                    sec_analysis["extracted_user_id_from_sec_uid"] = str(user_id)
        except Exception as e:
            sec_analysis["decode_error"] = str(e)[:100]
        analysis["sec_uid"] = sec_analysis

    # ─── sign hashes deep analysis ──────────────────────────────────
    if result.live and result.live.get("stream_urls"):
        signs = {}
        for fmt_id, fmt_data in result.live["stream_urls"].items():
            url = fmt_data.get("url") or ""
            m = re.search(r'sign=([a-f0-9]+)', url)
            if m:
                sign_val = m.group(1)
                signs[fmt_id] = {
                    "value": sign_val,
                    "length": len(sign_val),
                    "hash_type": "MD5" if len(sign_val) == 32 else "SHA-1" if len(sign_val) == 40 else "SHA-256" if len(sign_val) == 64 else "unknown",
                }
        if signs:
            # Compare signs across formats
            unique_signs = set(s["value"] for s in signs.values())
            analysis["stream_signs"] = {
                "per_format": signs,
                "unique_count": len(unique_signs),
                "all_same": len(unique_signs) == 1,
                "unique_values": list(unique_signs),
            }

    # ─── share_link_id UUID analysis ────────────────────────────────
    share_link_id = result.all_ids.get("share_link_id") or ""
    if share_link_id:
        uuid_analysis = {
            "raw": share_link_id,
            "length": len(share_link_id),
            "format_valid": bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', share_link_id, re.I)),
        }
        if uuid_analysis["format_valid"]:
            # Extract UUID version
            version_char = share_link_id[14].lower()
            version_map = {"1": "v1 (time-based)", "2": "v2 (DCE)", "3": "v3 (name+MD5)",
                           "4": "v4 (random)", "5": "v5 (name+SHA-1)"}
            uuid_analysis["version"] = version_map.get(version_char, f"unknown ({version_char})")
            # Extract variant
            variant_char = share_link_id[19].lower()
            if variant_char in ("8", "9", "a", "b"):
                uuid_analysis["variant"] = "RFC 4122"
            elif variant_char in ("c", "d"):
                uuid_analysis["variant"] = "Microsoft"
            elif variant_char in ("e", "f"):
                uuid_analysis["variant"] = "reserved"
        analysis["share_link_id"] = uuid_analysis

    # ─── checksum hash analysis ─────────────────────────────────────
    checksum = result.all_ids.get("checksum") or ""
    if checksum:
        cs_analysis = {
            "raw": checksum,
            "length": len(checksum),
            "hash_type": "SHA-256" if len(checksum) == 64 else "SHA-1" if len(checksum) == 40 else "MD5" if len(checksum) == 32 else "unknown",
        }
        # Check if it's all hex
        cs_analysis["is_hex"] = bool(re.match(r'^[0-9a-f]+$', checksum))
        analysis["checksum"] = cs_analysis

    # ─── room_id / stream_id numeric analysis ───────────────────────
    room_id = result.all_ids.get("room_id")
    stream_id = result.all_ids.get("stream_id")
    if room_id and stream_id:
        ids_analysis = {}
        for name, val in [("room_id", room_id), ("stream_id", stream_id)]:
            try:
                num = int(val)
                ids_analysis[name] = {
                    "raw": val,
                    "numeric_value": num,
                    "digit_count": len(val),
                    "likely_epoch": num > 1_700_000_000_000_000_000,  # 2023+ in ms
                    "likely_snowflake_id": num > 1_000_000_000_000_000_000,  # Snowflake IDs are huge
                }
            except (ValueError, TypeError):
                pass
        analysis["numeric_ids"] = ids_analysis

    result.deep_token_analysis = analysis
    if analysis:
        result.raw_keys.append("deep_token_analysis")
        logger.info(f"✅ Deep token analysis: {len(analysis)} tokens deeply decoded")


def _analyze_stream_urls_deeply(result: ExtractionResult) -> None:
    """تحليل عميق لكل رابط بث — تفكيك كامل + مقارنة + تقدير bitrate.

    لكل format:
    - URL decomposition كامل (scheme, host, path, query)
    - كل query param (expire, sign, session_id, only_audio, إلخ)
    - CDN routing (which datacenter)
    - bitrate estimate
    - protocol requirements
    """
    if not result.live or not result.live.get("stream_urls"):
        return

    analysis = {
        "total_formats": len(result.live["stream_urls"]),
        "formats": {},
        "sign_comparison": {},
        "expire_comparison": {},
        "cdn_routing": {},
        "quality_ranking": [],
    }

    signs_by_format = {}
    expires_by_format = {}

    for fmt_id, fmt_data in result.live["stream_urls"].items():
        url = fmt_data.get("url") or ""
        fmt_analysis = {
            "format_id": fmt_id,
            "format": fmt_data.get("format"),
            "protocol": fmt_data.get("protocol"),
            "ext": fmt_data.get("ext"),
            "quality": fmt_data.get("quality"),
            "resolution": fmt_data.get("resolution"),
            "tbr": fmt_data.get("tbr"),
            "vcodec": fmt_data.get("vcodec"),
            "is_audio_only": "only_audio=1" in url,
            "url_decomposition": {},
            "query_params": {},
        }

        # Decompose URL
        try:
            parsed = urlparse(url)
            fmt_analysis["url_decomposition"] = {
                "scheme": parsed.scheme,
                "host": parsed.netloc,
                "path": parsed.path,
                "host_parts": parsed.netloc.split(".")[0].split("-"),
            }
            # Parse query params
            for k, v_list in parse_qs(parsed.query).items():
                v = v_list[0] if v_list else ""
                param_info = {"value": v}
                if k == "expire":
                    try:
                        exp_epoch = int(v)
                        param_info["datetime"] = datetime.utcfromtimestamp(exp_epoch).isoformat() + "Z"
                        param_info["remaining_seconds"] = exp_epoch - int(time.time())
                        expires_by_format[fmt_id] = exp_epoch
                    except ValueError:
                        pass
                elif k == "sign":
                    param_info["hash_type"] = "MD5" if len(v) == 32 else "unknown"
                    signs_by_format[fmt_id] = v
                elif k == "session_id":
                    param_info["type"] = "session_identifier"
                elif k == "only_audio":
                    param_info["type"] = "audio_only_flag"
                fmt_analysis["query_params"][k] = param_info
        except Exception as e:
            fmt_analysis["url_decomposition"] = {"error": str(e)}

        # CDN routing analysis
        host = fmt_analysis["url_decomposition"].get("host", "")
        host_parts = fmt_analysis["url_decomposition"].get("host_parts", [])
        cdn_info = {}
        if "hls" in host_parts:
            cdn_info["protocol"] = "HLS (m3u8)"
        elif "flv" in host_parts:
            cdn_info["protocol"] = "FLV (RTMP)"
        elif "pull" in host_parts:
            cdn_info["protocol"] = "HTTPS pull"
        for part in host_parts:
            if re.match(r'^[a-z]{2}\d+$', part):
                cdn_info["datacenter"] = part
                region_map = {"sg": "Singapore", "va": "Virginia", "my": "Malaysia", "jp": "Japan"}
                region_code = re.match(r'^([a-z]+)', part).group(1)
                cdn_info["region"] = region_map.get(region_code, f"Unknown ({region_code})")
                break
        for part in host_parts:
            if re.match(r'^[fq]\d+$', part):
                cdn_info["edge_node"] = part
                break
        fmt_analysis["cdn_routing"] = cdn_info

        # Bitrate estimate (if tbr not provided)
        if not fmt_data.get("tbr") and fmt_data.get("resolution"):
            # Rough estimate based on resolution
            res_map = {
                "480x864": "800 kbps (SD)",
                "640x1280": "1000 kbps (HD)",
                "720x1280": "1500 kbps (HD)",
                "1080x1920": "3000 kbps (Full HD)",
            }
            fmt_analysis["estimated_bitrate"] = res_map.get(fmt_data.get("resolution"), "unknown")

        analysis["formats"][fmt_id] = fmt_analysis

        # Quality ranking
        analysis["quality_ranking"].append({
            "format_id": fmt_id,
            "quality": fmt_data.get("quality"),
            "resolution": fmt_data.get("resolution"),
            "tbr": fmt_data.get("tbr"),
            "is_audio_only": fmt_analysis["is_audio_only"],
        })

    # Sign comparison
    unique_signs = set(signs_by_format.values())
    analysis["sign_comparison"] = {
        "signs_per_format": signs_by_format,
        "unique_sign_count": len(unique_signs),
        "all_formats_same_sign": len(unique_signs) == 1,
        "unique_signs": list(unique_signs),
        "formats_sharing_sign": _group_formats_by_sign(signs_by_format),
    }

    # Expire comparison
    unique_expires = set(expires_by_format.values())
    analysis["expire_comparison"] = {
        "expires_per_format": {k: str(v) for k, v in expires_by_format.items()},
        "all_same_expire": len(unique_expires) == 1,
        "unique_expires": [str(e) for e in unique_expires],
    }

    # CDN routing summary
    cdn_summary = {}
    for fmt_id, fmt_an in analysis["formats"].items():
        cdn = fmt_an.get("cdn_routing", {})
        dc = cdn.get("datacenter", "unknown")
        if dc not in cdn_summary:
            cdn_summary[dc] = []
        cdn_summary[dc].append(fmt_id)
    analysis["cdn_routing"] = cdn_summary

    # Sort quality ranking
    analysis["quality_ranking"].sort(key=lambda x: (x.get("is_audio_only"), -(x.get("quality") or 0)))

    result.stream_url_analysis = analysis
    result.raw_keys.append("stream_url_analysis")
    logger.info(f"✅ Stream URL analysis: {analysis['total_formats']} formats, "
                f"{len(unique_signs)} unique signs, "
                f"{len(unique_expires)} unique expires")


def _group_formats_by_sign(signs: Dict[str, str]) -> Dict[str, List[str]]:
    """يجمع الصيغ التي تشترك في نفس sign."""
    groups = {}
    for fmt, sign in signs.items():
        if sign not in groups:
            groups[sign] = []
        groups[sign].append(fmt)
    return groups


# ════════════════════════════════════════════════════════════════════════════
#  v3.3 Advanced Processors — from tiktokjson.py + tikhtml.py + mhmdz1.py
# ════════════════════════════════════════════════════════════════════════════

# ─── AdvancedIDExtractor patterns (from mhmdz1.py) ─────────────────────────
_ADVANCED_ID_PATTERNS = {
    "user": {
        "user_id": [r'"user_id"\s*[:=]\s*"(\d+)"', r'"userId"\s*[:=]\s*"(\d+)"', r'"uid"\s*[:=]\s*"(\d+)"'],
        "sec_uid": [r'"secUid"\s*[:=]\s*"([^"]+)"', r'"sec_user_id"\s*[:=]\s*"([^"]+)"', r'MS4wLjABAAAA[^"]+'],
        "unique_id": [r'"uniqueId"\s*[:=]\s*"([^"]+)"', r'"unique_id"\s*[:=]\s*"([^"]+)"'],
        "nickname": [r'"nickname"\s*[:=]\s*"([^"]+)"', r'"nickName"\s*[:=]\s*"([^"]+)"'],
        "signature": [r'"signature"\s*[:=]\s*"([^"]+)"', r'"bioDescription"\s*[:=]\s*"([^"]+)"'],
        "follower_count": [r'"followerCount"\s*[:=]\s*(\d+)', r'"follower_count"\s*[:=]\s*(\d+)'],
        "following_count": [r'"followingCount"\s*[:=]\s*(\d+)'],
        "like_count": [r'"heartCount"\s*[:=]\s*(\d+)', r'"likeCount"\s*[:=]\s*(\d+)'],
        "verified": [r'"verified"\s*[:=]\s*(true|false)'],
    },
    "live": {
        "room_id": [r'"roomId"\s*[:=]\s*"(\d+)"', r'"room_id"\s*[:=]\s*"(\d+)"'],
        "stream_id": [r'"streamId"\s*[:=]\s*"([^"]+)"'],
        "title": [r'"title"\s*[:=]\s*"([^"]+)"', r'"roomTitle"\s*[:=]\s*"([^"]+)"'],
        "status": [r'"status"\s*[:=]\s*(\d+)', r'"liveStatus"\s*[:=]\s*(\d+)'],
        "viewer_count": [r'"viewerCount"\s*[:=]\s*(\d+)', r'"userCount"\s*[:=]\s*(\d+)'],
        "like_count": [r'"likeCount"\s*[:=]\s*(\d+)', r'"totalLikeCount"\s*[:=]\s*(\d+)'],
        "diamond_count": [r'"diamondCount"\s*[:=]\s*(\d+)'],
        "start_time": [r'"startTime"\s*[:=]\s*(\d+)', r'"createTime"\s*[:=]\s*(\d+)'],
    },
    "security": {
        "csrf_token": [r'"csrfToken"\s*[:=]\s*"([^"]+)"', r'"csrf_token"\s*[:=]\s*"([^"]+)"'],
        "wid": [r'"wid"\s*[:=]\s*"(\d+)"', r'"webid"\s*[:=]\s*"(\d+)"'],
        "nonce": [r'"nonce"\s*[:=]\s*"([^"]+)"'],
        "request_id": [r'"requestId"\s*[:=]\s*"([^"]+)"'],
        "encrypted_webid": [r'"encryptedWebid"\s*[:=]\s*"([^"]+)"'],
    },
    "app": {
        "app_id": [r'"appId"\s*[:=]\s*"(\d+)"'],
        "aid": [r'"aid"\s*[:=]\s*"?(\d+)"?'],
        "language": [r'"language"\s*[:=]\s*"([^"]+)"'],
        "region": [r'"region"\s*[:=]\s*"([^"]+)"'],
        "cluster_region": [r'"clusterRegion"\s*[:=]\s*"([^"]+)"'],
    },
    "location": {
        "country": [r'"country"\s*[:=]\s*"([^"]+)"', r'"countryCode"\s*[:=]\s*"([^"]+)"'],
        "city": [r'"city"\s*[:=]\s*"([^"]+)"', r'"cityName"\s*[:=]\s*"([^"]+)"'],
    },
}

# ─── JSONExtractor patterns (from mhmdz1.py) ────────────────────────────────
_JSON_SCRIPT_PATTERNS = {
    "sigi_state": r'<script\s+id="SIGI_STATE"\s+type="application/json">(.*?)</script>',
    "universal_data": r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">(.*?)</script>',
    "pumbaa_rule": r'<script\s+id="pumbaa-rule"\s+type="application/json">(.*?)</script>',
    "slardar_config": r'<script\s+id="slardar-config"\s+type="application/json">(.*?)</script>',
    "api_domains": r'<script\s+id="api-domains"\s+type="application/json">(.*?)</script>',
    "script_manager": r'<script\s+id="script-manager"\s+type="application/json">(.*?)</script>',
    "region_data": r'<script\s+id="__REGION__DATA__INJECTED__"\s+type="application/json">(.*?)</script>',
    "scm_config": r'<script\s+id="scm-experiment-config"\s+type="application/json">(.*?)</script>',
}


def _extract_advanced_ids(html: str) -> Dict[str, Any]:
    """يستخرج كل المعرفات من HTML باستخدام regex patterns (AdvancedIDExtractor).

    يُرجع قاموس بـ 5 فئات: user, live, security, app, location.
    """
    results = {"user": {}, "live": {}, "security": {}, "app": {}, "location": {}}
    for category, patterns in _ADVANCED_ID_PATTERNS.items():
        for key, pattern_list in patterns.items():
            for pattern in pattern_list:
                try:
                    matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
                    if matches:
                        val = matches[0] if isinstance(matches[0], str) else matches[0][0]
                        if val and val not in ("", "null", "None"):
                            results[category][key] = val
                            break
                except Exception:
                    continue
    return results


def _extract_all_json_from_html(html: str) -> Dict[str, Any]:
    """يستخرج كل ملفات JSON المضمنة في HTML (JSONExtractor).

    يُرجع قاموس بـ 8 مصادر: sigi_state, universal_data, pumbaa_rule, slardar_config,
    api_domains, script_manager, region_data, scm_config.
    """
    results = {}
    for name, pattern in _JSON_SCRIPT_PATTERNS.items():
        try:
            matches = re.findall(pattern, html, re.DOTALL)
            if matches:
                content = matches[0].strip()
                # pumbaa_rule is base64-encoded
                if name == "pumbaa_rule":
                    try:
                        content = base64.b64decode(content).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                try:
                    data = json.loads(content)
                    results[name] = data
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
    return results


def _enrich_with_advanced_api(result: ExtractionResult, session: requests.Session,
                               parsed: ParsedLink) -> None:
    """يستدعي TikTokAdvancedAPI — 4 طرق فريدة من tiktokjson.py.

    1. get_user_info_by_sec_id() — معلومات المستخدم الكاملة
    2. get_live_detail_by_user_id() — تفاصيل البث عبر user_id
    3. get_live_gift_rank() — قائمة المتبرعين الكاملة
    4. get_live_gift_list() — قائمة الهدايا المتاحة
    """
    ua = USER_AGENTS[0]
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id")
    user_id = result.author.user_id or result.all_ids.get("user_id")
    room_id = result.all_ids.get("room_id") or (result.live.get("room_id") if result.live else None)

    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.tiktok.com/@{parsed.username}" if parsed.username else "https://www.tiktok.com/",
        "Origin": "https://www.tiktok.com",
    }

    # ─── 1. get_user_info_by_sec_id ─────────────────────────────────
    if sec_uid:
        try:
            api_url = "https://www.tiktok.com/api/v1/user/detail/"
            params = {
                "sec_user_id": sec_uid, "aid": "1988",
                "app_language": "ar", "language": "ar",
                "version_code": "290102", "version_name": "29.1.2",
            }
            r = session.get(api_url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("status_code") == 0:
                    result.user_detail_full = data
                    # Enrich author with the data
                    user_info = data.get("userInfo") or data.get("user_info") or {}
                    user = user_info.get("user") or user_info
                    stats = user_info.get("stats") or {}
                    if stats.get("followerCount"):
                        result.author.follower_count = stats.get("followerCount")
                    if stats.get("followingCount"):
                        result.author.following_count = stats.get("followingCount")
                    if stats.get("heart") or stats.get("likeCount"):
                        result.author.like_count = stats.get("heart") or stats.get("likeCount")
                    if stats.get("videoCount"):
                        result.author.video_count = stats.get("videoCount")
                    if user.get("signature"):
                        result.author.signature = user.get("signature")
                    result.raw_keys.append("advanced_user_detail")
                    logger.info(f"✅ Advanced API: user_detail by sec_id — followers={result.author.follower_count}")
        except Exception as e:
            logger.debug(f"get_user_info_by_sec_id failed: {e}")

    # ─── 2. get_live_detail_by_user_id ─────────────────────────────
    if user_id:
        try:
            api_url = "https://www.tiktok.com/api/live/detail/"
            params = {
                "user_id": user_id, "aid": "1988",
                "app_language": "ar", "language": "ar",
            }
            r = session.get(api_url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("status_code") == 0:
                    result.live_detail_full = data
                    # Extract room_id if we don't have it
                    live_room = data.get("liveRoom") or {}
                    if live_room.get("id") and not result.all_ids.get("room_id"):
                        result.all_ids["room_id"] = str(live_room["id"])
                        room_id = str(live_room["id"])
                        logger.info(f"✅ Advanced API: extracted room_id={room_id} from live detail")
                    result.raw_keys.append("advanced_live_detail")
        except Exception as e:
            logger.debug(f"get_live_detail_by_user_id failed: {e}")

    # ─── 3. get_live_gift_rank ─────────────────────────────────────
    if room_id:
        try:
            api_url = "https://www.tiktok.com/api/live/gift/rank/"
            params = {"room_id": room_id, "aid": "1988", "app_language": "ar", "language": "ar"}
            r = session.get(api_url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("status_code") == 0:
                    result.full_gift_rank = data
                    # Extract donor list
                    ranks = data.get("ranks") or data.get("user_list") or []
                    if ranks:
                        result.donor_rankings = [{
                            "rank": r2.get("rank") or i + 1,
                            "user_id": r2.get("user_id"),
                            "nickname": r2.get("nickname"),
                            "unique_id": r2.get("unique_id"),
                            "diamond_count": r2.get("diamond_count") or r2.get("score"),
                        } for i, r2 in enumerate(ranks)]
                        if result.live:
                            result.live["top_donors"] = result.donor_rankings
                    result.raw_keys.append("advanced_gift_rank")
                    logger.info(f"✅ Advanced API: gift_rank — {len(ranks)} donors")
        except Exception as e:
            logger.debug(f"get_live_gift_rank failed: {e}")

    # ─── 4. get_live_gift_list ─────────────────────────────────────
    if room_id:
        try:
            api_url = "https://www.tiktok.com/api/live/gift/list/"
            params = {"room_id": room_id, "aid": "1988", "app_language": "ar", "language": "ar"}
            r = session.get(api_url, params=params, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("status_code") == 0:
                    result.full_gift_list = data
                    gifts = data.get("gifts") or data.get("data", {}).get("gifts") or []
                    if gifts:
                        result.gift_list = [{
                            "id": g.get("id"),
                            "name": g.get("name") or g.get("describe"),
                            "diamond_count": g.get("diamond_count"),
                            "type": g.get("type"),
                            "level": g.get("level"),
                            "rarity": g.get("rarity"),
                        } for g in gifts]
                    result.raw_keys.append("advanced_gift_list")
                    logger.info(f"✅ Advanced API: gift_list — {len(gifts)} gifts")
        except Exception as e:
            logger.debug(f"get_live_gift_list failed: {e}")


def _enrich_with_enter_live_room(result: ExtractionResult, session: requests.Session) -> None:
    """يدخل غرفة البث فعلياً عبر webcast.tiktok.com/webcast/room/enter.

    من mhmdz1.py — TikTokAPIClient.enter_live_room().
    يُرجع stream_urls حية + بيانات إضافية.
    """
    if not result.all_ids.get("room_id"):
        return

    room_id = result.all_ids["room_id"]
    ua = USER_AGENTS[0]
    user_id = result.author.user_id or result.all_ids.get("user_id", "")
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id", "")

    try:
        url = "https://webcast.tiktok.com/webcast/room/enter"
        params = {"room_id": room_id, "aid": "1988", "device_platform": "web"}
        data = {"room_id": room_id}
        if user_id:
            data["user_id"] = user_id
        if sec_uid:
            data["sec_uid"] = sec_uid

        headers = {
            "User-Agent": ua,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.tiktok.com",
            "Referer": "https://www.tiktok.com/",
        }

        r = session.post(url, params=params, json=data, headers=headers, timeout=15)
        if r.status_code == 200:
            resp_data = r.json()
            if resp_data.get("status_code") == 0:
                result.live_room_entered = resp_data
                result.raw_keys.append("enter_live_room")
                logger.info(f"✅ enter_live_room: success — room_id={room_id}")

                # Extract live stream_urls if present
                room_data = resp_data.get("data") or {}
                if room_data.get("stream_url"):
                    if result.live:
                        result.live["entered_stream_url"] = room_data.get("stream_url")
    except Exception as e:
        logger.debug(f"enter_live_room failed: {e}")


def _enrich_with_security_credentials(result: ExtractionResult, html: str) -> None:
    """يستخرج بيانات الأمان من HTML (csrf_token, wid, nonce, encrypted_webid, request_id).

    من mhmdz1.py — AdvancedIDExtractor category 'security'.
    من Interactiontik.py — _extract_cookies() for sessionid extraction.
    """
    if not html:
        return

    creds = {}
    # csrf_token
    m = re.search(r'"csrfToken"\s*[:=]\s*"([^"]+)"', html)
    if m:
        creds["csrf_token"] = m.group(1)
    # wid
    m = re.search(r'"wid"\s*[:=]\s*"(\d+)"', html)
    if m:
        creds["wid"] = m.group(1)
    # nonce
    m = re.search(r'"nonce"\s*[:=]\s*"([^"]+)"', html)
    if m:
        creds["nonce"] = m.group(1)
    # request_id
    m = re.search(r'"requestId"\s*[:=]\s*"([^"]+)"', html)
    if m:
        creds["request_id"] = m.group(1)
    # encrypted_webid
    m = re.search(r'"encryptedWebid"\s*[:=]\s*"([^"]+)"', html)
    if m:
        creds["encrypted_webid"] = m.group(1)
    # webid (raw)
    m = re.search(r'"webid"\s*[:=]\s*"(\d+)"', html)
    if m:
        creds["webid"] = m.group(1)

    # ─── sessionid extraction (from Interactiontik.py _extract_cookies) ───
    # Pattern 1: sessionid in Set-Cookie header or HTML
    m = re.search(r'sessionid[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["sessionid"] = m.group(1)
    # Pattern 2: sessionid in JSON context
    m = re.search(r'"sessionid"\s*[:=]\s*"([^"]+)"', html)
    if m and "sessionid" not in creds:
        creds["sessionid"] = m.group(1)
    # Pattern 3: sessionid_ss (secure session)
    m = re.search(r'sessionid_ss[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["sessionid_ss"] = m.group(1)
    # Pattern 4: sid_tt (TikTok tracking session)
    m = re.search(r'sid_tt[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["sid_tt"] = m.group(1)

    # ─── Additional cookies (from Interactiontik.py) ───
    # ttwid (TikTok web ID cookie)
    m = re.search(r'ttwid[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["ttwid"] = m.group(1)
    # odin_tt (Odin session)
    m = re.search(r'odin_tt[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["odin_tt"] = m.group(1)
    # passport_csrf_token
    m = re.search(r'passport_csrf_token[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["passport_csrf_token"] = m.group(1)
    # msToken
    m = re.search(r'msToken[_\s]*["\']?\s*[:=]\s*["\']([^"\'\s;]+)', html, re.IGNORECASE)
    if m:
        creds["msToken"] = m.group(1)

    if creds:
        result.security_credentials = creds
        # Also merge into all_ids
        for k, v in creds.items():
            if k not in result.all_ids:
                result.all_ids[k] = v
        result.raw_keys.append("security_credentials")
        logger.info(f"✅ Security credentials: {list(creds.keys())}")
        if "sessionid" in creds:
            logger.info(f"   ↳ sessionid found: {creds['sessionid'][:30]}...")
        if "msToken" in creds:
            logger.info(f"   ↳ msToken found: {creds['msToken'][:30]}...")


def _enrich_with_advanced_json_files(result: ExtractionResult, html: str) -> None:
    """يستخرج كل ملفات JSON المضمنة في HTML.

    من mhmdz1.py — JSONExtractor.
    8 مصادر: sigi_state, universal_data, pumbaa_rule, slardar_config,
    api_domains, script_manager, region_data, scm_config.
    """
    if not html:
        return

    json_files = _extract_all_json_from_html(html)
    if not json_files:
        return

    result.advanced_json_files = json_files
    result.raw_keys.append("advanced_json_files")

    # Extract api_domains if present
    if "api_domains" in json_files:
        domains = json_files["api_domains"]
        if isinstance(domains, dict):
            # api_domains has structure like {"domains": {"rootApi": "...", "webcastApi": "..."}}
            result.api_domains = domains.get("domains") or domains
            result.raw_keys.append("api_domains")
            logger.info(f"✅ API domains extracted: {list(result.api_domains.keys()) if isinstance(result.api_domains, dict) else 'N/A'}")

    # Merge sigi_state/universal_data IDs into all_ids
    for source in ("sigi_state", "universal_data"):
        if source in json_files:
            data = json_files[source]
            if isinstance(data, dict):
                # Look for csrf, wid, nonce in the data
                app_ctx = (data.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.app-context", {})
                if app_ctx:
                    for k in ("csrfToken", "wid", "nonce", "requestId", "encryptedWebid", "region"):
                        if app_ctx.get(k) and not result.all_ids.get(k.lower()):
                            result.all_ids[k.lower()] = app_ctx[k]

    logger.info(f"✅ Advanced JSON files: {list(json_files.keys())}")


def _enrich_with_advanced_id_extraction(result: ExtractionResult, html: str) -> None:
    """يستخرج كل المعرفات باستخدام AdvancedIDExtractor (regex patterns).

    من mhmdz1.py — 5 فئات: user, live, security, app, location.
    """
    if not html:
        return

    ids = _extract_advanced_ids(html)
    if not ids:
        return

    result.advanced_ids = ids
    result.raw_keys.append("advanced_ids")

    # Enrich all_ids with anything new
    for category, cat_ids in ids.items():
        for k, v in cat_ids.items():
            if v and k not in result.all_ids:
                result.all_ids[k] = v

    # Enrich author if we found new data
    user_ids = ids.get("user", {})
    if user_ids.get("follower_count"):
        try:
            result.author.follower_count = int(user_ids["follower_count"])
        except (ValueError, TypeError):
            pass
    if user_ids.get("following_count"):
        try:
            result.author.following_count = int(user_ids["following_count"])
        except (ValueError, TypeError):
            pass
    if user_ids.get("like_count"):
        try:
            result.author.like_count = int(user_ids["like_count"])
        except (ValueError, TypeError):
            pass
    if user_ids.get("signature"):
        result.author.signature = user_ids["signature"]
    if user_ids.get("nickname"):
        result.author.nickname = user_ids["nickname"]

    # Enrich live data
    live_ids = ids.get("live", {})
    if live_ids and result.live:
        if live_ids.get("viewer_count"):
            try:
                result.live["viewer_count"] = int(live_ids["viewer_count"])
            except (ValueError, TypeError):
                pass
        if live_ids.get("like_count"):
            try:
                result.live["like_count"] = int(live_ids["like_count"])
            except (ValueError, TypeError):
                pass
        if live_ids.get("diamond_count"):
            try:
                result.live["diamond_count"] = int(live_ids["diamond_count"])
            except (ValueError, TypeError):
                pass
        if live_ids.get("start_time"):
            try:
                result.live["start_time"] = int(live_ids["start_time"])
            except (ValueError, TypeError):
                pass

    logger.info(f"✅ Advanced ID extraction: {sum(len(v) for v in ids.values())} IDs across {len(ids)} categories")


def _save_html_file(result: ExtractionResult, html: str, parsed: ParsedLink) -> None:
    """يحفظ ملف HTML كامل (من tikhtml.py — save_html_file)."""
    if not html:
        return
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        username = parsed.username or "unknown"
        room_id = result.all_ids.get("room_id") or "no_room"
        filename = f"tiktok_page_{username}_{room_id}_{timestamp}.html"
        filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)

        # Save to /tmp (Render-compatible)
        save_dir = os.environ.get("HTML_SAVE_DIR", "/tmp/tiktok_html")
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        result.saved_html_path = filepath
        result.raw_keys.append("saved_html")
        logger.info(f"✅ HTML saved: {filepath} ({len(html)} bytes)")
    except Exception as e:
        logger.debug(f"save_html_file failed (non-fatal): {e}")


# ════════════════════════════════════════════════════════════════════════════
#  v3.6: Interaction Analysis + Webcast Full Data Surfacing
# ════════════════════════════════════════════════════════════════════════════

def _build_interaction_analysis(result: ExtractionResult) -> None:
    """يبني تحليل تفاعل كامل — ما هو متاح وما هو مفقود للتفاعل التلقائي.

    يصنّف كل حقل إلى:
    - available: متاح ويمكن استخدامه
    - missing: مطلوب للتفاعل لكن غير متاح
    - not_needed: غير مطلوب

    يحدد:
    - interaction_ready: هل يمكن التفاعل؟ (True/False)
    - available_fields: قائمة بالحقول المتاحة
    - missing_fields: قائمة بالحقول المفقودة
    - interaction_endpoints: APIs اللازمة لكل نوع تفاعل
    - targets: قائمة بالمستخدمين المستهدفين (owner + top_fans)
    """
    analysis = {
        "interaction_ready": False,
        "available_fields": {},
        "missing_fields": {},
        "interaction_endpoints": {},
        "required_for": {},
    }

    # ─── 1. Check available fields ──────────────────────────────────
    available = {}
    # sec_uid (target user)
    if result.author.sec_uid:
        available["sec_uid"] = {"value": result.author.sec_uid[:50] + "...", "source": "webcast/yt_dlp"}
    # user_id (target user)
    if result.author.user_id:
        available["user_id"] = {"value": result.author.user_id, "source": "webcast/yt_dlp"}
    # unique_id (target username)
    if result.author.unique_id:
        available["unique_id"] = {"value": result.author.unique_id, "source": "yt_dlp/share_link"}
    # room_id (for live interactions)
    if result.all_ids.get("room_id"):
        available["room_id"] = {"value": result.all_ids["room_id"], "source": "yt_dlp/webcast"}
    # stream_id
    if result.all_ids.get("stream_id"):
        available["stream_id"] = {"value": result.all_ids["stream_id"], "source": "webcast"}
    # share_url
    if result.all_ids.get("share_url"):
        available["share_url"] = {"value": result.all_ids["share_url"], "source": "webcast"}
    # csrf_token (from security_credentials)
    if result.security_credentials.get("csrf_token"):
        available["csrf_token"] = {"value": result.security_credentials["csrf_token"][:30] + "...", "source": "html"}
    # wid (device ID)
    if result.security_credentials.get("wid"):
        available["wid"] = {"value": result.security_credentials["wid"], "source": "html"}
    # nonce
    if result.security_credentials.get("nonce"):
        available["nonce"] = {"value": result.security_credentials["nonce"][:30] + "...", "source": "html"}
    analysis["available_fields"] = available

    # ─── 2. Check missing fields ────────────────────────────────────
    missing = {}
    if not result.security_credentials.get("csrf_token"):
        missing["csrf_token"] = {
            "description": "توكن الحماية — مطلوب لكل طلبات POST (متابعة، إعجاب، تعليق)",
            "how_to_get": "سجّل الدخول في TikTok → افتح Developer Tools → Application → Cookies → ابحث عن csrf_session_id",
            "source": "browser_cookies",
        }
    if not result.security_credentials.get("sessionid"):
        missing["sessionid"] = {
            "description": "معرف الجلسة — يثبت أنك مسجّل الدخول",
            "how_to_get": "Developer Tools → Application → Cookies → sessionid",
            "source": "browser_cookies",
        }
    if not result.security_credentials.get("wid"):
        missing["wid"] = {
            "description": "معرف الجهاز — يعرّف جهازك في TikTok",
            "how_to_get": "Developer Tools → Application → Cookies → ttwid أو من HTML في window.__INITIAL_STATE__",
            "source": "browser_cookies_or_html",
        }
    if not result.all_ids.get("x_bogus"):
        missing["X-Bogus"] = {
            "description": "توقيع طلب — يمنع الكشف عن الأتمتة",
            "how_to_get": "يُولّد من webmssdk.js أو عبر Playwright",
            "source": "javascript_runtime",
        }
    if not result.all_ids.get("ms_token"):
        missing["msToken"] = {
            "description": "توكن أمان إضافي — يُطلب في بعض APIs",
            "how_to_get": "من cookies أو من window.msToken",
            "source": "browser_cookies_or_js",
        }
    analysis["missing_fields"] = missing

    # ─── 3. Interaction readiness ───────────────────────────────────
    has_target = bool(result.author.sec_uid or result.author.user_id)
    has_session = bool(result.security_credentials.get("csrf_token") and result.security_credentials.get("sessionid"))
    has_signing = bool(result.all_ids.get("x_bogus") or result.all_ids.get("ms_token"))
    analysis["interaction_ready"] = has_target and has_session
    analysis["interaction_partial"] = has_target and not has_session
    analysis["has_target_user"] = has_target
    analysis["has_session_credentials"] = has_session
    analysis["has_request_signing"] = has_signing

    # ─── 4. Interaction endpoints ───────────────────────────────────
    sec_uid = result.author.sec_uid or ""
    user_id = result.author.user_id or ""
    room_id = result.all_ids.get("room_id", "")

    analysis["interaction_endpoints"] = {
        "follow": {
            "url": "https://www.tiktok.com/api/relation/follow/",
            "method": "POST",
            "requires": ["csrf_token", "sessionid", "sec_uid"],
            "payload": {"sec_uid": sec_uid, "type": 1},
            "available": bool(sec_uid),
        },
        "unfollow": {
            "url": "https://www.tiktok.com/api/relation/follow/",
            "method": "POST",
            "requires": ["csrf_token", "sessionid", "sec_uid"],
            "payload": {"sec_uid": sec_uid, "type": 0},
            "available": bool(sec_uid),
        },
        "like_video": {
            "url": "https://www.tiktok.com/api/v1/item/like/",
            "method": "POST",
            "requires": ["csrf_token", "sessionid", "video_id"],
            "payload": {"id": result.content_id or "", "type": 1},
            "available": bool(result.content_id),
        },
        "send_comment": {
            "url": "https://www.tiktok.com/api/v1/comment/publish/",
            "method": "POST",
            "requires": ["csrf_token", "sessionid", "video_id", "X-Bogus"],
            "payload": {"aweme_id": result.content_id or "", "text": ""},
            "available": bool(result.content_id),
        },
        "enter_live_room": {
            "url": "https://webcast.tiktok.com/webcast/room/enter",
            "method": "POST",
            "requires": ["room_id"],
            "payload": {"room_id": room_id},
            "available": bool(room_id),
        },
        "send_live_like": {
            "url": "https://webcast.tiktok.com/webcast/like/",
            "method": "POST",
            "requires": ["room_id", "csrf_token", "sessionid"],
            "payload": {"room_id": room_id, "like_count": 1},
            "available": bool(room_id),
        },
        "send_gift": {
            "url": "https://webcast.tiktok.com/webcast/gift/send/",
            "method": "POST",
            "requires": ["room_id", "gift_id", "csrf_token", "sessionid", "X-Bogus"],
            "payload": {"room_id": room_id, "gift_id": "", "gift_count": 1},
            "available": bool(room_id),
        },
    }

    # ─── 5. Interaction targets (users we can interact with) ────────
    targets = []
    # Owner / broadcaster
    if result.author.sec_uid or result.author.user_id:
        targets.append({
            "type": "broadcaster",
            "unique_id": result.author.unique_id,
            "nickname": result.author.nickname,
            "user_id": result.author.user_id,
            "sec_uid": (result.author.sec_uid or "")[:50] + "...",
            "follower_count": result.author.follower_count,
            "following_count": result.author.following_count,
            "verified": result.author.verified,
            "avatar": (result.author.avatar or "")[:80],
            "interactions_available": ["follow", "like_video", "send_comment", "enter_live_room", "send_live_like"],
        })
    # Top fans / donors
    for fan in result.donor_rankings:
        targets.append({
            "type": "top_fan",
            "rank": fan.get("rank"),
            "unique_id": fan.get("unique_id"),
            "nickname": fan.get("nickname"),
            "user_id": fan.get("user_id"),
            "sec_uid": (fan.get("sec_uid") or "")[:50] + "...",
            "follower_count": fan.get("follower_count"),
            "interactions_available": ["follow", "like_video"],
        })

    result.interaction_targets = targets
    analysis["targets_count"] = len(targets)

    # ─── 6. Required-for mapping ────────────────────────────────────
    analysis["required_for"] = {
        "follow": ["sec_uid", "csrf_token", "sessionid"],
        "like": ["video_id", "csrf_token", "sessionid"],
        "comment": ["video_id", "csrf_token", "sessionid", "X-Bogus"],
        "live_like": ["room_id", "csrf_token", "sessionid"],
        "gift": ["room_id", "gift_id", "csrf_token", "sessionid", "X-Bogus"],
        "enter_room": ["room_id"],
    }

    result.interaction_analysis = analysis
    result.raw_keys.append("interaction_analysis")
    logger.info(f"✅ Interaction analysis: ready={analysis['interaction_ready']}, "
                f"available={len(available)}, missing={len(missing)}, targets={len(targets)}")


def _surface_webcast_data(result: ExtractionResult) -> None:
    """يُظهر بيانات webcast_room_info الكاملة في المستوى الأعلى للنتيجة.

    raw_json.webcast_room_info.data تحتوي على 242+ مفتاح و 1490+ حقل متداخل.
    هذه الدالة تستخرج أهمها إلى المستوى الأعلى لسهولة الوصول.
    """
    raw = result.raw_json or {}
    webcast = raw.get("webcast_room_info", {})
    data = webcast.get("data", {})

    if not data:
        return

    surfaced = {}
    # ─── Owner data (94 keys) ───────────────────────────────────────
    owner = data.get("owner", {})
    if owner:
        surfaced["owner"] = {
            "id": owner.get("id"),
            "id_str": owner.get("id_str"),
            "sec_uid": owner.get("sec_uid"),
            "display_id": owner.get("display_id"),
            "nickname": owner.get("nickname"),
            "bio_description": owner.get("bio_description"),
            "status": owner.get("status"),
            "modify_time": owner.get("modify_time"),
            "link_mic_stats": owner.get("link_mic_stats"),
            "follow_info": owner.get("follow_info"),
            "pay_grade": owner.get("pay_grade"),
            "own_room": owner.get("own_room"),
            "user_attr": owner.get("user_attr"),
            "avatar_thumb_urls": (owner.get("avatar_thumb") or {}).get("url_list", []),
            "avatar_medium_urls": (owner.get("avatar_medium") or {}).get("url_list", []),
            "avatar_large_urls": (owner.get("avatar_large") or {}).get("url_list", []),
        }

    # ─── Stats data (20 keys) ──────────────────────────────────────
    stats = data.get("stats", {})
    if stats:
        surfaced["stats"] = stats

    # ─── Stream URL data (31 keys) ─────────────────────────────────
    stream_url = data.get("stream_url", {})
    if stream_url:
        surfaced["stream_url"] = {
            "hls_pull_url": stream_url.get("hls_pull_url"),
            "rtmp_pull_url": stream_url.get("rtmp_pull_url"),
            "flv_pull_url": stream_url.get("flv_pull_url"),
            "complete_push_urls": stream_url.get("complete_push_urls"),
            "live_core_sdk_data": stream_url.get("live_core_sdk_data"),
            "default_resolution": stream_url.get("default_resolution"),
            "candidate_resolution": stream_url.get("candidate_resolution"),
            "resolution_name": stream_url.get("resolution_name"),
            "push_resolution": stream_url.get("push_resolution"),
            "stream_size_width": stream_url.get("stream_size_width"),
            "stream_size_height": stream_url.get("stream_size_height"),
            "stream_delay_ms": stream_url.get("stream_delay_ms"),
            "stream_language": stream_url.get("stream_language"),
            "provider": stream_url.get("provider"),
            "drm_type": stream_url.get("drm_type"),
            "id": stream_url.get("id"),
            "id_str": stream_url.get("id_str"),
            "stream_app_id": stream_url.get("stream_app_id"),
            "stream_control_type": stream_url.get("stream_control_type"),
            "alive_timestamp": stream_url.get("alive_timestamp"),
            "extra": stream_url.get("extra"),
            "hls_pull_url_map": stream_url.get("hls_pull_url_map"),
            "hls_pull_url_params": stream_url.get("hls_pull_url_params"),
            "rtmp_pull_url_params": stream_url.get("rtmp_pull_url_params"),
            "flv_pull_url_params": stream_url.get("flv_pull_url_params"),
            "rtmp_push_url": stream_url.get("rtmp_push_url"),
            "rtmp_push_url_params": stream_url.get("rtmp_push_url_params"),
            "push_urls": stream_url.get("push_urls"),
            "media_track_enable": stream_url.get("media_track_enable"),
            "vr_type": stream_url.get("vr_type"),
        }

    # ─── Top fans ──────────────────────────────────────────────────
    top_fans = data.get("top_fans", [])
    if top_fans:
        surfaced["top_fans"] = [{
            "user": {
                "id": fan.get("user", {}).get("id"),
                "id_str": fan.get("user", {}).get("id_str"),
                "sec_uid": fan.get("user", {}).get("sec_uid"),
                "display_id": fan.get("user", {}).get("display_id"),
                "nickname": fan.get("user", {}).get("nickname"),
                "follow_info": fan.get("user", {}).get("follow_info"),
                "fan_ticket_count": fan.get("user", {}).get("fan_ticket_count"),
                "ticket_count": fan.get("user", {}).get("ticket_count"),
                "badge_list": fan.get("user", {}).get("badge_list"),
                "avatar_urls": (fan.get("user", {}).get("avatar_thumb") or {}).get("url_list", []),
            },
            "score": fan.get("score"),
            "rank": fan.get("rank"),
        } for fan in top_fans]

    # ─── Link mic (17 keys) ────────────────────────────────────────
    link_mic = data.get("link_mic", {})
    if link_mic:
        surfaced["link_mic"] = link_mic

    # ─── Commerce info ─────────────────────────────────────────────
    commerce = data.get("commerce_info", {})
    if commerce:
        surfaced["commerce_info"] = commerce

    # ─── Room metadata (selected important fields) ────────────────
    room_fields = {}
    for k in ("title", "status", "create_time", "finish_time", "last_ping_time",
              "share_url", "cover", "cover_type", "deco_list", "content_tag",
              "common_label_list", "anchor_tab_type", "business_live",
              "age_restricted", "audio_mute", "auto_cover", "client_version",
              "app_id", "live_id", "book_time", "book_end_time",
              "allow_preview_time", "anchor_share_text", "answering_question_content",
              "challenge_info", "fansclub_msg_style", "follow_msg_style",
              "continuous_room_quota_config", "admin_user_ids",
              "living_room_attrs", "multi_stream_id", "multi_stream_url",
              "owner_device_id", "owner_user_id", "paid_event",
              "partnership_info", "room_auth", "search_id", "smb_board_id",
              "social_interaction", "feed_room_label", "log_id"):
        if k in data:
            room_fields[k] = data[k]
    surfaced["room_metadata"] = room_fields

    # ─── Total field count ────────────────────────────────────────
    def count_fields(obj):
        count = 0
        if isinstance(obj, dict):
            for k, v in obj.items():
                count += 1
                count += count_fields(v)
        elif isinstance(obj, list):
            for item in obj[:5]:
                count += count_fields(item)
        return count
    surfaced["_total_webcast_fields"] = count_fields(data)
    surfaced["_total_webcast_top_level_keys"] = len(data)

    result.webcast_full_data = surfaced
    result.raw_keys.append("webcast_full_data")
    logger.info(f"✅ Webcast full data surfaced: {surfaced['_total_webcast_top_level_keys']} top-level keys, "
                f"{surfaced['_total_webcast_fields']} total nested fields")


# ════════════════════════════════════════════════════════════════════════════
#  v3.7: Interaction Bot — from Interactiontik.py
# ════════════════════════════════════════════════════════════════════════════

# ─── PRELOADED_ACCOUNTS (from Interactiontik.py) ───────────────────────────
# These are test accounts with csrf_token, wid, nonce pre-extracted.
# Used as fallback when HTML is geo-blocked and we can't extract them.
PRELOADED_ACCOUNTS = [
    {
        'user_id': '7217245384412890114',
        'sec_uid': 'MS4wLjABAAAAINWAP8TyL2aKrKCy7n38WbqXWdmgEVFO_mkum9A8C7WJ3QXFpZxy9yYDH3S27SuX',
        'unique_id': 'fw__qg',
        'nickname': 'قـمـر𝑄𝐚𝑀𝐚ℛعـدنf͜͡',
        'csrf_token': 'uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk',
        'wid': '7658084697712657940',
        'nonce': '6vaarBpVsrfBDm_nl5DbJ',
        'follower_count': 85703,
        'following_count': 764,
        'room_id': '7658060648169671440',
        'stream_id': '1849039456225984558',
    },
    {
        'user_id': '7232775175329858565',
        'sec_uid': 'MS4wLjABAAAAus9pOG5BDEomTpOnjSJoIu5EvhL-vh4p5kvYZ_VmLPJ20S_bnFt6PhttHi0PmdAC',
        'unique_id': 'hadwtamsr4',
        'nickname': 'حدوته💪محارب💪الصحراء🔥',
        'csrf_token': 'a6oTMbVL-QkEynoaFoDYCzOl-6TWwXDrtytE',
        'wid': '7658084453952521746',
        'nonce': 'suasGRobcGULManRZz2VN',
        'follower_count': 174544,
        'following_count': 590,
        'room_id': '7658082358457420564',
        'stream_id': '1560809419068407851',
    },
    {
        'user_id': '7085220786483692550',
        'sec_uid': 'MS4wLjABAAAAZgPZJ6IDZjAo23OwNJFMXOIPefE7wvhWKOrPx6GQYlg8AiaboBDiMpE40g9LllrT',
        'unique_id': 'bentmlok1',
        'nickname': 'بنت سعود 🇸🇦',
        'csrf_token': 'MSN9neXH-tPvWGD07il83ruYV1uGpk_Gn0z8',
        'wid': '7658060225526842896',
        'nonce': 'Y4TfgvnxD2qbaHcrsB3VT',
        'follower_count': 206651,
        'following_count': 0,
        'room_id': '7657962888087079700',
        'stream_id': '1560807551762694187',
    },
]

# Target account (the account that the bot interacts FROM)
TARGET_ACCOUNT = {
    'user_id': '7648363402758294805',
    'sec_uid': 'MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT',
    'unique_id': 'Dr.TiKToK',
    'video_id': '7648363402758294805',
    'csrf_token': 'fiFKgdVp-4kgcYqiKEsfJHANfGW33UfkAcZ4',
    'wid': '7658068252849030672',
}

# API endpoints for interaction (from Interactiontik.py TikTokInteractionBot)
INTERACTION_ENDPOINTS = {
    "send_like": {
        "url": "https://www.tiktok.com/api/live/digg/",
        "method": "POST",
        "payload_fields": ["room_id", "count", "type", "channel_id", "enter_method", "user_id"],
        "requires": ["csrf_token", "wid", "nonce", "room_id"],
    },
    "follow_user": {
        "url": "https://www.tiktok.com/api/relation/follow/",
        "method": "POST",
        "payload_fields": ["user_id", "sec_uid", "type", "channel_id", "enter_method"],
        "requires": ["csrf_token", "wid", "nonce", "sec_uid"],
    },
    "unfollow_user": {
        "url": "https://www.tiktok.com/api/relation/unfollow/",
        "method": "POST",
        "payload_fields": ["user_id", "sec_uid", "type", "channel_id"],
        "requires": ["csrf_token", "wid", "nonce", "sec_uid"],
    },
    "like_video": {
        "url": "https://www.tiktok.com/api/commit/item/digg/",
        "method": "POST",
        "payload_fields": ["aweme_id", "channel_id", "item_type", "type"],
        "requires": ["csrf_token", "wid", "nonce", "video_id"],
    },
    "send_comment": {
        "url": "https://www.tiktok.com/api/comment/publish/",
        "method": "POST",
        "payload_fields": ["aweme_id", "text", "channel_id", "type"],
        "requires": ["csrf_token", "wid", "nonce", "video_id"],
    },
    "enter_live_room": {
        "url": "https://webcast.tiktok.com/webcast/room/enter/",
        "method": "GET",
        "payload_fields": ["room_id", "aid", "device_platform", "user_id", "sec_uid"],
        "requires": ["room_id"],
    },
}


def _enrich_with_preloaded_accounts(result: ExtractionResult) -> None:
    """يستخدم PRELOADED_ACCOUNTS من Interactiontik.py كقاعدة بيانات للحسابات.

    يضيف:
    - قائمة الحسابات المتاحة (csrf_token, wid, nonce)
    - حساب الهدف (TARGET_ACCOUNT)
    - endpoints التفاعل الكاملة
    - جاهزية كل endpoint
    """
    # If we don't have csrf_token from HTML, use PRELOADED_ACCOUNTS as fallback
    if not result.security_credentials.get("csrf_token"):
        # Use the first preloaded account as a session source
        if PRELOADED_ACCOUNTS:
            account = PRELOADED_ACCOUNTS[0]
            result.security_credentials = {
                "csrf_token": account.get("csrf_token"),
                "wid": account.get("wid"),
                "nonce": account.get("nonce"),
                "source": "preloaded_account (fw__qg)",
                "note": "These credentials are from PRELOADED_ACCOUNTS in Interactiontik.py — they may be expired",
            }
            result.all_ids["csrf_token"] = account.get("csrf_token")
            result.all_ids["wid"] = account.get("wid")
            result.all_ids["nonce"] = account.get("nonce")
            result.raw_keys.append("preloaded_credentials")
            logger.info(f"✅ Using preloaded account credentials: csrf_token={account['csrf_token'][:20]}...")

    # Build full interaction endpoints with actual payloads
    sec_uid = result.author.sec_uid or result.all_ids.get("sec_user_id", "")
    user_id = result.author.user_id or result.all_ids.get("user_id", "")
    room_id = result.all_ids.get("room_id", "")
    csrf = result.security_credentials.get("csrf_token", "")
    wid = result.security_credentials.get("wid", "")
    nonce = result.security_credentials.get("nonce", "")
    video_id = result.content_id or room_id

    # Update interaction_analysis with preloaded data
    if result.interaction_analysis:
        result.interaction_analysis["preloaded_accounts"] = {
            "count": len(PRELOADED_ACCOUNTS),
            "accounts": [
                {
                    "unique_id": a.get("unique_id"),
                    "nickname": a.get("nickname"),
                    "follower_count": a.get("follower_count"),
                    "csrf_token": (a.get("csrf_token") or "")[:30] + "...",
                    "wid": a.get("wid"),
                    "nonce": (a.get("nonce") or "")[:30] + "...",
                    "room_id": a.get("room_id"),
                    "stream_id": a.get("stream_id"),
                }
                for a in PRELOADED_ACCOUNTS
            ],
        }
        result.interaction_analysis["target_account"] = {
            "unique_id": TARGET_ACCOUNT.get("unique_id"),
            "user_id": TARGET_ACCOUNT.get("user_id"),
            "csrf_token": (TARGET_ACCOUNT.get("csrf_token") or "")[:30] + "...",
            "wid": TARGET_ACCOUNT.get("wid"),
        }

        # Build ready-to-use payloads
        result.interaction_analysis["ready_payloads"] = {
            "send_like": {
                "url": "https://www.tiktok.com/api/live/digg/",
                "method": "POST",
                "headers": {
                    "X-CSRF-Token": csrf,
                    "X-Wid": wid,
                    "X-Nonce": nonce,
                    "Content-Type": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": f"https://www.tiktok.com/@{result.author.unique_id}/live" if result.author.unique_id else "https://www.tiktok.com/",
                },
                "payload": {
                    "room_id": room_id,
                    "count": 1,
                    "type": 1,
                    "channel_id": 6,
                    "enter_method": "share",
                    "user_id": user_id,
                },
                "ready": bool(csrf and wid and nonce and room_id),
            },
            "follow_user": {
                "url": "https://www.tiktok.com/api/relation/follow/",
                "method": "POST",
                "headers": {
                    "X-CSRF-Token": csrf,
                    "X-Wid": wid,
                    "X-Nonce": nonce,
                    "Content-Type": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/",
                },
                "payload": {
                    "user_id": user_id,
                    "sec_uid": sec_uid,
                    "type": 1,
                    "channel_id": 6,
                    "enter_method": "share",
                },
                "ready": bool(csrf and wid and nonce and sec_uid),
            },
            "like_video": {
                "url": "https://www.tiktok.com/api/commit/item/digg/",
                "method": "POST",
                "headers": {
                    "X-CSRF-Token": csrf,
                    "X-Wid": wid,
                    "X-Nonce": nonce,
                    "Content-Type": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/",
                },
                "payload": {
                    "aweme_id": video_id,
                    "channel_id": 6,
                    "item_type": 0,
                    "type": 1,
                },
                "ready": bool(csrf and wid and nonce and video_id),
            },
            "enter_live_room": {
                "url": "https://webcast.tiktok.com/webcast/room/enter/",
                "method": "GET",
                "params": {
                    "room_id": room_id,
                    "aid": "1988",
                    "device_platform": "web",
                    "user_id": user_id,
                    "sec_uid": sec_uid,
                },
                "ready": bool(room_id),
            },
            "send_comment": {
                "url": "https://www.tiktok.com/api/comment/publish/",
                "method": "POST",
                "headers": {
                    "X-CSRF-Token": csrf,
                    "X-Wid": wid,
                    "X-Nonce": nonce,
                    "Content-Type": "application/json",
                    "Origin": "https://www.tiktok.com",
                    "Referer": "https://www.tiktok.com/",
                },
                "payload": {
                    "aweme_id": video_id,
                    "text": " PLACEHOLDER_COMMENT_TEXT ",
                    "channel_id": 6,
                    "type": 1,
                },
                "ready": bool(csrf and wid and nonce and video_id),
            },
        }

        # Recalculate interaction readiness
        has_csrf = bool(csrf)
        has_wid = bool(wid)
        has_nonce = bool(nonce)
        has_target = bool(sec_uid or user_id)
        has_room = bool(room_id)
        result.interaction_analysis["interaction_ready"] = has_csrf and has_wid and has_nonce and has_target
        result.interaction_analysis["credentials_source"] = result.security_credentials.get("source", "html")
        result.interaction_analysis["missing_fields"] = {}
        if not has_csrf:
            result.interaction_analysis["missing_fields"]["csrf_token"] = "Not found in HTML (geo-blocked) and no preloaded account available"
        if not has_wid:
            result.interaction_analysis["missing_fields"]["wid"] = "Not found in HTML (geo-blocked)"
        if not has_nonce:
            result.interaction_analysis["missing_fields"]["nonce"] = "Not found in HTML (geo-blocked)"
        # sessionid and X-Bogus are always missing from server-side extraction
        result.interaction_analysis["missing_fields"]["sessionid"] = "Requires browser login — cannot extract server-side"
        result.interaction_analysis["missing_fields"]["X-Bogus"] = "Requires webmssdk.js JS runtime — needs Playwright"
        result.interaction_analysis["missing_fields"]["msToken"] = "Requires browser cookies — cannot extract server-side"

        logger.info(f"✅ Interaction analysis enriched with preloaded accounts + ready payloads: "
                    f"ready={result.interaction_analysis['interaction_ready']}")


# ════════════════════════════════════════════════════════════════════════════
#  v4.2: Deep Token Extraction — extract every possible credential from all sources
# ════════════════════════════════════════════════════════════════════════════

def _extract_tea_analytics(session: requests.Session, result: ExtractionResult) -> None:
    """يستخرج بيانات Tea Analytics من TikTok — web_id, user_unique_id, device_id.

    Tea هو نظام تتبع TikTok الداخلي. يستخدم web_id لربط الجلسات.
    """
    ua = USER_AGENTS[0]
    web_id = None

    # 1. Try to register a web_id via TikTok's API
    try:
        r = session.get(
            "https://m.tiktok.com/api/v1/webid/register/",
            params={"aid": "1988", "device_platform": "web"},
            headers={"User-Agent": ua, "Origin": "https://www.tiktok.com", "Referer": "https://www.tiktok.com/"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status_code") == 0:
                web_id = data.get("data", {}).get("web_id")
                if not web_id:
                    # Sometimes it's in a different path
                    web_id = data.get("web_id")
    except Exception:
        pass

    # 2. Check response headers for Set-Cookie
    try:
        for cookie in session.cookies:
            if cookie.name in ("ttwid", "sessionid", "sessionid_ss", "sid_tt", "passport_csrf_token"):
                if not result.security_credentials.get(cookie.name):
                    result.security_credentials[cookie.name] = cookie.value
                    result.all_ids[cookie.name] = cookie.value
                    logger.info(f"   ↳ Found cookie: {cookie.name}={cookie.value[:30]}...")
    except Exception:
        pass

    # 3. Try to get ttwid by requesting TikTok directly
    try:
        r2 = session.get(
            "https://www.tiktok.com/tiktok",
            headers={"User-Agent": ua},
            allow_redirects=False,
            timeout=10,
        )
        # Check Set-Cookie headers
        set_cookies = r2.headers.get("Set-Cookie", "")
        if set_cookies:
            for cookie_str in set_cookies.split(","):
                m = re.search(r'(\w+)=([^;]+)', cookie_str.strip())
                if m:
                    name, value = m.group(1), m.group(2)
                    if name in ("ttwid", "sessionid", "csrf", "sid") and not result.security_credentials.get(name):
                        result.security_credentials[name] = value
                        result.all_ids[name] = value
                        logger.info(f"   ↳ Found Set-Cookie: {name}={value[:30]}...")
    except Exception:
        pass

    if web_id:
        result.all_ids["tea_web_id"] = str(web_id)
        result.security_credentials["tea_web_id"] = str(web_id)
        logger.info(f"✅ Tea Analytics: web_id={web_id}")

    result.raw_keys.append("tea_analytics")


def _extract_slardar_data(result: ExtractionResult) -> None:
    """يستخرج بيانات Slardar (device fingerprint) من webcast_room_info.

    Slardar هو نظام مراقبة TikTok. يحتوي على deviceId و userId فريدين.
    """
    raw = result.raw_json or {}
    webcast = raw.get("webcast_room_info", {})
    data = webcast.get("data", {})

    slardar = {}
    # Check for device-related fields in webcast data
    for key in ("owner_device_id", "owner_device_id_str", "client_version", "app_id",
                "multi_stream_id", "search_id", "smb_board_id"):
        if data.get(key):
            slardar[key] = data.get(key)

    # Extract log_id (TikTok request tracking ID)
    log_pb = webcast.get("extra", {}).get("log_pb", {})
    if log_pb.get("impr_id"):
        slardar["impr_id"] = log_pb.get("impr_id")

    # Extract now timestamp from extra
    now_ts = webcast.get("extra", {}).get("now")
    if now_ts:
        from datetime import datetime as dt
        try:
            slardar["server_time"] = dt.utcfromtimestamp(now_ts / 1000).isoformat() + "Z"
        except Exception:
            slardar["server_time_ms"] = now_ts

    if slardar:
        result.security_credentials["slardar"] = slardar
        result.raw_keys.append("slardar_data")
        logger.info(f"✅ Slardar data: {list(slardar.keys())}")


def _extract_argus_token(result: ExtractionResult) -> None:
    """يستخرج توكن ARGUS_XSS_V3 من webcast data.

    ARGUS هو نظام حماية TikTok ضد XSS وال自动化.
    """
    raw = result.raw_json or {}
    webcast = raw.get("webcast_room_info", {})
    data = webcast.get("data", {})

    # Check for ARGUS-related fields
    argus = {}
    for key in list(data.keys()):
        if "argus" in key.lower() or "xss" in key.lower() or "risk" in key.lower():
            argus[key] = data.get(key)

    # Check for risk_control data
    risk_data = data.get("risk_control") or data.get("RiskControl") or {}
    if risk_data:
        argus["risk_control"] = risk_data

    # Check for deco_list (decoration/sticker data — can contain tracking pixels)
    deco_list = data.get("deco_list") or []
    if deco_list:
        argus["deco_count"] = len(deco_list)

    if argus:
        result.security_credentials["argus"] = argus
        result.raw_keys.append("argus_token")
        logger.info(f"✅ ARGUS token: {list(argus.keys())}")


def _deep_webcast_analysis(result: ExtractionResult) -> None:
    """تحليل عميق لكل حقل في webcast_room_info.data (242 مفتاح، 1490 حقل).

    يستخرج كل حقل غير فارغ ويصنّفه.
    """
    raw = result.raw_json or {}
    webcast = raw.get("webcast_room_info", {})
    data = webcast.get("data", {})

    if not data:
        return

    deep = {
        "total_fields_scanned": 0,
        "non_empty_fields": 0,
        "fields_by_category": {},
        "sensitive_fields_found": [],
        "interaction_relevant": {},
        "stream_metadata": {},
    }

    # Scan every top-level field
    for key, value in data.items():
        deep["total_fields_scanned"] += 1

        # Skip if empty/null/0
        if value is None or value == "" or value == 0 or value == [] or value == {}:
            continue

        deep["non_empty_fields"] += 1

        # Categorize
        if any(k in key.lower() for k in ("owner", "user", "author")):
            category = "user"
        elif any(k in key.lower() for k in ("room", "live", "stream", "title", "status")):
            category = "live"
        elif any(k in key.lower() for k in ("gift", "diamond", "donor", "fan", "rank")):
            category = "gifts"
        elif any(k in key.lower() for k in ("commerce", "ticket", "paid", "event")):
            category = "commerce"
        elif any(k in key.lower() for k in ("link_mic", "battle", "cohost", "multi")):
            category = "link_mic"
        elif any(k in key.lower() for k in ("cover", "image", "avatar", "icon", "sticker", "deco")):
            category = "media"
        elif any(k in key.lower() for k in ("create", "start", "finish", "time", "ping")):
            category = "timestamps"
        elif any(k in key.lower() for k in ("id", "sec_uid", "room_id", "stream_id")):
            category = "ids"
        else:
            category = "other"

        if category not in deep["fields_by_category"]:
            deep["fields_by_category"][category] = []
        deep["fields_by_category"][category].append(key)

        # Flag sensitive fields
        if any(k in key.lower() for k in ("sec_uid", "owner_id", "user_id", "room_id", "stream_id")):
            deep["sensitive_fields_found"].append({
                "field": key,
                "value": str(value)[:80] if not isinstance(value, (dict, list)) else f"({type(value).__name__})",
            })

        # Interaction-relevant fields
        if key in ("room_id", "stream_id", "owner_user_id", "owner_device_id",
                    "share_url", "live_id", "app_id", "content_tag",
                    "business_live", "anchor_tab_type"):
            deep["interaction_relevant"][key] = str(value)[:100] if not isinstance(value, (dict, list)) else f"({type(value).__name__})"

        # Stream metadata
        if key in ("title", "status", "create_time", "finish_time", "last_ping_time",
                    "cover", "cover_type", "client_version", "audio_mute",
                    "auto_cover", "book_time", "book_end_time"):
            deep["stream_metadata"][key] = str(value)[:100] if not isinstance(value, (dict, list)) else f"({type(value).__name__})"

    result.raw_json = result.raw_json or {}
    result.raw_json["deep_webcast_analysis"] = deep
    result.raw_keys.append("deep_webcast_analysis")
    logger.info(f"✅ Deep Webcast analysis: {deep['non_empty_fields']}/{deep['total_fields_scanned']} non-empty fields, "
                f"{len(deep['sensitive_fields_found'])} sensitive, "
                f"{len(deep['interaction_relevant'])} interaction-relevant")


def _build_interaction_fingerprint(result: ExtractionResult) -> None:
    """يبني بصمة تفاعل شاملة من جميع المصادر المتاحة.

    يجمع كل توكن + ID + credential من كل المصادر في مكان واحد
    ليكون جاهزاً للاستخدام في أي تفاعل.
    """
    fingerprint = {
        "target": {
            "unique_id": result.author.unique_id,
            "nickname": result.author.nickname,
            "user_id": result.author.user_id,
            "sec_uid": (result.author.sec_uid or "")[:60],
            "follower_count": result.author.follower_count,
            "following_count": result.author.following_count,
            "verified": result.author.verified,
            "signature": (result.author.signature or "")[:80],
            "avatar": (result.author.avatar or "")[:80],
        },
        "room": {
            "room_id": result.all_ids.get("room_id"),
            "stream_id": result.all_ids.get("stream_id"),
            "video_id": result.content_id,
            "share_url": result.all_ids.get("share_url"),
            "owner_room_ids": result.all_ids.get("owner_room_ids"),
            "is_live": result.live.get("is_live") if result.live else None,
            "viewer_count": result.live.get("viewer_count") if result.live else None,
            "enter_count": result.live.get("enter_count") if result.live else None,
            "replay_viewers": result.live.get("replay_viewers") if result.live else None,
            "start_time": result.live.get("start_time") if result.live else None,
        },
        "credentials": {},
        "targets_for_interaction": [],
        "ready_actions": [],
        "missing_for_full_interaction": [],
    }

    # Gather all credentials from all sources
    sc = result.security_credentials or {}
    all_creds = {**sc, **{k: v for k, v in result.all_ids.items()
                          if k in ("csrf_token", "wid", "nonce", "sessionid", "sessionid_ss",
                                    "sid_tt", "ttwid", "odin_tt", "passport_csrf_token", "msToken",
                                    "tea_web_id", "encrypted_webid", "request_id", "webid")}}

    for name, value in all_creds.items():
        if value and str(value) != "None" and str(value) != "":
            fingerprint["credentials"][name] = str(value)[:60] + ("..." if len(str(value)) > 60 else "")

    # Determine which actions are ready
    has_csrf = bool(fingerprint["credentials"].get("csrf_token"))
    has_wid = bool(fingerprint["credentials"].get("wid"))
    has_nonce = bool(fingerprint["credentials"].get("nonce"))
    has_sessionid = bool(fingerprint["credentials"].get("sessionid"))
    has_room = bool(fingerprint["room"].get("room_id"))
    has_target = bool(fingerprint["target"].get("sec_uid"))
    has_video = bool(fingerprint["room"].get("video_id"))

    actions = {
        "send_like": has_csrf and has_wid and has_nonce and has_room,
        "follow_user": has_csrf and has_wid and has_nonce and has_target,
        "like_video": has_csrf and has_wid and has_nonce and has_video,
        "send_comment": has_csrf and has_wid and has_nonce and has_video and has_sessionid,
        "enter_live_room": has_room,
        "unfollow_user": has_csrf and has_wid and has_nonce and has_target,
    }

    for action, ready in actions.items():
        if ready:
            fingerprint["ready_actions"].append(action)

    if not has_sessionid:
        fingerprint["missing_for_full_interaction"].append("sessionid (requires browser login)")
    if not fingerprint["credentials"].get("msToken"):
        fingerprint["missing_for_full_interaction"].append("msToken (requires browser cookies)")
    if not fingerprint["credentials"].get("ttwid"):
        fingerprint["missing_for_full_interaction"].append("ttwid (requires non-blocked IP)")

    # Add top_fans as interaction targets
    for fan in result.donor_rankings:
        fingerprint["targets_for_interaction"].append({
            "type": "top_fan",
            "unique_id": fan.get("unique_id"),
            "user_id": fan.get("user_id"),
            "sec_uid": (fan.get("sec_uid") or "")[:60],
            "follower_count": fan.get("follower_count"),
            "actions_available": ["follow_user", "like_video"] if has_csrf else [],
        })

    # Summary
    fingerprint["summary"] = {
        "total_credentials": len(fingerprint["credentials"]),
        "ready_actions_count": len(fingerprint["ready_actions"]),
        "targets_count": len(fingerprint["targets_for_interaction"]),
        "missing_count": len(fingerprint["missing_for_full_interaction"]),
        "can_interact": len(fingerprint["ready_actions"]) > 0,
        "can_comment": has_sessionid,
        "credential_sources": [k for k in result.raw_keys if "cred" in k or "preloaded" in k or "security" in k],
    }

    result.interaction_analysis = result.interaction_analysis or {}
    result.interaction_analysis["fingerprint"] = fingerprint
    result.raw_keys.append("interaction_fingerprint")
    logger.info(f"✅ Interaction fingerprint: {len(fingerprint['credentials'])} credentials, "
                f"{len(fingerprint['ready_actions'])} ready actions, "
                f"{len(fingerprint['targets_for_interaction'])} targets, "
                f"{len(fingerprint['missing_for_full_interaction'])} missing")


# ════════════════════════════════════════════════════════════════════════════
#  v4.3: Deep Analytics Processors — تحليل عميق للبيانات المُستخرَجة
# ════════════════════════════════════════════════════════════════════════════
#  تُضيف هذه المعالجات طبقة تحليلية فوق البيانات الخام:
#    - جودة التفاعل (engagement rate, velocity, density)
#    - اقتصاد الهدايا (coin value, rarity, distribution)
#    - صحة البث (codec, bitrate, stability metrics)
#    - درجة التأثير (weighted influence score 0-100)
#    - نموذج الجمهور (language, region, timezone hints)
#    - الملف الزمني (snapshot for temporal tracking)
#    - تقييم المخاطر (account verification, age, suspicious patterns)
#    - البيانات التجارية (shop links, product tags)
#    - تجميع شامل (aggregated v4.3 analytics summary)
# ════════════════════════════════════════════════════════════════════════════


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """قسمة آمنة تتجنب القسمة على صفر."""
    try:
        if not denominator or denominator == 0:
            return default
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """تحويل آمن إلى int."""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """تحويل آمن إلى float."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _classify_rate(value: float, low: float = 0.01, mid: float = 0.05, high: float = 0.15) -> str:
    """يصنّف نسبةً إلى: very_low / low / mid / high / very_high."""
    if value < low:
        return "very_low"
    elif value < mid:
        return "low"
    elif value < high:
        return "mid"
    elif value < high * 2:
        return "high"
    else:
        return "very_high"


def _analyze_engagement_quality(result: ExtractionResult) -> None:
    """يحلل جودة التفاعل على المحتوى/البث.

    يحسب:
      - engagement_rate: نسبة التفاعل الإجمالية (likes + comments + shares) / views
      - like_view_ratio: نسبة الإعجابات إلى المشاهدات
      - comment_density: كثافة التعليقات (تعليق لكل ألف مشاهدة)
      - share_velocity: سرعة المشاركة (مشاركة لكل ألف مشاهدة)
      - interaction_velocity: التفاعل لكل دقيقة (إذا توفّر create_time)
      - live_engagement_index: مؤشر تفاعل البث المباشر (viewer/follower ratio)
      - quality_grade: تقييم A/B/C/D/E بناءً على المعطيات
    """
    stats = result.stats
    views = _safe_int(stats.play_count)
    likes = _safe_int(stats.digg_count if hasattr(stats, 'digg_count') else 0)
    comments = _safe_int(stats.comment_count)
    shares = _safe_int(stats.share_count)

    # استخراج المزيد من live stats إذا توفّر
    live = result.live or {}
    live_viewers = _safe_int(live.get("viewer_count"))
    live_likes = _safe_int(live.get("like_count") or live.get("total_like"))
    live_comments = _safe_int(live.get("comment_count"))
    live_shares = _safe_int(live.get("share_count"))
    enter_count = _safe_int(live.get("enter_count"))
    replay_viewers = _safe_int(live.get("replay_viewers"))

    followers = _safe_int(result.author.follower_count)
    create_time = result.create_time
    now_ts = int(time.time())
    content_age_minutes = (now_ts - create_time) / 60.0 if create_time else 0

    # ── المعادلات ────────────────────────────────────────────────────────────
    engagement_rate = _safe_div(likes + comments + shares, max(views, 1))
    like_view_ratio = _safe_div(likes, max(views, 1))
    comment_density = _safe_div(comments * 1000, max(views, 1))
    share_velocity = _safe_div(shares * 1000, max(views, 1))
    interaction_velocity = _safe_div(likes + comments + shares, max(content_age_minutes, 1))

    # مؤشرات خاصة بالبث المباشر
    live_engagement_index = _safe_div(live_viewers, max(followers, 1))
    live_conversion_rate = _safe_div(live_viewers, max(enter_count, 1))
    live_drop_off_rate = _safe_div(enter_count - live_viewers, max(enter_count, 1))
    live_like_density = _safe_div(live_likes, max(live_viewers, 1))

    # ── التقييم الحرفي A/B/C/D/E ─────────────────────────────────────────────
    if engagement_rate >= 0.20:
        grade = "A+"
    elif engagement_rate >= 0.10:
        grade = "A"
    elif engagement_rate >= 0.05:
        grade = "B"
    elif engagement_rate >= 0.02:
        grade = "C"
    elif engagement_rate >= 0.01:
        grade = "D"
    else:
        grade = "E"

    result.engagement_quality = {
        "video_metrics": {
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement_rate": round(engagement_rate, 4),
            "like_view_ratio": round(like_view_ratio, 4),
            "comment_density": round(comment_density, 4),
            "share_velocity": round(share_velocity, 4),
            "interaction_velocity_per_min": round(interaction_velocity, 4),
            "content_age_minutes": round(content_age_minutes, 2),
            "classification": {
                "engagement_rate": _classify_rate(engagement_rate, 0.01, 0.05, 0.15),
                "comment_density": _classify_rate(comment_density, 0.5, 2, 10),
                "share_velocity": _classify_rate(share_velocity, 0.1, 1, 5),
            },
        },
        "live_metrics": {
            "viewer_count": live_viewers,
            "like_count": live_likes,
            "comment_count": live_comments,
            "share_count": live_shares,
            "enter_count": enter_count,
            "replay_viewers": replay_viewers,
            "live_engagement_index": round(live_engagement_index, 4),
            "live_conversion_rate": round(live_conversion_rate, 4),
            "live_drop_off_rate": round(live_drop_off_rate, 4),
            "live_like_density": round(live_like_density, 4),
        },
        "quality_grade": grade,
        "benchmark": {
            "avg_engagement_rate_short_video": 0.05,
            "avg_engagement_rate_live": 0.08,
            "above_avg_engagement": engagement_rate > 0.05,
            "above_avg_live_conversion": live_conversion_rate > 0.30,
        },
        "recommendations": _build_engagement_recommendations(
            grade, engagement_rate, live_conversion_rate, comment_density, share_velocity
        ),
    }
    result.raw_keys.append("engagement_quality")
    logger.info(f"📊 Engagement quality: grade={grade}, "
                f"engagement_rate={engagement_rate:.4f}, "
                f"live_conversion={live_conversion_rate:.4f}")


def _build_engagement_recommendations(grade: str, eng_rate: float,
                                      live_conv: float, comment_density: float,
                                      share_velocity: float) -> List[str]:
    """يبني توصيات قابلة للتنفيذ بناءً على المقاييس."""
    recs = []
    if grade in ("A+", "A"):
        recs.append("تفاعل ممتاز — حافظ على وتيرة النشر الحالية وزد من البث المباشر")
    elif grade in ("B", "C"):
        recs.append("تفاعل متوسط — جرّب تحسين العنوان والصورة المصغّرة لجذب مشاهدات أكثر")
    else:
        recs.append("تفاعل ضعيف — راجع جودة المحتوى وتوقيت النشر (جرب 8-10 مساءً بتوقيت الجمهور)")

    if comment_density < 1.0:
        recs.append("كثافة التعليقات منخفضة — أضف سؤالاً في الوصف أو استخدم CTA لتحفيز النقاش")
    if share_velocity < 1.0:
        recs.append("معدل المشاركة ضعيف — اجعل المحتوى أكثر قابلية للمشاركة (قوائم، إحصائيات، أخبار)")
    if live_conv < 0.30 and eng_rate > 0:
        recs.append("تحويل البث المباشر منخفض — افحص جودة الصورة والصوت قبل بدء البث")
    if not recs:
        recs.append("المقاييس ضمن النطاق الطبيعي — واصل المراقبة الأسبوعية")
    return recs


def _analyze_gift_economy(result: ExtractionResult) -> None:
    """يحلل اقتصاد الهدايا في البث المباشر.

    يستخرج من result.gift_list و result.donor_rankings:
      - total_gifts_available: عدد الهدايا المتاحة
      - gift_rarity_distribution: توزيع الندرة (common/rare/epic/legendary)
      - total_donated: إجمالي ما تبرع به أعلى المعجبين
      - top_donor_concentration: تركّز التبرعات (نسبة أعلى 3 معجبين من الإجمالي)
      - average_donor_contribution: متوسط تبرع المعجب
      - gift_value_distribution: توزيع قيم الهدايا (diamonds/coins)
      - economy_health: صحة اقتصاد البث (low/mid/high)
    """
    gifts = result.gift_list or []
    donors = result.donor_rankings or []

    if not gifts and not donors:
        result.gift_economy = {"available": False, "reason": "No gift data — not a live stream or no gifts sent"}
        result.raw_keys.append("gift_economy_empty")
        return

    # ── توزيع الندرة ─────────────────────────────────────────────────────────
    rarity_count = {}
    gift_values = []
    for g in gifts:
        rarity = (g.get("rarity") or g.get("gift_type") or "unknown").lower()
        rarity_count[rarity] = rarity_count.get(rarity, 0) + 1
        value = _safe_int(g.get("diamond_count") or g.get("value") or g.get("coin_count"))
        if value > 0:
            gift_values.append(value)

    # ── تحليل المعجبين ────────────────────────────────────────────────────────
    donor_amounts = []
    for d in donors:
        amount = _safe_int(d.get("total_sent") or d.get("contribution") or d.get("amount"))
        if amount > 0:
            donor_amounts.append(amount)

    total_donated = sum(donor_amounts)
    top3_sum = sum(sorted(donor_amounts, reverse=True)[:3])
    top3_concentration = _safe_div(top3_sum, total_donated) if total_donated else 0
    avg_donor = _safe_div(total_donated, len(donor_amounts)) if donor_amounts else 0

    # ── توزيع القيم ───────────────────────────────────────────────────────────
    if gift_values:
        gift_values_sorted = sorted(gift_values)
        median_value = gift_values_sorted[len(gift_values_sorted) // 2]
        max_value = max(gift_values)
        min_value = min(gift_values)
        avg_value = sum(gift_values) / len(gift_values)
    else:
        median_value = max_value = min_value = avg_value = 0

    # ── صحة الاقتصاد ─────────────────────────────────────────────────────────
    if total_donated > 100000:
        health = "very_high"
    elif total_donated > 10000:
        health = "high"
    elif total_donated > 1000:
        health = "mid"
    elif total_donated > 0:
        health = "low"
    else:
        health = "none"

    # ── تركّز التبرعات (مؤشر الاعتماد على معجبين قلّة) ──────────────────────────
    if top3_concentration > 0.7:
        concentration_label = "high_concentration"
        concentration_note = "اعتماد كبير على عدد قليل من المعجبين — خطر اقتصادي"
    elif top3_concentration > 0.4:
        concentration_label = "medium_concentration"
        concentration_note = "توزيع متوازن نسبياً بين المعجبين الرئيسيين"
    else:
        concentration_label = "diversified"
        concentration_note = "توزيع متنوّع — قاعدة معجبين صحية"

    result.gift_economy = {
        "available": True,
        "total_gifts_available": len(gifts),
        "rarity_distribution": rarity_count,
        "gift_value_stats": {
            "min_diamonds": min_value,
            "max_diamonds": max_value,
            "median_diamonds": median_value,
            "average_diamonds": round(avg_value, 2),
            "total_unique_values": len(set(gift_values)),
        },
        "donor_stats": {
            "total_donors_analyzed": len(donor_amounts),
            "total_donated_diamonds": total_donated,
            "top_donor_concentration": round(top3_concentration, 4),
            "average_donor_contribution": round(avg_donor, 2),
            "concentration_label": concentration_label,
            "concentration_note": concentration_note,
        },
        "top_donors_summary": [
            {
                "rank": i + 1,
                "unique_id": d.get("unique_id"),
                "user_id": d.get("user_id"),
                "amount": _safe_int(d.get("total_sent") or d.get("contribution")),
                "follower_count": d.get("follower_count"),
            }
            for i, d in enumerate(donors[:5])
        ],
        "economy_health": health,
    }
    result.raw_keys.append("gift_economy")
    logger.info(f"💰 Gift economy: {len(gifts)} gifts, {len(donor_amounts)} donors, "
                f"total={total_donated}, health={health}, concentration={concentration_label}")


def _detect_stream_health(result: ExtractionResult) -> None:
    """يحلل صحة روابط البث وجودتها.

    يفحص:
      - codec: H.264 / H.265 / VP9 / AV1
      - resolution: 360p / 480p / 720p / 1080p / 4K
      - fps: 24 / 30 / 60
      - bitrate_estimate: ميجابت/ثانية (تقديري من الحجم)
      - cdn_distribution: عدد خوادم CDN المختلفة
      - url_expiry_risk: خطر انتهاء صلاحية الرابط (دقائق متبقية)
      - format_redundancy: هل هناك نسخ احتياطية متعددة؟
      - stability_score: 0-100 (بناءً على عدة عوامل)
    """
    video = result.video
    stream_access = result.stream_access or {}
    cdn_meta = result.cdn_metadata or {}

    all_urls = []
    # Video class uses play_url and download_url (no .url attribute)
    if getattr(video, "play_url", None):
        all_urls.append(video.play_url)
    if getattr(video, "download_url", None):
        all_urls.append(video.download_url)
    formats = stream_access.get("signed_urls") or stream_access.get("formats") or []
    if isinstance(formats, list):
        for f in formats:
            if isinstance(f, dict):
                u = f.get("url") or f.get("signed_url")
                if u:
                    all_urls.append(u)
            elif isinstance(f, str):
                all_urls.append(f)

    if not all_urls:
        result.stream_health = {"available": False, "reason": "No stream URLs available"}
        result.raw_keys.append("stream_health_empty")
        return

    # ── تحليل الروابط ─────────────────────────────────────────────────────────
    codec_pattern = re.compile(r'(h264|h265|hevc|vp9|av1|avc)', re.IGNORECASE)
    res_pattern = re.compile(r'_(\d{3,4})p?[_\.]')
    fps_pattern = re.compile(r'_(\d{2})fps', re.IGNORECASE)

    codecs_detected = set()
    resolutions_detected = set()
    fps_detected = set()
    cdn_hosts = set()
    expiries = []

    for url in all_urls:
        if not isinstance(url, str):
            continue
        m = codec_pattern.search(url)
        if m:
            codecs_detected.add(m.group(1).lower())
        m = res_pattern.search(url)
        if m:
            resolutions_detected.add(int(m.group(1)))
        m = fps_pattern.search(url)
        if m:
            fps_detected.add(int(m.group(1)))

        # CDN host
        try:
            host = urlparse(url).netloc
            if host:
                cdn_hosts.add(host)
        except Exception:
            pass

        # expiry time
        try:
            params = parse_qs(urlparse(url).query)
            for k in ("X-Tt-Token", "expire", "expires", "token_expire"):
                if k in params:
                    val = params[k][0]
                    # حاول فكّ timestamp
                    if val.isdigit():
                        ts = int(val)
                        if ts > 1_000_000_000:  # looks like epoch
                            expiries.append(ts)
        except Exception:
            pass

    # ── تقييم الاستقرار ──────────────────────────────────────────────────────
    score = 0
    reasons = []

    if codecs_detected:
        score += 15
        reasons.append(f"codec detected: {codecs_detected}")
    if resolutions_detected:
        score += 15
        reasons.append(f"resolutions: {sorted(resolutions_detected)}")
    if fps_detected:
        score += 10
        reasons.append(f"fps: {sorted(fps_detected)}")
    if len(cdn_hosts) > 1:
        score += 25
        reasons.append(f"multiple CDNs: {len(cdn_hosts)}")
    elif len(cdn_hosts) == 1:
        score += 10
        reasons.append("single CDN")
    if len(all_urls) >= 3:
        score += 20
        reasons.append(f"format redundancy: {len(all_urls)} URLs")
    elif len(all_urls) >= 1:
        score += 10

    # expiry risk
    now_ts = int(time.time())
    expiry_risk = "unknown"
    minutes_to_expiry = None
    if expiries:
        soonest_expiry = min(expiries)
        minutes_to_expiry = max(0, (soonest_expiry - now_ts) // 60)
        if minutes_to_expiry < 5:
            expiry_risk = "critical"
            score -= 30
        elif minutes_to_expiry < 30:
            expiry_risk = "high"
            score -= 10
        elif minutes_to_expiry < 60:
            expiry_risk = "medium"
        else:
            expiry_risk = "low"
            score += 10

    score = max(0, min(100, score))

    result.stream_health = {
        "available": True,
        "total_stream_urls": len(all_urls),
        "codecs_detected": sorted(codecs_detected),
        "resolutions_detected": sorted(resolutions_detected),
        "fps_detected": sorted(fps_detected),
        "cdn_hosts": sorted(cdn_hosts),
        "cdn_distribution_count": len(cdn_hosts),
        "format_redundancy": len(all_urls) >= 3,
        "expiry_risk": expiry_risk,
        "minutes_to_expiry": minutes_to_expiry,
        "stability_score": score,
        "stability_label": "excellent" if score >= 80 else "good" if score >= 60 else "fair" if score >= 40 else "poor",
        "scoring_factors": reasons,
    }
    result.raw_keys.append("stream_health")
    logger.info(f"🎬 Stream health: score={score}/100, "
                f"codecs={len(codecs_detected)}, CDNs={len(cdn_hosts)}, "
                f"expiry={expiry_risk}")


def _compute_influence_score(result: ExtractionResult) -> None:
    """يحسب درجة التأثير من 0 إلى 100 بناءً على معطيات متعددة.

    المعادلة المرجّحة:
      - followers (40%): log-scaled score
      - engagement (25%): من engagement_quality
      - verification (15%): +15 إذا موثّق
      - live presence (10%): +10 إذا يبث الآن
      - content freshness (10%): محتوى جديد خلال آخر 7 أيام
    """
    followers = _safe_int(result.author.follower_count)
    verified = bool(result.author.verified)
    live = result.live or {}
    is_live = bool(live.get("is_live"))
    create_time = result.create_time or 0
    now_ts = int(time.time())
    days_since_post = (now_ts - create_time) / 86400.0 if create_time else 999

    # ── درجة المتابعين (log-scaled) ────────────────────────────────────────────
    import math
    if followers <= 0:
        follower_score = 0
    elif followers < 1000:
        follower_score = 10 + (followers / 1000) * 10  # 10-20
    elif followers < 10000:
        follower_score = 20 + (math.log10(followers / 1000)) * 15  # 20-35
    elif followers < 100000:
        follower_score = 35 + (math.log10(followers / 10000)) * 20  # 35-55
    elif followers < 1000000:
        follower_score = 55 + (math.log10(followers / 100000)) * 20  # 55-75
    else:
        follower_score = min(100, 75 + (math.log10(followers / 1000000)) * 15)

    # ── درجة التفاعل ───────────────────────────────────────────────────────────
    eq = result.engagement_quality or {}
    vm = eq.get("video_metrics", {})
    engagement_rate = _safe_float(vm.get("engagement_rate"))
    if engagement_rate >= 0.15:
        engagement_score = 100
    elif engagement_rate >= 0.10:
        engagement_score = 85
    elif engagement_rate >= 0.05:
        engagement_score = 65
    elif engagement_rate >= 0.02:
        engagement_score = 40
    elif engagement_rate > 0:
        engagement_score = 20
    else:
        engagement_score = 0

    # ── درجة التوثيق ──────────────────────────────────────────────────────────
    verification_score = 100 if verified else 30

    # ── درجة البث المباشر ──────────────────────────────────────────────────────
    live_score = 100 if is_live else 50

    # ── درجة نضارة المحتوى ─────────────────────────────────────────────────────
    if days_since_post < 1:
        freshness_score = 100
    elif days_since_post < 7:
        freshness_score = 90 - (days_since_post * 5)
    elif days_since_post < 30:
        freshness_score = 60 - (days_since_post - 7) * 1
    elif days_since_post < 90:
        freshness_score = 30
    else:
        freshness_score = 10

    # ── المجموع المرجّح ────────────────────────────────────────────────────────
    total = (
        follower_score * 0.40 +
        engagement_score * 0.25 +
        verification_score * 0.15 +
        live_score * 0.10 +
        freshness_score * 0.10
    )

    # ── التصنيف ──────────────────────────────────────────────────────────────────
    if total >= 90:
        tier = "tier_1_megastar"
    elif total >= 75:
        tier = "tier_2_influencer"
    elif total >= 55:
        tier = "tier_3_creator"
    elif total >= 35:
        tier = "tier_4_growing"
    else:
        tier = "tier_5_novice"

    result.influence_score = {
        "total_score": round(total, 2),
        "tier": tier,
        "components": {
            "follower_score": round(follower_score, 2),
            "engagement_score": round(engagement_score, 2),
            "verification_score": verification_score,
            "live_presence_score": live_score,
            "content_freshness_score": round(freshness_score, 2),
        },
        "weights": {
            "follower_score": 0.40,
            "engagement_score": 0.25,
            "verification_score": 0.15,
            "live_presence_score": 0.10,
            "content_freshness_score": 0.10,
        },
        "input_data": {
            "followers": followers,
            "verified": verified,
            "is_live": is_live,
            "engagement_rate": round(engagement_rate, 4),
            "days_since_last_post": round(days_since_post, 2),
        },
    }
    result.raw_keys.append("influence_score")
    logger.info(f"⭐ Influence score: {total:.1f}/100, tier={tier}")


def _build_audience_profile(result: ExtractionResult) -> None:
    """يبني نموذج أولي للجمهور من المعطيات المتاحة.

    يستخرج:
      - language_hints: من signature, bio, title
      - region_hints: من cdn_metadata, stream_access, all_ids
      - timezone_hints: من start_time, create_time (UTC offset)
      - active_hours_estimate: تقدير ساعات الذروة (إذا توفّر create_time)
      - audience_size_estimate: تقدير حجم الجمهور من followers + avg_viewers
    """
    # ── لغة من النص ────────────────────────────────────────────────────────────
    arabic_pattern = re.compile(r'[\u0600-\u06FF]')
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    russian_pattern = re.compile(r'[\u0400-\u04FF]')
    latin_pattern = re.compile(r'[a-zA-Z]')

    text_sources = []
    if result.title:
        text_sources.append(result.title)
    if result.description:
        text_sources.append(result.description)
    if result.author.signature:
        text_sources.append(result.author.signature)
    if result.author.nickname:
        text_sources.append(result.author.nickname)
    combined_text = " ".join(text_sources)

    languages_detected = []
    if arabic_pattern.search(combined_text):
        languages_detected.append("ar")
    if chinese_pattern.search(combined_text):
        languages_detected.append("zh")
    if russian_pattern.search(combined_text):
        languages_detected.append("ru")
    if latin_pattern.search(combined_text):
        languages_detected.append("en")

    # ── منطقة من CDN ────────────────────────────────────────────────────────────
    cdn_meta = result.cdn_metadata or {}
    region_hints = set()
    if cdn_meta.get("region"):
        region_hints.add(str(cdn_meta["region"]).lower())
    if cdn_meta.get("datacenter"):
        region_hints.add(str(cdn_meta["datacenter"]).lower())
    if cdn_meta.get("idc"):
        region_hints.add(str(cdn_meta["idc"]).lower())

    # استخراج منطقة من avatar URL
    avatar = result.author.avatar or ""
    if avatar:
        m = re.search(r'[?&](?:region|idc|app|lang)=([a-z]{2})', avatar, re.IGNORECASE)
        if m:
            region_hints.add(m.group(1).lower())

    # ── timezone من create_time ─────────────────────────────────────────────────
    create_time = result.create_time
    active_hours_utc = None
    if create_time:
        try:
            dt = datetime.utcfromtimestamp(create_time)
            active_hours_utc = dt.hour
        except Exception:
            pass

    # ── تقدير حجم الجمهور ────────────────────────────────────────────────────────
    followers = _safe_int(result.author.follower_count)
    live_viewers = _safe_int((result.live or {}).get("viewer_count"))
    views = _safe_int(result.stats.play_count)

    # تقدير: الجمهور النشط ≈ 5-15% من المتابعين
    estimated_active_audience = int(followers * 0.10) if followers else 0

    # ── ساعات الذروة (نظري بناءً على المنطقة إن وُجدت) ────────────────────────────
    peak_hours_estimate = []
    for region in region_hints:
        if region in ("eg", "sa", "ae", "kw", "qa", "bh", "om", "jo", "iq", "ye", "ps", "lb", "sy"):
            peak_hours_estimate.append({"region": region, "peak_hours_utc": "18:00-22:00", "note": "GCC/MENA evening"})
        elif region in ("us", "ca", "mx"):
            peak_hours_estimate.append({"region": region, "peak_hours_utc": "01:00-05:00", "note": "Americas evening"})
        elif region in ("gb", "fr", "de", "es", "it", "nl"):
            peak_hours_estimate.append({"region": region, "peak_hours_utc": "19:00-23:00", "note": "Europe evening"})
        elif region in ("id", "my", "th", "ph", "vn", "sg"):
            peak_hours_estimate.append({"region": region, "peak_hours_utc": "13:00-16:00", "note": "SEA evening"})

    result.audience_profile = {
        "languages_detected": languages_detected,
        "primary_language_guess": languages_detected[0] if languages_detected else None,
        "region_hints": sorted(region_hints),
        "active_hours_utc": active_hours_utc,
        "estimated_active_audience": estimated_active_audience,
        "audience_size_breakdown": {
            "followers": followers,
            "live_viewers": live_viewers,
            "video_views": views,
            "live_to_follower_ratio": round(_safe_div(live_viewers, max(followers, 1)), 4),
            "views_to_follower_ratio": round(_safe_div(views, max(followers, 1)), 4),
        },
        "peak_hours_estimate": peak_hours_estimate,
        "data_confidence": "high" if region_hints and languages_detected else "medium" if languages_detected else "low",
    }
    result.raw_keys.append("audience_profile")
    logger.info(f"👥 Audience profile: langs={languages_detected}, "
                f"regions={sorted(region_hints)}, "
                f"estimated_active={estimated_active_audience}")


def _build_temporal_profile(result: ExtractionResult) -> None:
    """يبني لقطة زمنية للبيانات لتتبع التغييرات.

    يُنتج snapshot يمكن مقارنته مع لقطات مستقبلية لكشف:
      - نمو/انخفاض المتابعين
      - تغيير في المشاهدات
      - تغيير في حالة البث (live ↔ offline)
      - تغيير في درجة التأثير
    """
    followers = _safe_int(result.author.follower_count)
    following = _safe_int(result.author.following_count)
    likes_total = _safe_int(result.author.likes_count if hasattr(result.author, 'likes_count') else 0)
    views = _safe_int(result.stats.play_count)
    likes = _safe_int(result.stats.digg_count if hasattr(result.stats, 'digg_count') else 0)
    comments = _safe_int(result.stats.comment_count)
    shares = _safe_int(result.stats.share_count)
    live = result.live or {}
    viewer_count = _safe_int(live.get("viewer_count"))
    enter_count = _safe_int(live.get("enter_count"))
    is_live = bool(live.get("is_live"))

    snapshot = {
        "snapshot_at": datetime.utcnow().isoformat() + "Z",
        "snapshot_timestamp": int(time.time()),
        "target": {
            "unique_id": result.author.unique_id,
            "user_id": result.author.user_id,
            "sec_uid": (result.author.sec_uid or "")[:60],
        },
        "metrics": {
            "followers": followers,
            "following": following,
            "total_likes_received": likes_total,
            "video_views": views,
            "video_likes": likes,
            "video_comments": comments,
            "video_shares": shares,
            "live_is_streaming": is_live,
            "live_viewer_count": viewer_count,
            "live_enter_count": enter_count,
        },
        "influence_score_at_snapshot": (result.influence_score or {}).get("total_score"),
        "engagement_grade_at_snapshot": (result.engagement_quality or {}).get("quality_grade"),
    }

    # ── تخزين اللقطة في ملف منفصل لتتبع التغيرات عبر الزمن ─────────────────────
    try:
        snapshots_dir = "/tmp/tiktok_snapshots"
        os.makedirs(snapshots_dir, exist_ok=True)
        uid = result.author.unique_id or result.author.user_id or "unknown"
        safe_uid = re.sub(r'[^a-zA-Z0-9_]', '_', str(uid))
        snapshot_file = os.path.join(snapshots_dir, f"{safe_uid}_snapshots.json")

        history = []
        if os.path.exists(snapshot_file):
            try:
                with open(snapshot_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []

        # ── حساب التغييرات عن آخر لقطة ────────────────────────────────────────────
        delta = None
        if history and isinstance(history[-1], dict):
            last = history[-1]
            last_metrics = last.get("metrics") or {}
            if not isinstance(last_metrics, dict):
                last_metrics = {}
            delta = {
                "compared_to": last.get("snapshot_at"),
                "followers_change": followers - _safe_int(last_metrics.get("followers")),
                "views_change": views - _safe_int(last_metrics.get("video_views")),
                "likes_change": likes - _safe_int(last_metrics.get("video_likes")),
                "viewer_count_change": viewer_count - _safe_int(last_metrics.get("live_viewer_count")),
                "minutes_since_last": round((int(time.time()) - _safe_int(last.get("snapshot_timestamp"))) / 60.0, 2),
            }

        snapshot["delta_from_last"] = delta
        history.append(snapshot)

        # احتفظ بآخر 100 لقطة فقط
        if len(history) > 100:
            history = history[-100:]

        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.debug(f"Snapshot persistence failed: {e}")

    result.temporal_profile = snapshot
    result.raw_keys.append("temporal_profile")
    delta_obj = snapshot.get('delta_from_last') or {}
    if not isinstance(delta_obj, dict):
        delta_obj = {}
    delta_followers = delta_obj.get('followers_change', 'first snapshot')
    logger.info(f"⏱️  Temporal snapshot: followers={followers}, "
                f"delta={delta_followers}")


def _assess_account_risk(result: ExtractionResult) -> None:
    """يقيّم مخاطر الحساب ومستوى الثقة.

    يفحص:
      - verification_status: موثّق رسمياً أم لا
      - account_age_estimate: تقدير عمر الحساب من sec_uid/user_id
      - follower_anomaly: هل عدد المتابعين متناسق مع المشاهدات؟
      - risk_factors: قائمة بالمؤشرات المشبوهة
      - trust_score: 0-100 (الأعلى = أكثر ثقة)
    """
    risk_factors = []
    trust_signals = []

    # ── التوثيق ────────────────────────────────────────────────────────────────
    if result.author.verified:
        trust_signals.append("verified_account")
    else:
        risk_factors.append("not_verified")

    # ─ـ تناسق المتابعين مع المشاهدات ───────────────────────────────────────────
    followers = _safe_int(result.author.follower_count)
    views = _safe_int(result.stats.play_count)
    likes = _safe_int(result.stats.digg_count if hasattr(result.stats, 'digg_count') else 0)

    if followers > 0 and views > 0:
        views_to_followers = views / followers
        if views_to_followers < 0.05:
            risk_factors.append(f"very_low_view_to_follower_ratio ({views_to_followers:.4f}) — may indicate fake followers")
        elif views_to_followers > 100:
            risk_factors.append(f"abnormally_high_view_to_follower_ratio ({views_to_followers:.2f}) — possible viral anomaly")
        else:
            trust_signals.append(f"normal_view_to_follower_ratio ({views_to_followers:.4f})")

    if likes > 0 and views > 0:
        likes_to_views = likes / views
        if likes_to_views > 0.5:
            risk_factors.append(f"abnormally_high_like_ratio ({likes_to_views:.4f}) — possible manipulation")
        elif likes_to_views < 0.005:
            risk_factors.append(f"very_low_like_ratio ({likes_to_views:.4f}) — low quality content indicator")

    # ── توقيت الإنشاء (create_time) ───────────────────────────────────────────
    create_time = result.create_time
    if create_time:
        now_ts = int(time.time())
        age_days = (now_ts - create_time) / 86400.0
        if age_days < 0:
            risk_factors.append(f"future_dated_content ({age_days:.2f} days in future) — impossible timestamp")
        elif age_days > 365 * 2:
            trust_signals.append(f"old_content ({age_days:.0f} days) — long-lived")

    # ── sec_uid length (TikTok uses fixed-length sec_uids) ─────────────────────
    sec_uid = result.author.sec_uid or ""
    if sec_uid:
        if len(sec_uid) < 30:
            risk_factors.append(f"unusually_short_sec_uid ({len(sec_uid)} chars)")
        else:
            trust_signals.append(f"normal_sec_uid_length ({len(sec_uid)} chars)")

    # ── Trust Score ────────────────────────────────────────────────────────────
    trust_score = 50  # baseline
    trust_score += len(trust_signals) * 10
    trust_score -= len(risk_factors) * 15
    trust_score = max(0, min(100, trust_score))

    if trust_score >= 80:
        trust_label = "high_trust"
    elif trust_score >= 60:
        trust_label = "moderate_trust"
    elif trust_score >= 40:
        trust_label = "neutral"
    elif trust_score >= 20:
        trust_label = "suspicious"
    else:
        trust_label = "high_risk"

    result.account_risk = {
        "trust_score": trust_score,
        "trust_label": trust_label,
        "verification_status": "verified" if result.author.verified else "not_verified",
        "risk_factors": risk_factors,
        "trust_signals": trust_signals,
        "audited_metrics": {
            "followers": followers,
            "views": views,
            "likes": likes,
            "views_to_followers_ratio": round(_safe_div(views, max(followers, 1)), 4),
            "likes_to_views_ratio": round(_safe_div(likes, max(views, 1)), 4),
        },
    }
    result.raw_keys.append("account_risk")
    logger.info(f"🛡️  Account risk: trust={trust_score}/100 ({trust_label}), "
                f"factors={len(risk_factors)}, signals={len(trust_signals)}")


def _extract_commerce_data(result: ExtractionResult) -> None:
    """يستخرج البيانات التجارية من المحتوى (روابط متاجر، منتجات).

    يبحث في:
      - description: روابط TikTok Shop
      - signature: روابط bio
      - html_analysis: أي روابط تجارية
      - all_ids: product_id, shop_id إن وُجدت
    """
    commerce = {
        "shop_links": [],
        "product_links": [],
        "external_links": [],
        "has_tiktok_shop": False,
        "detected_product_ids": [],
        "bio_link": None,
    }

    # ── تجميع النصوص ──────────────────────────────────────────────────────────
    text_sources = []
    if result.description:
        text_sources.append(result.description)
    if result.author.signature:
        text_sources.append(result.author.signature)
    html_an = result.html_analysis or {}
    inline_js = html_an.get("inline_js_snippets", []) if isinstance(html_an, dict) else []
    if isinstance(inline_js, list):
        text_sources.extend([s for s in inline_js if isinstance(s, str)][:5])

    combined = " ".join(text_sources)

    # ── أنماط الروابط ─────────────────────────────────────────────────────────
    shop_pattern = re.compile(r'https?://shop\.tiktok\.com/view/[\w\-]+', re.IGNORECASE)
    product_pattern = re.compile(r'https?://\w*\.?tiktok\.com/view/product/[\w\-]+', re.IGNORECASE)
    generic_tiktok_pattern = re.compile(r'https?://(?:www\.|m\.)?tiktok\.com/[^\s"\'<>]+', re.IGNORECASE)
    external_pattern = re.compile(r'https?://(?:linktr\.ee|bio\.link|wa\.me|t\.me|instagram\.com|youtube\.com|snapchat\.com|twitter\.com|x\.com|facebook\.com)/[^\s"\'<>]+', re.IGNORECASE)

    for m in shop_pattern.finditer(combined):
        commerce["shop_links"].append(m.group(0))
        commerce["has_tiktok_shop"] = True

    for m in product_pattern.finditer(combined):
        commerce["product_links"].append(m.group(0))

    for m in external_pattern.finditer(combined):
        commerce["external_links"].append(m.group(0))

    # bio link (عادةً يكون في signature)
    if result.author.signature:
        bio_match = re.search(r'(https?://[^\s]+)', result.author.signature)
        if bio_match:
            commerce["bio_link"] = bio_match.group(1)

    # ── product_id, shop_id من all_ids ─────────────────────────────────────────
    for k, v in (result.all_ids or {}).items():
        if k in ("product_id", "shop_id", "product_id_str", "shop_id_str") and v:
            commerce["detected_product_ids"].append({"key": k, "value": str(v)})

    # ── من html_analysis ─────────────────────────────────────────────────────────
    if isinstance(html_an, dict):
        for k, v in html_an.items():
            if "commerce" in k.lower() or "shop" in k.lower() or "product" in k.lower():
                commerce[f"html_{k}"] = v

    commerce["total_commerce_signals"] = (
        len(commerce["shop_links"]) +
        len(commerce["product_links"]) +
        len(commerce["external_links"]) +
        len(commerce["detected_product_ids"]) +
        (1 if commerce["bio_link"] else 0)
    )

    result.commerce_data = commerce
    result.raw_keys.append("commerce_data")
    logger.info(f"🛒 Commerce data: {commerce['total_commerce_signals']} signals "
                f"(shop={len(commerce['shop_links'])}, product={len(commerce['product_links'])}, "
                f"external={len(commerce['external_links'])})")


def _aggregate_deep_analytics(result: ExtractionResult) -> None:
    """يجمع كل تحليلات v4.3 في ملخّص واحد سهل القراءة.

    يُنتج result.deep_analytics يحتوي على:
      - v4.3_version: رقم الإصدار
      - processors_run: قائمة المعالجات المنفّذة
      - headline_metrics: أهم 5 مقاييس
      - actionable_insights: توصيات قابلة للتنفيذ
      - comparison_vs_benchmark: مقارنة بالمعايير
    """
    # ── استخراج أهم المقاييس ──────────────────────────────────────────────────
    influence = result.influence_score or {}
    engagement = result.engagement_quality or {}
    gift_eco = result.gift_economy or {}
    stream_h = result.stream_health or {}
    audience = result.audience_profile or {}
    risk = result.account_risk or {}
    temporal = result.temporal_profile or {}

    headline = {
        "influence_score": influence.get("total_score"),
        "influence_tier": influence.get("tier"),
        "engagement_grade": engagement.get("quality_grade"),
        "engagement_rate": (engagement.get("video_metrics") or {}).get("engagement_rate"),
        "live_conversion_rate": (engagement.get("live_metrics") or {}).get("live_conversion_rate"),
        "stream_stability_score": stream_h.get("stability_score") if isinstance(stream_h, dict) else None,
        "gift_economy_health": gift_eco.get("economy_health") if isinstance(gift_eco, dict) else None,
        "trust_score": risk.get("trust_score") if isinstance(risk, dict) else None,
        "followers": temporal.get("metrics", {}).get("followers"),
        "viewer_count": temporal.get("metrics", {}).get("live_viewer_count"),
    }

    # ── توصيات قابلة للتنفيذ ────────────────────────────────────────────────────
    insights = []

    # من التفاعل
    recs = (engagement.get("recommendations") or [])[:2]
    insights.extend(recs)

    # من المخاطر
    if isinstance(risk, dict) and risk.get("trust_label") in ("high_risk", "suspicious"):
        insights.append("⚠️ حساب مشبوه — راجع التوثيق قبل التعاون التجاري")

    # من صحة البث
    if isinstance(stream_h, dict) and stream_h.get("expiry_risk") in ("critical", "high"):
        insights.append(f"⏳ روابط البث ستنتهي خلال {stream_h.get('minutes_to_expiry')} دقيقة — حمّل الفيديو الآن")

    # من اقتصاد الهدايا
    if isinstance(gift_eco, dict) and gift_eco.get("available"):
        if gift_eco.get("economy_health") in ("high", "very_high"):
            insights.append("💰 اقتصاد هدايا قوي — ابدأ بثاً مباشراً قريباً لتحقيق دخل")
        if (gift_eco.get("donor_stats") or {}).get("concentration_label") == "high_concentration":
            insights.append("🎯 اعتماد على عدد قليل من المعجبين — وفّر محتوى يجذب معجبين جدد")

    # من درجة التأثير
    if influence.get("total_score") and influence["total_score"] >= 75:
        insights.append("⭐ مؤثر عالي التأثير — مناسب للتعاون التجاري مع العلامات")

    # من الجمهور
    if isinstance(audience, dict) and audience.get("languages_detected"):
        langs = audience["languages_detected"]
        if "ar" in langs:
            insights.append("🌍 محتوى عربي — استهدف منطقة MENA في الإعلانات")

    if not insights:
        insights.append("📊 لا توصيات مستعجلة — واصل المراقبة الدورية")

    # ── مقارنة بالمعايير ──────────────────────────────────────────────────────
    benchmark = (engagement.get("benchmark") or {}) if isinstance(engagement, dict) else {}
    comparison = {
        "above_avg_engagement": benchmark.get("above_avg_engagement", False),
        "above_avg_live_conversion": benchmark.get("above_avg_live_conversion", False),
        "influence_vs_avg": (
            "above_avg" if (influence.get("total_score") or 0) >= 55
            else "below_avg" if (influence.get("total_score") or 0) < 35
            else "average"
        ),
        "trust_vs_avg": (
            "above_avg" if (risk.get("trust_score") if isinstance(risk, dict) else 0) >= 70
            else "below_avg" if (risk.get("trust_score") if isinstance(risk, dict) else 50) < 40
            else "average"
        ),
    }

    result.deep_analytics = {
        "v43_version": "v4.3_deep_analytics",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "processors_run": [
            "_analyze_engagement_quality",
            "_analyze_gift_economy",
            "_detect_stream_health",
            "_compute_influence_score",
            "_build_audience_profile",
            "_build_temporal_profile",
            "_assess_account_risk",
            "_extract_commerce_data",
            "_aggregate_deep_analytics",
        ],
        "headline_metrics": headline,
        "actionable_insights": insights,
        "comparison_vs_benchmark": comparison,
        "summary_text": _build_summary_text(headline, insights),
    }
    result.raw_keys.append("deep_analytics")
    logger.info(f"📋 Deep analytics aggregated: {len(insights)} insights, "
                f"influence={headline.get('influence_score')}, "
                f"trust={headline.get('trust_score')}")


def _build_summary_text(headline: Dict[str, Any], insights: List[str]) -> str:
    """يبني نصّاً ملخّصاً قصيراً للعرض السريع."""
    parts = []
    if headline.get("influence_score"):
        parts.append(f"Influence: {headline['influence_score']}/100")
    if headline.get("engagement_grade"):
        parts.append(f"Engagement: {headline['engagement_grade']}")
    if headline.get("trust_score"):
        parts.append(f"Trust: {headline['trust_score']}/100")
    if headline.get("followers"):
        parts.append(f"Followers: {headline['followers']:,}")
    if headline.get("viewer_count"):
        parts.append(f"Live viewers: {headline['viewer_count']:,}")
    summary = " | ".join(parts)
    if insights:
        summary += f"\n→ {insights[0]}"
    return summary


# ════════════════════════════════════════════════════════════════════════════
#  v4.4: User Database — حفظ بيانات كل مستخدم في ملف JSON منفصل
# ════════════════════════════════════════════════════════════════════════════
#  يحفظ هذا المعالج بيانات كل مستخدم (صاحب البث + الداعمين) في ملف JSON
#  منفصل على شكل:
#    /data/users/<unique_id>.json
#
#  يحتوي الملف على:
#    - بيانات المستخدم الأساسية (unique_id, sec_uid, nickname, avatar, followers)
#    - سجل البثوث (stream_history): متى بدأ، كم استمر، عدد المشاهدين
#    - سجل التغييرات (snapshots): لقطات زمنية للمتابعين والإحصائيات
#    - الداعمون (top_fans) الذين ظهروا في بثوثه
#    - آخر مرة شُوهد فيها (last_seen)
#    - عدد مرات الظهور (appearance_count)
#
#  يمكن لاحقاً مزامنة هذه الملفات مع GitHub عبر sync_users_to_github()
# ════════════════════════════════════════════════════════════════════════════

# المسار الأساسي لحفظ بيانات المستخدمين
USERS_DB_DIR = os.environ.get("USERS_DB_DIR", "/tmp/tiktok_users_db")
GITHUB_SYNC_ENABLED = bool(os.environ.get("GITHUB_SYNC_ENABLED", "true").lower() == "true")


def _get_user_file_path(unique_id: str) -> str:
    """يُرجع مسار ملف JSON لمستخدم محدد."""
    safe_uid = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', str(unique_id or "unknown"))
    os.makedirs(USERS_DB_DIR, exist_ok=True)
    return os.path.join(USERS_DB_DIR, f"{safe_uid}.json")


def _load_user_record(unique_id: str) -> Optional[Dict[str, Any]]:
    """يحمّل سجل مستخدم من ملف JSON (أو None إذا لم يوجد)."""
    path = _get_user_file_path(unique_id)
    if not os.path.exists(path):
        return None  # ← المستخدم غير موجود
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        # تأكد من وجود المفاتيح الأساسية
        data.setdefault("stream_history", [])
        data.setdefault("snapshots", [])
        data.setdefault("top_fans_seen", [])
        data.setdefault("stats_history", [])
        return data
    except Exception as e:
        logger.warning(f"Failed to load user record for {unique_id}: {e}")
        return None


def _new_user_record(unique_id: str) -> Dict[str, Any]:
    """ينشئ سجل مستخدم فارغ جديد."""
    return {
        "unique_id": unique_id,
        "first_seen": datetime.utcnow().isoformat() + "Z",
        "last_seen": None,
        "appearance_count": 0,
        "profile": {},
        "stream_history": [],
        "snapshots": [],
        "top_fans_seen": [],
        "stats_history": [],
    }


def _save_user_record(unique_id: str, record: Dict[str, Any]) -> bool:
    """يحفظ سجل مستخدم في ملف JSON."""
    if not unique_id:
        return False
    try:
        path = _get_user_file_path(unique_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, default=str)
        return True
    except Exception as e:
        logger.warning(f"Failed to save user record for {unique_id}: {e}")
        return False


def _persist_user_data(result: ExtractionResult) -> None:
    """يحفظ بيانات المستخدم المستخرَجة في ملف JSON منفصل.

    يُحفظ:
      1. بيانات صاحب البث/الفيديو (result.author)
      2. بيانات الداعمين (result.donor_rankings) — كل داعم يُحفظ في ملفه
      3. سجل البث المباشر (stream_history) إن كان البث مباشراً
      4. لقطة زمنية للمتابعين والإحصائيات
    """
    saved_users = []

    # ── 1) حفظ بيانات صاحب البث ───────────────────────────────────────────────
    owner = result.author
    if owner.unique_id or owner.user_id:
        unique_id = owner.unique_id or f"uid_{owner.user_id}"
        record = _load_user_record(unique_id)
        if record is None:
            record = _new_user_record(unique_id)

        # تحديث آخر ظهور
        now_iso = datetime.utcnow().isoformat() + "Z"
        record["last_seen"] = now_iso
        record["appearance_count"] = _safe_int(record.get("appearance_count")) + 1

        # تحديث البيانات الشخصية (آخر نسخة)
        record["profile"] = {
            "unique_id": owner.unique_id,
            "nickname": owner.nickname,
            "user_id": owner.user_id,
            "sec_uid": owner.sec_uid,
            "avatar": owner.avatar,
            "signature": owner.signature,
            "verified": bool(owner.verified),
            "follower_count": _safe_int(owner.follower_count),
            "following_count": _safe_int(owner.following_count),
            "likes_count": _safe_int(getattr(owner, "likes_count", 0)),
            "updated_at": now_iso,
        }

        # إضافة لقطة إحصائية
        snapshot = {
            "timestamp": now_iso,
            "epoch": int(time.time()),
            "source_url": result.url,
            "kind": result.kind,
            "follower_count": _safe_int(owner.follower_count),
            "view_count": _safe_int(result.stats.play_count),
            "like_count": _safe_int(getattr(result.stats, "digg_count", 0) or result.stats.play_count),
            "comment_count": _safe_int(result.stats.comment_count),
            "share_count": _safe_int(result.stats.share_count),
            "is_live": bool((result.live or {}).get("is_live")),
            "viewer_count": _safe_int((result.live or {}).get("viewer_count")),
            "influence_score": (result.influence_score or {}).get("total_score"),
            "engagement_grade": (result.engagement_quality or {}).get("quality_grade"),
            "trust_score": (result.account_risk or {}).get("trust_score"),
        }
        record["snapshots"].append(snapshot)
        # احتفظ بآخر 500 لقطة فقط
        if len(record["snapshots"]) > 500:
            record["snapshots"] = record["snapshots"][-500:]

        # إضافة سجل بث إن كان البث مباشراً
        live = result.live or {}
        if live.get("is_live") or live.get("start_time"):
            stream_entry = {
                "started_at": live.get("start_time"),
                "started_at_iso": (
                    datetime.utcfromtimestamp(int(live["start_time"])).isoformat() + "Z"
                    if live.get("start_time") else None
                ),
                "detected_at": now_iso,
                "room_id": result.all_ids.get("room_id"),
                "stream_id": result.all_ids.get("stream_id"),
                "peak_viewer_count": _safe_int(live.get("viewer_count")),
                "enter_count": _safe_int(live.get("enter_count")),
                "replay_viewers": _safe_int(live.get("replay_viewers")),
                "live_likes": _safe_int(live.get("like_count") or live.get("total_like")),
                "live_comments": _safe_int(live.get("comment_count")),
                "live_shares": _safe_int(live.get("share_count")),
                "source_url": result.url,
            }
            # تحقق من عدم تكرار نفس البث (نفس room_id خلال آخر ساعة)
            room_id = stream_entry.get("room_id")
            is_duplicate = False
            if room_id and record["stream_history"]:
                last_stream = record["stream_history"][-1]
                if last_stream.get("room_id") == room_id:
                    last_epoch = _safe_int(last_stream.get("detected_at_epoch", 0))
                    if not last_epoch:
                        # حاول تحويل ISO إلى epoch
                        try:
                            from datetime import datetime as _dt
                            last_epoch = int(_dt.fromisoformat(
                                last_stream.get("detected_at", "").replace("Z", "")
                            ).timestamp())
                        except Exception:
                            last_epoch = 0
                    if int(time.time()) - last_epoch < 3600:  # أقل من ساعة
                        is_duplicate = True
                        # حدّث peak_viewer_count إذا كان أعلى
                        if stream_entry["peak_viewer_count"] > _safe_int(last_stream.get("peak_viewer_count")):
                            last_stream["peak_viewer_count"] = stream_entry["peak_viewer_count"]
                            last_stream["last_seen_at"] = now_iso
                            last_stream["last_seen_epoch"] = int(time.time())

            if not is_duplicate:
                stream_entry["detected_at_epoch"] = int(time.time())
                stream_entry["last_seen_at"] = now_iso
                stream_entry["last_seen_epoch"] = int(time.time())
                record["stream_history"].append(stream_entry)
                # احتفظ بآخر 100 بث فقط
                if len(record["stream_history"]) > 100:
                    record["stream_history"] = record["stream_history"][-100:]

        # حفظ الداعمين المرتبطين بهذا المستخدم
        fans_seen_now = []
        for fan in (result.donor_rankings or []):
            fan_uid = fan.get("unique_id") or f"uid_{fan.get('user_id')}"
            if fan_uid:
                fans_seen_now.append({
                    "unique_id": fan_uid,
                    "user_id": fan.get("user_id"),
                    "amount": _safe_int(fan.get("total_sent") or fan.get("contribution")),
                    "seen_at": now_iso,
                    "source_url": result.url,
                })
        if fans_seen_now:
            record["top_fans_seen"].extend(fans_seen_now)
            # احتفظ بآخر 1000 سجل داعمين
            if len(record["top_fans_seen"]) > 1000:
                record["top_fans_seen"] = record["top_fans_seen"][-1000:]

        # إحصائيات تجميعية
        record["stats_summary"] = {
            "total_appearances": record["appearance_count"],
            "total_streams_detected": len(record["stream_history"]),
            "total_snapshots": len(record["snapshots"]),
            "total_fans_seen": len(record["top_fans_seen"]),
            "latest_follower_count": _safe_int(owner.follower_count),
            "latest_viewer_count": _safe_int((result.live or {}).get("viewer_count")),
        }

        _save_user_record(unique_id, record)
        saved_users.append({"unique_id": unique_id, "type": "owner"})

    # ── 2) حفظ بيانات كل داعم ───────────────────────────────────────────────────
    for fan in (result.donor_rankings or []):
        fan_uid = fan.get("unique_id")
        if not fan_uid:
            continue
        fan_record = _load_user_record(fan_uid)
        if fan_record is None:
            fan_record = _new_user_record(fan_uid)
        fan_record["last_seen"] = datetime.utcnow().isoformat() + "Z"
        fan_record["appearance_count"] = _safe_int(fan_record.get("appearance_count")) + 1
        fan_record.setdefault("profile", {})
        fan_record["profile"].update({
            "unique_id": fan_uid,
            "user_id": fan.get("user_id"),
            "sec_uid": fan.get("sec_uid"),
            "follower_count": fan.get("follower_count"),
            "nickname": fan.get("nickname") or fan_record["profile"].get("nickname"),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        fan_record.setdefault("donations_seen", [])
        fan_record["donations_seen"].append({
            "to_streamer": owner.unique_id,
            "amount": _safe_int(fan.get("total_sent") or fan.get("contribution")),
            "seen_at": datetime.utcnow().isoformat() + "Z",
            "source_url": result.url,
        })
        if len(fan_record["donations_seen"]) > 200:
            fan_record["donations_seen"] = fan_record["donations_seen"][-200:]

        _save_user_record(fan_uid, fan_record)
        saved_users.append({"unique_id": fan_uid, "type": "fan"})

    # تسجيل في raw_keys
    if saved_users:
        result.raw_keys.append(f"persisted_users_{len(saved_users)}")
        result.persisted_users = saved_users  # type: ignore[attr-defined]
        logger.info(f"💾 Persisted {len(saved_users)} users to JSON DB at {USERS_DB_DIR}")
    else:
        logger.debug("No users to persist (no author or fans found)")


def list_saved_users() -> Dict[str, Any]:
    """يُرجع قائمة بكل المستخدمين المحفوظين في قاعدة البيانات المحلية.

    Returns:
        {
            "total_users": N,
            "users": [
                {
                    "unique_id": "dr.tiktok",
                    "nickname": "Dr. TiKToK",
                    "follower_count": 174699,
                    "verified": true,
                    "last_seen": "2026-...",
                    "first_seen": "2026-...",
                    "appearance_count": 5,
                    "streams_detected": 3,
                    "latest_viewer_count": 24427,
                    "is_live_now": false,
                    "influence_score": 66.19,
                    "trust_score": 80,
                },
                ...
            ]
        }
    """
    if not os.path.exists(USERS_DB_DIR):
        return {"total_users": 0, "users": [], "db_dir": USERS_DB_DIR}

    users = []
    for fname in sorted(os.listdir(USERS_DB_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(USERS_DB_DIR, fname), "r", encoding="utf-8") as f:
                record = json.load(f)
            if not isinstance(record, dict):
                continue

            profile = record.get("profile", {}) or {}
            snapshots = record.get("snapshots", []) or []
            latest = snapshots[-1] if snapshots else {}

            users.append({
                "unique_id": record.get("unique_id") or profile.get("unique_id") or fname[:-5],
                "nickname": profile.get("nickname"),
                "user_id": profile.get("user_id"),
                "avatar": profile.get("avatar"),
                "verified": profile.get("verified"),
                "follower_count": profile.get("follower_count"),
                "following_count": profile.get("following_count"),
                "first_seen": record.get("first_seen"),
                "last_seen": record.get("last_seen"),
                "appearance_count": record.get("appearance_count", 0),
                "streams_detected": len(record.get("stream_history", [])),
                "snapshots_count": len(snapshots),
                "fans_seen_count": len(record.get("top_fans_seen", [])),
                "latest_viewer_count": latest.get("viewer_count"),
                "latest_is_live": latest.get("is_live"),
                "latest_influence_score": latest.get("influence_score"),
                "latest_trust_score": latest.get("trust_score"),
                "latest_engagement_grade": latest.get("engagement_grade"),
            })
        except Exception as e:
            logger.warning(f"Failed to read user file {fname}: {e}")
            continue

    # ترتيب: الأحدث ظهوراً أولاً
    users.sort(key=lambda u: u.get("last_seen") or "", reverse=True)
    return {"total_users": len(users), "users": users, "db_dir": USERS_DB_DIR}


def get_user_detail(unique_id: str) -> Dict[str, Any]:
    """يُرجع التفاصيل الكاملة لمستخدم محدد من قاعدة البيانات."""
    record = _load_user_record(unique_id)
    if not record:
        return {"success": False, "error": "User not found", "unique_id": unique_id}

    # إضافة إحصائيات البثوث
    streams = record.get("stream_history", [])
    if streams:
        total_stream_time_minutes = 0
        for s in streams:
            started = _safe_int(s.get("started_at"))
            last_seen = _safe_int(s.get("last_seen_epoch"))
            if started and last_seen and last_seen > started:
                total_stream_time_minutes += (last_seen - started) // 60

        record["stream_stats"] = {
            "total_streams": len(streams),
            "total_stream_time_minutes": total_stream_time_minutes,
            "total_stream_time_hours": round(total_stream_time_minutes / 60.0, 2),
            "average_peak_viewers": (
                sum(_safe_int(s.get("peak_viewer_count")) for s in streams) // len(streams)
                if streams else 0
            ),
            "first_stream_at": streams[0].get("started_at_iso") if streams else None,
            "latest_stream_at": streams[-1].get("last_seen_at") if streams else None,
            "latest_stream_room_id": streams[-1].get("room_id") if streams else None,
        }

    record["success"] = True
    return record


def sync_users_to_github(commit_message: str = None) -> Dict[str, Any]:
    """يدفع جميع ملفات المستخدمين المحفوظة إلى GitHub repo.

    يتطلب:
      - GH_TOKEN في متغيرات البيئة (يُضبط على Render)
      - GITHUB_REPO_OWNER (افتراضي: abuhoney)
      - GITHUB_REPO_NAME (افتراضي: tiktok-extractor-pro)

    Returns:
        {
            "success": true/false,
            "pushed_files": N,
            "commit_sha": "...",
            "commit_url": "..."
        }
    """
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        return {
            "success": False,
            "error": "GH_TOKEN not set in environment",
            "hint": "Set GH_TOKEN env var on Render to enable GitHub sync",
        }

    repo_owner = os.environ.get("GITHUB_REPO_OWNER", "abuhoney")
    repo_name = os.environ.get("GITHUB_REPO_NAME", "tiktok-extractor-pro")
    target_dir = "data/users"  # المسار في GitHub repo

    if not os.path.exists(USERS_DB_DIR):
        return {"success": False, "error": f"Users DB dir not found: {USERS_DB_DIR}"}

    files_to_push = [
        f for f in os.listdir(USERS_DB_DIR)
        if f.endswith(".json")
    ]
    if not files_to_push:
        return {"success": True, "pushed_files": 0, "message": "No files to sync"}

    api_base = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{target_dir}"
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "tiktok-extractor-pro",
    }

    pushed = 0
    failed = []
    commit_sha = None

    for fname in files_to_push[:50]:  # حد أقصى 50 ملف لكل دورة (تجنّب rate limit)
        try:
            file_path = os.path.join(USERS_DB_DIR, fname)
            with open(file_path, "rb") as f:
                content_bytes = f.read()
            content_b64 = base64.b64encode(content_bytes).decode("ascii")

            # تحقق من وجود الملف للحصول على sha (للتحديث بدل الإنشاء)
            check_url = f"{api_base}/{fname}"
            check_resp = requests.get(check_url, headers=headers, timeout=15)
            existing_sha = None
            if check_resp.status_code == 200:
                existing_sha = check_resp.json().get("sha")

            payload = {
                "message": commit_message or f"chore: sync user data {fname}",
                "content": content_b64,
                "branch": "main",
            }
            if existing_sha:
                payload["sha"] = existing_sha

            put_resp = requests.put(check_url, headers=headers, json=payload, timeout=30)
            if put_resp.status_code in (200, 201):
                pushed += 1
                if not commit_sha:
                    commit_sha = put_resp.json().get("commit", {}).get("sha")
            else:
                failed.append({"file": fname, "status": put_resp.status_code,
                               "error": put_resp.text[:200]})
        except Exception as e:
            failed.append({"file": fname, "error": str(e)})

    return {
        "success": pushed > 0,
        "pushed_files": pushed,
        "failed_files": failed[:5],
        "total_in_db": len(files_to_push),
        "commit_sha": commit_sha,
        "commit_url": f"https://github.com/{repo_owner}/{repo_name}/commit/{commit_sha}" if commit_sha else None,
        "repo": f"{repo_owner}/{repo_name}",
        "target_dir": target_dir,
    }


# ────────────────────────────────────────────────────────────────────────────
#  (تم الحذف) وضع العرض التجريبي — تم الإزالة في v3.0 بناءً على طلب المستخدم
# ────────────────────────────────────────────────────────────────────────────
def generate_demo(*args, **kwargs):
    """تم تعطيله في v3.0 — الاستخراج الآن حقيقي فقط."""
    raise RuntimeError("demo mode disabled in v3.0 — extraction is real only")


# ════════════════════════════════════════════════════════════════════════════
#  v3.8: Interaction Execution — real API calls using csrf+wid+nonce
# ════════════════════════════════════════════════════════════════════════════

def execute_interaction(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """ينفذ تفاعل حقيقي مع TikTok باستخدام csrf_token + wid + nonce.

    Args:
        action: نوع التفاعل ('send_like', 'follow_user', 'like_video', 'send_comment', 'enter_live_room')
        params: قاموس يحتوي على:
            - csrf_token, wid, nonce (من PRELOADED_ACCOUNTS أو HTML)
            - room_id, stream_id, user_id, sec_uid (من الاستخراج)
            - comment_text (للـ send_comment فقط)
            - count (للـ send_like فقط)

    Returns:
        قاموس بنتيجة التفاعل
    """
    result = {
        "action": action,
        "success": False,
        "message": "",
        "response": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "sessionid_used": False,
    }

    csrf = params.get("csrf_token", "")
    wid = params.get("wid", "")
    nonce = params.get("nonce", "")
    sessionid = params.get("sessionid", "")
    room_id = params.get("room_id", "")
    user_id = params.get("user_id", "")
    sec_uid = params.get("sec_uid", "")
    video_id = params.get("video_id") or room_id
    comment_text = params.get("comment_text", "👍")
    like_count = int(params.get("count", 1))

    # Build session
    session = build_session()
    ua = USER_AGENTS[0]
    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://www.tiktok.com",
        "Referer": f"https://www.tiktok.com/@{params.get('unique_id', '')}/live" if params.get('unique_id') else "https://www.tiktok.com/",
        "X-CSRF-Token": csrf,
        "X-Wid": wid,
        "X-Nonce": nonce,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    # If sessionid is provided, set it as cookie — this authenticates the request
    # as a logged-in user (the owner of that sessionid)
    if sessionid:
        session.cookies.set("sessionid", sessionid, domain=".tiktok.com")
        session.cookies.set("sessionid_ss", sessionid, domain=".tiktok.com")
        session.cookies.set("sid_tt", sessionid, domain=".tiktok.com")
        session.cookies.set("uid_tt", user_id, domain=".tiktok.com")
        result["sessionid_used"] = True
        logger.info(f"Session ID provided — request will be authenticated as user_id={user_id}")
    else:
        logger.warning("No sessionid provided — request may fail with 'Login expired' for some actions")

    try:
        if action == "send_like":
            # Send like to live room
            url = "https://www.tiktok.com/api/live/digg/"
            payload = {
                "room_id": room_id,
                "count": like_count,
                "type": 1,
                "channel_id": 6,
                "enter_method": "share",
                "user_id": user_id,
            }
            r = session.post(url, json=payload, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم إرسال {like_count} إعجاب للبث {room_id}"
            else:
                result["message"] = f"❌ فشل الإعجاب: {data.get('status_msg', data.get('error', 'خطأ'))}"

        elif action == "follow_user":
            # Follow target user
            url = "https://www.tiktok.com/api/relation/follow/"
            payload = {
                "user_id": user_id,
                "sec_uid": sec_uid,
                "type": 1,
                "channel_id": 6,
                "enter_method": "share",
            }
            r = session.post(url, json=payload, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم متابعة المستخدم (user_id={user_id})"
            else:
                result["message"] = f"❌ فشل المتابعة: {data.get('status_msg', data.get('error', 'خطأ'))}"

        elif action == "unfollow_user":
            url = "https://www.tiktok.com/api/relation/unfollow/"
            payload = {
                "user_id": user_id,
                "sec_uid": sec_uid,
                "type": 1,
                "channel_id": 6,
            }
            r = session.post(url, json=payload, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم إلغاء متابعة المستخدم (user_id={user_id})"
            else:
                result["message"] = f"❌ فشل إلغاء المتابعة: {data.get('status_msg', data.get('error', 'خطأ'))}"

        elif action == "like_video":
            url = "https://www.tiktok.com/api/commit/item/digg/"
            payload = {
                "aweme_id": video_id,
                "channel_id": 6,
                "item_type": 0,
                "type": 1,
            }
            r = session.post(url, json=payload, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم الإعجاب بالفيديو (aweme_id={video_id})"
            else:
                result["message"] = f"❌ فشل الإعجاب: {data.get('status_msg', data.get('error', 'خطأ'))}"

        elif action == "send_comment":
            url = "https://www.tiktok.com/api/comment/publish/"
            payload = {
                "aweme_id": video_id,
                "text": comment_text,
                "channel_id": 6,
                "type": 1,
            }
            r = session.post(url, json=payload, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم إرسال التعليق: '{comment_text[:50]}'"
            else:
                result["message"] = f"❌ فشل التعليق: {data.get('status_msg', data.get('error', 'خطأ'))}"

        elif action == "enter_live_room":
            url = "https://webcast.tiktok.com/webcast/room/enter/"
            query_params = {
                "room_id": room_id,
                "aid": "1988",
                "device_platform": "web",
            }
            if user_id:
                query_params["user_id"] = user_id
            if sec_uid:
                query_params["sec_uid"] = sec_uid
            r = session.get(url, params=query_params, headers=headers, timeout=30)
            data = r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
            result["response"] = data
            if data.get("status_code") == 0:
                result["success"] = True
                result["message"] = f"✅ تم الدخول إلى غرفة البث {room_id}"
            else:
                result["message"] = f"❌ فشل الدخول: {data.get('status_msg', data.get('error', 'خطأ'))}"

        else:
            result["message"] = f"❌ إجراء غير معروف: {action}"

    except Exception as e:
        result["message"] = f"❌ خطأ: {str(e)}"
        result["response"] = {"exception": str(e)}

    logger.info(f"Interaction {action}: success={result['success']} msg={result['message']}")
    return result


# ════════════════════════════════════════════════════════════════════════════
#  v3.9: Auto-Interaction — session builder + auto-target + batch + credential rotation
# ════════════════════════════════════════════════════════════════════════════

def build_interaction_session(account_index: int = 0) -> Tuple[requests.Session, Dict[str, str]]:
    """يبني جلسة تفاعل جاهزة باستخدام بيانات PRELOADED_ACCOUNTS.

    Args:
        account_index: فهرس الحساب في PRELOADED_ACCOUNTS (0=fw__qg, 1=hadwtamsr4, 2=bentmlok1)

    Returns:
        (session, account_data) — جلسة جاهزة + بيانات الحساب المستخدم
    """
    if account_index >= len(PRELOADED_ACCOUNTS):
        account_index = 0

    account = PRELOADED_ACCOUNTS[account_index]
    session = build_session()
    ua = USER_AGENTS[0]

    headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://www.tiktok.com",
        "Referer": "https://www.tiktok.com/",
        "X-CSRF-Token": account["csrf_token"],
        "X-Wid": account["wid"],
        "X-Nonce": account["nonce"],
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    session.headers.update(headers)
    logger.info(f"✅ Session built for account: @{account['unique_id']} (index={account_index})")
    return session, account


def auto_interact_with_target(url: str, actions: List[str] = None,
                                account_index: int = 0,
                                sessionid: str = "") -> Dict[str, Any]:
    """يستخرج بيانات الرابط ثم ينفذ تفاعلات تلقائية على صاحب الرابط.

    Args:
        url: رابط TikTok (بث/فيديو/بروفايل)
        actions: قائمة التفاعلات المطلوبة (default: ["follow_user", "send_like", "like_video"])
        account_index: أي حساب من PRELOADED_ACCOUNTS نستخدم
        sessionid: معرف جلسة من متصفحك (يضيف مصادقة حقيقية للطلبات)

    Returns:
        قاموس بنتائج الاستخراج + نتائج التفاعلات
    """
    if actions is None:
        actions = ["follow_user", "send_like", "like_video"]

    result = {
        "url": url,
        "extraction": None,
        "interactions": [],
        "account_used": None,
        "success_count": 0,
        "fail_count": 0,
    }

    # 1. Extract data from URL
    logger.info(f"Auto-interact: extracting {url}")
    extraction = extract(url, timeout=90)
    extraction_dict = extraction.to_dict()
    result["extraction"] = {
        "success": extraction_dict["success"],
        "kind": extraction_dict.get("kind"),
        "author": {
            "unique_id": extraction.author.unique_id,
            "nickname": extraction.author.nickname,
            "user_id": extraction.author.user_id,
            "sec_uid": (extraction.author.sec_uid or "")[:50] + "...",
            "follower_count": extraction.author.follower_count,
        },
        "room_id": extraction.all_ids.get("room_id"),
        "stream_id": extraction.all_ids.get("stream_id"),
        "video_id": extraction.content_id,
    }

    if not extraction.success:
        result["error"] = "Extraction failed — cannot interact"
        return result

    # 2. Build session from PRELOADED_ACCOUNTS
    session, account = build_interaction_session(account_index)
    result["account_used"] = {
        "unique_id": account["unique_id"],
        "nickname": account["nickname"],
        "follower_count": account.get("follower_count"),
    }

    # 3. Prepare interaction params
    target_params = {
        "csrf_token": account["csrf_token"],
        "wid": account["wid"],
        "nonce": account["nonce"],
        "sessionid": sessionid,
        "user_id": extraction.author.user_id or extraction.all_ids.get("user_id", ""),
        "sec_uid": extraction.author.sec_uid or extraction.all_ids.get("sec_user_id", ""),
        "room_id": extraction.all_ids.get("room_id", ""),
        "video_id": extraction.content_id or extraction.all_ids.get("room_id", ""),
        "unique_id": extraction.author.unique_id or "",
        "count": 1,
        "comment_text": "👍",
    }

    # 4. Execute each action
    for action in actions:
        logger.info(f"Auto-interact: executing {action} on @{extraction.author.unique_id}")
        interaction_result = execute_interaction(action, target_params)
        result["interactions"].append(interaction_result)
        if interaction_result["success"]:
            result["success_count"] += 1
        else:
            result["fail_count"] += 1
        # Rate limit: wait 1s between interactions
        time.sleep(1)

    result["overall_success"] = result["success_count"] > 0
    logger.info(f"Auto-interact complete: {result['success_count']} success, {result['fail_count']} failed")
    return result


def batch_follow_top_fans(extraction_result: Dict[str, Any],
                           account_index: int = 0) -> Dict[str, Any]:
    """يتبع جميع المعجبين الأوائل (top_fans) المستخرجين من البث.

    Args:
        extraction_result: نتيجة الاستخراج (dict من to_dict())
        account_index: أي حساب من PRELOADED_ACCOUNTS

    Returns:
        قاموس بنتائج المتابعة لكل معجب
    """
    result = {
        "total_fans": 0,
        "followed": 0,
        "failed": 0,
        "details": [],
        "account_used": None,
    }

    # Get top_fans from extraction result
    donor_rankings = extraction_result.get("donor_rankings", [])
    if not donor_rankings:
        # Try from live.top_donors
        live = extraction_result.get("live", {})
        donor_rankings = live.get("top_donors", [])

    if not donor_rankings:
        result["error"] = "No top_fans found in extraction result"
        return result

    result["total_fans"] = len(donor_rankings)

    # Build session
    session, account = build_interaction_session(account_index)
    result["account_used"] = {
        "unique_id": account["unique_id"],
        "nickname": account["nickname"],
    }

    # Follow each fan
    for i, fan in enumerate(donor_rankings):
        fan_user_id = fan.get("user_id", "")
        fan_sec_uid = fan.get("sec_uid", "")
        fan_unique_id = fan.get("unique_id", "unknown")

        if not fan_sec_uid:
            result["details"].append({
                "fan": fan_unique_id,
                "success": False,
                "message": "No sec_uid for this fan",
            })
            result["failed"] += 1
            continue

        logger.info(f"Batch follow: [{i+1}/{len(donor_rankings)}] @{fan_unique_id}")

        params = {
            "csrf_token": account["csrf_token"],
            "wid": account["wid"],
            "nonce": account["nonce"],
            "user_id": fan_user_id,
            "sec_uid": fan_sec_uid,
            "unique_id": fan_unique_id,
        }
        interaction_result = execute_interaction("follow_user", params)
        result["details"].append({
            "fan": fan_unique_id,
            "user_id": fan_user_id,
            "success": interaction_result["success"],
            "message": interaction_result["message"],
        })
        if interaction_result["success"]:
            result["followed"] += 1
        else:
            result["failed"] += 1

        # Rate limit: wait 2s between follows
        time.sleep(2)

    result["overall_success"] = result["followed"] > 0
    logger.info(f"Batch follow complete: {result['followed']}/{result['total_fans']} followed")
    return result


def get_credential_rotation() -> Dict[str, Any]:
    """يدور بين حسابات PRELOADED_ACCOUNTS لتفادي rate limiting.

    Returns:
        قاموس بكل الحسابات المتاحة + أيها نشط حالياً
    """
    return {
        "total_accounts": len(PRELOADED_ACCOUNTS),
        "accounts": [
            {
                "index": i,
                "unique_id": a["unique_id"],
                "nickname": a["nickname"],
                "follower_count": a.get("follower_count"),
                "csrf_token": (a["csrf_token"] or "")[:30] + "...",
                "wid": a["wid"],
                "nonce": (a["nonce"] or "")[:30] + "...",
                "room_id": a.get("room_id"),
            }
            for i, a in enumerate(PRELOADED_ACCOUNTS)
        ],
        "current_index": 0,
        "current_account": PRELOADED_ACCOUNTS[0]["unique_id"],
        "note": "Call /api/interact with account_index=0,1,2 to rotate between accounts",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python extractor.py <tiktok_url>")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s",
                        datefmt="%H:%M:%S")
    res = extract(sys.argv[1], timeout=60)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2, default=str)[:4000])
