#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok Universal Extractor — Flask Server v3.0 (REAL MODE, NO DEMO)
====================================================================

خادم ويب احترافي يعرض محرك الاستخراج عبر API + واجهة ويب.

v3.0 تغييرات:
  - تم تعطيل الوضع التجريبي بالكامل — الاستخراج الآن حقيقي فقط
  - yt-dlp كاستراتيجية أساسية (تعمل من أي IP)
  - عودة أخطاء حقيقية عند فشل كل الاستراتيجيات
  - لا مزيد من البيانات المزيفة

المسارات:
  GET  /            -> الصفحة الرئيسية (UI)
  POST /api/extract -> استخراج بيانات رابط TikTok
  GET  /api/proxy?url=<media_url> -> وكيل تحميل الوسائط (لتجاوز CORS)
  GET  /api/health  -> فحص الصحة
  GET  /api/info    -> معلومات الإصدار والاستراتيجيات
  GET  /well-known/assetlinks.json -> Digital Asset Links للـ TWA APK
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any, Dict

import requests
from flask import (Flask, Response, jsonify, render_template, request,
                   stream_with_context)

from extractor import (ExtractionResult, build_session, extract,
                       parse_link, resolve_short_url, USER_AGENTS)

# ────────────────────────────────────────────────────────────────────────────
#  إعداد السجلّات
# ────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tiktok_server")

# ────────────────────────────────────────────────────────────────────────────
#  التطبيق
# ────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64MB للوكيل

# قائمة السماح لمضيفين تحميل الوسائط (للأمان)
ALLOWED_MEDIA_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "tiktokcdn.com", "www.tiktokcdn.com", "tiktokcdn-us.com",
    "tiktokcdn-eu.com", "tiktokv.com", "vt.tiktok.com", "vm.tiktok.com",
    "v19-webcast.tiktokcdn.com", "v16-webcast.tiktokcdn.com",
    "v77.tiktokcdn.com", "p16-sign-sg.tiktokcdn.com",
    "p19-sign-sg.tiktokcdn.com", "p77-sign-va.tiktokcdn.com",
    "p77-sign-sg.tiktokcdn.com", "p16-sign-va.tiktokcdn.com",
    "p19-sign-va.tiktokcdn.com",
    # yt-dlp sometimes redirects to these
    "v16-webcast.tiktokcdn.com", "v19-webcast.tiktokcdn.com",
    "toktrail.com", "musical.ly",
}

# Digital Asset Links fingerprint — populated from env var
# (set in Render dashboard after generating the APK signing key)
ASSET_LINKS_SHA256 = os.environ.get("APK_SIGNING_SHA256", "")


# ────────────────────────────────────────────────────────────────────────────
#  صفحات الواجهة
# ────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "tiktok-extractor",
        "version": "3.0.0",
        "demo_mode": False,
        "ytdlp_enabled": True,
        "playwright_enabled": os.environ.get("ENABLE_PLAYWRIGHT", "").lower() == "true",
        "proxy_configured": bool(os.environ.get("TIKTOK_PROXY")),
    })


@app.route("/api/info")
def info():
    """معلومات الإصدار والاستراتيجيات المدعومة."""
    return jsonify({
        "version": "3.0.0",
        "demo_mode": False,
        "strategies": [
            "yt-dlp (primary — works from any IP)",
            "__UNIVERSAL_DATA_FOR_REHYDRATION__",
            "SIGI_STATE / _SIGI_STATE",
            "Webcast API + X-Bogus (Playwright)",
            "oEmbed API",
            "Meta Tags (og:*, twitter:*)",
            "DOM Fallback",
        ],
        "endpoints": {
            "GET  /": "Web UI",
            "POST /api/extract": "Extract TikTok URL data",
            "GET  /api/extract?url=...": "GET variant",
            "GET  /api/proxy?url=...": "Media proxy (CORS bypass)",
            "GET  /api/health": "Health check",
            "GET  /api/info": "Service info",
            "GET  /.well-known/assetlinks.json": "TWA deep-link config",
        },
        "env": {
            "TIKTOK_PROXY": "configured" if os.environ.get("TIKTOK_PROXY") else "not set",
            "ENABLE_PLAYWRIGHT": os.environ.get("ENABLE_PLAYWRIGHT", "false"),
            "PORT": os.environ.get("PORT", "10000"),
        },
    })


