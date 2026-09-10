#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Playwright-based X-Bogus signer using TikTok's real webmssdk.js.

Strategy:
    1. Download webmssdk.js directly from TikTok's CDN
       (the CDN is not geo-blocked, only www.tiktok.com is).
    2. Boot a headless Chromium via Playwright.
    3. Use page.set_content() with a minimal HTML page that
       includes <script src="webmssdk.js"> — this loads the real
       byted_acrawler.signer without navigating to www.tiktok.com.
    4. Call window.byted_acrawler.frontierSign(url) and read back the
       X-Bogus value.
    5. Append X-Bogus to the URL and return.

Reuses a single browser instance across calls — typical sign() latency
is 5-20ms after the initial ~2s warm-up.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# TikTok CDN — NOT geo-blocked. Serves webmssdk.js for the login flow
# but the script is identical to the one used on www.tiktok.com.
WEBMSSDK_URL = (
    "https://sf16-website-login.neutral.ttwstatic.com/obj/"
    "tiktok_web_login_static/webmssdk/1.0.0.417/webmssdk.js"
)

# Minimal HTML that just loads webmssdk.js and waits for it to register
# window.byted_acrawler
_BOOTSTRAP_HTML = """<!doctype html>
<html><head>
  <meta charset="utf-8">
  <script src="%s"></script>
</head>
<body><div id="ready"></div></body>
</html>""" % WEBMSSDK_URL

# JS to inject after webmssdk.js has loaded — wraps frontierSign
_SIGN_JS = """
(url) => {
    return new Promise((resolve) => {
        const trySign = (attempts) => {
            const acrawler = window.byted_acrawler || window.byted || window._0x32d5ce;
            if (!acrawler) {
                if (attempts < 50) {
                    return setTimeout(() => trySign(attempts + 1), 100);
                }
                return resolve({ ok: false, error: 'byted_acrawler not found after 5s',
                                 window_keys: Object.keys(window).filter(k =>
                                   k.toLowerCase().includes('byted') ||
                                   k.toLowerCase().includes('acrawler') ||
                                   k.toLowerCase().includes('frontier')).slice(0, 20) });
            }
            try {
                // frontierSign returns either a string "X-Bogus=..." or an object
                const result = acrawler.frontierSign
                    ? acrawler.frontierSign(url)
                    : (acrawler.sign ? acrawler.sign(url) : null);

                if (!result) {
                    return resolve({ ok: false, error: 'sign returned null',
                                     acrawler_keys: Object.keys(acrawler).slice(0, 20) });
                }

                let xbogus = null;
                if (typeof result === 'string') {
                    if (result.startsWith('X-Bogus=')) {
                        xbogus = result.slice('X-Bogus='.length);
                    } else if (result.includes('X-Bogus=')) {
                        xbogus = result.split('X-Bogus=')[1].split('&')[0];
                    } else {
                        // Treat the whole string as the X-Bogus value
                        xbogus = result;
                    }
                } else if (typeof result === 'object') {
                    xbogus = result['X-Bogus'] || result.X_Bogus ||
                             result.x_bogus || result.xbogus || null;
                }

                if (!xbogus) {
                    return resolve({ ok: false, error: 'no X-Bogus in result',
                                     result_type: typeof result,
                                     result_preview: String(result).slice(0, 200) });
                }

                resolve({ ok: true, xbogus: xbogus, msToken: window.msToken || null });
            } catch (e) {
                resolve({ ok: false, error: 'frontierSign threw: ' + String(e) });
            }
        };
        trySign(0);
    });
}
"""


class PlaywrightSigner:
    """Singleton headless-browser-backed X-Bogus signer."""

    _instance: Optional["PlaywrightSigner"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PlaywrightSigner":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._ready = False
        self._warmup_error: Optional[str] = None

    def _warmup(self, timeout: int = 20) -> bool:
        if self._ready:
            return True
        if self._warmup_error:
            return False

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            self._warmup_error = f"playwright not installed: {e}"
            logger.warning(self._warmup_error)
            return False

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            ctx = self._browser.new_context(
                user_agent=DEFAULT_UA,
                locale="en-US",
                viewport={"width": 1280, "height": 720},
            )
            self._page = ctx.new_page()

            # Block images/fonts/css for speed
            def _block(route, request):
                if request.resource_type in ("image", "font", "stylesheet", "media"):
                    return route.abort()
                return route.continue_()

            self._page.route("**/*", _block)

            # Load a minimal page that just loads webmssdk.js from CDN
            self._page.set_content(_BOOTSTRAP_HTML, wait_until="load")

            # Wait briefly for the script to register byted_acrawler
            for _ in range(int(timeout / 0.2)):
                has = self._page.evaluate(
                    "() => !!(window.byted_acrawler || window.byted || window._0x32d5ce)"
                )
                if has:
                    self._ready = True
                    logger.info("Playwright X-Bogus signer ready (webmssdk.js loaded from CDN)")
                    return True
                time.sleep(0.2)

            self._warmup_error = "webmssdk.js didn't register byted_acrawler within timeout"
            logger.warning(self._warmup_error)
            return False

        except Exception as e:
            self._warmup_error = f"warmup crashed: {e}"
            logger.exception(self._warmup_error)
            return False

    def sign(self, url: str, user_agent: str = DEFAULT_UA) -> Tuple[str, Dict[str, str]]:
        if not self._warmup(timeout=15):
            logger.warning(f"signer unavailable ({self._warmup_error}); returning unsigned URL")
            return url, {"User-Agent": user_agent}

        try:
            result = self._page.evaluate(_SIGN_JS, url)

            if not result.get("ok"):
                logger.warning(f"frontierSign failed: {result}")
                return url, {"User-Agent": user_agent}

            xbogus = result["xbogus"]
            sep = "&" if "?" in url else "?"
            signed_url = f"{url}{sep}X-Bogus={xbogus}"
            headers = {"User-Agent": user_agent, "X-Bogus": xbogus}
            logger.info(f"X-Bogus signed OK: {xbogus[:24]}... (len={len(xbogus)})")
            return signed_url, headers

        except Exception as e:
            logger.exception(f"sign() crashed: {e}")
            return url, {"User-Agent": user_agent}

    def close(self) -> None:
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._ready = False
            self._pw = None
            self._browser = None
            self._page = None


def sign_with_playwright(url: str, user_agent: str = DEFAULT_UA) -> Tuple[str, Dict[str, str]]:
    """Module-level convenience wrapper."""
    return PlaywrightSigner.get_instance().sign(url, user_agent=user_agent)


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else (
        "https://webcast.tiktok.com/webcast/room/page/info/"
        "?unique_id=humixc&device_platform=web&aid=1988&channel=channel_unknown"
    )

    print(f"Signing: {target}\n")
    t0 = time.time()
    signed, headers = sign_with_playwright(target)
    elapsed = time.time() - t0
    print(f"Signed in {elapsed:.2f}s")
    print(f"X-Bogus: {headers.get('X-Bogus', '(missing)')}")
    print(f"Signed URL: {signed}\n")

    r = requests.get(signed, headers=headers, verify=False, timeout=15)
    print(f"HTTP {r.status_code}")
    print(f"Body: {r.text[:600]}")
