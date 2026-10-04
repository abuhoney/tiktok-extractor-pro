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
import time
from datetime import datetime
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
        "version": "5.0.0",
        "demo_mode": False,
        "ytdlp_enabled": True,
        "playwright_enabled": os.environ.get("ENABLE_PLAYWRIGHT", "").lower() == "true",
        "proxy_configured": bool(os.environ.get("TIKTOK_PROXY")),
    })


@app.route("/api/info")
def info():
    """Version info, supported strategies, and full endpoint list."""
    return jsonify({
        "version": "5.0.0",
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
        "v43_processors": [
            "_analyze_engagement_quality",
            "_analyze_gift_economy",
            "_detect_stream_health",
            "_compute_influence_score",
            "_build_audience_profile",
            "_build_temporal_profile",
            "_assess_account_risk",
            "_extract_commerce_data",
            "_aggregate_deep_analytics",
            "_persist_user_data (v4.4)",
            "_upload_user_to_github (v4.5)",
            "_enrich_with_session_values (v5.0 — auto-run integrator)",
        ],
        "endpoints": {
            "GET  /": "Web UI (English)",
            "POST /api/extract": "Extract TikTok URL data",
            "GET  /api/extract?url=...": "GET variant",
            "GET  /api/proxy?url=...": "Media proxy (CORS bypass)",
            "GET  /api/health": "Health check (returns ok=true, version=5.0.0)",
            "GET  /api/info": "Service info (this endpoint)",
            "POST /api/interact": "Execute real TikTok interaction",
            "POST /api/auto-interact": "Auto-extract + interact",
            "POST /api/batch-follow-fans": "Batch follow top fans",
            "GET  /api/accounts": "List preloaded accounts",
            "POST /api/analytics": "v4.3 deep analytics summary",
            "POST /api/influence-score": "Influence + trust score only",
            "GET  /api/temporal-snapshots/<unique_id>": "Snapshot history",
            "POST /api/insights": "Actionable insights only",
            "GET  /api/users": "v4.4 List users (local /tmp)",
            "GET  /api/users/<unique_id>": "Get user full record",
            "GET  /api/users/<unique_id>/stream-history": "User stream timeline",
            "GET  /api/users/<unique_id>/snapshots": "User follower chart data",
            "POST /api/sync-db": "Sync users DB to GitHub",
            "DELETE /api/users/<unique_id>": "Delete user from DB",
            "GET  /api/github/users": "v4.5 List users from GitHub (persistent)",
            "POST /api/session": "v4.5 Save sessionid to GitHub",
            "GET  /api/session?unique_id=<id>": "Read saved sessionid",
            "DELETE /api/session/<unique_id>": "Delete session",
            "GET  /api/session/<unique_id>/status": "Check if session exists",
            "GET  /.well-known/assetlinks.json": "TWA deep-link config",
            "POST /api/session-values": "v5.0 — Generate all 34 session values via live HTML fetch + webmssdk.js via Playwright + xbogus.py fallback + hashlib",
            "POST /api/session-values/download": "v5.0 — Download session_values.json file",
            "POST /api/mssdk-analyze": "v5.0 — 8-phase MSSDK analyzer (static, base64, zip, xor, ACrawler, deobfuscation)",
            "POST /api/mssdk-sign": "v5.0 — Generate REAL X-Bogus / X-Gnarly / X-Mssdk-Info via Playwright + frontierSign()",
        },
        "v5_features": {
            "tiktok_session_integrator": "Generates all 34 session values via full integration (live fetch + Playwright + hashlib + xbogus.py)",
            "mssdk_analyzer": "Python port of strong signature.html — 8-phase analyzer + signature generator",
            "auto_run": "Integrator auto-runs on every successful extraction, saving data/sessions/<unique_id>_session_values.json",
            "playwright_executes_real_webmssdk": True,
        },
        "env": {
            "TIKTOK_PROXY": "configured" if os.environ.get("TIKTOK_PROXY") else "not set",
            "ENABLE_PLAYWRIGHT": os.environ.get("ENABLE_PLAYWRIGHT", "false"),
            "PORT": os.environ.get("PORT", "10000"),
            "USERS_DB_DIR": os.environ.get("USERS_DB_DIR", "/tmp/tiktok_users_db"),
            "GITHUB_SYNC_ENABLED": "true" if os.environ.get("GH_TOKEN") else "no GH_TOKEN — sync disabled",
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
#  v3.8: Interaction API — execute real interactions
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/interact", methods=["POST"])
def api_interact():
    """ينفذ تفاعل حقيقي مع TikTok.

    Body:
    {
        "action": "send_like" | "follow_user" | "unfollow_user" | "like_video" | "send_comment" | "enter_live_room",
        "csrf_token": "uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk",
        "wid": "7658084697712657940",
        "nonce": "6vaarBpVsrfBDm_nl5DbJ",
        "room_id": "7683963746938555152",
        "user_id": "7123696644457743366",
        "sec_uid": "MS4wLjABAAAA...",
        "video_id": "7683963746938555152",
        "unique_id": "live_fest2026",
        "comment_text": "👍",  // for send_comment only
        "count": 1  // for send_like only
    }
    """
    from extractor import execute_interaction

    data = request.get_json(silent=True) or request.form
    action = (data.get("action") or "").strip()

    if not action:
        return jsonify({"success": False, "error": "action is required"}), 400

    valid_actions = ("send_like", "follow_user", "unfollow_user", "like_video",
                     "send_comment", "enter_live_room")
    if action not in valid_actions:
        return jsonify({"success": False,
                        "error": f"invalid action '{action}'. valid: {valid_actions}"}), 400

    logger.info(f"interaction request: action={action}")

    try:
        result = execute_interaction(action, data)
        return jsonify(result)
    except Exception as e:
        logger.exception("interaction crashed")
        return jsonify({"success": False, "error": str(e),
                        "action": action}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v3.9: Auto-Interaction + Batch + Credential Rotation
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/auto-interact", methods=["POST"])
def api_auto_interact():
    """يستخرج بيانات الرابط ثم ينفذ تفاعلات تلقائية على صاحبه.

    Body:
    {
        "url": "https://vt.tiktok.com/...",
        "actions": ["follow_user", "send_like", "like_video"],  // optional
        "account_index": 0  // 0=fw__qg, 1=hadwtamsr4, 2=bentmlok1
    }
    """
    from extractor import auto_interact_with_target

    data = request.get_json(silent=True) or request.form
    url = (data.get("url") or "").strip()
    actions = data.get("actions") or ["follow_user", "send_like", "like_video"]
    account_index = int(data.get("account_index", 0))
    sessionid = (data.get("sessionid") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    logger.info(f"auto-interact: url={url} actions={actions} account={account_index} sessionid={'yes' if sessionid else 'no'}")

    try:
        result = auto_interact_with_target(url, actions, account_index, sessionid)
        return jsonify(result)
    except Exception as e:
        logger.exception("auto-interact crashed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/batch-follow-fans", methods=["POST"])
def api_batch_follow_fans():
    """يتبع جميع المعجبين الأوائل من نتيجة استخراج سابقة.

    Body:
    {
        "extraction_result": { ... },  // نتيجة الاستخراج الكاملة
        "account_index": 0
    }
    """
    from extractor import batch_follow_top_fans

    data = request.get_json(silent=True) or request.form
    extraction_result = data.get("extraction_result", {})
    account_index = int(data.get("account_index", 0))

    if not extraction_result:
        return jsonify({"success": False, "error": "extraction_result is required"}), 400

    try:
        result = batch_follow_top_fans(extraction_result, account_index)
        return jsonify(result)
    except Exception as e:
        logger.exception("batch-follow crashed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/accounts")
def api_accounts():
    """يعرض جميع الحسابات المتاحة للتفاعل (PRELOADED_ACCOUNTS)."""
    from extractor import get_credential_rotation
    return jsonify(get_credential_rotation())


# ────────────────────────────────────────────────────────────────────────────
#  v4.3: Deep Analytics Endpoints — نقاط نهاية التحليل العميق
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/analytics", methods=["POST", "GET"])
def api_analytics():
    """يُرجع تحليلات v4.3 العميقة لأي رابط TikTok.

    يعمل كـ /api/extract لكنه يُرجع فقط قسم deep_analytics + headline metrics،
    مما يجعله مثالياً للوحات المعلومات (dashboards).

    Body / Query:
        url: رابط TikTok

    Returns:
        {
            "success": true,
            "url": "...",
            "deep_analytics": {...},
            "engagement_quality": {...},
            "influence_score": {...},
            "audience_profile": {...},
            "account_risk": {...},
            "stream_health": {...},
            "gift_economy": {...},
            "commerce_data": {...},
            "temporal_profile": {...},
        }
    """
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    logger.info(f"analytics request: {url}")
    try:
        result = extract(url, timeout=60)
    except Exception as e:
        logger.exception("analytics extraction crashed")
        return jsonify({"success": False, "error": str(e), "url": url}), 500

    payload = result.to_dict()
    # نُرجع فقط الأقسام التحليلية (بدون raw_json المُقطّع)
    response = {
        "success": result.success,
        "url": result.url,
        "kind": result.kind,
        "target": {
            "unique_id": result.author.unique_id,
            "nickname": result.author.nickname,
            "sec_uid": (result.author.sec_uid or "")[:60],
            "user_id": result.author.user_id,
            "verified": result.author.verified,
            "follower_count": result.author.follower_count,
        },
        "deep_analytics": payload.get("deep_analytics", {}),
        "engagement_quality": payload.get("engagement_quality", {}),
        "influence_score": payload.get("influence_score", {}),
        "audience_profile": payload.get("audience_profile", {}),
        "account_risk": payload.get("account_risk", {}),
        "stream_health": payload.get("stream_health", {}),
        "gift_economy": payload.get("gift_economy", {}),
        "commerce_data": payload.get("commerce_data", {}),
        "temporal_profile": payload.get("temporal_profile", {}),
    }
    return jsonify(response)


@app.route("/api/influence-score", methods=["POST", "GET"])
def api_influence_score():
    """يحسب درجة التأثير فقط (سريع ومُختصر).

    Returns:
        {
            "success": true,
            "url": "...",
            "influence_score": 78.5,
            "tier": "tier_2_influencer",
            "components": {...},
            "trust_score": 80,
            "trust_label": "high_trust"
        }
    """
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    try:
        result = extract(url, timeout=60)
        influence = (result.influence_score or {})
        risk = (result.account_risk or {})
        return jsonify({
            "success": result.success,
            "url": result.url,
            "target": result.author.unique_id,
            "influence_score": influence.get("total_score"),
            "tier": influence.get("tier"),
            "components": influence.get("components", {}),
            "trust_score": risk.get("trust_score"),
            "trust_label": risk.get("trust_label"),
            "verified": result.author.verified,
            "followers": result.author.follower_count,
        })
    except Exception as e:
        logger.exception("influence-score crashed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/temporal-snapshots/<unique_id>")
def api_temporal_snapshots(unique_id: str):
    """يُرجع سجل اللقطات الزمنية لمستخدم محدد (إن وُجدت).

    ملاحظة: اللقطات تُخزَّن محلياً في /tmp/tiktok_snapshots/ على الخادم.
    في Render free tier، يُعاد تشغيل الخادم دورياً مما يمحو /tmp.
    للإنتاج: استخدم قاعدة بيانات حقيقية.
    """
    import re as _re
    safe_uid = _re.sub(r'[^a-zA-Z0-9_]', '_', unique_id)
    snapshot_file = f"/tmp/tiktok_snapshots/{safe_uid}_snapshots.json"

    if not os.path.exists(snapshot_file):
        return jsonify({
            "success": False,
            "error": "no snapshots found for this user",
            "unique_id": unique_id,
            "hint": "Call /api/extract?url=<user_live_url> first to create a snapshot",
        }), 404

    try:
        with open(snapshot_file, "r", encoding="utf-8") as f:
            history = json.load(f)

        if not isinstance(history, list) or not history:
            return jsonify({"success": False, "error": "empty snapshot history"}), 404

        return jsonify({
            "success": True,
            "unique_id": unique_id,
            "total_snapshots": len(history),
            "latest": history[-1],
            "history": history[-20:],  # آخر 20 لقطة فقط
            "growth_summary": _build_growth_summary(history),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _build_growth_summary(history: list) -> dict:
    """يبني ملخّصاً للنمو من أول لقطة إلى آخرها."""
    if len(history) < 2:
        return {"available": False, "reason": "need at least 2 snapshots"}

    first = history[0]
    last = history[-1]
    fm = first.get("metrics", {})
    lm = last.get("metrics", {})

    def safe_int(v):
        try:
            return int(v) if v else 0
        except Exception:
            return 0

    return {
        "available": True,
        "first_snapshot_at": first.get("snapshot_at"),
        "last_snapshot_at": last.get("snapshot_at"),
        "duration_minutes": round(
            (last.get("snapshot_timestamp", 0) - first.get("snapshot_timestamp", 0)) / 60.0, 2
        ),
        "followers_growth": safe_int(lm.get("followers")) - safe_int(fm.get("followers")),
        "views_growth": safe_int(lm.get("video_views")) - safe_int(fm.get("video_views")),
        "likes_growth": safe_int(lm.get("video_likes")) - safe_int(fm.get("video_likes")),
        "influence_score_change": (
            (last.get("influence_score_at_snapshot") or 0) -
            (first.get("influence_score_at_snapshot") or 0)
        ),
    }


@app.route("/api/insights", methods=["POST", "GET"])
def api_insights():
    """يُرجع التوصيات القابلة للتنفيذ فقط (سريع وملخّص).

    مثالي للتطبيقات التي تحتاج فقط إلى "ماذا أفعل الآن؟".
    """
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(silent=True) or request.form
        url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "error": "url is required"}), 400

    try:
        result = extract(url, timeout=60)
        deep = (result.deep_analytics or {})
        return jsonify({
            "success": result.success,
            "url": result.url,
            "target": result.author.unique_id,
            "headline": deep.get("headline_metrics", {}),
            "insights": deep.get("actionable_insights", []),
            "summary_text": deep.get("summary_text", ""),
            "comparison_vs_benchmark": deep.get("comparison_vs_benchmark", {}),
        })
    except Exception as e:
        logger.exception("insights crashed")
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v4.4: Users Database Endpoints — قاعدة بيانات المستخدمين
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/users")
def api_users():
    """يُرجع قائمة بكل المستخدمين المحفوظين في قاعدة البيانات.

    Query params:
        ?search=<query>  للبحث بالاسم/unique_id
        ?limit=N         (افتراضي 100، أقصى 500)
        ?live_only=true  فقط المستخدمون الذين بثّوا مباشر آخر مرة
    """
    from extractor import list_saved_users

    search = request.args.get("search", "").strip().lower()
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    limit = max(1, min(500, limit))
    live_only = request.args.get("live_only", "").lower() in ("true", "1", "yes")

    data = list_saved_users()
    users = data.get("users", [])

    # فلترة بالبحث
    if search:
        users = [
            u for u in users
            if search in (u.get("unique_id") or "").lower()
            or search in (u.get("nickname") or "").lower()
        ]

    # فلترة بالبث المباشر
    if live_only:
        users = [u for u in users if u.get("latest_is_live")]

    users = users[:limit]

    return jsonify({
        "success": True,
        "total_users": data.get("total_users", 0),
        "returned_count": len(users),
        "db_dir": data.get("db_dir"),
        "users": users,
    })


@app.route("/api/users/<unique_id>")
def api_user_detail(unique_id: str):
    """يُرجع التفاصيل الكاملة لمستخدم محدد: profile + snapshots + stream_history + top_fans."""
    from extractor import get_user_detail
    data = get_user_detail(unique_id)
    if not data.get("success"):
        return jsonify(data), 404
    return jsonify(data)


@app.route("/api/users/<unique_id>/stream-history")
def api_user_stream_history(unique_id: str):
    """يُرجع سجل البثوث لمستخدم محدد فقط (للجدول الزمني)."""
    from extractor import get_user_detail
    data = get_user_detail(unique_id)
    if not data.get("success"):
        return jsonify(data), 404

    streams = data.get("stream_history", [])
    return jsonify({
        "success": True,
        "unique_id": unique_id,
        "total_streams": len(streams),
        "streams": streams,
        "stream_stats": data.get("stream_stats", {}),
    })


@app.route("/api/users/<unique_id>/snapshots")
def api_user_snapshots(unique_id: str):
    """يُرجع لقطات المتابعين والإحصائيات عبر الزمن (لرسم بياني)."""
    from extractor import get_user_detail
    data = get_user_detail(unique_id)
    if not data.get("success"):
        return jsonify(data), 404

    snapshots = data.get("snapshots", [])
    # اختصر البيانات للعرض البياني
    chart_data = []
    for s in snapshots[-100:]:  # آخر 100 لقطة
        chart_data.append({
            "timestamp": s.get("timestamp"),
            "follower_count": s.get("follower_count"),
            "viewer_count": s.get("viewer_count"),
            "view_count": s.get("view_count"),
            "is_live": s.get("is_live"),
            "influence_score": s.get("influence_score"),
        })

    return jsonify({
        "success": True,
        "unique_id": unique_id,
        "total_snapshots": len(snapshots),
        "chart_data": chart_data,
    })


@app.route("/api/sync-db", methods=["POST"])
def api_sync_db():
    """يدفع جميع ملفات المستخدمين المحفوظة إلى GitHub repo (data/users/)."""
    from extractor import sync_users_to_github
    data = request.get_json(silent=True) or {}
    commit_message = data.get("commit_message") or f"sync users DB - {datetime.utcnow().isoformat()}"

    try:
        result = sync_users_to_github(commit_message)
        return jsonify(result)
    except Exception as e:
        logger.exception("sync-db crashed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/users/<unique_id>", methods=["DELETE"])
def api_delete_user(unique_id: str):
    """يحذف مستخدماً من قاعدة البيانات المحلية."""
    from extractor import _get_user_file_path
    path = _get_user_file_path(unique_id)
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "User not found"}), 404
    try:
        os.remove(path)
        return jsonify({"success": True, "deleted": unique_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v4.5: GitHub-backed Users + Session Management
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/github/users")
def api_github_users():
    """يقرأ قائمة المستخدمين مباشرةً من GitHub repo (data/users/).

    هذا البديل لـ /api/users (الذي يقرأ من /tmp المؤقت على Render).
    """
    from extractor import list_github_users
    data = list_github_users()
    if not data.get("success"):
        return jsonify(data), 500
    return jsonify(data)


@app.route("/api/session", methods=["POST", "GET"])
def api_save_session():
    """يحفظ sessionid (وكوكيز إضافية) لمستخدم في GitHub repo.

    POST body:
        {
            "unique_id": "abuhoney77",
            "sessionid": "abcd1234...",
            "extra_cookies": {  // optional
                "ttwid": "...",
                "msToken": "...",
                "sid_tt": "...",
                "passport_csrf_token": "..."
            }
        }

    GET ?unique_id=<id>  لقراءة sessionid المحفوظ
    """
    from extractor import save_sessionid, get_sessionid

    if request.method == "GET":
        unique_id = request.args.get("unique_id", "").strip()
        if not unique_id:
            return jsonify({"success": False, "error": "unique_id is required"}), 400
        data = get_sessionid(unique_id)
        if not data.get("success"):
            return jsonify(data), 404
        return jsonify(data)

    # POST
    data = request.get_json(silent=True) or request.form
    unique_id = (data.get("unique_id") or "").strip()
    sessionid = (data.get("sessionid") or "").strip()
    extra_cookies = data.get("extra_cookies") or {}

    if not unique_id or not sessionid:
        return jsonify({
            "success": False,
            "error": "unique_id and sessionid are required",
        }), 400

    logger.info(f"Saving session for user: {unique_id}")

    try:
        result = save_sessionid(unique_id, sessionid, extra_cookies)
        return jsonify(result)
    except Exception as e:
        logger.exception("save_session crashed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/session/<unique_id>", methods=["DELETE"])
def api_delete_session(unique_id: str):
    """يحذف ملف جلسة مستخدم من GitHub repo."""
    gh_token = os.environ.get("GH_TOKEN")
    if not gh_token:
        return jsonify({"success": False, "error": "GH_TOKEN not set"}), 500

    from extractor import save_sessionid  # noqa — reuse via direct GitHub API
    # GitHub DELETE requires the file's SHA
    import re as _re
    import base64 as _b64
    import requests as _req
    repo_owner = os.environ.get("GITHUB_REPO_OWNER", "abuhoney")
    repo_name = os.environ.get("GITHUB_REPO_NAME", "tiktok-extractor-pro")
    safe_uid = _re.sub(r'[^a-zA-Z0-9_\.\-]', '_', unique_id)
    file_path = f"data/sessions/{safe_uid}.json"
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "tiktok-extractor-pro",
    }
    try:
        check = _req.get(api_url, headers=headers, timeout=15)
        if check.status_code == 404:
            return jsonify({"success": False, "error": "Session not found"}), 404
        if check.status_code != 200:
            return jsonify({"success": False, "error": f"GitHub GET {check.status_code}"}), 500
        sha = check.json().get("sha")
        payload = {"message": f"session: delete {unique_id}", "sha": sha, "branch": "main"}
        delete_resp = _req.delete(api_url, headers=headers, json=payload, timeout=30)
        if delete_resp.status_code == 200:
            return jsonify({"success": True, "deleted": unique_id})
        return jsonify({"success": False, "error": f"GitHub DELETE {delete_resp.status_code}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/session/<unique_id>/status")
def api_session_status(unique_id: str):
    """يفحص ما إذا كان هناك sessionid محفوظ للمستخدم."""
    from extractor import get_sessionid
    data = get_sessionid(unique_id)
    return jsonify({
        "unique_id": unique_id,
        "has_session": data.get("success", False),
        "saved_at": data.get("saved_at") if data.get("success") else None,
        "sessionid_preview": (data.get("sessionid", "")[:20] + "...") if data.get("success") else None,
    })


# ════════════════════════════════════════════════════════════════════════════
#  v5.2: ALL NEW ROUTES — fixes all 404 and 500 errors
# ════════════════════════════════════════════════════════════════════════════

# ── Deep Data ──
@app.route("/api/deep/extract", methods=["POST", "GET"])
def api_deep_extract():
    try:
        from onlinetiktok import deep_extract
        if request.method == "GET": url = request.args.get("url", "").strip(); html = None
        else: d = request.get_json(silent=True) or request.form; url = (d.get("url") or "").strip(); html = d.get("html")
        if not url: return jsonify({"success": False, "error": "url is required"}), 400
        result = deep_extract(url, html=html)
        return jsonify(result), 200 if result.get("success") else 500
    except Exception as e:
        logger.exception("deep_extract crashed"); return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/deep/users")
def api_deep_users():
    try:
        from onlinetiktok import list_deep_users
        return jsonify(list_deep_users())
    except Exception as e:
        return jsonify({"success": True, "total_users": 0, "users": [], "error": str(e)})

@app.route("/api/deep/users/<unique_id>/extractions")
def api_deep_user_extractions(unique_id):
    try:
        from onlinetiktok import list_user_extractions
        d = list_user_extractions(unique_id)
        return jsonify(d) if d.get("success") else (jsonify(d), 404)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/deep/users/<unique_id>/live<int:live_number>")
def api_deep_extraction_detail(unique_id, live_number):
    try:
        from onlinetiktok import get_extraction_files
        d = get_extraction_files(unique_id, live_number)
        return jsonify(d) if d.get("success") else (jsonify(d), 404)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/deep/users/<unique_id>/live<int:live_number>/download/<path:filename>")
def api_deep_download_file(unique_id, live_number, filename):
    try:
        from onlinetiktok import download_file
        result = download_file(unique_id, live_number, filename)
        if result is None: return jsonify({"success": False, "error": "File not found"}), 404
        fname, content = result
        return Response(content, mimetype="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── Reactor ──
@app.route("/api/react/accounts")
def api_react_accounts():
    try:
        from reactor import list_reactor_accounts
        return jsonify(list_reactor_accounts())
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "total_accounts": 0, "accounts": []})

@app.route("/api/react/execute", methods=["POST"])
def api_react_execute():
    try:
        from reactor import execute_reaction
        d = request.get_json(silent=True) or request.form
        action = (d.get("action") or "").strip()
        if not action: return jsonify({"success": False, "error": "action is required"}), 400
        return jsonify(execute_reaction(action, d))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/react/stats")
def api_react_stats():
    try:
        from reactor import get_reactor_stats
        return jsonify(get_reactor_stats(request.args.get("account")))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ── Monitor ──
@app.route("/api/monitor/live")
def api_monitor_live():
    try:
        from monitor import monitor_live
        room_id = request.args.get("room_id", "").strip()
        url = request.args.get("url", "").strip()
        if not room_id and not url: return jsonify({"success": False, "error": "room_id or url required"}), 400
        return jsonify(monitor_live(room_id=room_id or None, url=url or None))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/monitor/live/all")
def api_monitor_live_all():
    try:
        from monitor import monitor_all_live
        return jsonify(monitor_all_live())
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "total_monitors": 0, "monitors": []})

@app.route("/api/monitor/sessions")
def api_monitor_sessions():
    try:
        from monitor import monitor_sessions
        return jsonify(monitor_sessions())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/monitor/deep")
def api_monitor_deep():
    try:
        from monitor import monitor_deep_data
        return jsonify(monitor_deep_data())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/monitor/deep/<unique_id>/live<int:live_number>")
def api_monitor_deep_single(unique_id, live_number):
    try:
        from monitor import monitor_deep_single
        return jsonify(monitor_deep_single(unique_id, live_number))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/monitor/gifts", methods=["POST"])
def api_monitor_gifts():
    try:
        from monitor import monitor_gift_boxes
        d = request.get_json(silent=True) or request.form
        return jsonify(monitor_gift_boxes(d.get("html", ""), d.get("webcast_data", {})))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/monitor/report")
def api_monitor_report():
    try:
        from monitor import full_monitor_report
        return jsonify(full_monitor_report())
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "version": "v5.2_monitor"})

