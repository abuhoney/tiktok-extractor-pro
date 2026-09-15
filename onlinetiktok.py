#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TikTok Deep Info Extractor v3.0
- Fixed DNS resolution issues
- Direct IP connection fallback
- Multiple proxy fallbacks
- Robust error handling
"""

import os
import sys
import json
import re
import time
import base64
import hashlib
import random
import socket
import threading
import queue
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from html import unescape
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== SSL and warnings ==========
import ssl
import warnings
warnings.filterwarnings('ignore')
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

# ========== Imports ==========
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ========== Logging ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== Configuration ==========
MAX_RESULTS = 20
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
]

# TikTok IP addresses (hardcoded for DNS fallback)
TIKTOK_IPS = [
    '23.13.228.4',
    '23.13.228.5',
    '23.13.228.6',
    '23.13.228.7',
    '23.13.228.8',
]

# =====================================================================
# ========== DNS RESOLVER ==========
# =====================================================================

class DNSResolver:
    """Custom DNS resolver with fallback"""
    
    @staticmethod
    def resolve(hostname: str) -> List[str]:
        """Resolve hostname to IP addresses"""
        ips = []
        
        # Try system DNS first
        try:
            ips = socket.gethostbyname_ex(hostname)[2]
            if ips:
                logger.debug(f"System DNS resolved {hostname} -> {ips[0]}")
                return ips
        except:
            pass
        
        # Try Google DNS (8.8.8.8)
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.nameservers = ['8.8.8.8', '1.1.1.1']
            answers = resolver.resolve(hostname, 'A')
            ips = [str(r) for r in answers]
            if ips:
                logger.debug(f"Google DNS resolved {hostname} -> {ips[0]}")
                return ips
        except:
            pass
        
        # Fallback to hardcoded IPs for tiktok.com
        if 'tiktok.com' in hostname:
            logger.warning(f"Using fallback IPs for {hostname}")
            return TIKTOK_IPS
        
        return []


# =====================================================================
# ========== PROXY MANAGER ==========
# =====================================================================

class ProxyManager:
    """Manage proxies with automatic fallback"""
    
    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self._init_proxies()
    
    def _init_proxies(self):
        """Initialize proxy list with fallbacks"""
        # Direct connection
        self.proxies.append(None)
        
        # Free proxies (updated periodically)
        self._fetch_free_proxies()
    
    def _fetch_free_proxies(self):
        """Fetch free proxies from online sources"""
        try:
            # Use multiple sources
            sources = [
                'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all',
                'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
            ]
            
            for url in sources:
                try:
                    response = requests.get(url, timeout=5)
                    if response.status_code == 200:
                        for line in response.text.strip().split('\n'):
                            line = line.strip()
                            if ':' in line and not line.startswith('#'):
                                proxy = f"http://{line}"
                                self.proxies.append({'http': proxy, 'https': proxy})
                        logger.info(f"✅ Loaded {len(self.proxies)} proxies")
                        break
                except:
                    continue
        except:
            pass
    
    def get_proxy(self) -> Optional[Dict]:
        """Get next proxy with rotation"""
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.current_index % len(self.proxies)]
        self.current_index += 1
        
        if proxy:
            logger.debug(f"🔄 Using proxy: {proxy.get('http', 'unknown')}")
        
        return proxy


# =====================================================================
# ========== DATA STRUCTURES ==========
# =====================================================================

@dataclass
class TikTokVideo:
    id: str = ''
    desc: str = ''
    author: str = ''
    author_id: str = ''
    author_sec_uid: str = ''
    avatar: str = ''
    cover: str = ''
    play_url: str = ''
    wm_play_url: str = ''
    hd_play_url: str = ''
    music_title: str = ''
    music_author: str = ''
    stats_plays: int = 0
    stats_likes: int = 0
    stats_comments: int = 0
    stats_shares: int = 0
    stats_bookmarks: int = 0
    duration: int = 0
    create_time: int = 0
    is_ads: bool = False
    is_live: bool = False
    room_id: str = ''
    url: str = ''
    video_url_mp4: str = ''

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TikTokUserData:
    user_id: str = ''
    sec_uid: str = ''
    unique_id: str = ''
    nickname: str = ''
    bio: str = ''
    avatar_url: str = ''
    follower_count: int = 0
    following_count: int = 0
    like_count: int = 0
    verified: bool = False
    private: bool = False
    country: str = ''
    region: str = ''
    language: str = ''
    signature: str = ''


@dataclass
class TikTokLiveData:
    room_id: str = ''
    stream_id: str = ''
    title: str = ''
    status: int = 0
    is_live: bool = False
    viewer_count: int = 0
    like_count: int = 0
    diamond_count: int = 0
    start_time: int = 0
    cover_url: str = ''
    duration: str = ''
    stream_urls: Dict = field(default_factory=dict)


@dataclass
class TikTokSecurityData:
    csrf_token: str = ''
    nonce: str = ''
    wid: str = ''
    signature: str = ''
    webid: str = ''


@dataclass
class TikTokExtractedFiles:
    html: str = ''
    sigi_state: Dict = field(default_factory=dict)
    universal_data: Dict = field(default_factory=dict)
    pumbaa_rule: Dict = field(default_factory=dict)
    slardar_config: Dict = field(default_factory=dict)
    api_domains: Dict = field(default_factory=dict)
    css_files: List[str] = field(default_factory=list)
    js_files: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)


@dataclass
class TikTokCompleteData:
    url: str = ''
    final_url: str = ''
    timestamp: str = ''
    user: TikTokUserData = field(default_factory=TikTokUserData)
    live: TikTokLiveData = field(default_factory=TikTokLiveData)
    security: TikTokSecurityData = field(default_factory=TikTokSecurityData)
    files: TikTokExtractedFiles = field(default_factory=TikTokExtractedFiles)
    all_ids: Dict = field(default_factory=dict)
    url_params: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)
    videos: List[TikTokVideo] = field(default_factory=list)
    raw_data: Dict = field(default_factory=dict)


# =====================================================================
# ========== TIKTOK SEARCH ENGINE ==========
# =====================================================================

class TikTokSearchEngine:
    def __init__(self, proxy_manager: ProxyManager = None):
        self.proxy_manager = proxy_manager or ProxyManager()
        self.session = self._create_session()
        self._ms_token = ''
        self._verify_fp = 'verify_' + hashlib.md5(os.urandom(16)).hexdigest()
        self._init_tokens()

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = False
        
        # Disable DNS caching to avoid stale entries
        session.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        })
        
        # Custom DNS resolver for session
        session.mount('https://', CustomHTTPAdapter())
        session.mount('http://', CustomHTTPAdapter())
        
        return session

    def _init_tokens(self):
        """Initialize tokens with retry"""
        for attempt in range(3):
            try:
                # Use IP directly if DNS fails
                response = self.session.get('https://www.tiktok.com/', timeout=15, allow_redirects=True)
                html = response.text
                
                ms_match = re.search(r'"msToken":"([^"]+)"', html)
                if ms_match:
                    self._ms_token = ms_match.group(1)
                
                vf_match = re.search(r'"verifyFp":"([^"]+)"', html)
                if vf_match:
                    self._verify_fp = vf_match.group(1)
                    
                logger.info(f"✅ Tokens initialized")
                return
            except Exception as e:
                logger.warning(f"⚠️ Token init attempt {attempt+1} failed: {e}")
                time.sleep(2)
        
        logger.warning("⚠️ Could not initialize tokens, using defaults")

    def _fix_url(self, url: str) -> str:
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return 'https://www.tiktok.com' + url
        return url

    def search(self, query: str, count: int = MAX_RESULTS) -> List[TikTokVideo]:
        """Search videos with retry"""
        logger.info(f"🔍 Searching: {query}")
        
        for attempt in range(3):
            try:
                # Rotate proxy
                if self.proxy_manager:
                    proxy = self.proxy_manager.get_proxy()
                    if proxy:
                        self.session.proxies.update(proxy)
                
                encoded_query = quote(query)
                url = f"https://www.tiktok.com/search/video?q={encoded_query}"
                
                response = self.session.get(url, timeout=30, allow_redirects=True)
                
                if response.status_code == 200:
                    html = response.text
                    videos = self._extract_videos(html)
                    if videos:
                        logger.info(f"✅ Found {len(videos)} videos")
                        return videos[:count]
                    
                    videos = self._regex_fallback(html)
                    if videos:
                        logger.info(f"✅ Found {len(videos)} videos (fallback)")
                        return videos[:count]
                
                logger.warning(f"⚠️ Attempt {attempt+1} failed: HTTP {response.status_code}")
                time.sleep(2)
                
            except Exception as e:
                logger.warning(f"⚠️ Attempt {attempt+1} error: {e}")
                time.sleep(3)
        
        logger.error(f"❌ Search failed after 3 attempts")
        return []

    def _extract_videos(self, html: str) -> List[TikTokVideo]:
        videos = []
        
        pattern = r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                raw = match.group(1).strip()
                if raw.startswith('undefined'):
                    raw = raw.replace('undefined', '', 1).strip().lstrip(',')
                data = json.loads(raw)
                
                item_list = self._find_items_list(data)
                if item_list and isinstance(item_list, list):
                    for item in item_list:
                        if isinstance(item, dict):
                            video = self._parse_video_item(item)
                            if video and not video.is_ads:
                                videos.append(video)
            except Exception as e:
                logger.debug(f"Universal data parse error: {e}")
        
        return videos

    def _find_items_list(self, obj, depth: int = 0):
        if depth > 15:
            return None
        
        if isinstance(obj, list) and len(obj) > 0:
            if isinstance(obj[0], dict):
                if obj[0].get('id') or obj[0].get('itemStruct') or obj[0].get('desc'):
                    return obj
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ['itemList', 'items', 'list', 'videoList', 'feedData']:
                    if isinstance(value, list) and len(value) > 0:
                        if isinstance(value[0], dict):
                            if value[0].get('id') or value[0].get('itemStruct'):
                                return value
                result = self._find_items_list(value, depth + 1)
                if result is not None:
                    return result
        
        return None

    def _parse_video_item(self, item: Dict) -> Optional[TikTokVideo]:
        if not isinstance(item, dict):
            return None
        
        video = TikTokVideo()
        
        video.id = str(item.get('id', ''))
        if not video.id:
            video.id = str(item.get('itemStruct', {}).get('id', ''))
        if not video.id:
            return None
        
        video.desc = unescape(str(item.get('desc', '')))
        if not video.desc:
            video.desc = unescape(str(item.get('itemStruct', {}).get('desc', '')))
        
        author = item.get('author', {})
        if not author:
            author = item.get('itemStruct', {}).get('author', {})
        
        if author:
            video.author = author.get('uniqueId', '') or author.get('nickname', '')
            video.author_id = str(author.get('id', ''))
            video.author_sec_uid = author.get('secUid', '')
            video.avatar = self._fix_url(author.get('avatarLarger', '') or author.get('avatarMedium', '') or '')
        
        video_data = item.get('video', {})
        if not video_data:
            video_data = item.get('itemStruct', {}).get('video', {})
        
        if video_data:
            video.cover = self._fix_url(video_data.get('originCover', '') or video_data.get('cover', ''))
            video.play_url = self._fix_url(video_data.get('playAddr', ''))
            video.wm_play_url = self._fix_url(video_data.get('downloadAddr', ''))
            video.hd_play_url = self._fix_url(video_data.get('playAddrH264', ''))
            video.duration = int(video_data.get('duration', 0) or 0)
        
        stats = item.get('stats', {})
        if not stats:
            stats = item.get('itemStruct', {}).get('stats', {})
        
        if stats:
            video.stats_plays = int(stats.get('playCount', 0) or 0)
            video.stats_likes = int(stats.get('diggCount', 0) or 0)
            video.stats_comments = int(stats.get('commentCount', 0) or 0)
            video.stats_shares = int(stats.get('shareCount', 0) or 0)
            video.stats_bookmarks = int(stats.get('collectCount', 0) or 0)
        
        music = item.get('music', {})
        if not music:
            music = item.get('itemStruct', {}).get('music', {})
        
        if music:
            video.music_title = music.get('title', '')
            video.music_author = music.get('authorName', '')
        
        video.create_time = int(item.get('createTime', 0) or item.get('itemStruct', {}).get('createTime', 0) or 0)
        video.is_ads = bool(item.get('isAds', False) or False)
        
        if video.author and video.id:
            video.url = f"https://www.tiktok.com/@{video.author}/video/{video.id}"
        else:
            video.url = f"https://www.tiktok.com/video/{video.id}"
        
        return video

    def _regex_fallback(self, html: str) -> List[TikTokVideo]:
        videos = []
        seen = set()
        
        id_pattern = r'"id"\s*:\s*"(\d{15,})"'
        for match in re.finditer(id_pattern, html):
            video_id = match.group(1)
            if video_id in seen:
                continue
            seen.add(video_id)
            
            start = max(0, match.start() - 3000)
            end = min(len(html), match.end() + 5000)
            chunk = html[start:end]
            
            video = TikTokVideo(id=video_id, url=f"https://www.tiktok.com/video/{video_id}")
            
            desc_match = re.search(r'"desc"\s*:\s*"([^"]{5,})"', chunk)
            if desc_match:
                video.desc = unescape(desc_match.group(1)[:200])
            
            author_match = re.search(r'"uniqueId"\s*:\s*"([^"]+)"', chunk)
            if author_match:
                video.author = author_match.group(1)
                video.url = f"https://www.tiktok.com/@{video.author}/video/{video_id}"
            
            plays_match = re.search(r'"playCount"\s*:\s*(\d+)', chunk)
            if plays_match:
                video.stats_plays = int(plays_match.group(1))
            
            likes_match = re.search(r'"diggCount"\s*:\s*(\d+)', chunk)
            if likes_match:
                video.stats_likes = int(likes_match.group(1))
            
            videos.append(video)
        
        return videos


# =====================================================================
# ========== CUSTOM HTTP ADAPTER WITH DNS FALLBACK ==========
# =====================================================================

class CustomHTTPAdapter(HTTPAdapter):
    """HTTP adapter with custom DNS resolution"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def get_connection(self, url, proxies=None):
        """Override to handle DNS resolution"""
        parsed = urlparse(url)
        hostname = parsed.hostname
        
        if hostname and 'tiktok.com' in hostname:
            # Try to resolve using custom resolver
            ips = DNSResolver.resolve(hostname)
            if ips:
                # Use first resolved IP
                original_url = url
                url = url.replace(hostname, ips[0])
                # Add host header for SNI
                self.headers = {'Host': hostname}
        
        return super().get_connection(url, proxies)
    
    def add_headers(self, request, **kwargs):
        """Add custom headers"""
        if hasattr(self, 'headers'):
            for key, value in self.headers.items():
                request.headers[key] = value
        return super().add_headers(request, **kwargs)


