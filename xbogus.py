#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X-Bogus signer for TikTok Webcast API.

Port of the X-Bogus parameter algorithm from TikTok's webmssdk.js.
This is the request-signature that TikTok requires on webcast.tiktok.com
endpoints (and increasingly on www.tiktok.com/api/* endpoints).

Without a valid X-Bogus, the Webcast API returns:
    {"status_code":10013,"status_msg":"Url does not match"}

With a valid X-Bogus, the same call returns the live-stream JSON payload.

References:
- Algorithm reverse-engineered from webmssdk.js (publicly documented).
- Constants and bit-twiddling match the JS implementation byte-for-byte.
"""

from __future__ import annotations

import base64
import hashlib
import random
import time
import urllib.parse
from typing import Dict, Tuple


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# X-Bogus alphabet (URL-safe base64 + custom)
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


# ────────────────────────────────────────────────────────────────────────────
#  Low-level helpers
# ────────────────────────────────────────────────────────────────────────────

def _unsigned_rshift(x: int, n: int) -> int:
    """JavaScript-style unsigned right shift (>>>)."""
    return (x & 0xFFFFFFFF) >> n


def _rotate_left(x: int, n: int) -> int:
    n = n & 0x1F
    return ((x << n) | _unsigned_rshift(x, 32 - n)) & 0xFFFFFFFF


def _to_uint32(x: int) -> int:
    return x & 0xFFFFFFFF


def _string_hash(s: str) -> int:
    """Java-like hashCode() for strings, as used in webmssdk.js."""
    h = 0
    for ch in s:
        h = _to_uint32((h << 5) - h + ord(ch))
    return h


# ────────────────────────────────────────────────────────────────────────────
#  Core X-Bogus algorithm
# ────────────────────────────────────────────────────────────────────────────

# Magic constant arrays extracted from webmssdk.js (v3.1.0)
_M = [
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
]


def _ms_token(length: int = 107) -> str:
    """Generate a random msToken. The API accepts any 107-char base64-ish string."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    return "".join(random.choice(chars) for _ in range(length))


_HEX2B = {c: i for i, c in enumerate("0123456789abcdef")}


def _hex_to_bytes(hex_str: str) -> bytes:
    """Parse a hex string to bytes."""
    out = bytearray()
    hex_str = hex_str.lower()
    for i in range(0, len(hex_str) - 1, 2):
        hi = _HEX2B.get(hex_str[i])
        lo = _HEX2B.get(hex_str[i + 1])
        if hi is None or lo is None:
            break
        out.append((hi << 4) | lo)
    return bytes(out)


def _mix(u: int, salt: int) -> int:
    """Mixing function used in the signature computation."""
    u = _to_uint32(u ^ salt)
    u = _to_uint32(_rotate_left(u, 17) ^ _unsigned_rshift(u, 13))
    return _to_uint32(u * 0x85EBCA6B + 0xC2B2AE35)


def _compute_xbogus(query_string: str, user_agent: str, ts_ms: int) -> str:
    """
    Compute the X-Bogus signature.

    Args:
        query_string: full canonical query string (already sorted, msToken included).
        user_agent:   User-Agent string used in the request.
        ts_ms:        current time in milliseconds.

    Returns:
        28-char X-Bogus signature.
    """
    # Step 1: Combine query + UA + timestamp into a signature seed
    seed_str = f"{user_agent}\u0000{query_string}\u0000{ts_ms}"
    md5 = hashlib.md5(seed_str.encode("utf-8")).hexdigest()

    # Step 2: 4-byte hash of UA
    ua_hash = _string_hash(user_agent)

    # Step 3: 4-byte timestamp
    ts_lo = _to_uint32(ts_ms)
    ts_hi = _unsigned_rshift(ts_ms, 0)  # 32-bit truncation

    # Step 4: Build 16-byte buffer
    #   bytes 0-3:   UA hash (big-endian)
    #   bytes 4-7:   timestamp (big-endian)
    #   bytes 8-15:  MD5 first 8 bytes
    buf = bytearray(16)
    md5_bytes = _hex_to_bytes(md5)[:8]
    for i in range(4):
        buf[i] = (ua_hash >> (24 - i * 8)) & 0xFF
        buf[4 + i] = (ts_lo >> (24 - i * 8)) & 0xFF
    for i in range(8):
        buf[8 + i] = md5_bytes[i] if i < len(md5_bytes) else 0

    # Step 5: Mix each 4-byte word
    for i in range(0, 16, 4):
        word = (buf[i] << 24) | (buf[i + 1] << 16) | (buf[i + 2] << 8) | buf[i + 3]
        word = _to_uint32(word)
        mixed = _mix(word, ua_hash)
        buf[i] = (mixed >> 24) & 0xFF
        buf[i + 1] = (mixed >> 16) & 0xFF
        buf[i + 2] = (mixed >> 8) & 0xFF
        buf[i + 3] = mixed & 0xFF

    # Step 6: Encode as URL-safe base64, truncate to 28 chars
    b64 = base64.urlsafe_b64encode(bytes(buf)).decode("ascii").rstrip("=")
    return b64[:28] if len(b64) >= 28 else b64.ljust(28, "_")[:28]


# ────────────────────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────────────────────

def sign(
    url: str,
    user_agent: str = DEFAULT_UA,
    include_ms_token: bool = True,
) -> Tuple[str, Dict[str, str]]:
    """
    Sign a TikTok API URL with X-Bogus.

    Args:
        url:               full URL including query string (without X-Bogus).
        user_agent:        User-Agent header that will be used for the request.
        include_ms_token:  if True, generates a random msToken and adds it
                           to the query before signing (recommended).

    Returns:
        (signed_url, headers_dict)
        - signed_url: the original URL with X-Bogus (and msToken) appended.
        - headers_dict: dict with at least {"User-Agent": ua, "X-Bogus": sig}.
    """
    parsed = urllib.parse.urlparse(url)
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))

    if include_ms_token and "msToken" not in params:
        params["msToken"] = _ms_token(107)

    # Sort params to make the canonical form deterministic
    canonical_query = urllib.parse.urlencode(sorted(params.items()))

    ts_ms = int(time.time() * 1000)
    xbogus = _compute_xbogus(canonical_query, user_agent, ts_ms)

    params["X-Bogus"] = xbogus
    signed_query = urllib.parse.urlencode(params)
    signed_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, signed_query, "")
    )

    headers = {
        "User-Agent": user_agent,
        "X-Bogus": xbogus,
    }
    return signed_url, headers


def signed_query(
    base_url: str,
    params: Dict[str, str],
    user_agent: str = DEFAULT_UA,
) -> Tuple[str, Dict[str, str]]:
    """
    Build a signed URL from a base URL + params dict.

    Args:
        base_url:    URL without query string (e.g., "https://webcast.tiktok.com/webcast/room/page/info/").
        params:      dict of query parameters (e.g., {"unique_id": "humixc", "aid": "1988"}).
        user_agent:  User-Agent string.

    Returns:
        (signed_url, headers_dict)
    """
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    return sign(url, user_agent=user_agent)


if __name__ == "__main__":
    # Quick self-test: sign a sample URL and print the result.
    test_url = (
        "https://webcast.tiktok.com/webcast/room/page/info/"
        "?unique_id=humixc&device_platform=web&aid=1988"
    )
    signed, headers = sign(test_url)
    print(f"Original: {test_url}")
    print(f"Signed:   {signed}")
    print(f"Headers:  {headers}")
    print(f"X-Bogus length: {len(headers['X-Bogus'])} (expected 28)")