# ── Stats ──
@app.route("/api/stats")
def api_stats():
    try:
        from statictor import full_stats
        return jsonify(full_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/headline")
def api_stats_headline():
    try:
        from statictor import headline_stats
        return jsonify(headline_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/users")
def api_stats_users():
    try:
        from statictor import users_db_stats
        return jsonify(users_db_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/streams")
def api_stats_streams():
    try:
        from statictor import streams_stats
        return jsonify(streams_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/deep")
def api_stats_deep():
    try:
        from statictor import deep_data_stats
        return jsonify(deep_data_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/fans")
def api_stats_fans():
    try:
        from statictor import fans_stats
        return jsonify(fans_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/stats/sessions")
def api_stats_sessions():
    try:
        from statictor import sessions_stats
        return jsonify(sessions_stats())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ── Session Capture ──
@app.route("/api/session/capture", methods=["POST"])
def api_capture_session():
    try:
        from extractor import save_sessionid
        d = request.get_json(silent=True) or request.form
        sessionid = (d.get("sessionid") or "").strip()
        if not sessionid: return jsonify({"success": False, "error": "sessionid is required"}), 400
        extra_cookies = d.get("extra_cookies") or {}
        unique_id = "me"
        save_result = save_sessionid(unique_id, sessionid, extra_cookies)
        if not save_result.get("success"):
            import re as _re
            local_dir = "/tmp/tiktok_sessions_local"
            os.makedirs(local_dir, exist_ok=True)
            safe_uid = _re.sub(r'[^a-zA-Z0-9_\.\-]', '_', unique_id)
            local_path = os.path.join(local_dir, f"{safe_uid}.json")
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump({"unique_id": unique_id, "saved_at": datetime.utcnow().isoformat()+"Z", "sessionid": sessionid, "extra_cookies": extra_cookies}, f, ensure_ascii=False, indent=2, default=str)
            return jsonify({"success": True, "unique_id": unique_id, "saved_to": "local", "download_url": f"/api/session/local/{safe_uid}/download"})
        save_result["unique_id"] = unique_id
        return jsonify(save_result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/session/local")
def api_list_local_sessions():
    local_dir = "/tmp/tiktok_sessions_local"
    if not os.path.exists(local_dir):
        return jsonify({"success": True, "total": 0, "sessions": []})
    sessions = []
    for fname in sorted(os.listdir(local_dir)):
        if not fname.endswith(".json"): continue
        try:
            with open(os.path.join(local_dir, fname), "r", encoding="utf-8") as f:
                record = json.load(f)
            sessions.append({"unique_id": record.get("unique_id"), "saved_at": record.get("saved_at"), "filename": fname})
        except: continue
    return jsonify({"success": True, "total": len(sessions), "sessions": sessions})

@app.route("/api/session/local/<unique_id>/download")
def api_download_local_session(unique_id):
    import re as _re
    local_dir = "/tmp/tiktok_sessions_local"
    safe_uid = _re.sub(r'[^a-zA-Z0-9_\.\-]', '_', unique_id)
    path = os.path.join(local_dir, f"{safe_uid}.json")
    if not os.path.exists(path): return jsonify({"success": False, "error": "Not found"}), 404
    with open(path, "r", encoding="utf-8") as f: content = f.read()
    return Response(content, mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="session_{unique_id}.json"'})

# ── Session Logs ──
@app.route("/api/session/log", methods=["POST"])
def api_session_log():
    d = request.get_json(silent=True) or request.form
    entry = {"timestamp": datetime.utcnow().isoformat()+"Z", "action": d.get("action",""), "url": d.get("url",""), "result": d.get("result",""), "success": d.get("success",False)}
    log_dir = "/tmp/tiktok_session_logs"; os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"session_log_{datetime.utcnow().strftime('%Y%m%d')}.json")
    history = []
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f: history = json.load(f)
            if not isinstance(history, list): history = []
        except: history = []
    history.append(entry)
    if len(history) > 1000: history = history[-1000:]
    with open(log_file, "w", encoding="utf-8") as f: json.dump(history, f, ensure_ascii=False, indent=2, default=str)
    return jsonify({"success": True, "total_logs": len(history)})

@app.route("/api/session/logs")
def api_session_logs():
    log_dir = "/tmp/tiktok_session_logs"
    if not os.path.exists(log_dir): return jsonify({"success": True, "total_logs": 0, "logs": []})
    all_logs = []
    for fname in sorted(os.listdir(log_dir)):
        if not fname.endswith(".json"): continue
        try:
            with open(os.path.join(log_dir, fname), "r", encoding="utf-8") as f: logs = json.load(f)
            if isinstance(logs, list): all_logs.extend(logs)
        except: pass
    return jsonify({"success": True, "total_logs": len(all_logs), "logs": all_logs[-100:]})

@app.route("/api/session/logs/download")
def api_session_logs_download():
    log_dir = "/tmp/tiktok_session_logs"
    all_logs = []
    if os.path.exists(log_dir):
        for fname in sorted(os.listdir(log_dir)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(log_dir, fname), "r", encoding="utf-8") as f: logs = json.load(f)
                    if isinstance(logs, list): all_logs.extend(logs)
                except: pass
    content = json.dumps(all_logs, ensure_ascii=False, indent=2, default=str)
    return Response(content, mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="session_logs.json"'})

# ── Export ZIP ──
@app.route("/api/export/<export_type>")
def api_export_zip(export_type):
    import io as _io, zipfile as _zipf
    buf = _io.BytesIO(); zf = _zipf.ZipFile(buf, 'w', _zipf.ZIP_DEFLATED)
    data_root = os.environ.get("DATA_ROOT", "data")
    def _add_json(name, data): zf.writestr(f"{name}.json", json.dumps(data, ensure_ascii=False, indent=2, default=str))
    def _add_dir(prefix, src):
        if not os.path.exists(src): return
        for root, dirs, files in os.walk(src):
            for fname in files:
                fpath = os.path.join(root, fname); arcname = os.path.relpath(fpath, src)
                zf.write(fpath, os.path.join(prefix, arcname))
    if export_type == "stats":
        try:
            from statictor import full_stats; _add_json("full_stats", full_stats())
        except: _add_json("error", {"message": "statictor not available"})
    elif export_type == "deep": _add_dir("tiktok_deep_data", os.path.join(data_root, "tiktok_deep_data"))
    elif export_type == "sessions": _add_dir("sessions_github", os.path.join(data_root, "sessions")); _add_dir("sessions_local", "/tmp/tiktok_sessions_local")
    elif export_type == "users": _add_dir("users", os.path.join(data_root, "users"))
    elif export_type == "all":
        try:
            from statictor import full_stats; _add_json("full_stats", full_stats())
        except: pass
        _add_dir("tiktok_deep_data", os.path.join(data_root, "tiktok_deep_data"))
        _add_dir("users", os.path.join(data_root, "users"))
        _add_dir("sessions_github", os.path.join(data_root, "sessions"))
        _add_dir("sessions_local", "/tmp/tiktok_sessions_local")
        zf.writestr("README.txt", "TikTok Extractor Pro v5.2 — Full Export\n")
    else:
        return jsonify({"success": False, "error": f"Unknown type: {export_type}", "valid_types": ["stats","deep","sessions","users","all"]}), 400
    zf.close(); buf.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return Response(buf.getvalue(), mimetype="application/zip", headers={"Content-Disposition": f'attachment; filename="tiktok_export_{export_type}_{ts}.zip"'})

@app.route("/api/export/deep/<unique_id>/live<int:live_number>")
def api_export_deep_single(unique_id, live_number):
    import io as _io, zipfile as _zipf
    try:
        from onlinetiktok import DeepDataStorage
        storage = DeepDataStorage()
        uid_safe = storage._sanitize_id(unique_id)
        live_dir = os.path.join(storage.base_dir, uid_safe, f"live{live_number}")
        if not os.path.exists(live_dir): return jsonify({"success": False, "error": "Not found"}), 404
        buf = _io.BytesIO(); zf = _zipf.ZipFile(buf, 'w', _zipf.ZIP_DEFLATED)
        for root, dirs, files in os.walk(live_dir):
            for fname in files:
                fpath = os.path.join(root, fname); arcname = os.path.relpath(fpath, live_dir)
                zf.write(fpath, arcname)
        zf.close(); buf.seek(0)
        return Response(buf.getvalue(), mimetype="application/zip", headers={"Content-Disposition": f'attachment; filename="deep_{unique_id}_live{live_number}.zip"'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v5.0: Session Integrator endpoints (34 values + X-Bogus via Playwright)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/session-values", methods=["POST", "GET"])
def api_session_values():
    """Generate all 34 TikTok session values for a given URL.

    Combines:
      1. Live HTML fetch (msToken, verifyFp, csrf_token)
      2. Real webmssdk.js execution via Playwright (X-Bogus)
      3. xbogus.py fallback (Python port of the X-Bogus algorithm)
      4. hashlib (MD5, SHA-256, SHA-512)
      5. CSPRNG (msToken variants, verifyFp, etc.)

    POST params:
      - url: target TikTok URL (required)
      - enable_playwright: "true"/"false" (default: from env)
      - session_constants: optional JSON with custom device_id, region, etc.
    """
    if request.method == "GET":
        url = request.args.get("url", "").strip()
        enable_pw = request.args.get("enable_playwright", "").lower() == "true"
        custom_constants = None
    else:
        try:
            data = request.get_json(force=True, silent=True) or {}
        except Exception:
            data = {}
        url = data.get("url", "").strip()
        ep_val = data.get("enable_playwright")
        if isinstance(ep_val, bool):
            enable_pw = ep_val
        elif isinstance(ep_val, str):
            enable_pw = ep_val.lower() == "true"
        else:
            enable_pw = os.environ.get("ENABLE_PLAYWRIGHT", "").lower() == "true"
        custom_constants = data.get("session_constants")

    if not url:
        return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400

    try:
        from tiktok_session_integrator import generate_session_values, DEFAULT_SESSION_CONSTANTS
        from pathlib import Path as _Path

        session_constants = dict(DEFAULT_SESSION_CONSTANTS)
        if isinstance(custom_constants, dict):
            session_constants.update(custom_constants)

        sessions_dir = _Path(__file__).parent / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        output_path = sessions_dir / f"session_values_{int(time.time())}.json"

        report = generate_session_values(
            target_url=url,
            session_constants=session_constants,
            output_path=output_path,
            cache_dir=sessions_dir,
            enable_playwright=enable_pw,
        )
        return jsonify({
            "success": True,
            "output_path": str(output_path),
            "values": report.get("values", {}),
            "values_with_metadata": report.get("values_with_metadata", {}),
            "live_fetch": report.get("_live_fetch_attempt", {}),
            "playwright_attempt": {
                "tried": report.get("_playwright_attempt", {}).get("tried", False),
                "ok": report.get("_playwright_attempt", {}).get("ok", False),
                "xbogus": report.get("_playwright_attempt", {}).get("xbogus"),
                "byted_acrawler_props": report.get("_playwright_attempt", {}).get("byted_acrawler_props", []),
                "isWebmssdk": report.get("_playwright_attempt", {}).get("isWebmssdk"),
                "error": report.get("_playwright_attempt", {}).get("error"),
            },
        })
    except ImportError as e:
        return jsonify({"success": False, "error": f"tiktok_session_integrator not available: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/session-values/download", methods=["POST", "GET"])
def api_session_values_download():
    """Download the latest session_values.json as a file."""
    if request.method == "GET":
        url = request.args.get("url", "").strip()
    else:
        data = request.get_json(force=True, silent=True) or {}
        url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400

    try:
        from tiktok_session_integrator import generate_session_values, DEFAULT_SESSION_CONSTANTS
        from pathlib import Path as _Path

        sessions_dir = _Path(__file__).parent / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        output_path = sessions_dir / f"session_values_{int(time.time())}.json"

        report = generate_session_values(
            target_url=url,
            output_path=output_path,
            cache_dir=sessions_dir,
            enable_playwright=os.environ.get("ENABLE_PLAYWRIGHT", "").lower() == "true",
        )
        content = json.dumps(report, ensure_ascii=False, indent=2)
        return Response(
            content,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="session_values_{int(time.time())}.json"'}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/mssdk-analyze", methods=["POST", "GET"])
def api_mssdk_analyze():
    """Run the 8-phase MSSDK analyzer on a webmssdk.js file.

    POST params:
      - code: full webmssdk.js source (required if no url)
      - url: URL to fetch webmssdk.js from (alternative to code)
      - do_runtime: "true" to run Phase 3 (Playwright runtime probe)
    """
    if request.method == "GET":
        code = ""
        url = request.args.get("url", "").strip()
        do_runtime = request.args.get("do_runtime", "").lower() == "true"
    else:
        data = request.get_json(force=True, silent=True) or {}
        code = data.get("code", "")
        url = data.get("url", "").strip()
        do_runtime = data.get("do_runtime", False)

    if not code and not url:
        return jsonify({"success": False, "error": "Provide either 'code' or 'url'"}), 400

    try:
        from mssdk_analyzer import analyze_webmssdk

        if not code and url:
            import requests as _r
            r = _r.get(url, timeout=30, verify=False)
            if r.status_code != 200:
                return jsonify({"success": False, "error": f"Failed to fetch: HTTP {r.status_code}"}), 502
            code = r.text

        analysis = analyze_webmssdk(code, do_runtime=do_runtime)
        return jsonify({
            "success": True,
            "summary": analysis.get("_summary", {}),
            "phases": {
                "phase1_static": {
                    "totalVariables": len(analysis.get("_phase1_static", {}).get("variables", {})),
                    "totalFunctions": len(analysis.get("_phase1_static", {}).get("functions", {})),
                    "totalStringLiterals": len(analysis.get("_phase1_static", {}).get("string_literals", [])),
                },
                "phase2_decoded": analysis.get("_phase2_decoded", {}),
                "phase3_runtime": analysis.get("_phase3_runtime", {}),
                "phase4_base64": analysis.get("_phase4_base64", {}).get("stats", {}),
                "phase5_zip": analysis.get("_phase5_zip", {}).get("stats", {}),
                "phase6_xor": analysis.get("_phase6_xor", {}).get("stats", {}),
                "phase7_acrawler": analysis.get("_phase7_acrawler", {}).get("stats", {}),
                "phase8_deobfuscation": analysis.get("_phase8_deobfuscation", {}).get("stats", {}),
            },
            "string_literals_sample": analysis.get("_phase1_static", {}).get("string_literals", [])[:100],
        })
    except ImportError as e:
        return jsonify({"success": False, "error": f"mssdk_analyzer not available: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/mssdk-sign", methods=["POST", "GET"])
def api_mssdk_sign():
    """Generate real X-Bogus / X-Gnarly / X-Mssdk-Info signatures via Playwright.

    POST params:
      - url: request URL to sign (required)
      - method: HTTP method (default: GET)
      - params: request params dict
      - ticket: optional msToken
      - user_mode: optional int (0=normal, 1=incognito)
    """
    if request.method == "GET":
        sign_url = request.args.get("url", "").strip()
        method = request.args.get("method", "GET")
        params_str = request.args.get("params", "{}")
        ticket = request.args.get("ticket", "")
        user_mode = int(request.args.get("user_mode", "0"))
    else:
        data = request.get_json(force=True, silent=True) or {}
        sign_url = data.get("url", "").strip()
        method = data.get("method", "GET")
        params_str = data.get("params", "{}") if isinstance(data.get("params"), str) else json.dumps(data.get("params", {}))
        ticket = data.get("ticket", "")
        user_mode = int(data.get("user_mode", 0))

    if not sign_url:
        return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400

    try:
        params = json.loads(params_str) if params_str else {}
    except Exception:
        params = {}

    try:
        from tiktok_session_integrator import ensure_webmssdk_local, WEBMSSDK_CDN_URL
        from mssdk_analyzer import generate_signatures_via_playwright
        from pathlib import Path as _Path

        sessions_dir = _Path(__file__).parent / "data" / "sessions"
        local_path = ensure_webmssdk_local(sessions_dir)
        if not local_path:
            return jsonify({"success": False, "error": "Failed to download webmssdk.js from CDN"}), 502
        with open(local_path, "r", encoding="utf-8") as f:
            code = f.read()

        sig = generate_signatures_via_playwright(
            code, sign_url, method=method, params=params, ticket=ticket, user_mode=user_mode
        )
        return jsonify({
            "success": sig.get("ok", False),
            "x-bogus": sig.get("x-bogus"),
            "x-gnarly": sig.get("x-gnarly"),
            "x-mssdk-info": sig.get("x-mssdk-info"),
            "x-mssdk-rc": sig.get("x-mssdk-rc"),
            "raw": sig.get("raw"),
            "generatedAt": sig.get("generatedAt"),
            "request": sig.get("request"),
            "error": sig.get("error"),
        })
    except ImportError as e:
        return jsonify({"success": False, "error": f"required modules not available: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v5.0: Local cache + attached file processing endpoints
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/local-cache", methods=["GET"])
def api_local_cache_list():
    """List all locally-cached extractions (offline-downloadable artifacts)."""
    try:
        from local_save_processor import list_local_cache
        unique_id = request.args.get("unique_id", "").strip()
        return jsonify(list_local_cache(unique_id if unique_id else None))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-cache/<unique_id>/download/<path:filename>")
def api_local_cache_download(unique_id, filename):
    """Download a specific cached file for a user."""
    from pathlib import Path as _Path
    try:
        from local_save_processor import get_local_file
        file_path = get_local_file(unique_id, filename)
        if not file_path:
            return jsonify({"success": False, "error": f"File '{filename}' not found for user '{unique_id}'"}), 404
        content = _Path(file_path).read_bytes()
        mime = "application/json" if filename.endswith(".json") else (
            "application/javascript" if filename.endswith(".js") else "application/octet-stream"
        )
        return Response(content, mimetype=mime, headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-cache/<unique_id>/download-zip")
def api_local_cache_download_zip(unique_id):
    """Download a ZIP containing all locally-cached files for a user."""
    try:
        from local_save_processor import build_local_zip
        zip_path = build_local_zip(unique_id)
        if not zip_path.exists():
            return jsonify({"success": False, "error": "ZIP build failed"}), 500
        content = zip_path.read_bytes()
        return Response(content, mimetype="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{unique_id}_local_cache.zip"'
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-cache/<unique_id>/list")
def api_local_cache_list_user(unique_id):
    """List all cached files for a specific user (detailed)."""
    try:
        from local_save_processor import get_user_cache_dir
        user_dir = get_user_cache_dir(unique_id)
        files = []
        for entry in user_dir.iterdir():
            if entry.is_file():
                stat = entry.stat()
                files.append({
                    "name": entry.name,
                    "size_bytes": stat.st_size,
                    "size_human": _human_size(stat.st_size),
                    "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                    "download_url": f"/api/local-cache/{unique_id}/download/{entry.name}",
                })
            elif entry.is_dir():
                for sub in entry.iterdir():
                    if sub.is_file():
                        stat = sub.stat()
                        files.append({
                            "name": f"{entry.name}/{sub.name}",
                            "size_bytes": stat.st_size,
                            "size_human": _human_size(stat.st_size),
                            "modified": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                            "download_url": f"/api/local-cache/{unique_id}/download/{entry.name}/{sub.name}",
                        })
        return jsonify({
            "success": True,
            "unique_id": unique_id,
            "cache_dir": str(user_dir),
            "total_files": len(files),
            "files": files,
            "zip_download_url": f"/api/local-cache/{unique_id}/download-zip",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/process-uploads", methods=["POST"])
def api_process_uploads():
    """Process uploaded files (JSON, images, cookies) with error handling.

    Accepts multipart/form-data with one or more files:
      - .json files: parsed and merged
      - .jpg/.png files: analyzed with VLM (if it's a screenshot)
      - .txt files: tried as cookie strings or JSON

    Returns a unified dict with all extracted data.
    """
    try:
        from attached_file_processor import (
            validate_and_parse_json,
            extract_cookies_from_text,
            merge_multiple_jsons,
            save_uploaded_file,
            analyze_screenshot_with_vlm,
        )
        from pathlib import Path as _Path

        files = request.files.getlist("files")
        if not files:
            return jsonify({"success": False, "error": "No files uploaded"}), 400

        unique_id = request.form.get("unique_id", f"upload_{int(time.time())}")
        upload_paths = []
        images = []
        json_paths = []

        for f in files:
            if not f or not f.filename:
                continue
            try:
                data = f.read()
                saved = save_uploaded_file(data, f.filename, unique_id)
                upload_paths.append(str(saved))
                if f.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    images.append(saved)
                elif f.filename.lower().endswith((".json", ".txt")):
                    json_paths.append(saved)
            except Exception as e:
                return jsonify({"success": False, "error": f"Failed to save {f.filename}: {e}"}), 500

        merged = merge_multiple_jsons(json_paths) if json_paths else {"ids": {}, "cookies": {}}

        image_analyses = []
        for img_path in images:
            analysis = analyze_screenshot_with_vlm(img_path)
            analysis["file"] = str(img_path)
            image_analyses.append(analysis)

        return jsonify({
            "success": True,
            "unique_id": unique_id,
            "uploaded_files": upload_paths,
            "merged_data": merged,
            "image_analyses": image_analyses,
            "summary": {
                "total_files": len(upload_paths),
                "json_files": len(json_paths),
                "image_files": len(images),
                "total_ids_extracted": len(merged.get("ids", {})),
                "total_cookies_extracted": len(merged.get("cookies", {})),
                "total_session_values": len(merged.get("session_values", {})),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _human_size(n):
    """Convert bytes to human-readable size."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ────────────────────────────────────────────────────────────────────────────
#  v8/v9 API endpoints — receive monitoring data from APK
# ────────────────────────────────────────────────────────────────────────────
import sqlite3 as _sqlite3
_V8_CACHE_DB = "/tmp/tiktok_v8_cache.db"


def _v8_init_db():
    """Initialize v8 cache database (matches APK's cache.db schema)."""
    conn = _sqlite3.connect(_V8_CACHE_DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        live_url TEXT,
        room_id TEXT,
        unique_id TEXT,
        streamer_user_id TEXT,
        started_at REAL,
        ended_at REAL,
        duration_s REAL,
        total_polls INTEGER,
        total_events INTEGER,
        total_gifts INTEGER,
        total_mstoken_renewals INTEGER,
        cookies_count INTEGER,
        has_sessionid INTEGER,
        final_viewer_count INTEGER,
        final_like_count INTEGER,
        final_diamond_count INTEGER,
        session_dir TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS session_polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        poll_n INTEGER,
        timestamp REAL,
        elapsed_s REAL,
        http_status INTEGER,
        status_code INTEGER,
        is_live INTEGER,
        viewer_count INTEGER,
        like_count INTEGER,
        diamond_count INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS session_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        event_type TEXT,
        event_timestamp REAL,
        poll_n INTEGER,
        event_data_json TEXT
    )""")
    conn.commit()
    conn.close()


_v8_init_db()


@app.route("/api/v8/session/start", methods=["POST"])
def api_v8_session_start():
    """Receive session start metadata from APK."""
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"success": False, "error": "session_id required"}), 400
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO sessions
            (session_id, live_url, room_id, unique_id, streamer_user_id,
             started_at, cookies_count, has_sessionid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, data.get("live_url", ""), data.get("room_id", ""),
             data.get("unique_id", ""), data.get("streamer_user_id", ""),
             __import__("time").time(),
             data.get("cookies_count", 0),
             1 if data.get("has_sessionid") else 0))
        conn.commit()
        conn.close()
        logger.info("v8 session start: %s", session_id)
        return jsonify({"success": True, "session_id": session_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/v8/poll", methods=["POST"])
def api_v8_poll():
    """Receive a single poll from APK."""
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"success": False, "error": "session_id required"}), 400
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""INSERT INTO session_polls
            (session_id, poll_n, timestamp, elapsed_s, http_status, status_code,
             is_live, viewer_count, like_count, diamond_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, data.get("poll_n", 0), __import__("time").time(),
             data.get("elapsed_s"), data.get("http_status"),
             data.get("status_code"),
             1 if data.get("is_live") else 0,
             data.get("viewer_count", 0), data.get("like_count", 0),
             data.get("diamond_count", 0)))
        c.execute("""UPDATE sessions SET
            total_polls = total_polls + 1,
            final_viewer_count = ?,
            final_like_count = ?,
            final_diamond_count = ?
            WHERE session_id = ?""",
            (data.get("viewer_count", 0), data.get("like_count", 0),
             data.get("diamond_count", 0), session_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/v8/event", methods=["POST"])
def api_v8_event():
    """Receive a single event from APK."""
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"success": False, "error": "session_id required"}), 400
        event_type = data.get("type", "unknown")
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""INSERT INTO session_events
            (session_id, event_type, event_timestamp, poll_n, event_data_json)
            VALUES (?, ?, ?, ?, ?)""",
            (session_id, event_type, __import__("time").time(),
             data.get("poll_n"), json.dumps(data, ensure_ascii=False)))
        # Update session totals
        if event_type == "gift_received":
            c.execute("""UPDATE sessions SET
                total_gifts = total_gifts + 1 WHERE session_id = ?""", (session_id,))
        elif event_type == "mstoken_renewed":
            c.execute("""UPDATE sessions SET
                total_mstoken_renewals = total_mstoken_renewals + 1
                WHERE session_id = ?""", (session_id,))
        c.execute("""UPDATE sessions SET
            total_events = total_events + 1 WHERE session_id = ?""", (session_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/v8/session/end", methods=["POST"])
def api_v8_session_end():
    """Receive final session summary from APK."""
    try:
        data = request.get_json(force=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"success": False, "error": "session_id required"}), 400
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""UPDATE sessions SET
            ended_at = ?, duration_s = ?, total_polls = ?, total_events = ?,
            total_gifts = ?, total_mstoken_renewals = ?
            WHERE session_id = ?""",
            (__import__("time").time(), data.get("duration_s", 0),
             data.get("total_polls", 0), data.get("total_events", 0),
             data.get("total_gifts", 0),
             data.get("total_mstoken_renewals", 0), session_id))
        conn.commit()
        conn.close()
        logger.info("v8 session end: %s", session_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/v8/sessions", methods=["GET"])
def api_v8_sessions_list():
    """List all v8 monitoring sessions."""
    try:
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""SELECT session_id, live_url, room_id, unique_id,
                streamer_user_id, started_at, ended_at, duration_s,
                total_polls, total_events, total_gifts, total_mstoken_renewals,
                cookies_count, has_sessionid,
                final_viewer_count, final_like_count, final_diamond_count
            FROM sessions ORDER BY started_at DESC LIMIT 50""")
        rows = c.fetchall()
        conn.close()
        return jsonify({"sessions": [
            {
                "session_id": r[0], "live_url": r[1], "room_id": r[2],
                "unique_id": r[3], "streamer_user_id": r[4],
                "started_at": r[5], "ended_at": r[6], "duration_s": r[7],
                "total_polls": r[8], "total_events": r[9], "total_gifts": r[10],
                "total_mstoken_renewals": r[11], "cookies_count": r[12],
                "has_sessionid": bool(r[13]),
                "final_viewer_count": r[14], "final_like_count": r[15],
                "final_diamond_count": r[16],
            } for r in rows
        ]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/v8/stats", methods=["GET"])
def api_v8_stats():
    """Aggregate v8 monitoring stats."""
    try:
        conn = _sqlite3.connect(_V8_CACHE_DB)
        c = conn.cursor()
        c.execute("""SELECT COUNT(*), COALESCE(SUM(total_polls), 0),
                COALESCE(SUM(total_events), 0), COALESCE(SUM(total_gifts), 0),
                COALESCE(SUM(total_mstoken_renewals), 0),
                COALESCE(SUM(duration_s), 0)
            FROM sessions""")
        row = c.fetchone()
        conn.close()
        return jsonify({
            "total_sessions": row[0],
            "total_polls": row[1],
            "total_events": row[2],
            "total_gifts": row[3],
            "total_mstoken_renewals": row[4],
            "total_duration_s": round(row[5], 1),
            "cache_db_path": _V8_CACHE_DB,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ────────────────────────────────────────────────────────────────────────────
#  v11 API endpoints — universal URL handling (ANY URL, not just live)
# ────────────────────────────────────────────────────────────────────────────
@app.route("/api/v11/detect", methods=["POST"])
def api_v11_detect():
    """Detect platform + content type from any URL."""
    try:
        data = request.get_json(force=True) or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"success": False, "error": "url required"}), 400

        import re as _re
        url_lower = url.lower()

        # Detect platform
        platform = "unknown"
        platform_patterns = {
            "tiktok": [r'tiktok\.com', r'vt\.tiktok\.com', r'webcast\.tiktok\.com'],
            "youtube": [r'youtube\.com', r'youtu\.be', r'm\.youtube\.com'],
            "instagram": [r'instagram\.com'],
            "twitch": [r'twitch\.tv', r'clips\.twitch\.tv'],
            "facebook": [r'facebook\.com', r'fb\.watch'],
            "twitter_x": [r'twitter\.com', r'x\.com'],
            "kick": [r'kick\.com'],
            "bilibili": [r'bilibili\.com', r'live\.bilibili\.com'],
            "douyin": [r'douyin\.com', r'live\.douyin\.com'],
        }
        for p, patterns in platform_patterns.items():
            for pat in patterns:
                if _re.search(pat, url_lower):
                    platform = p
                    break
            if platform != "unknown":
                break

        # Detect content type
        content_type = "unknown"
        if _re.search(r'/live|live\.bilibili|/spaces/', url_lower):
            content_type = "live"
        elif _re.search(r'/video/|/watch\?v=|/shorts/|/reel/|/reels/|youtu\.be/|/videos/|fb\.watch/|/status/|/clip/', url_lower):
            content_type = "video"
        elif _re.search(r'/p/|/post/|/posts/', url_lower):
            content_type = "post"
        elif _re.search(r'/@[^/]+/?$|/user/|/channel/|/c/|twitch\.tv/[^/]+/?$|kick\.com/[^/]+/?$', url_lower):
            content_type = "profile"

        return jsonify({
            "success": True,
            "url": url,
            "platform": platform,
            "content_type": content_type,
            "supported_platforms": list(platform_patterns.keys()),
            "supported_content_types": ["live", "video", "post", "profile"],
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/v11/handle", methods=["POST"])
def api_v11_handle():
    """Handle any URL — fetch metadata for non-live, return live status for live."""
    try:
        data = request.get_json(force=True) or {}
        url = data.get("url", "")
        cookies = data.get("cookies", "")
        if not url:
            return jsonify({"success": False, "error": "url required"}), 400

        # Detect platform + content type
        import re as _re
        url_lower = url.lower()
        platform = "unknown"
        platform_patterns = {
            "tiktok": [r'tiktok\.com', r'vt\.tiktok\.com'],
            "youtube": [r'youtube\.com', r'youtu\.be'],
            "instagram": [r'instagram\.com'],
            "twitch": [r'twitch\.tv'],
            "facebook": [r'facebook\.com', r'fb\.watch'],
            "twitter_x": [r'twitter\.com', r'x\.com'],
            "kick": [r'kick\.com'],
            "bilibili": [r'bilibili\.com'],
            "douyin": [r'douyin\.com'],
        }
        for p, patterns in platform_patterns.items():
            for pat in patterns:
                if _re.search(pat, url_lower):
                    platform = p
                    break
            if platform != "unknown":
                break

        content_type = "unknown"
        if _re.search(r'/live|live\.bilibili|/spaces/', url_lower):
            content_type = "live"
        elif _re.search(r'/video/|/watch\?v=|/shorts/|/reel/|youtu\.be/|/videos/|fb\.watch/|/status/', url_lower):
            content_type = "video"
        elif _re.search(r'/p/|/post/', url_lower):
            content_type = "post"
        else:
            content_type = "profile"

        # For non-live URLs: try oEmbed
        # v1.0.43: For YouTube, NEVER fetch the full page (causes 429).
        # Use YouTubeURLFixer approach: oEmbed primary, noembed.com fallback.
        metadata = {}
        if content_type != "live":
            try:
                # v1.0.43: YouTube-specific fix — strip ?si= param, use oEmbed with retry
                if platform == "youtube":
                    # Extract video_id (strip ?si= tracking param)
                    import re as _re2
                    vid_match = _re2.search(r'(?:youtu\.be/|v=|shorts/|live/|embed/)([a-zA-Z0-9_-]{11})', url)
                    if vid_match:
                        video_id = vid_match.group(1)
                        # Try YouTube oEmbed (primary)
                        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                        import requests as _requests
                        import urllib3 as _urllib3
                        _urllib3.disable_warnings()
                        r = _requests.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"},
                                          timeout=8, verify=False, allow_redirects=False)
                        metadata["http_status"] = r.status_code
                        if r.status_code == 200 and r.text.startswith("{"):
                            d = r.json()
                            metadata["title"] = d.get("title", "")
                            metadata["streamer_nickname"] = d.get("author_name", "")
                            metadata["source"] = "youtube_oembed"
                        else:
                            # Fallback: noembed.com
                            noembed_url = f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}"
                            r2 = _requests.get(noembed_url, headers={"User-Agent": "Mozilla/5.0"},
                                               timeout=8, verify=False, allow_redirects=False)
                            if r2.status_code == 200 and r2.text.startswith("{"):
                                d2 = r2.json()
                                metadata["title"] = d2.get("title", "")
                                metadata["streamer_nickname"] = d2.get("author_name", "")
                                metadata["source"] = "noembed"
                                metadata["http_status"] = 200
                            else:
                                metadata["thumbnail_url"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                                metadata["source"] = "thumbnail_only"
                    else:
                        metadata["error"] = "Could not extract YouTube video_id"
                else:
                    # Non-YouTube: use platform-specific oEmbed
                    oembed_endpoints = {
                        "tiktok": f"https://www.tiktok.com/oembed?url={url}",
                        "instagram": f"https://api.instagram.com/oembed?url={url}",
                    }
                    oembed_url = oembed_endpoints.get(platform)
                    if oembed_url:
                        import requests as _requests
                        import urllib3 as _urllib3
                        _urllib3.disable_warnings()
                        r = _requests.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"},
                                          timeout=8, verify=False, allow_redirects=False)
                        metadata["http_status"] = r.status_code
                        if r.status_code == 200 and r.text.startswith("{"):
                            d = r.json()
                            metadata["title"] = d.get("title", "")
                            metadata["streamer_nickname"] = d.get("author_name", "")
            except Exception as e:
                metadata["oembed_error"] = str(e)[:200]

        return jsonify({
            "success": True,
            "url": url,
            "platform": platform,
            "content_type": content_type,
            "metadata": metadata,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
