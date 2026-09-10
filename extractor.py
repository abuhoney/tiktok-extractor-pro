#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok Universal Extractor - Core Engine
=========================================
يدعم جميع صيغ روابط TikTok:
  - https://www.tiktok.com/@username/video/1234567890
  - https://vm.tiktok.com/ZSJxxxxxxx/
  - https://vt.tiktok.com/ZSRxxxxxxx/
  - https://m.tiktok.com/v/1234567890.html
  - https://www.tiktok.com/@username
  - https://www.tiktok.com/@username/live
  - https://www.tiktok.com/t/PRxxxxxx/
  - tiktok.com/@username/video/ID (بدون https)
  - @username (mention مباشر)

يستخدم 5 استراتيجيات متتالية:
  1. __UNIVERSAL_DATA_FOR_REHYDRATION__  (الحديثة)
  2. SIGI_STATE / _SIGI_STATE             (الكلاسيكية)
  3. oEmbed API                            (الرسمية)
  4. Meta Tags (og:*, twitter:*)          (الاحتياطية)
  5. DOM Fallback                          (الأخيرة)
"""

from __future__ import annotations

import os
import sys
import json
import re
import logging
import socket
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlunparse, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
from bs4 import BeautifulSoup

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("tiktok_extractor")

# ────────────────────────────────────────────────────────────────────────────
#  جلسة HTTP موحّدة مع إعادة المحاولة
# ────────────────────────────────────────────────────────────────────────────
USER_AGENTS = [
    # متصفحات حقيقية حديثة
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
      - TIKTOK_PROXY   أو  HTTPS_PROXY   أو  ALL_PROXY
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
        # طلب socks5:// يتطلب PySocks
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

# نمط username: حروف، أرقام، نقطة، شرطة سفلية
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
    # إزالة بادئات شائعة من المحادثة
    s = re.sub(r"^(?:url|link|رابط|الرابط)\s*[:：]\s*", "", s, flags=re.I)
    # إن كان مجرد @username (mention)
    if re.fullmatch(r"@?[A-Za-z0-9_.]{2,32}", s) and "." not in s and "/" not in s:
        s = f"https://www.tiktok.com/@{s.lstrip('@')}"
    # إضافة https:// إن لزم
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

    # روابط مختصرة → نحتاج resolve لاحقاً
    if host in SHORT_DOMAINS and host not in ("www.tiktok.com", "tiktok.com"):
        result.is_short = True
        result.kind = "short"
        return result

    # www.tiktok.com/@user/video/ID
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

    # m.tiktok.com/v/ID.html
    m_mobile = re.search(r"/v/(\d+)", path)
    if m_mobile:
        result.content_id = m_mobile.group(1)
        result.kind = "video"
        return result

    # /t/PRxxxxxx/ → مختصر
    if re.match(r"/t/[A-Za-z0-9]+/?$", path):
        result.is_short = True
        result.kind = "short"
        return result

    # /@user/live
    if LIVE_PATH_RE.search(path) and result.username:
        result.kind = "live"
        return result

    # /@user فقط → بروفايل
    if result.username and path.strip("/") == f"@{result.username}":
        result.kind = "profile"
        return result

    return result


def resolve_short_url(session: requests.Session, url: str,
                      timeout: int = 15) -> Optional[str]:
    """يحلّ رابطاً مختصراً إلى الرابط الكامل النهائي."""
    try:
        # نستخدم GET لأن TikTok يمنع HEAD أحياناً
        r = session.get(url, allow_redirects=True, timeout=timeout,
                        headers={"User-Agent": USER_AGENTS[0]})
        if r.url and "tiktok.com" in r.url.lower():
            return r.url
    except Exception as e:
        logger.warning("resolve_short_url failed: %s", e)
    return None


# ────────────────────────────────────────────────────────────────────────────
#  الاستراتيجيات الخمس للاستخراج
# ────────────────────────────────────────────────────────────────────────────
def _safe_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:
        return None


def _deep_get(d: Any, *keys, default=None):
    """يجلب قيمة من قاموس متداخل بأمان."""
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and -len(cur) <= k < len(cur):
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur


def extract_universal_data(soup: BeautifulSoup) -> Optional[dict]:
    """استراتيجية 1: __UNIVERSAL_DATA_FOR_REHYDRATION__"""
    tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if not tag:
        return None
    return _safe_json(tag.string or tag.get_text() or "")


def extract_sigi_state(soup: BeautifulSoup) -> Optional[dict]:
    """استراتيجية 2: SIGI_STATE أو _SIGI_STATE"""
    for script in soup.find_all("script"):
        txt = script.string or ""
        if not txt:
            continue
        # window.SIGI_STATE = {...};
        m = re.search(r"window\[['\"]?SIGI_STATE['\"]?\]\s*=\s*(\{.*?\});\s*(?:window\.|$)",
                      txt, re.S)
        if m:
            return _safe_json(m.group(1))
        m = re.search(r"window\._SIGI_STATE\s*=\s*(\{.*?\});\s*(?:window\.|$)",
                      txt, re.S)
        if m:
            return _safe_json(m.group(1))
    return None


def extract_oembed(session: requests.Session,
                   video_url: str) -> Optional[dict]:
    """استراتيجية 3: oEmbed API الرسمي"""
    try:
        api = "https://www.tiktok.com/oembed"
        r = session.get(api, params={"url": video_url}, timeout=10,
                        headers={"User-Agent": USER_AGENTS[0]})
        if r.status_code == 200 and r.text.strip().startswith("{"):
            return r.json()
    except Exception as e:
        logger.debug("oembed failed: %s", e)
    return None


def extract_meta_tags(soup: BeautifulSoup) -> Dict[str, str]:
    """استراتيجية 4: وسوم Open Graph / Twitter / itemprop"""
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("property") or tag.get("name") or tag.get("itemprop")
        content = tag.get("content")
        if name and content:
            meta[name] = content
    # عنوان الصفحة
    if soup.title and soup.title.string:
        meta["title"] = soup.title.string.strip()
    # canonical
    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        meta["canonical"] = canon["href"]
    return meta


def extract_links_from_dom(soup: BeautifulSoup) -> Dict[str, str]:
    """استراتيجية 5: استخراج روابط الوسائط من DOM مباشرةً."""
    out: Dict[str, List[str]] = {"videos": [], "images": [], "audios": []}
    for v in soup.find_all("video"):
        src = v.get("src") or (v.find("source") and v.find("source").get("src"))
        if src:
            out["videos"].append(src)
        poster = v.get("poster")
        if poster:
            out["images"].append(poster)
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "tiktok" in src.lower():
            out["images"].append(src)
    # رابط الصوت من data-attrs أحياناً
    for el in soup.select("[data-music]"):
        m = el.get("data-music")
        if m:
            out["audios"].append(m)
    return out


# ────────────────────────────────────────────────────────────────────────────
#  تحويل البيانات الخام إلى نموذج موحّد
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class Author:
    unique_id: Optional[str] = None        # username
    nickname: Optional[str] = None         # display name
    user_id: Optional[str] = None
    avatar: Optional[str] = None
    signature: Optional[str] = None
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    like_count: Optional[int] = None
    video_count: Optional[int] = None
    verified: Optional[bool] = None
    sec_uid: Optional[str] = None


@dataclass
class Stats:
    digg_count: Optional[int] = None       # إعجابات
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    play_count: Optional[int] = None       # مشاهدات
    collect_count: Optional[int] = None    # مفضلة


@dataclass
class VideoMedia:
    play_url: Optional[str] = None         # رابط التشغيل (مع علامة مائية)
    download_url: Optional[str] = None     # رابط مباشر بدون علامة إن أمكن
    cover: Optional[str] = None            # صورة الغلاف
    dynamic_cover: Optional[str] = None    # gif
    origin_cover: Optional[str] = None
    duration: Optional[int] = None         # ثوانٍ
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
    kind: str = "unknown"
    url: Optional[str] = None
    final_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    create_time: Optional[int] = None
    author: Author = field(default_factory=Author)
    stats: Stats = field(default_factory=Stats)
    video: VideoMedia = field(default_factory=VideoMedia)
    music: Music = field(default_factory=Music)
    images: List[str] = field(default_factory=list)   # لمنشورات الصور
    hashtags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    raw_keys: List[str] = field(default_factory=list)  # أسماء المصادر المستخدمة
    meta_tags: Dict[str, str] = field(default_factory=dict)
    raw_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> dict:
        return asdict(self)


# ────────────────────────────────────────────────────────────────────────────
#  المنسّق الرئيسي - يحوّل البيانات الخام إلى ExtractionResult
# ────────────────────────────────────────────────────────────────────────────
def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _from_universal(uni: dict) -> ExtractionResult:
    """يحوّل __UNIVERSAL_DATA_FOR_REHYDRATION__ إلى ExtractionResult."""
    res = ExtractionResult(success=True)
    res.raw_keys.append("universal_data")

    # المسار الكلاسيكي: default -> webapp.video-detail -> ItemModule.* -> videoData
    vd = _deep_get(uni, "webapp", "video-detail", "videoDetail") \
         or _deep_get(uni, "webapp", "video-detail", "itemInfo", "itemStruct") \
         or _deep_get(uni, "webapp", "video-detail", "itemStruct")
    if not vd:
        # المحاولة على بنية live
        ld = _deep_get(uni, "webapp", "live-detail", "liveRoomInfo")
        if ld:
            res.kind = "live"
            res.title = ld.get("title")
            res.description = ld.get("description") or ld.get("introduce")
            res.author.unique_id = ld.get("owner", {}).get("unique_id") \
                or ld.get("host", {}).get("unique_id")
            res.author.nickname = ld.get("owner", {}).get("nickname") \
                or ld.get("host", {}).get("nickname")
            res.author.user_id = str(ld.get("owner", {}).get("id") or ld.get("host", {}).get("id") or "")
            res.author.avatar = ld.get("owner", {}).get("avatar_thumb", {}).get("url_list", [None])[0]
            res.stats.play_count = _to_int(ld.get("user_count"))
            res.video.cover = (ld.get("cover", {}).get("url_list") or [None])[0]
            res.raw_json = uni
            return res
        res.raw_json = uni
        res.error = "UNIVERSAL_DATA found but no video-detail"
        res.success = False
        return res

    # نوع المحتوى
    if vd.get("imagePost") or vd.get("images"):
        res.kind = "photo"
        for img in vd.get("images", []):
            url = (img.get("imageURL", {}).get("url_list") or [None])[0]
            if url:
                res.images.append(url)
    else:
        res.kind = "video"

    # المؤلف
    au = vd.get("author") or {}
    res.author = Author(
        unique_id=au.get("unique_id"),
        nickname=au.get("nickname"),
        user_id=str(au.get("id") or "") or None,
        avatar=((au.get("avatar_thumb") or {}).get("url_list") or [None])[0],
        signature=au.get("signature"),
        sec_uid=au.get("sec_uid"),
        verified=au.get("verified"),
    )

    user_stats = vd.get("authorStats") or {}
    if user_stats:
        res.author.follower_count = _to_int(user_stats.get("follower_count"))
        res.author.following_count = _to_int(user_stats.get("following_count"))
        res.author.like_count = _to_int(user_stats.get("heart") or user_stats.get("total_favorited"))
        res.author.video_count = _to_int(user_stats.get("video_count"))

    # الفيديو
    v = vd.get("video") or {}
    if v:
        play = (v.get("playAddr") or v.get("play_addr") or {})
        res.video = VideoMedia(
            play_url=(play.get("url_list") or [None])[0],
            cover=((v.get("cover") or v.get("origin_cover") or {}).get("url_list") or [None])[0],
            dynamic_cover=((v.get("dynamicCover") or v.get("dynamic_cover") or {}).get("url_list") or [None])[0],
            origin_cover=((v.get("originCover") or v.get("origin_cover") or {}).get("url_list") or [None])[0],
            duration=_to_int(v.get("duration")),
            width=_to_int(v.get("width")),
            height=_to_int(v.get("height")),
            ratio=v.get("ratio"),
            format=v.get("format"),
        )

    # الإحصائيات
    res.stats = Stats(
        digg_count=_to_int(vd.get("diggCount") or vd.get("stats", {}).get("diggCount")),
        comment_count=_to_int(vd.get("commentCount") or vd.get("stats", {}).get("commentCount")),
        share_count=_to_int(vd.get("shareCount") or vd.get("stats", {}).get("shareCount")),
        play_count=_to_int(vd.get("playCount") or vd.get("stats", {}).get("playCount")),
        collect_count=_to_int(vd.get("collectCount") or vd.get("stats", {}).get("collectCount")),
    )

    # الموسيقى
    m = vd.get("music") or {}
    if m:
        res.music = Music(
            id=str(m.get("id") or ""),
            title=m.get("title"),
            author=m.get("authorName") or m.get("author"),
            play_url=((m.get("playUrl") or m.get("play_url") or {}).get("url_list") or [None])[0],
            cover=((m.get("coverLarge") or m.get("cover_thumb") or {}).get("url_list") or [None])[0],
            duration=_to_int(m.get("duration")),
        )

    # وصف / توقيت / وسوم
    res.title = vd.get("desc") or vd.get("title")
    res.description = vd.get("desc")
    res.create_time = _to_int(vd.get("createTime") or vd.get("createTimeISO"))

    # نصHashtags
    desc = (vd.get("desc") or "")
    res.hashtags = list(set(re.findall(r"#(\w+)", desc)))
    res.mentions = list(set(re.findall(r"@([A-Za-z0-9_.]+)", desc)))

    res.raw_json = uni
    return res


def _from_sigi(sigi: dict) -> ExtractionResult:
    """يحوّل SIGI_STATE إلى ExtractionResult."""
    res = ExtractionResult(success=True)
    res.raw_keys.append("sigi_state")

    # ItemModule يحوي كل المنشورات بمعرّفها
    items = sigi.get("ItemModule") or {}
    if not items:
        # قد يكون في LiveModule
        live = sigi.get("LiveRoomModule") or sigi.get("LiveModule")
        if live:
            for room in live.values():
                res.kind = "live"
                res.title = room.get("title")
                res.author.unique_id = room.get("owner", {}).get("unique_id")
                res.author.nickname = room.get("owner", {}).get("nickname")
                res.video.cover = (room.get("cover", {}).get("url_list") or [None])[0]
                break
        res.raw_json = sigi
        return res

    # أول عنصر
    item_id, item = next(iter(items.items()))
    vd = item
    res.kind = "video" if not vd.get("imagePost") else "photo"

    au = vd.get("author") or {}
    res.author = Author(
        unique_id=au.get("unique_id"),
        nickname=au.get("nickname"),
        user_id=str(au.get("id") or "") or None,
        avatar=((au.get("avatar_thumb") or {}).get("url_list") or [None])[0],
        signature=au.get("signature"),
        sec_uid=au.get("sec_uid"),
        verified=au.get("verified"),
    )

    # UserModule: إحصائيات
    um = (sigi.get("UserModule") or {}).get("users") or {}
    for u in um.values():
        if u.get("unique_id") == res.author.unique_id:
            res.author.follower_count = _to_int(u.get("follower_count"))
            res.author.following_count = _to_int(u.get("following_count"))
            res.author.like_count = _to_int(u.get("heart"))
            res.author.video_count = _to_int(u.get("video_count"))
            break

    v = vd.get("video") or {}
    if v:
        res.video = VideoMedia(
            play_url=((v.get("playAddr") or v.get("play_addr") or {}).get("url_list") or [None])[0],
            cover=((v.get("cover") or {}).get("url_list") or [None])[0],
            dynamic_cover=((v.get("dynamic_cover") or {}).get("url_list") or [None])[0],
            origin_cover=((v.get("origin_cover") or {}).get("url_list") or [None])[0],
            duration=_to_int(v.get("duration")),
            width=_to_int(v.get("width")),
            height=_to_int(v.get("height")),
            ratio=v.get("ratio"),
        )

    stats = vd.get("stats") or {}
    res.stats = Stats(
        digg_count=_to_int(stats.get("diggCount")),
        comment_count=_to_int(stats.get("commentCount")),
        share_count=_to_int(stats.get("shareCount")),
        play_count=_to_int(stats.get("playCount")),
        collect_count=_to_int(stats.get("collectCount")),
    )

    m = vd.get("music") or {}
    if m:
        res.music = Music(
            id=str(m.get("id") or ""),
            title=m.get("title"),
            author=m.get("authorName") or m.get("author"),
            play_url=((m.get("playUrl") or {}).get("url_list") or [None])[0],
            cover=((m.get("coverLarge") or {}).get("url_list") or [None])[0],
            duration=_to_int(m.get("duration")),
        )

    res.title = vd.get("desc")
    res.description = vd.get("desc")
    res.create_time = _to_int(vd.get("createTime"))
    desc = vd.get("desc") or ""
    res.hashtags = list(set(re.findall(r"#(\w+)", desc)))
    res.mentions = list(set(re.findall(r"@([A-Za-z0-9_.]+)", desc)))

    res.raw_json = sigi
    return res


def _from_oembed(oem: dict) -> ExtractionResult:
    """يحوّل بيانات oEmbed إلى ExtractionResult جزئي."""
    res = ExtractionResult(success=True)
    res.raw_keys.append("oembed")
    res.kind = "video"
    res.title = oem.get("title")
    au = oem.get("author_name")
    if au:
        res.author.unique_id = oem.get("author_url", "").rstrip("/").split("@")[-1] or None
        res.author.nickname = au
    if oem.get("author_url"):
        res.author.user_id = None
    if oem.get("thumbnail_url"):
        res.video.cover = oem["thumbnail_url"]
    if oem.get("html"):
        res.meta_tags["embed_html"] = oem["embed_html"]
    res.raw_json = oem
    return res


def _from_meta(meta: Dict[str, str], dom: Dict[str, List[str]]) -> ExtractionResult:
    """يبني ExtractionResult جزئي من وسوم الميتا."""
    res = ExtractionResult(success=True)
    res.raw_keys.append("meta_tags")
    res.kind = "video" if "og:video" in meta or meta.get("og:type") == "video" else "unknown"
    res.title = meta.get("og:title") or meta.get("twitter:title") or meta.get("title")
    res.description = meta.get("og:description") or meta.get("twitter:description")
    res.video.cover = meta.get("og:image") or meta.get("twitter:image")
    res.video.play_url = meta.get("og:video") or meta.get("og:video:url") or meta.get("og:video:secure_url")
    # مؤلف من canonical
    canon = meta.get("canonical", "")
    m = re.search(r"/@([A-Za-z0-9_.]+)/?", canon)
    if m:
        res.author.unique_id = m.group(1)
    # وسائط DOM
    if dom.get("videos"):
        res.video.play_url = res.video.play_url or dom["videos"][0]
    if dom.get("images"):
        res.video.cover = res.video.cover or dom["images"][0]
    res.meta_tags = meta
    res.raw_json = {"meta": meta, "dom": dom}
    return res


# ────────────────────────────────────────────────────────────────────────────
#  استراتيجية Webcast API (مع X-Bogus) — تعمل حتى عند حجب صفحة HTML
# ────────────────────────────────────────────────────────────────────────────
def _try_webcast_api(session: requests.Session,
                     parsed: ParsedLink) -> Optional[ExtractionResult]:
    """يستدعي Webcast API لاستخراج بيانات البث المباشر حتى عند حجب HTML.

    يتطلب X-Bogus صالح؛ إذا فشل التوقيع (Playwright غير متاح أو webmssdk.js
    لم يتم تحميله) يحاول بدون توقيع كحل أخير، وسيفشل بـ 10013.
    """
    if not parsed.username:
        logger.debug("Webcast API skipped: no username in parsed link")
        return None

    # جرب نسخة Playwright من الموقّع أولاً (حقيقي 100%)
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
    url = base + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    signed_url = url
    headers = {"User-Agent": ua}
    try:
        # Lazy import — Playwright is heavy, only import when actually needed
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xbogus_playwright import sign_with_playwright
        signed_url, headers = sign_with_playwright(url, user_agent=ua)
        if "X-Bogus" not in headers:
            logger.warning("Webcast API: X-Bogus signing failed; trying unsigned (will likely get 10013)")
        else:
            logger.info(f"Webcast API: signed OK, X-Bogus={headers['X-Bogus'][:24]}...")
    except Exception as e:
        logger.warning(f"Webcast API: signer unavailable ({e}); trying unsigned")

    try:
        r = session.get(signed_url, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.warning(f"Webcast API: HTTP {r.status_code}")
            return None
        data = r.json()
        if data.get("status_code") != 0:
            logger.warning(f"Webcast API: status_code={data.get('status_code')} "
                           f"msg={data.get('status_msg')}")
            return None
        # نجاح! تحويل إلى ExtractionResult
        return _from_webcast(data, parsed, ua)
    except Exception as e:
        logger.exception(f"Webcast API call crashed: {e}")
        return None


def _from_webcast(data: dict, parsed: ParsedLink, ua: str) -> Optional[ExtractionResult]:
    """يحوّل استجابة Webcast API إلى ExtractionResult."""
    try:
        room = (data.get("data") or {}).get("data") or data.get("data") or {}
        if not room:
            return None
        owner = room.get("owner") or {}
        stats = room.get("stats") or {}
        live_room = room.get("live_room") or room

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
        res.author.avatar = owner.get("avatar_thumb", {}).get("url_list", [None])[0] if owner.get("avatar_thumb") else ""
        res.author.signature = owner.get("signature") or ""
        res.author.follower_count = owner.get("follower_count") or 0
        res.author.following_count = owner.get("following_count") or 0
        res.author.verified = bool(owner.get("verified"))
        res.author.video_count = owner.get("room_event_cnt") or 0

        # البث
        res.stats.play_count = stats.get("total_user") or stats.get("total_user_desp") or 0
        res.stats.digg_count = stats.get("like_count") or stats.get("like_count_str") or 0
        res.stats.comment_count = stats.get("comment_count") or 0
        res.stats.share_count = stats.get("share_count") or 0

        res.raw_keys.append("webcast_api")
        res.raw_json = {"webcast": data}
        return res
    except Exception as e:
        logger.exception(f"_from_webcast parse failed: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────────
#  نقطة الدخول الرئيسية
# ────────────────────────────────────────────────────────────────────────────
def extract(raw_url: str, timeout: int = 20) -> ExtractionResult:
    """يستخرج بيانات أي رابط TikTok باستخدام كل الاستراتيجيات."""
    session = build_session()
    parsed = parse_link(raw_url)

    # 1) حلّ الرابط المختصر
    if parsed.is_short or parsed.kind == "short":
        final = resolve_short_url(session, parsed.normalized, timeout=timeout)
        if not final:
            return ExtractionResult(success=False,
                                    url=parsed.normalized,
                                    error="تعذّر حلّ الرابط المختصر - قد يكون محذوفاً")
        parsed = parse_link(final)
        parsed.final_url = final
        parsed.notes.append("resolved_short")

    target = parsed.final_url or parsed.normalized

    # 2) جلب HTML
    ua = USER_AGENTS[hash(target) % len(USER_AGENTS)]
    try:
        r = session.get(target, timeout=timeout, headers={"User-Agent": ua})
    except requests.exceptions.SSLError:
        r = session.get(target, timeout=timeout, headers={"User-Agent": ua},
                        verify=False)
    except (requests.exceptions.ConnectionError, socket.timeout) as e:
        return ExtractionResult(success=False, url=target,
                                error=f"فشل الاتصال: {e}")
    except Exception as e:
        return ExtractionResult(success=False, url=target,
                                error=f"خطأ غير متوقع: {e}")

    if r.status_code >= 400:
        return ExtractionResult(success=False, url=target,
                                error=f"استجابة HTTP {r.status_code}")

    # كشف إعادة التوجيه الجغرافي (TikTok يحجب بعض المناطق)
    final = r.url or ""
    if re.search(r"/(hk|kr|jp|tw|sg|about|notfound)/?$", final, re.I) \
       and "video" not in final and "@@" not in final:
        # الخادم محجوب جغرافياً - نحاول Webcast API كحل أخير قبل الفشل
        logger.info("geo-block detected, trying Webcast API fallback")
        wc_result = _try_webcast_api(session, parsed)
        if wc_result and wc_result.success:
            return wc_result

        # الخادم محجوب جغرافياً - نرجع رسالة واضحة + بيانات وصفية بسيطة
        return ExtractionResult(
            success=False,
            url=target,
            final_url=final,
            kind=parsed.kind or "unknown",
            error="الخادم محجوب جغرافياً من TikTok. الكود يدعم الرابط بشكل صحيح "
                  "(تم تحليله كـ " + (parsed.kind or "رابط") + ")، لكن خوادم TikTok "
                  "ترفض الطلب من عنوان IP هذا. انشر التطبيق على خادم في منطقة مدعومة "
                  "لاستخراج البيانات الحقيقية. يمكنك تجربة الواجهة عبر وضع العرض التجريبي.",
        )

    html = r.text or ""
    soup = BeautifulSoup(html, "lxml")

    # 3) تطبيق الاستراتيجيات بالترتيب
    result: Optional[ExtractionResult] = None

    # (أ) UNIVERSAL_DATA
    uni = extract_universal_data(soup)
    if uni:
        try:
            result = _from_universal(uni)
        except Exception as e:
            logger.exception("universal parse failed: %s", e)

    # (ب) SIGI_STATE
    if not result or not result.success:
        sigi = extract_sigi_state(soup)
        if sigi:
            try:
                result = _from_sigi(sigi)
            except Exception as e:
                logger.exception("sigi parse failed: %s", e)

    # (ج) oEmbed (للفيديوهات فقط)
    if (not result or not result.success) and parsed.kind in ("video", "photo", "note"):
        oem = extract_oembed(session, target)
        if oem and "title" in oem:
            result = _from_oembed(oem)

    # (د) Meta tags + DOM fallback
    meta = extract_meta_tags(soup)
    dom = extract_links_from_dom(soup)
    if not result or not result.success:
        if meta:
            result = _from_meta(meta, dom)
    else:
        # أكمل الناقص من meta
        if not result.video.cover and meta.get("og:image"):
            result.video.cover = meta["og:image"]
        if not result.title and meta.get("og:title"):
            result.title = meta["og:title"]
        if not result.description and meta.get("og:description"):
            result.description = meta["og:description"]
        result.meta_tags = meta

    if not result:
        return ExtractionResult(
            success=False,
            url=target,
            error="لم يتم العثور على أي بيانات قابلة للاستخراج في الصفحة",
        )

    result.url = parsed.normalized
    result.final_url = target
    if not result.kind or result.kind == "unknown":
        result.kind = parsed.kind or "unknown"
    if not result.author.unique_id and parsed.username:
        result.author.unique_id = parsed.username
    if not result.video.cover and dom.get("images"):
        result.video.cover = dom["images"][0]

    return result


# ────────────────────────────────────────────────────────────────────────────
#  اختبار سريع عند التشغيل المباشر
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python extractor.py <tiktok_url>")
        sys.exit(1)
    res = extract(sys.argv[1])
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2,
                     default=str)[:4000])


# ────────────────────────────────────────────────────────────────────────────
#  مولّد البيانات التجريبية - يُنتج بيانات واقعية بناءً على نوع الرابط المُحلَّل
# ────────────────────────────────────────────────────────────────────────────
def generate_demo(raw_url: str) -> ExtractionResult:
    """يُنشئ بيانات تجريبية واقعية بناءً على رابط TikTok المُدخل.
    مفيد لعرض الواجهة عند حظر الخادم جغرافياً أو في البيئات التوضيحية."""
    import random

    parsed = parse_link(raw_url)
    # إذا كان مختصراً، نولّد كأنه فيديو (لأن أغلب المختصرات لفيديوهات)
    if parsed.kind == "short":
        parsed.kind = "video"
        parsed.username = parsed.username or "demo_user"
        parsed.content_id = parsed.content_id or str(random.randint(7100000000000000000, 7300000000000000000))

    kind = parsed.kind if parsed.kind != "unknown" else "video"
    username = parsed.username or "demo_creator"
    video_id = parsed.content_id or "7106594312292453675"

    # عناوين تجريبية واقعية
    demo_titles = [
        f"POV: عندما تكتشف أن اليوم عطلة 🎉 #fyp #foryou",
        f"جربت هذا التحدي وأصبح فيروسي! 😱 #{username} #viral",
        f"أفضل لحظات اليوم مع العائلة ❤️ #family #love",
        f"هذه الوصفة ستغيّر حياتك 🍳 #cooking #recipe",
        f"تعليم البرمجة من الصفر - الدرس 1 💻 #coding #tech",
    ]
    title = random.choice(demo_titles)

    # روابط CDN تجريبية (لن تعمل فعلياً، لكنها تُظهر البنية الصحيحة)
    cdn_base = "https://v19-webcast.tiktokcdn.com"
    demo_video_url = f"{cdn_base}/video/mosaic/trans/{video_id}.mp4"
    demo_cover = f"https://p16-sign-va.tiktokcdn.com/obj/tos-useast2a-p-0037-aiso/{video_id}~tplv-r00ih4n6xq-origin-cover.jpeg"
    demo_avatar = f"https://p16-sign-va.tiktokcdn.com/tos-useast2a-avt-0068-aiso/avatar-{username}.jpeg"
    demo_music_url = f"https://v16-webcast.tiktokcdn.com/music/{random.randint(6700000000000000000, 7300000000000000000)}.mp3"

    # إحصائيات عشوائية واقعية
    follower_base = random.choice([12_500, 45_800, 89_200, 156_000, 432_000, 1_200_000, 5_400_000])
    play_base = random.randint(50_000, 8_500_000)

    result = ExtractionResult(
        success=True,
        kind=kind,
        url=parsed.normalized,
        final_url=parsed.normalized,
        title=title,
        description=title,
        create_time=int(datetime.utcnow().timestamp()) - random.randint(3600, 86400 * 30),
        author=Author(
            unique_id=username,
            nickname=username.replace("_", " ").title(),
            user_id=str(random.randint(6700000000000000000, 7300000000000000000)),
            avatar=demo_avatar,
            signature=f"📍 Welcome to my channel\n📩 Business: {username}@email.com\n🎯 Goal: 1M followers",
            follower_count=follower_base,
            following_count=random.randint(50, 800),
            like_count=follower_base * random.randint(15, 35),
            video_count=random.randint(45, 320),
            verified=random.random() > 0.7,
            sec_uid="MS4wLjABAAAA" + hashlib.sha256(username.encode()).hexdigest()[:40],
        ),
        stats=Stats(
            digg_count=int(play_base * random.uniform(0.08, 0.18)),
            comment_count=int(play_base * random.uniform(0.005, 0.02)),
            share_count=int(play_base * random.uniform(0.003, 0.015)),
            play_count=play_base,
            collect_count=int(play_base * random.uniform(0.01, 0.04)),
        ),
        video=VideoMedia(
            play_url=demo_video_url,
            download_url=demo_video_url,
            cover=demo_cover,
            dynamic_cover=demo_cover.replace("origin-cover", "dynamic-cover"),
            origin_cover=demo_cover,
            duration=random.randint(15, 60),
            width=1080,
            height=1920,
            ratio="9:16",
            format="mp4",
        ) if kind in ("video", "live") else VideoMedia(cover=demo_cover),
        music=Music(
            id=str(random.randint(6700000000000000000, 7300000000000000000)),
            title=random.choice([
                "original sound - " + username,
                "Sofia - Clairo",
                "Espresso - Sabrina Carpenter",
                "Birds of a Feather - Billie Eilish",
                "Aesthetic - Tollan Kim",
            ]),
            author=username,
            play_url=demo_music_url,
            cover=demo_cover,
            duration=random.randint(15, 60),
        ),
        images=[demo_cover.replace(f"{video_id}", f"{video_id}_{i}")
                for i in range(random.randint(2, 5))] if kind == "photo" else [],
        hashtags=re.findall(r"#(\w+)", title),
        mentions=re.findall(r"@(\w+)", title),
        raw_keys=["demo_mode"],
        meta_tags={
            "_demo_mode": "true",
            "_note": "بيانات تجريبية لأغراض العرض فقط. الكود حاول الوصول إلى TikTok "
                     "ولكن الخادم محجوب جغرافياً. عند النشر على خادم مدعوم، سيتم استخراج بيانات حقيقية.",
            "og:title": title,
            "og:description": title,
            "og:image": demo_cover,
            "og:type": "video.other",
            "og:video": demo_video_url,
        },
        raw_json={
            "_demo": True,
            "_parsed_link": asdict(parsed),
            "_note": "Demo data generated because real extraction was blocked or unavailable.",
        },
    )
    return result
