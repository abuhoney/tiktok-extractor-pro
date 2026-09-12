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
        "version": "4.4.0",
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
        ],
        "endpoints": {
            "GET  /": "Web UI",
            "POST /api/extract": "Extract TikTok URL data",
            "GET  /api/extract?url=...": "GET variant",
            "GET  /api/proxy?url=...": "Media proxy (CORS bypass)",
            "GET  /api/health": "Health check",
            "GET  /api/info": "Service info",
            "POST /api/interact": "Execute real TikTok interaction",
            "POST /api/auto-interact": "Auto-extract + interact",
            "POST /api/batch-follow-fans": "Batch follow top fans",
            "GET  /api/accounts": "List preloaded accounts",
            "POST /api/analytics": "v4.3 deep analytics summary",
            "POST /api/influence-score": "Influence + trust score only",
            "GET  /api/temporal-snapshots/<unique_id>": "Snapshot history",
            "POST /api/insights": "Actionable insights only",
            "GET  /api/users": "v4.4 List all saved users",
            "GET  /api/users/<unique_id>": "Get user full record",
            "GET  /api/users/<unique_id>/stream-history": "User stream timeline",
            "GET  /api/users/<unique_id>/snapshots": "User follower chart data",
            "POST /api/sync-db": "Sync users DB to GitHub",
            "DELETE /api/users/<unique_id>": "Delete user from DB",
            "GET  /.well-known/assetlinks.json": "TWA deep-link config",
        },
        "env": {
            "TIKTOK_PROXY": "configured" if os.environ.get("TIKTOK_PROXY") else "not set",
            "ENABLE_PLAYWRIGHT": os.environ.get("ENABLE_PLAYWRIGHT", "false"),
            "PORT": os.environ.get("PORT", "10000"),
            "USERS_DB_DIR": os.environ.get("USERS_DB_DIR", "/tmp/tiktok_users_db"),
            "GITHUB_SYNC_ENABLED": "true" if os.environ.get("GH_TOKEN") else "no GH_TOKEN",
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
