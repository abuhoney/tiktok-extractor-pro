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
import logging
import socket
import hashlib
import subprocess
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse, quote, urlencode

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
    """يحوّل استجابة yt-dlp إلى ExtractionResult."""
    extra_images = extra_images or []

    # Determine the kind based on yt-dlp's extractor + URL
    kind = parsed.kind or "video"
    if info.get("is_live"):
        kind = "live"
    elif info.get("_type") == "image" or extra_images:
        kind = "photo"

    res = ExtractionResult(
        success=True,
        url=parsed.final_url or parsed.normalized,
        final_url=parsed.final_url or info.get("webpage_url") or parsed.normalized,
        kind=kind,
        title=info.get("title") or "",
        description=info.get("description") or info.get("title") or "",
        content_id=str(info.get("id") or parsed.content_id or ""),
        create_time=int(info.get("timestamp") or info.get("upload_date") or 0) if info.get("timestamp") or info.get("upload_date") else None,
    )

    # Author
    uploader = info.get("uploader") or info.get("channel") or parsed.username
    res.author.unique_id = uploader
    res.author.nickname = info.get("uploader") or info.get("channel") or uploader
    res.author.user_id = str(info.get("uploader_id") or info.get("channel_id") or "")
    res.author.avatar = info.get("uploader_avatar") or info.get("channel_avatar")
    res.author.follower_count = info.get("uploader_follower_count") or info.get("channel_follower_count")
    res.author.following_count = info.get("uploader_following_count")
    res.author.like_count = info.get("uploader_like_count") or info.get("channel_like_count")
    res.author.video_count = info.get("uploader_video_count") or info.get("channel_video_count")
    res.author.verified = bool(info.get("uploader_verified") or info.get("channel_verified"))

    # Stats
    res.stats.play_count = info.get("view_count") or info.get("play_count")
    res.stats.digg_count = info.get("like_count")
    res.stats.comment_count = info.get("comment_count")
    res.stats.share_count = info.get("repost_count") or info.get("share_count")
    res.stats.collect_count = info.get("collect_count") or info.get("favorite_count")

    # Video
    if info.get("url"):
        res.video.play_url = info["url"]
        res.video.download_url = info["url"]
    res.video.cover = info.get("thumbnail") or info.get("thumbnails", [{}])[0].get("url") if info.get("thumbnails") else info.get("thumbnail")
    res.video.dynamic_cover = info.get("dynamic_cover") or info.get("preview_url")
    res.video.origin_cover = info.get("thumbnail")
    res.video.duration = int(info.get("duration") or 0) or None
    res.video.width = info.get("width")
    res.video.height = info.get("height")
    res.video.ratio = info.get("aspect_ratio")
    res.video.format = info.get("ext") or "mp4"

    # Music
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

    # Images (for photo carousels)
    if extra_images:
        res.images = extra_images
    elif info.get("_type") == "image" and info.get("url"):
        res.images = [info["url"]]

    # Hashtags & mentions
    res.hashtags = [t.lstrip("#") for t in (info.get("tags") or []) if t]
    res.mentions = re.findall(r"@([A-Za-z0-9_.]+)",
                              info.get("description") or info.get("title") or "")

    # Live-specific fields
    if kind == "live":
        res.live = {
            "is_live": True,
            "room_id": str(info.get("id") or ""),
            "stream_id": str(info.get("stream_id") or ""),
            "viewer_count": info.get("concurrent_viewers") or info.get("view_count") or 0,
            "title": info.get("title") or "",
            "cover": info.get("thumbnail") or "",
        }

    res.raw_keys.append("yt_dlp")
    res.raw_json = {"yt_dlp": info}

    # Extract sec_uid from uploader_url if present
    uploader_url = info.get("uploader_url") or ""
    m_sec = re.search(r"sec_uid=([A-Za-z0-9_-]+)", uploader_url)
    if m_sec:
        res.author.sec_uid = m_sec.group(1)

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
                    f"author=@{ytdlp_result.author.unique_id}")
        _enrich_with_html(ytdlp_result, session, parsed, timeout)
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
                    return share_result
                # No share data — propagate the yt-dlp error
                return alt_result
            elif not alt_result:
                # yt-dlp itself failed (subprocess error) — try share-link params
                share_data = _extract_share_link_params(target)
                if share_data:
                    share_result = _from_share_link(share_data, parsed, target)
                    share_result.raw_keys.append("ytdlp_subprocess_failed")
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
            return _from_share_link(share_data, parsed, target)

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
#  (تم الحذف) وضع العرض التجريبي — تم الإزالة في v3.0 بناءً على طلب المستخدم
# ────────────────────────────────────────────────────────────────────────────
def generate_demo(*args, **kwargs):
    """تم تعطيله في v3.0 — الاستخراج الآن حقيقي فقط."""
    raise RuntimeError("demo mode disabled in v3.0 — extraction is real only")


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
