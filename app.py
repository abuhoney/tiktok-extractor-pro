#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok Universal Extractor - Flask Server
==========================================
خادم ويب احترافي يعرض محرك الاستخراج عبر API + واجهة ويب.

المسارات:
  GET  /            -> الصفحة الرئيسية (UI)
  POST /api/extract -> استخراج بيانات رابط TikTok
  GET  /api/proxy?url=<media_url> -> وكيل تحميل الوسائط (لتجاوز CORS)
  GET  /api/health  -> فحص الصحة
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

from extractor import (ExtractionResult, build_session, extract, generate_demo,
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

# قائمة السماح لمضافين تحميل الوسائط (للأمان)
ALLOWED_MEDIA_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "tiktokcdn.com", "www.tiktokcdn.com", "tiktokcdn-us.com",
    "tiktokcdn-eu.com", "tiktokv.com", "vt.tiktok.com", "vm.tiktok.com",
    "v19-webcast.tiktokcdn.com", "v16-webcast.tiktokcdn.com",
    "v77.tiktokcdn.com", "p16-sign-sg.tiktokcdn.com",
    "p19-sign-sg.tiktokcdn.com", "p77-sign-va.tiktokcdn.com",
    "p77-sign-sg.tiktokcdn.com", "p16-sign-va.tiktokcdn.com",
    "p19-sign-va.tiktokcdn.com",
}


# ────────────────────────────────────────────────────────────────────────────
#  صفحات الواجهة
# ────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "tiktok-extractor", "version": "1.0.0"})


# ────────────────────────────────────────────────────────────────────────────
#  API الاستخراج الرئيسي
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/extract", methods=["POST", "GET"])
def api_extract():
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False,
                        "error": "الرجاء إدخال رابط TikTok صالح"}), 400

    # تحليل مبدئي للتحقق من الصلاحية
    parsed = parse_link(url)
    if parsed.kind == "unknown" and not parsed.is_short:
        # نسمح بالمحاولة على أي حال، لكن ننبّه المستخدم
        logger.info("link looks unusual: %s", parsed.normalized)

    logger.info("extract request: %s", url)

    # معرفة إذا كان الطلب يطلب وضع العرض التجريبي صراحةً
    want_demo = False
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()
        want_demo = bool(data.get("demo"))
    else:
        want_demo = request.args.get("demo") == "1"

    try:
        # إجراء الاستخراج الحقيقي فقط إذا لم يُطلب وضع العرض التجريبي صراحةً
        result: ExtractionResult = None
        if not want_demo:
            try:
                result = extract(url, timeout=25)
            except Exception as e_inner:
                logger.warning("real extract raised: %s", e_inner)
                result = None

        # شرط تفعيل الوضع التجريبي:
        #   - طلب صريح (want_demo)  OR
        #   - لا يوجد result       OR
        #   - result.failed        OR
        #   - result.success لكن دون بيانات مفيدة (الخادم محجوب)
        need_demo = (
            want_demo
            or result is None
            or not result.success
            or (
                result.success
                and "demo_mode" not in (result.raw_keys or [])
                and not result.video.play_url
                and (not result.video.cover or "ttwstatic" in (result.video.cover or ""))
                and (result.title in (None, "TikTok", "") or result.title is None)
            )
        )

        if need_demo:
            demo = generate_demo(url)
            if result and result.success and "demo_mode" not in (result.raw_keys or []):
                # دمج أي بيانات حقيقية حصلنا عليها مع العلم
                demo.raw_keys = (result.raw_keys or []) + ["demo_mode"]
                demo.meta_tags["_real_attempt"] = True
                if result.error:
                    demo.meta_tags["_real_error"] = result.error
                if result.final_url:
                    demo.final_url = result.final_url
            elif result and result.error:
                demo.meta_tags["_real_error"] = result.error
            elif result and result.final_url and "about" in result.final_url:
                demo.meta_tags["_real_error"] = "geo-blocked (redirected to " + result.final_url + ")"
            demo.meta_tags["_demo_mode"] = "true"
            result = demo
    except Exception as e:
        logger.exception("extraction crashed")
        # حتى عند الخطأ، نرجع بيانات تجريبية بدلاً من فشل كامل
        try:
            result = generate_demo(url)
            result.meta_tags["_crash_error"] = str(e)
        except Exception:
            return jsonify({"success": False,
                            "error": f"خطأ داخلي: {e}",
                            "url": url}), 500

    payload = result.to_dict()
    # تقليص raw_json حتى لا نرسل ميجابايتات للمتصفح
    if payload.get("raw_json") and isinstance(payload["raw_json"], dict):
        size = len(json.dumps(payload["raw_json"], ensure_ascii=False))
        if size > 200_000:  # 200KB
            payload["raw_json"] = {"_truncated": True,
                                   "_size_bytes": size,
                                   "_top_keys": list(payload["raw_json"].keys())[:20]}

    return jsonify(payload)


# ────────────────────────────────────────────────────────────────────────────
#  وكيل تحميل الوسائط (لتجاوز CORS وتوفير رابط تنزيل)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/proxy")
def api_proxy():
    media_url = request.args.get("url", "").strip()
    if not media_url:
        return Response("missing url", status=400)

    # تحقق من المضيف
    try:
        from urllib.parse import urlparse
        host = urlparse(media_url).hostname or ""
    except Exception:
        return Response("bad url", status=400)

    if not any(host == h or host.endswith("." + h) for h in ALLOWED_MEDIA_HOSTS):
        # للسماح بمضيفات CDN إضافية، نتحقق من نمط tiktokcdn
        if not re.search(r"tiktokcdn|tiktokv|tiktok\.com", host, re.I):
            return Response(f"host not allowed: {host}", status=403)

    download = request.args.get("download") == "1"
    fname = request.args.get("name") or "tiktok_media"

    try:
        session = build_session()
        ua = USER_AGENTS[0]
        upstream = session.get(media_url, stream=True, timeout=30,
                               headers={"User-Agent": ua, "Referer": "https://www.tiktok.com/"})
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
    logger.info("Starting TikTok Extractor on http://%s:%s", host, port)

    # استخدم waitress كخادم إنتاجي (أكثر استقراراً من Werkzeug dev server)
    try:
        from waitress import serve as waitress_serve
        logger.info("Using waitress production WSGI server")
        waitress_serve(app, host=host, port=port, threads=8,
                       connection_limit=100, channel_timeout=60)
    except ImportError:
        logger.warning("waitress not installed, falling back to Flask dev server")
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
