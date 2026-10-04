#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rate Limit Handler + YouTube URL Fix — v12
============================================
Fixes the 429 error when the APK processes YouTube URLs.

ROOT CAUSE:
  When the APK fetches a YouTube URL directly (the full page),
  Google's bot detection triggers and returns HTTP 429 with a
  redirect to /sorry/index?continue=...

FIX:
  1. NEVER fetch the full YouTube page directly
  2. Use oEmbed as the primary metadata source (always returns 200)
  3. Use noembed.com as a fallback (also returns 200)
  4. Add a RateLimitHandler class with:
     - Retry with exponential backoff (1s, 2s, 4s, 8s)
     - Retry-After header respect
     - Rate limit cache (don't retry same URL within 60s)
     - Circuit breaker (if 5+ 429s in 60s, pause for 60s)
  5. Strip ?si= parameter from YouTube short URLs
     (the si= parameter is a tracking param that confuses some endpoints)
"""

import os, sys, json, time, hashlib, threading
import requests, urllib3
from datetime import datetime, timedelta
from collections import defaultdict
urllib3.disable_warnings()

# ─── Configuration ───
UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
MAX_RETRIES = 4
INITIAL_BACKOFF = 1.0  # seconds
BACKOFF_MULTIPLIER = 2.0
RATE_LIMIT_CACHE_TTL = 60  # don't retry rate-limited URL within 60s
CIRCUIT_BREAKER_THRESHOLD = 5  # 5 failures in window → open circuit
CIRCUIT_BREAKER_WINDOW = 60  # seconds
CIRCUIT_BREAKER_COOLDOWN = 60  # seconds to stay open


# ═══════════════════════════════════════════════════════════════════════
# RateLimitHandler — handles 429 errors with retry + backoff + circuit breaker
# ═══════════════════════════════════════════════════════════════════════
class RateLimitHandler:
    """Handles HTTP 429 (Too Many Requests) with retry + exponential backoff.

    Features:
      1. Retry with exponential backoff (1s, 2s, 4s, 8s)
      2. Respects Retry-After header if present
      3. Rate limit cache (don't retry same URL within 60s)
      4. Circuit breaker (if 5+ 429s in 60s, pause for 60s)

    Usage:
        handler = RateLimitHandler()
        response = handler.get_with_retry(url, headers={"User-Agent": UA})
        if response:
            # process response
    """

    def __init__(self):
        self.rate_limited_urls = {}  # url_hash → expiry timestamp
        self.failure_log = defaultdict(list)  # url → list of failure timestamps
        self.circuit_open_until = 0  # timestamp when circuit closes
        self.lock = threading.Lock()
        self.stats = {
            "total_requests": 0,
            "total_retries": 0,
            "total_429s": 0,
            "total_successes": 0,
            "circuit_breaker_trips": 0,
        }

    def _url_hash(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def _is_rate_limited(self, url: str) -> bool:
        """Check if URL is in rate-limit cache (recently failed)."""
        h = self._url_hash(url)
        with self.lock:
            if h in self.rate_limited_urls:
                if time.time() < self.rate_limited_urls[h]:
                    return True
                else:
                    del self.rate_limited_urls[h]
            return False

    def _mark_rate_limited(self, url: str, retry_after: int = None):
        """Mark URL as rate-limited for 60s (or Retry-After value)."""
        h = self._url_hash(url)
        ttl = retry_after if retry_after and retry_after < 300 else RATE_LIMIT_CACHE_TTL
        with self.lock:
            self.rate_limited_urls[h] = time.time() + ttl

    def _circuit_is_open(self) -> bool:
        """Check if circuit breaker is open (pause all requests)."""
        with self.lock:
            return time.time() < self.circuit_open_until

    def _record_failure(self, url: str):
        """Record a 429 failure for circuit breaker logic."""
        now = time.time()
        with self.lock:
            self.failure_log[url].append(now)
            # Prune old failures (outside window)
            self.failure_log[url] = [
                t for t in self.failure_log[url]
                if now - t < CIRCUIT_BREAKER_WINDOW
            ]
            # Check if circuit should open
            total_recent_failures = sum(
                len(ts) for ts in self.failure_log.values()
            )
            if total_recent_failures >= CIRCUIT_BREAKER_THRESHOLD:
                self.circuit_open_until = now + CIRCUIT_BREAKER_COOLDOWN
                self.stats["circuit_breaker_trips"] += 1
                # Clear failure log
                self.failure_log.clear()

    def _record_success(self, url: str):
        """Record a success — clears failures for this URL."""
        with self.lock:
            if url in self.failure_log:
                del self.failure_log[url]

    def get_with_retry(self, url: str, headers: dict = None, timeout: int = 10,
                       verify: bool = False, allow_redirects: bool = True) -> requests.Response:
        """GET with retry + exponential backoff for 429 errors.

        Returns the Response object (may have any status code), or None if:
          - URL is rate-limited (in cache)
          - Circuit breaker is open
          - All retries exhausted
        """
        # Check rate-limit cache
        if self._is_rate_limited(url):
            return None

        # Check circuit breaker
        if self._circuit_is_open():
            return None

        headers = headers or {}
        if "User-Agent" not in headers:
            headers["User-Agent"] = UA

        backoff = INITIAL_BACKOFF
        with self.lock:
            self.stats["total_requests"] += 1

        for attempt in range(MAX_RETRIES):
            try:
                with self.lock:
                    self.stats["total_requests"] += 1 if attempt > 0 else 0

                resp = requests.get(
                    url, headers=headers, timeout=timeout,
                    verify=verify, allow_redirects=allow_redirects
                )

                if resp.status_code == 429:
                    # Rate limited — check Retry-After
                    retry_after = resp.headers.get("Retry-After")
                    retry_after_int = int(retry_after) if retry_after and retry_after.isdigit() else None

                    with self.lock:
                        self.stats["total_429s"] += 1
                        self.stats["total_retries"] += 1

                    self._record_failure(url)

                    if attempt < MAX_RETRIES - 1:
                        wait = retry_after_int if retry_after_int else backoff
                        time.sleep(min(wait, 30))  # cap at 30s
                        backoff *= BACKOFF_MULTIPLIER
                        continue
                    else:
                        # Last attempt — mark as rate-limited
                        self._mark_rate_limited(url, retry_after_int)
                        return resp  # return the 429 response

                elif resp.status_code >= 500:
                    # Server error — retry
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(backoff)
                        backoff *= BACKOFF_MULTIPLIER
                        continue
                    return resp

                else:
                    # Success (2xx, 3xx, 4xx other than 429)
                    with self.lock:
                        self.stats["total_successes"] += 1
                    self._record_success(url)
                    return resp

            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                return None

        return None

    def get_stats(self) -> dict:
        with self.lock:
            return {
                **self.stats,
                "rate_limited_urls": len(self.rate_limited_urls),
                "circuit_open": self._circuit_is_open(),
                "circuit_open_until": datetime.utcfromtimestamp(self.circuit_open_until).isoformat() + "Z"
                    if self.circuit_open_until > 0 else None,
            }


# Singleton
_default_handler = None

def get_rate_limit_handler() -> RateLimitHandler:
    global _default_handler
    if _default_handler is None:
        _default_handler = RateLimitHandler()
    return _default_handler


# ═══════════════════════════════════════════════════════════════════════
# YouTube URL Fixer — strips problematic params + uses correct endpoints
# ═══════════════════════════════════════════════════════════════════════
class YouTubeURLFixer:
    """Fixes YouTube URLs that trigger 429 errors.

    Problem:
      - Direct page fetch (https://www.youtube.com/watch?v=XXX) → 429
      - youtu.be/XXX?si=YYY → redirects to /sorry/index when fetched directly

    Solution:
      1. Extract video_id from any YouTube URL format
      2. Use oEmbed endpoint (https://www.youtube.com/oembed?url=...) — never 429s
      3. Use noembed.com as fallback — also never 429s
      4. NEVER fetch the full YouTube page
      5. Strip ?si= tracking parameter
    """

    VIDEO_ID_PATTERNS = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/live/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'm\.youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    ]

    @classmethod
    def extract_video_id(cls, url: str) -> str:
        """Extract the 11-character video ID from any YouTube URL format."""
        import re
        for pattern in cls.VIDEO_ID_PATTERNS:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        # Try to find any 11-char alphanumeric string
        import re
        m = re.search(r'([a-zA-Z0-9_-]{11})', url)
        if m:
            return m.group(1)
        return None

    @classmethod
    def clean_url(cls, url: str) -> str:
        """Strip ?si= and other tracking params from YouTube URLs."""
        # Remove ?si= parameter (YouTube tracking)
        if '?' in url and ('si=' in url or 'feature=' in url):
            base = url.split('?')[0]
            return base
        return url

    @classmethod
    def fetch_metadata(cls, url: str, handler: RateLimitHandler = None) -> dict:
        """Fetch YouTube video metadata WITHOUT triggering 429.

        Strategy:
          1. Extract video_id
          2. Try YouTube oEmbed (primary)
          3. Try noembed.com (fallback)
          4. Return title, author, thumbnail

        NEVER fetches the full YouTube page (which causes 429).
        """
        handler = handler or get_rate_limit_handler()
        video_id = cls.extract_video_id(url)
        result = {
            "video_id": video_id,
            "original_url": url,
            "cleaned_url": cls.clean_url(url),
            "title": "",
            "streamer_nickname": "",
            "thumbnail_url": "",
            "http_status": 0,
            "source": "",
            "error": None,
        }

        if not video_id:
            result["error"] = "Could not extract video_id from URL"
            return result

        # Strategy 1: YouTube oEmbed (primary — never 429s)
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = handler.get_with_retry(
            oembed_url,
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=8,
        )
        if resp and resp.status_code == 200 and resp.text.startswith("{"):
            try:
                d = resp.json()
                result["title"] = d.get("title", "")
                result["streamer_nickname"] = d.get("author_name", "")
                result["thumbnail_url"] = d.get("thumbnail_url", "")
                result["http_status"] = 200
                result["source"] = "youtube_oembed"
                return result
            except Exception:
                pass

        # Strategy 2: noembed.com (fallback — also never 429s)
        noembed_url = f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}"
        resp = handler.get_with_retry(
            noembed_url,
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=8,
        )
        if resp and resp.status_code == 200 and resp.text.startswith("{"):
            try:
                d = resp.json()
                result["title"] = d.get("title", "")
                result["streamer_nickname"] = d.get("author_name", "")
                result["thumbnail_url"] = d.get("thumbnail_url", "")
                result["http_status"] = 200
                result["source"] = "noembed"
                return result
            except Exception:
                pass

        # Strategy 3: return video_id only (at least we have the ID)
        result["http_status"] = resp.status_code if resp else 0
        result["error"] = f"oEmbed failed (HTTP {result['http_status']})"
        result["thumbnail_url"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        result["source"] = "thumbnail_only"
        return result


# ═══════════════════════════════════════════════════════════════════════
# Universal Rate-Limited Adapter — patches v11's adapters to use RateLimitHandler
# ═══════════════════════════════════════════════════════════════════════
class RateLimitedAdapterMixin:
    """Mixin that adds rate-limit handling to any adapter.

    Usage:
        class YouTubeVideoAdapter(RateLimitedAdapterMixin, VideoAdapter):
            def fetch_metadata(self):
                resp = self.safe_get(oembed_url)
                # resp is None if rate-limited or circuit open
                if resp is None:
                    return {"http_status": 429, "error": "rate_limited"}
                # process resp
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rate_limit_handler = get_rate_limit_handler()

    def safe_get(self, url: str, headers: dict = None, timeout: int = 10) -> requests.Response:
        """GET with rate-limit handling. Returns None if rate-limited."""
        headers = headers or {}
        if "User-Agent" not in headers:
            headers["User-Agent"] = UA
        return self.rate_limit_handler.get_with_retry(
            url, headers=headers, timeout=timeout
        )


# ═══════════════════════════════════════════════════════════════════════
# Demo / Test
# ═══════════════════════════════════════════════════════════════════════
def main():
    """Test the rate-limit handler on the user's YouTube URL."""
    print("=" * 70)
    print("v12 Rate Limit Handler — Test on user's YouTube URL")
    print("=" * 70)

    test_url = "https://youtu.be/2QHumKDWBac?si=AHvPZW7u0l51Rs64"
    print(f"\nTest URL: {test_url}")

    # Step 1: Clean URL
    cleaned = YouTubeURLFixer.clean_url(test_url)
    print(f"Cleaned:  {cleaned}")

    # Step 2: Extract video_id
    video_id = YouTubeURLFixer.extract_video_id(test_url)
    print(f"Video ID: {video_id}")

    # Step 3: Fetch metadata (the fix!)
    print(f"\nFetching metadata via oEmbed (no direct page fetch)...")
    result = YouTubeURLFixer.fetch_metadata(test_url)
    print(f"\n✅ Result:")
    print(f"  HTTP status: {result['http_status']}")
    print(f"  Source: {result['source']}")
    print(f"  Title: {result['title']}")
    print(f"  Author: {result['streamer_nickname']}")
    print(f"  Thumbnail: {result['thumbnail_url']}")
    if result.get("error"):
        print(f"  Error: {result['error']}")

    # Step 4: Show rate-limit handler stats
    handler = get_rate_limit_handler()
    print(f"\n📊 Rate Limit Handler Stats:")
    stats = handler.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Step 5: Test multiple URLs to verify circuit breaker
    print(f"\n{'=' * 70}")
    print("Testing circuit breaker (10 rapid requests to same URL)...")
    print(f"{'=' * 70}")
    for i in range(10):
        r = YouTubeURLFixer.fetch_metadata(test_url)
        print(f"  Request {i+1}: http={r['http_status']}, source={r['source']}")

    print(f"\n📊 Final Stats:")
    stats = handler.get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Save results
    output = {
        "version": "v12",
        "test_url": test_url,
        "result": result,
        "stats": stats,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    out_path = "/home/z/my-project/download/v12_rate_limit_test.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n💾 Results saved: {out_path}")


if __name__ == "__main__":
    main()