# ────────────────────────────────────────────────────────────────────────────
#  Digital Asset Links (لربط APK بموقع الويب)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links — يتيح لتطبيق Android (TWA) فتح الروابط
    بدون شريط المتصفح العلوي. يجب أن يحتوي على بصمة SHA-256 لمفتاح
    توقيع الـ APK.

    يتم ضبط APK_SIGNING_SHA256 كمتغير بيئي على Render بعد توليد المفتاح.
    """
    if not ASSET_LINKS_SHA256:
        return jsonify([{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.tiktok.extractor",
                "sha256_cert_fingerprints": [
                    "PENDING:SET APK_SIGNING_SHA256 env var on Render after first APK build"
                ]
            }
        }])
    return jsonify([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.tiktok.extractor",
            "sha256_cert_fingerprints": [ASSET_LINKS_SHA256]
        }
    }])


# ────────────────────────────────────────────────────────────────────────────
#  API الاستخراج الرئيسي — حقيقي فقط (no demo fallback)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/extract", methods=["POST", "GET"])
def api_extract():
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()

    if not url:
        return jsonify({
            "success": False,
            "error": "الرجاء إدخال رابط TikTok صالح",
        }), 400

    # تحليل مبدئي للتحقق من الصلاحية
    parsed = parse_link(url)
    if parsed.kind == "unknown" and not parsed.is_short:
        logger.info("link looks unusual: %s", parsed.normalized)

    logger.info("extract request: %s", url)

    try:
        # استخراج حقيقي فقط — بدون وضع تجريبي
        result = extract(url, timeout=60)
    except Exception as e:
        logger.exception("extraction crashed")
        return jsonify({
            "success": False,
            "error": f"خطأ داخلي: {e}",
            "url": url,
        }), 500

    payload = result.to_dict()

    # إذا فشل الاستخراج، نرجع الخطأ كما هو (بدون demo fallback)
    if not result.success:
        logger.warning(f"extraction failed: {result.error}")
        return jsonify(payload), 200  # 200 + success=false ليتمكن الـ frontend من قراءة الخطأ

    # تقليص raw_json حتى لا نرسل ميجابايتات للمتصفح
    if payload.get("raw_json") and isinstance(payload["raw_json"], dict):
        size = len(json.dumps(payload["raw_json"], ensure_ascii=False))
        if size > 200_000:  # 200KB
            payload["raw_json"] = {
                "_truncated": True,
                "_size_bytes": size,
                "_top_keys": list(payload["raw_json"].keys())[:20],
            }

    logger.info(f"✅ extraction succeeded: kind={result.kind} "
                f"author=@{result.author.unique_id} keys={result.raw_keys}")
    return jsonify(payload)


# ────────────────────────────────────────────────────────────────────────────
#  وكيل تحميل الوسائط (لتجاوز CORS وتوفير رابط تنزيل)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/proxy")
def api_proxy():
    media_url = request.args.get("url", "").strip()
    if not media_url:
        return Response("missing url", status=400)

    try:
        from urllib.parse import urlparse
        host = urlparse(media_url).hostname or ""
    except Exception:
        return Response("bad url", status=400)

    if not any(host == h or host.endswith("." + h) for h in ALLOWED_MEDIA_HOSTS):
        if not re.search(r"tiktokcdn|tiktokv|tiktok\.com", host, re.I):
            return Response(f"host not allowed: {host}", status=403)

    download = request.args.get("download") == "1"
    fname = request.args.get("name") or "tiktok_media"

    try:
        session = build_session()
        ua = USER_AGENTS[0]
        upstream = session.get(media_url, stream=True, timeout=30,
                               headers={"User-Agent": ua,
                                        "Referer": "https://www.tiktok.com/"})
        if upstream.status_code >= 400:
            return Response(f"upstream {upstream.status_code}", status=502)

        ctype = upstream.headers.get("Content-Type", "application/octet-stream")
        clen = upstream.headers.get("Content-Length")

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        headers = {"Content-Type": ctype, "Cache-Control": "public, max-age=3600"}
        if clen:
            headers["Content-Length"] = clen
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        else:
            headers["Access-Control-Allow-Origin"] = "*"

        return Response(stream_with_context(generate()),
                        headers=headers, status=200)
    except Exception as e:
        logger.exception("proxy failed")
        return Response(f"proxy error: {e}", status=502)


# ────────────────────────────────────────────────────────────────────────────
#  تشغيل الخادم
# ────────────────────────────────────────────────────────────────────────────
def main():
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info("Starting TikTok Extractor v3.0 on http://%s:%s", host, port)
    logger.info("Demo mode: DISABLED (real extraction only)")
    logger.info("Primary strategy: yt-dlp")
    logger.info("Proxy: %s",
                "configured" if os.environ.get("TIKTOK_PROXY") else "none (direct)")
    logger.info("Playwright: %s",
                "enabled" if os.environ.get("ENABLE_PLAYWRIGHT", "").lower() == "true" else "disabled")

    # استخدم waitress كخادم إنتاجي
    try:
        from waitress import serve as waitress_serve
        logger.info("Using waitress production WSGI server")
        waitress_serve(app, host=host, port=port, threads=8,
                       connection_limit=100, channel_timeout=120)
    except ImportError:
        logger.warning("waitress not installed, falling back to Flask dev server")
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