# =====================================================================
# ========== TIKTOK EXTRACTOR ==========
# =====================================================================

class TikTokExtractor:
    def __init__(self, proxy_manager: ProxyManager = None):
        self.proxy_manager = proxy_manager or ProxyManager()
        self.session = self._create_session()
        self.saved_files: List[str] = []
        self.dir_path: str = ''
        self.current_data: TikTokCompleteData = None

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = False
        
        # Use custom adapter with DNS fallback
        session.mount('https://', CustomHTTPAdapter())
        session.mount('http://', CustomHTTPAdapter())
        
        session.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
        
        return session

    def _fix_url(self, url: str) -> str:
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return 'https://www.tiktok.com' + url
        return url

    def _deep_get(self, data: Dict, *keys, default=None):
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return default
            if current is None:
                return default
        return current

    def extract(self, url: str) -> TikTokCompleteData:
        """Extract all data from TikTok URL with retry"""
        logger.info(f"🔍 Extracting: {url}")
        
        for attempt in range(3):
            try:
                # Rotate proxy
                if self.proxy_manager:
                    proxy = self.proxy_manager.get_proxy()
                    if proxy:
                        self.session.proxies.update(proxy)
                
                response = self.session.get(url, allow_redirects=True, timeout=30)
                if response.status_code == 200:
                    html = response.text
                    final_url = str(response.url)
                    break
                else:
                    logger.warning(f"⚠️ Attempt {attempt+1}: HTTP {response.status_code}")
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"⚠️ Attempt {attempt+1} error: {e}")
                time.sleep(3)
        else:
            logger.error(f"❌ Failed after 3 attempts")
            return None
        
        # Parse URL params
        url_params = {}
        try:
            parsed = urlparse(final_url)
            for key, values in parse_qs(parsed.query).items():
                url_params[key] = values[0] if len(values) == 1 else values
            path_parts = [p for p in parsed.path.split('/') if p]
            url_params['path_parts'] = path_parts
            for part in path_parts:
                if part.startswith('@'):
                    url_params['username'] = part[1:]
                    break
        except:
            pass
        
        # Extract all IDs
        ids = self._extract_ids(html, url_params)
        
        # Extract JSON files
        json_files = self._extract_json(html)
        
        # Extract files
        css_files = self._extract_css(html)
        js_files = self._extract_js(html)
        images = self._extract_images(html)
        
        # Extract user data
        user_data = self._extract_user_data(ids, json_files)
        
        # Extract live data
        live_data = self._extract_live_data(ids, json_files)
        
        # Extract security data
        security_data = self._extract_security_data(ids, json_files)
        
        # Extract videos from page
        videos = self._extract_page_videos(html)
        
        # Build complete data
        data = TikTokCompleteData(
            url=url,
            final_url=final_url,
            timestamp=datetime.now().isoformat(),
            user=user_data,
            live=live_data,
            security=security_data,
            files=TikTokExtractedFiles(
                html=html,
                sigi_state=json_files.get('sigi_state', {}),
                universal_data=json_files.get('universal_data', {}),
                pumbaa_rule=json_files.get('pumbaa_rule', {}),
                slardar_config=json_files.get('slardar_config', {}),
                api_domains=json_files.get('api_domains', {}),
                css_files=css_files,
                js_files=js_files,
                images=images
            ),
            all_ids=ids,
            url_params=url_params,
            metadata={
                'html_size': len(html),
                'json_count': len(json_files),
                'total_ids': sum(len(v) for v in ids.values()),
                'css_count': len(css_files),
                'js_count': len(js_files),
                'images_count': len(images),
                'videos_count': len(videos)
            },
            videos=videos,
            raw_data=json_files
        )
        
        self.current_data = data
        self._save_files(data)
        
        return data

    def _extract_ids(self, html: str, url_params: Dict) -> Dict:
        ids = {
            'user': {}, 'live': {}, 'security': {}, 'app': {},
            'request': {}, 'url': url_params.copy(), 'other': {}, 'extracted': {}
        }
        
        patterns = [
            (r'"id"\s*[:=]\s*"(\d+)"', 'user_id', 'user'),
            (r'"secUid"\s*[:=]\s*"([^"]+)"', 'sec_uid', 'user'),
            (r'"uniqueId"\s*[:=]\s*"([^"]+)"', 'unique_id', 'user'),
            (r'"nickname"\s*[:=]\s*"([^"]+)"', 'nickname', 'user'),
            (r'"followerCount"\s*[:=]\s*(\d+)', 'follower_count', 'user'),
            (r'"followingCount"\s*[:=]\s*(\d+)', 'following_count', 'user'),
            (r'"heartCount"\s*[:=]\s*(\d+)', 'like_count', 'user'),
            (r'"verified"\s*[:=]\s*(true|false)', 'verified', 'user'),
            (r'"private"\s*[:=]\s*(true|false)', 'private', 'user'),
            (r'"signature"\s*[:=]\s*"([^"]+)"', 'signature', 'user'),
            (r'"roomId"\s*[:=]\s*"(\d+)"', 'room_id', 'live'),
            (r'"streamId"\s*[:=]\s*"([^"]+)"', 'stream_id', 'live'),
            (r'"title"\s*[:=]\s*"([^"]+)"', 'title', 'live'),
            (r'"status"\s*[:=]\s*(\d+)', 'status', 'live'),
            (r'"viewerCount"\s*[:=]\s*(\d+)', 'viewer_count', 'live'),
            (r'"csrfToken"\s*[:=]\s*"([^"]+)"', 'csrf_token', 'security'),
            (r'"nonce"\s*[:=]\s*"([^"]+)"', 'nonce', 'security'),
            (r'"wid"\s*[:=]\s*"(\d+)"', 'wid', 'security'),
            (r'"webid"\s*[:=]\s*"(\d+)"', 'webid', 'security'),
        ]
        
        for pattern, key, category in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                value = match.group(1)
                if key in ['verified', 'private']:
                    value = value.lower() == 'true'
                elif key in ['follower_count', 'following_count', 'like_count', 'status', 'viewer_count']:
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                ids[category][key] = value
        
        return ids

    def _extract_json(self, html: str) -> Dict:
        files = {}
        
        json_patterns = [
            ('sigi_state', r'<script\s+id="SIGI_STATE"\s+type="application/json">(.*?)</script>'),
            ('universal_data', r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">(.*?)</script>'),
            ('pumbaa_rule', r'<script\s+id="pumbaa-rule"\s+type="application/json">(.*?)</script>'),
            ('slardar_config', r'<script\s+id="slardar-config"\s+type="application/json">(.*?)</script>'),
            ('api_domains', r'<script\s+id="api-domains"\s+type="application/json">(.*?)</script>'),
            ('tiktok_environment', r'<script\s+id="tiktok-environment-webapp"\s+type="application/json">(.*?)</script>'),
        ]
        
        for name, pattern in json_patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    content = match.group(1).strip()
                    if name == 'pumbaa_rule':
                        try:
                            content = base64.b64decode(content).decode('utf-8')
                        except:
                            pass
                    files[name] = json.loads(content)
                except:
                    files[name] = {'error': 'parse_failed', 'raw': match.group(1)[:500]}
        
        return files

    def _extract_css(self, html: str) -> List[str]:
        css_files = set()
        pattern = r'href=["\']([^"\']*\.css[^"\']*)["\']'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            css_files.add(self._fix_url(match.group(1)))
        return list(css_files)

    def _extract_js(self, html: str) -> List[str]:
        js_files = set()
        pattern = r'src=["\']([^"\']*\.js[^"\']*)["\']'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            js_files.add(self._fix_url(match.group(1)))
        return list(js_files)

    def _extract_images(self, html: str) -> List[str]:
        images = set()
        pattern = r'<img[^>]+src=["\']([^"\']+)["\']'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            url = self._fix_url(match.group(1))
            if any(ext in url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']):
                images.add(url)
        return list(images)

    def _extract_user_data(self, ids: Dict, json_files: Dict) -> TikTokUserData:
        user_ids = ids.get('user', {})
        sigi_state = json_files.get('sigi_state', {})
        
        if sigi_state:
            live_room_user = self._deep_get(sigi_state, 'LiveRoom', 'liveRoomUserInfo', default={})
            sigi_user = self._deep_get(live_room_user, 'user', default={})
            sigi_stats = self._deep_get(live_room_user, 'stats', default={})
            
            if sigi_user:
                return TikTokUserData(
                    user_id=str(sigi_user.get('id', user_ids.get('user_id', ''))),
                    sec_uid=str(sigi_user.get('secUid', user_ids.get('sec_uid', ''))),
                    unique_id=str(sigi_user.get('uniqueId', user_ids.get('unique_id', ''))),
                    nickname=str(sigi_user.get('nickname', user_ids.get('nickname', ''))),
                    signature=str(sigi_user.get('signature', user_ids.get('signature', ''))),
                    verified=bool(sigi_user.get('verified', user_ids.get('verified', False))),
                    private=bool(sigi_user.get('private', user_ids.get('private', False))),
                    avatar_url=str(sigi_user.get('avatarLarger', '')),
                    follower_count=int(sigi_stats.get('followerCount', user_ids.get('follower_count', 0))),
                    following_count=int(sigi_stats.get('followingCount', user_ids.get('following_count', 0))),
                    like_count=int(user_ids.get('like_count', 0)),
                    region=str(user_ids.get('region', '')),
                    language=str(user_ids.get('language', ''))
                )
        
        return TikTokUserData(
            user_id=str(user_ids.get('user_id', '')),
            sec_uid=str(user_ids.get('sec_uid', '')),
            unique_id=str(user_ids.get('unique_id', '')),
            nickname=str(user_ids.get('nickname', '')),
            signature=str(user_ids.get('signature', '')),
            verified=bool(user_ids.get('verified', False)),
            private=bool(user_ids.get('private', False)),
            follower_count=int(user_ids.get('follower_count', 0)),
            following_count=int(user_ids.get('following_count', 0)),
            like_count=int(user_ids.get('like_count', 0))
        )

    def _extract_live_data(self, ids: Dict, json_files: Dict) -> TikTokLiveData:
        live_ids = ids.get('live', {})
        sigi_state = json_files.get('sigi_state', {})
        
        if sigi_state:
            live_room_user = self._deep_get(sigi_state, 'LiveRoom', 'liveRoomUserInfo', default={})
            live_room = self._deep_get(live_room_user, 'liveRoom', default={})
            stats = self._deep_get(live_room, 'liveRoomStats', default={})
            
            if live_room:
                room_id = str(live_room.get('roomId', live_ids.get('room_id', '')))
                status = int(live_room.get('status', live_ids.get('status', 0)))
                
                stream_urls = {}
                stream_data = live_room.get('streamData', {})
                if stream_data:
                    pull_data = stream_data.get('pull_data', {})
                    stream_json = pull_data.get('stream_data', '{}')
                    try:
                        if isinstance(stream_json, str):
                            stream_json = json.loads(stream_json)
                        if 'data' in stream_json:
                            for quality, data in stream_json['data'].items():
                                if 'main' in data:
                                    for key, value in data['main'].items():
                                        if isinstance(value, str) and (value.startswith('http') or value.startswith('https')):
                                            stream_urls[f"{quality}_{key}"] = value
                    except:
                        pass
                
                return TikTokLiveData(
                    room_id=room_id,
                    stream_id=str(live_room.get('streamId', live_ids.get('stream_id', ''))),
                    title=str(live_room.get('title', live_ids.get('title', ''))),
                    status=status,
                    is_live=(status == 2),
                    viewer_count=int(stats.get('userCount', live_ids.get('viewer_count', 0))),
                    like_count=int(stats.get('likeCount', live_ids.get('like_count', 0))),
                    diamond_count=int(stats.get('diamondCount', live_ids.get('diamond_count', 0))),
                    start_time=int(live_room.get('startTime', 0)),
                    cover_url=str(live_room.get('coverUrl', '')),
                    stream_urls=stream_urls
                )
        
        status = int(live_ids.get('status', 0))
        return TikTokLiveData(
            room_id=str(live_ids.get('room_id', '')),
            stream_id=str(live_ids.get('stream_id', '')),
            title=str(live_ids.get('title', '')),
            status=status,
            is_live=(status == 2),
            viewer_count=int(live_ids.get('viewer_count', 0)),
            like_count=int(live_ids.get('like_count', 0)),
            diamond_count=int(live_ids.get('diamond_count', 0)),
            start_time=int(live_ids.get('start_time', 0))
        )

    def _extract_security_data(self, ids: Dict, json_files: Dict) -> TikTokSecurityData:
        sec_ids = ids.get('security', {})
        universal = json_files.get('universal_data', {})
        
        if universal:
            app_context = self._deep_get(universal, '__DEFAULT_SCOPE__', 'webapp.app-context', default={})
            if app_context:
                return TikTokSecurityData(
                    csrf_token=str(app_context.get('csrfToken', sec_ids.get('csrf_token', ''))),
                    nonce=str(app_context.get('nonce', sec_ids.get('nonce', ''))),
                    wid=str(app_context.get('wid', sec_ids.get('wid', ''))),
                    webid=str(app_context.get('webid', sec_ids.get('webid', ''))),
                    signature=str(sec_ids.get('signature', ''))
                )
        
        return TikTokSecurityData(
            csrf_token=str(sec_ids.get('csrf_token', '')),
            nonce=str(sec_ids.get('nonce', '')),
            wid=str(sec_ids.get('wid', '')),
            webid=str(sec_ids.get('webid', '')),
            signature=str(sec_ids.get('signature', ''))
        )

    def _extract_page_videos(self, html: str) -> List[TikTokVideo]:
        videos = []
        
        pattern = r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                raw = match.group(1).strip()
                if raw.startswith('undefined'):
                    raw = raw.replace('undefined', '', 1).strip().lstrip(',')
                data = json.loads(raw)
                
                video_detail = self._deep_get(data, '__DEFAULT_SCOPE__', 'webapp.video-detail', default={})
                if video_detail:
                    video = TikTokVideo()
                    video.id = str(video_detail.get('id', ''))
                    video.desc = unescape(str(video_detail.get('desc', '')))
                    video.author = str(video_detail.get('author', {}).get('uniqueId', ''))
                    video.author_id = str(video_detail.get('author', {}).get('id', ''))
                    video.author_sec_uid = str(video_detail.get('author', {}).get('secUid', ''))
                    video.avatar = self._fix_url(video_detail.get('author', {}).get('avatarLarger', ''))
                    video.cover = self._fix_url(video_detail.get('video', {}).get('cover', ''))
                    video.play_url = self._fix_url(video_detail.get('video', {}).get('playAddr', ''))
                    video.duration = int(video_detail.get('video', {}).get('duration', 0))
                    video.stats_plays = int(video_detail.get('stats', {}).get('playCount', 0))
                    video.stats_likes = int(video_detail.get('stats', {}).get('diggCount', 0))
                    video.stats_comments = int(video_detail.get('stats', {}).get('commentCount', 0))
                    video.stats_shares = int(video_detail.get('stats', {}).get('shareCount', 0))
                    if video.author and video.id:
                        video.url = f"https://www.tiktok.com/@{video.author}/video/{video.id}"
                    if video.id:
                        videos.append(video)
            except Exception as e:
                logger.debug(f"Video detail parse error: {e}")
        
        return videos

    def _save_files(self, data: TikTokCompleteData):
        label = data.user.unique_id or data.user.nickname or 'extract'
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        folder_name = f"{label}_{timestamp}"[:50]
        
        base_path = os.path.join('tiktok_deep_data', folder_name)
        Path(base_path).mkdir(parents=True, exist_ok=True)
        self.dir_path = base_path
        self.saved_files = []
        
        # Save HTML
        html_path = os.path.join(base_path, 'page.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(data.files.html)
        self.saved_files.append(html_path)
        
        # Save JSON files
        json_dir = os.path.join(base_path, 'json')
        Path(json_dir).mkdir(parents=True, exist_ok=True)
        
        json_files = {
            'sigi_state': data.files.sigi_state,
            'universal_data': data.files.universal_data,
            'pumbaa_rule': data.files.pumbaa_rule,
            'slardar_config': data.files.slardar_config,
            'api_domains': data.files.api_domains,
        }
        for name, content in json_files.items():
            if content:
                path = os.path.join(json_dir, f'{name}.json')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(content, f, ensure_ascii=False, indent=2)
                self.saved_files.append(path)
        
        # Save complete report
        report = {
            'url': data.url,
            'final_url': data.final_url,
            'timestamp': data.timestamp,
            'user': asdict(data.user),
            'live': asdict(data.live),
            'security': asdict(data.security),
            'all_ids': data.all_ids,
            'metadata': data.metadata,
            'css_files': data.files.css_files,
            'js_files': data.files.js_files,
            'images': data.files.images,
            'videos': [v.to_dict() for v in data.videos],
        }
        report_path = os.path.join(base_path, 'complete_data.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self.saved_files.append(report_path)
        
        if data.videos:
            videos_path = os.path.join(base_path, 'videos_list.json')
            with open(videos_path, 'w', encoding='utf-8') as f:
                json.dump([v.to_dict() for v in data.videos], f, ensure_ascii=False, indent=2)
            self.saved_files.append(videos_path)
            
            urls_path = os.path.join(base_path, 'video_urls.txt')
            with open(urls_path, 'w', encoding='utf-8') as f:
                for v in data.videos:
                    if v.play_url:
                        f.write(f"{v.url} -> {v.play_url}\n")
            self.saved_files.append(urls_path)
        
        logger.info(f"💾 Saved {len(self.saved_files)} files to {base_path}")


# =====================================================================
# ========== HTTP WEB SERVER ==========
# =====================================================================

class TikTokWebHandler(BaseHTTPRequestHandler):
    server_version = "TikTokDeepInfo/3.0"
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(self.get_index_html().encode('utf-8'))
        elif self.path == '/style.css':
            self.send_response(200)
            self.send_header('Content-type', 'text/css')
            self.end_headers()
            self.wfile.write(self.get_css().encode('utf-8'))
        elif self.path == '/api/logs':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            logs = []
            if hasattr(self.server, 'log_queue'):
                temp_list = []
                while not self.server.log_queue.empty():
                    try:
                        temp_list.append(self.server.log_queue.get_nowait())
                    except queue.Empty:
                        break
                for item in temp_list:
                    self.server.log_queue.put(item)
                logs = temp_list
            self.wfile.write(json.dumps(logs[-100:]).encode('utf-8'))
        elif self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            status = {
                'status': 'running',
                'timestamp': datetime.now().isoformat(),
                'data': self.server.current_data_dict if hasattr(self.server, 'current_data_dict') else None,
                'files': self.server.saved_files if hasattr(self.server, 'saved_files') else []
            }
            self.wfile.write(json.dumps(status).encode('utf-8'))
        elif self.path.startswith('/download/'):
            filename = self.path.split('/')[-1]
            if hasattr(self.server, 'saved_files'):
                for f in self.server.saved_files:
                    if os.path.basename(f) == filename:
                        self.send_response(200)
                        self.send_header('Content-type', 'application/octet-stream')
                        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                        self.end_headers()
                        with open(f, 'rb') as file:
                            self.wfile.write(file.read())
                        return
            self.send_response(404)
            self.end_headers()
        elif self.path.startswith('/live/'):
            video_id = self.path.split('/')[-1]
            if hasattr(self.server, 'current_data') and self.server.current_data:
                for v in self.server.current_data.videos:
                    if v.id == video_id:
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(self.get_live_player_html(v).encode('utf-8'))
                        return
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        
        if self.path == '/api/extract':
            try:
                data = json.loads(body)
                url = data.get('url', '')
                if url:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'processing', 'url': url}).encode('utf-8'))
                    threading.Thread(target=self._process_extract, args=(url,), daemon=True).start()
                else:
                    self.send_response(400)
                    self.end_headers()
            except:
                self.send_response(400)
                self.end_headers()
        elif self.path == '/api/search':
            try:
                data = json.loads(body)
                query = data.get('query', '')
                if query:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'status': 'searching', 'query': query}).encode('utf-8'))
                    threading.Thread(target=self._process_search, args=(query,), daemon=True).start()
                else:
                    self.send_response(400)
                    self.end_headers()
            except:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def _process_extract(self, url):
        try:
            if hasattr(self.server, 'extractor'):
                self._add_log('info', f'⏳ Extracting: {url}')
                data = self.server.extractor.extract(url)
                if data:
                    self.server.current_data = data
                    self.server.current_data_dict = self._data_to_dict(data)
                    self.server.saved_files = self.server.extractor.saved_files
                    self._add_log('success', f'✅ Extraction complete: {len(self.server.saved_files)} files')
                else:
                    self._add_log('error', '❌ Extraction failed')
        except Exception as e:
            self._add_log('error', f'❌ Extraction error: {e}')
    
    def _process_search(self, query):
        try:
            if hasattr(self.server, 'search_engine'):
                self._add_log('info', f'⏳ Searching: {query}')
                videos = self.server.search_engine.search(query)
                if hasattr(self.server, 'current_data'):
                    self.server.current_data.videos = videos
                else:
                    data = TikTokCompleteData(videos=videos)
                    self.server.current_data = data
                self.server.current_data_dict = self._data_to_dict(self.server.current_data)
                self._add_log('success', f'✅ Search complete: {len(videos)} videos found')
        except Exception as e:
            self._add_log('error', f'❌ Search error: {e}')
    
    def _data_to_dict(self, data):
        if hasattr(data, 'to_dict'):
            return data.to_dict()
        return {
            'url': data.url,
            'final_url': data.final_url,
            'timestamp': data.timestamp,
            'user': asdict(data.user) if data.user else {},
            'live': asdict(data.live) if data.live else {},
            'security': asdict(data.security) if data.security else {},
            'metadata': data.metadata,
            'videos': [v.to_dict() for v in data.videos] if data.videos else []
        }
    
    def _add_log(self, level, message):
        if hasattr(self.server, 'log_queue'):
            self.server.log_queue.put({
                'timestamp': datetime.now().isoformat(),
                'level': level,
                'message': message
            })
        logger.info(f"{level.upper()}: {message}")
    
    def get_index_html(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TikTok Deep Info Extractor v3.0</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0a0e27 0%, #1a1a3e 50%, #0d0d2b 100%);
            min-height: 100vh;
            padding: 20px;
            color: #e0e0e0;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            text-align: center;
            padding: 20px 0;
            border-bottom: 2px solid #00d4ff33;
            margin-bottom: 20px;
        }
        header h1 {
            font-size: 2em;
            background: linear-gradient(90deg, #00d4ff, #7b2ffc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        header p { color: #8892b0; }
        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        @media (max-width: 900px) { .main-grid { grid-template-columns: 1fr; } }
        .panel {
            background: rgba(0, 0, 0, 0.4);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid #00d4ff22;
        }
        .panel h2 { color: #00d4ff; font-size: 1.2em; margin-bottom: 15px; }
        .input-group {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .input-group input {
            flex: 1;
            padding: 12px 16px;
            background: rgba(255,255,255,0.05);
            border: 1px solid #00d4ff44;
            border-radius: 10px;
            color: #e0e0e0;
            font-size: 1em;
            min-width: 200px;
        }
        .input-group input:focus {
            outline: none;
            border-color: #00d4ff;
            box-shadow: 0 0 20px rgba(0,212,255,0.1);
        }
        .input-group input::placeholder { color: #555; }
        button {
            padding: 12px 24px;
            border: none;
            border-radius: 10px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            color: white;
        }
        button:hover { transform: translateY(-2px); }
        .btn-extract { background: linear-gradient(135deg, #00d4ff, #7b2ffc); }
        .btn-search { background: rgba(0,184,148,0.3); color: #00b894; }
        .quick-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 10px;
        }
        .quick-actions button {
            padding: 8px 16px;
            font-size: 0.85em;
            background: rgba(255,255,255,0.05);
        }
        .status-box {
            margin-top: 15px;
            padding: 12px 16px;
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .status-item {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .status-item .label { color: #8892b0; }
        .console-output {
            height: 400px;
            overflow-y: auto;
            background: rgba(0,0,0,0.3);
            border-radius: 10px;
            padding: 12px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 0.85em;
        }
        .console-output::-webkit-scrollbar { width: 6px; }
        .console-output::-webkit-scrollbar-thumb { background: #00d4ff44; border-radius: 3px; }
        .log-entry { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .log-info { color: #00d4ff; }
        .log-error { color: #ff6b6b; }
        .log-warning { color: #fdcb6e; }
        .log-success { color: #00b894; }
        .console-controls {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 10px;
        }
        .console-controls button {
            padding: 6px 14px;
            font-size: 0.8em;
            background: rgba(255,255,255,0.05);
        }
        .results-content { max-height: 600px; overflow-y: auto; }
        .result-section {
            background: rgba(255,255,255,0.03);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .result-section h3 { color: #7b2ffc; margin-bottom: 10px; }
        .result-section p { padding: 2px 0; }
        .result-section code {
            background: rgba(0,0,0,0.4);
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.85em;
            word-break: break-all;
        }
        .video-item {
            padding: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        .video-item:last-child { border-bottom: none; }
        .video-item button {
            padding: 6px 14px;
            font-size: 0.8em;
            background: rgba(0,212,255,0.2);
            border: none;
            border-radius: 6px;
            color: #00d4ff;
            cursor: pointer;
        }
        .video-item button:hover { background: rgba(0,212,255,0.3); }
        .results-panel, .video-panel { margin-top: 20px; display: none; }
        .video-container { text-align: center; }
        .video-container video { max-width: 100%; border-radius: 12px; }
        .version { color: #555; font-size: 0.8em; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎬 TikTok Deep Info Extractor <span class="version">v3.0</span></h1>
            <p>Real-time extraction & monitoring with DNS fallback</p>
        </header>
        
        <div class="main-grid">
            <div class="panel input-panel">
                <h2>🔗 Input</h2>
                <div class="input-group">
                    <input type="text" id="urlInput" placeholder="Enter TikTok URL or search query..." value="https://www.tiktok.com/@username/video/123456789">
                    <button class="btn-extract" onclick="extract()">🚀 Extract</button>
                    <button class="btn-search" onclick="search()">🔍 Search</button>
                </div>
                <div class="quick-actions">
                    <button onclick="clearAll()">🗑️ Clear</button>
                    <button onclick="downloadAll()">📦 Download All</button>
                    <button onclick="refreshStatus()">🔄 Refresh</button>
                </div>
                <div class="status-box">
                    <div class="status-item"><span class="label">Status:</span> <span id="statusText">Ready</span></div>
                    <div class="status-item"><span class="label">Files:</span> <span id="fileCount">0</span></div>
                    <div class="status-item"><span class="label">Videos:</span> <span id="videoCount">0</span></div>
                </div>
            </div>
            
            <div class="panel console-panel">
                <h2>📡 Live Console</h2>
                <div class="console-controls">
                    <button onclick="clearConsole()">Clear</button>
                    <button onclick="exportConsole()">📥 Export JSON</button>
                    <button onclick="toggleAutoScroll()" id="scrollBtn">Auto Scroll</button>
                </div>
                <div id="consoleOutput" class="console-output"></div>
            </div>
        </div>
        
        <div class="panel results-panel" id="resultsPanel">
            <h2>📊 Results</h2>
            <div id="resultsContent" class="results-content"></div>
        </div>
        
        <div class="panel video-panel" id="videoPanel">
            <h2>📺 Live Stream Player</h2>
            <div id="videoContainer" class="video-container"></div>
        </div>
    </div>
    
    <script>
        let consoleLogs = [];
        let autoScrollEnabled = true;
        let currentData = null;
        let pollInterval = null;
        
        function extract() {
            const url = document.getElementById('urlInput').value.trim();
            if (!url) {
                alert('Please enter a TikTok URL');
                return;
            }
            
            fetch('/api/extract', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url})
            })
            .then(response => response.json())
            .then(data => {
                addConsoleLog('info', 'Extraction started: ' + url);
            })
            .catch(err => {
                addConsoleLog('error', 'Extraction error: ' + err.message);
            });
            
            setTimeout(pollStatus, 1000);
        }
        
        function search() {
            const query = document.getElementById('urlInput').value.trim();
            if (!query || query.length < 2) {
                alert('Please enter a search query (min 2 characters)');
                return;
            }
            
            fetch('/api/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query: query})
            })
            .then(response => response.json())
            .then(data => {
                addConsoleLog('info', 'Search started: ' + query);
            })
            .catch(err => {
                addConsoleLog('error', 'Search error: ' + err.message);
            });
            
            setTimeout(pollStatus, 1000);
        }
        
        function pollStatus() {
            fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                updateStatus(data);
                if (data.data) {
                    currentData = data.data;
                    displayResults(data.data);
                }
                if (data.files && data.files.length > 0) {
                    document.getElementById('fileCount').textContent = data.files.length;
                }
            })
            .catch(err => {
                addConsoleLog('error', 'Status poll error: ' + err.message);
            });
            
            fetch('/api/logs')
            .then(response => response.json())
            .then(logs => {
                if (logs.length > consoleLogs.length) {
                    const newLogs = logs.slice(consoleLogs.length);
                    newLogs.forEach(log => {
                        addConsoleLog(log.level, log.message);
                    });
                    consoleLogs = logs;
                }
            })
            .catch(err => {});
        }
        
        function updateStatus(data) {
            document.getElementById('statusText').textContent = data.status || 'Unknown';
            if (data.data && data.data.metadata) {
                document.getElementById('videoCount').textContent = data.data.metadata.videos_count || 0;
            }
        }
        
        function displayResults(data) {
            const container = document.getElementById('resultsContent');
            let html = '';
            
            if (data.user) {
                html += '<div class="result-section"><h3>👤 User</h3>';
                html += `<p><strong>Username:</strong> ${data.user.unique_id || 'N/A'}</p>`;
                html += `<p><strong>Nickname:</strong> ${data.user.nickname || 'N/A'}</p>`;
                html += `<p><strong>ID:</strong> ${data.user.user_id || 'N/A'}</p>`;
                html += `<p><strong>Followers:</strong> ${data.user.follower_count || 0}</p>`;
                html += `<p><strong>Following:</strong> ${data.user.following_count || 0}</p>`;
                html += `<p><strong>Verified:</strong> ${data.user.verified ? '✅ Yes' : '❌ No'}</p>`;
                if (data.user.signature) {
                    html += `<p><strong>Bio:</strong> ${data.user.signature}</p>`;
                }
                html += '</div>';
            }
            
            if (data.live && data.live.room_id) {
                html += '<div class="result-section"><h3>📡 Live Stream</h3>';
                html += `<p><strong>Room ID:</strong> ${data.live.room_id}</p>`;
                html += `<p><strong>Status:</strong> ${data.live.is_live ? '🟢 LIVE' : '🔴 Ended'}</p>`;
                html += `<p><strong>Title:</strong> ${data.live.title || 'N/A'}</p>`;
                html += `<p><strong>Viewers:</strong> ${data.live.viewer_count || 0}</p>`;
                if (data.live.stream_urls && Object.keys(data.live.stream_urls).length > 0) {
                    html += `<p><strong>Streams:</strong> ${Object.keys(data.live.stream_urls).length} available</p>`;
                    html += `<button onclick="openLivePlayer()" style="padding:8px 16px;background:rgba(0,212,255,0.2);border:none;border-radius:8px;color:#00d4ff;cursor:pointer;">▶️ Watch Live</button>`;
                }
                html += '</div>';
            }
            
            if (data.security) {
                html += '<div class="result-section"><h3>🔐 Security</h3>';
                html += `<p><strong>CSRF Token:</strong> <code>${data.security.csrf_token || 'N/A'}</code></p>`;
                html += `<p><strong>Nonce:</strong> <code>${data.security.nonce || 'N/A'}</code></p>`;
                html += `<p><strong>WID:</strong> <code>${data.security.wid || 'N/A'}</code></p>`;
                html += `<p><strong>WebID:</strong> <code>${data.security.webid || 'N/A'}</code></p>`;
                html += '</div>';
            }
            
            if (data.metadata) {
                html += '<div class="result-section"><h3>📊 Metadata</h3>';
                html += `<p><strong>HTML Size:</strong> ${data.metadata.html_size || 0} bytes</p>`;
                html += `<p><strong>JSON Files:</strong> ${data.metadata.json_count || 0}</p>`;
                html += `<p><strong>CSS Files:</strong> ${data.metadata.css_count || 0}</p>`;
                html += `<p><strong>JS Files:</strong> ${data.metadata.js_count || 0}</p>`;
                html += `<p><strong>Images:</strong> ${data.metadata.images_count || 0}</p>`;
                html += `<p><strong>Total IDs:</strong> ${data.metadata.total_ids || 0}</p>`;
                html += '</div>';
            }
            
            if (data.videos && data.videos.length > 0) {
                html += '<div class="result-section"><h3>🎬 Videos</h3>';
                data.videos.forEach((v, i) => {
                    html += `<div class="video-item">`;
                    html += `<p><strong>${i+1}.</strong> <a href="${v.url}" target="_blank" style="color:#00d4ff;">${v.desc || v.id}</a></p>`;
                    html += `<p>👤 ${v.author || 'Unknown'} | ❤️ ${v.stats_likes || 0} | ▶️ ${v.stats_plays || 0}</p>`;
                    if (v.play_url) {
                        html += `<button onclick="downloadVideo('${v.id}')">⬇️ Download MP4</button>`;
                    }
                    html += `</div>`;
                });
                html += '</div>';
            }
            
            container.innerHTML = html;
            document.getElementById('resultsPanel').style.display = 'block';
        }
        
        function openLivePlayer() {
            const panel = document.getElementById('videoPanel');
            panel.style.display = 'block';
            const container = document.getElementById('videoContainer');
            
            if (currentData && currentData.live && currentData.live.stream_urls) {
                let html = '<div class="player-container">';
                const urls = currentData.live.stream_urls;
                let videoUrl = null;
                for (const key in urls) {
                    if (key.includes('flv') || key.includes('m3u8') || key.includes('mp4')) {
                        videoUrl = urls[key];
                        break;
                    }
                }
                
                if (videoUrl) {
                    html += `<video controls autoplay style="width:100%;max-height:600px;border-radius:12px;">`;
                    html += `<source src="${videoUrl}" type="video/mp4">`;
                    html += `Your browser does not support video.`;
                    html += `</video>`;
                    html += `<p style="margin-top:10px;"><strong>Stream URL:</strong> <code style="background:rgba(0,0,0,0.4);padding:2px 8px;border-radius:4px;font-size:0.85em;word-break:break-all;">${videoUrl}</code></p>`;
                } else {
                    html += '<p>No playable stream found</p>';
                }
                html += '</div>';
                container.innerHTML = html;
            } else {
                container.innerHTML = '<p>No live stream data available</p>';
            }
        }
        
        function downloadVideo(videoId) {
            addConsoleLog('info', 'Downloading video: ' + videoId);
            window.open('/download/' + videoId + '.mp4', '_blank');
        }
        
        function addConsoleLog(level, message) {
            const output = document.getElementById('consoleOutput');
            const entry = document.createElement('div');
            const timestamp = new Date().toLocaleTimeString();
            const colors = {'info':'#00d4ff','error':'#ff6b6b','warning':'#fdcb6e','success':'#00b894'};
            entry.className = `log-entry log-${level}`;
            entry.textContent = `[${timestamp}] ${message}`;
            output.appendChild(entry);
            if (autoScrollEnabled) {
                output.scrollTop = output.scrollHeight;
            }
        }
        
        function clearConsole() {
            document.getElementById('consoleOutput').innerHTML = '';
            consoleLogs = [];
        }
        
        function exportConsole() {
            const logs = document.getElementById('consoleOutput').innerHTML;
            const blob = new Blob([logs], {type: 'text/html'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'console_logs.html';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
        
        function toggleAutoScroll() {
            autoScrollEnabled = !autoScrollEnabled;
            document.getElementById('scrollBtn').textContent = autoScrollEnabled ? 'Auto Scroll' : 'Manual Scroll';
        }
        
        function clearAll() {
            document.getElementById('urlInput').value = '';
            document.getElementById('resultsContent').innerHTML = '';
            document.getElementById('resultsPanel').style.display = 'none';
            document.getElementById('videoPanel').style.display = 'none';
            clearConsole();
        }
        
        function downloadAll() {
            fetch('/api/status')
            .then(response => response.json())
            .then(data => {
                if (data.files && data.files.length > 0) {
                    data.files.forEach(file => {
                        const filename = file.split('/').pop();
                        window.open('/download/' + filename, '_blank');
                    });
                }
            });
        }
        
        function refreshStatus() {
            pollStatus();
        }
        
        // Start polling
        pollInterval = setInterval(pollStatus, 5000);
        pollStatus();
        
        addConsoleLog('info', '🚀 TikTok Deep Info Extractor v3.0 started');
        addConsoleLog('info', '💡 Enter a TikTok URL to extract data');
        addConsoleLog('info', '🔍 Enter a search query to find videos');
    </script>
</body>
</html>"""

    def get_css(self):
        """Return CSS for the web interface"""
        return """* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: linear-gradient(135deg, #0a0e27 0%, #1a1a3e 50%, #0d0d2b 100%);
    min-height: 100vh;
    padding: 20px;
    color: #e0e0e0;
}
.container { max-width: 1400px; margin: 0 auto; }
header {
    text-align: center;
    padding: 20px 0;
    border-bottom: 2px solid #00d4ff33;
    margin-bottom: 20px;
}
header h1 {
    font-size: 2em;
    background: linear-gradient(90deg, #00d4ff, #7b2ffc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
header p { color: #8892b0; }
.main-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 20px;
}
@media (max-width: 900px) {
    .main-grid { grid-template-columns: 1fr; }
}
.panel {
    background: rgba(0, 0, 0, 0.4);
    border-radius: 16px;
    padding: 20px;
    border: 1px solid #00d4ff22;
}
.panel h2 {
    color: #00d4ff;
    font-size: 1.2em;
    margin-bottom: 15px;
}
.panel h3 { color: #7b2ffc; margin-bottom: 10px; }
.results-content { max-height: 600px; overflow-y: auto; }
.result-section {
    background: rgba(255,255,255,0.03);
    border-radius: 10px;
    padding: 15px;
    margin-bottom: 15px;
    border: 1px solid rgba(255,255,255,0.05);
}
.result-section p { padding: 2px 0; }
.result-section code {
    background: rgba(0,0,0,0.4);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.85em;
    word-break: break-all;
}
.video-item {
    padding: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}
.video-item:last-child { border-bottom: none; }
.video-item button {
    padding: 6px 14px;
    font-size: 0.8em;
    background: rgba(0,212,255,0.2);
    border: none;
    border-radius: 6px;
    color: #00d4ff;
    cursor: pointer;
}
.video-item button:hover { background: rgba(0,212,255,0.3); }
.console-output::-webkit-scrollbar { width: 6px; }
.console-output::-webkit-scrollbar-thumb { background: #00d4ff44; border-radius: 3px; }
button:hover { transform: translateY(-1px); }
.results-panel, .video-panel { display: none; }
.results-panel.active, .video-panel.active { display: block; }
"""

    def get_live_player_html(self, video: TikTokVideo):
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Live Stream Player</title>
    <style>
        body {{ background: #0a0e27; color: #e0e0e0; font-family: Arial, sans-serif; padding: 20px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ color: #00d4ff; text-align: center; }}
        .player {{ background: #000; border-radius: 12px; overflow: hidden; margin: 20px 0; }}
        video {{ width: 100%; max-height: 70vh; }}
        .info {{ background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; margin: 10px 0; }}
        .info p {{ margin: 5px 0; }}
        .back {{ display: inline-block; padding: 10px 20px; background: rgba(0,212,255,0.2); border-radius: 8px; color: #00d4ff; text-decoration: none; }}
        .back:hover {{ background: rgba(0,212,255,0.3); }}
        .download-btn {{ display: inline-block; padding: 10px 20px; background: rgba(0,184,148,0.2); border-radius: 8px; color: #00b894; text-decoration: none; margin-left: 10px; }}
        .download-btn:hover {{ background: rgba(0,184,148,0.3); }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📺 Live Stream</h1>
        <div class="player">
            <video controls autoplay playsinline>
                <source src="{video.play_url}" type="video/mp4">
                Your browser does not support video.
            </video>
        </div>
        <div class="info">
            <p><strong>Video ID:</strong> {video.id}</p>
            <p><strong>Author:</strong> @{video.author}</p>
            <p><strong>Description:</strong> {video.desc[:100]}{'...' if len(video.desc) > 100 else ''}</p>
            <p><strong>Likes:</strong> {video.stats_likes}</p>
            <p><strong>Views:</strong> {video.stats_plays}</p>
        </div>
        <a href="/" class="back">← Back to Dashboard</a>
        <a href="{video.play_url}" class="download-btn" download="video_{video.id}.mp4">⬇️ Download Video</a>
    </div>
</body>
</html>"""


# =====================================================================
# ========== MAIN SERVER ==========
# =====================================================================

class TikTokServer(HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.proxy_manager = ProxyManager()
        self.search_engine = TikTokSearchEngine(self.proxy_manager)
        self.extractor = TikTokExtractor(self.proxy_manager)
        self.current_data = None
        self.current_data_dict = None
        self.saved_files = []
        self.log_queue = queue.Queue()


def run_server(port: int = 8080):
    server_address = ('0.0.0.0', port)
    httpd = TikTokServer(server_address, TikTokWebHandler)
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   🚀 TikTok Deep Info Extractor v3.0                    ║
║                                                          ║
║   ✅ DNS fallback with hardcoded IPs                    ║
║   ✅ Automatic proxy rotation                           ║
║   ✅ Real-time extraction & monitoring                  ║
║   ✅ All data from JSON files                           ║
║                                                          ║
║   🌐 Server running on: http://localhost:{port}         ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹️ Server stopped")


# =====================================================================
# ========== CLI ==========
# =====================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='TikTok Deep Info Extractor')
    parser.add_argument('--server', '-s', action='store_true', help='Run web server')
    parser.add_argument('--port', '-p', type=int, default=8080, help='Port for web server')
    parser.add_argument('--extract', '-e', type=str, help='Extract data from URL')
    parser.add_argument('--search', '-q', type=str, help='Search videos')
    
    args = parser.parse_args()
    
    if args.server:
        run_server(args.port)
    elif args.extract:
        extractor = TikTokExtractor()
        data = extractor.extract(args.extract)
        if data:
            print(f"\n✅ Extraction complete!")
            print(f"📁 Saved to: {extractor.dir_path}")
            print(f"📄 Files: {len(extractor.saved_files)}")
            if data.user:
                print(f"👤 User: {data.user.nickname} (@{data.user.unique_id})")
            if data.live and data.live.is_live:
                print(f"🟢 LIVE: {data.live.title} ({data.live.viewer_count} viewers)")
            if data.metadata:
                print(f"📊 Videos found: {data.metadata.get('videos_count', 0)}")
    elif args.search:
        engine = TikTokSearchEngine()
        videos = engine.search(args.search)
        print(f"\n🔍 Search results for: {args.search}")
        print(f"📊 Found: {len(videos)} videos")
        for i, v in enumerate(videos[:10]):
            print(f"  {i+1}. @{v.author} | ❤️ {v.stats_likes} | ▶️ {v.stats_plays}")
            print(f"     {v.desc[:80]}{'...' if len(v.desc) > 80 else ''}")
    else:
        run_server(8080)


if __name__ == "__main__":
    main()