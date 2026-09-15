#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
statictor.py — Statistics Engine v1.0
====================================================================
Aggregates statistics from all extracted data:
1. Users database (data/users/)
2. Sessions (data/sessions/)
3. Deep data (data/tiktok_deep_data/)
4. Streams history
5. Fans
"""

from __future__ import annotations

import os, sys, json, logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("statictor")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from onlinetiktok import DeepDataStorage, TIKTOK_DEEP_DIR
except ImportError:
    DeepDataStorage = None
    TIKTOK_DEEP_DIR = None


def _safe_int(v, default=0):
    try:
        return int(v) if v else default
    except (TypeError, ValueError):
        return default

def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def users_db_stats() -> Dict[str, Any]:
    data_root = os.environ.get("DATA_ROOT", "data")
    users_dir = os.path.join(data_root, "users")
    if not os.path.exists(users_dir):
        return {"available": False, "total_users": 0}
    total_users = total_streams = total_snapshots = total_followers = verified_count = 0
    tiers = {"mega": 0, "macro": 0, "micro": 0, "nano": 0, "regular": 0}
    for fname in os.listdir(users_dir):
        if not fname.endswith(".json"):
            continue
        record = _read_json(os.path.join(users_dir, fname))
        if not isinstance(record, dict):
            continue
        total_users += 1
        total_streams += len(record.get("stream_history", []))
        total_snapshots += len(record.get("snapshots", []))
        profile = record.get("profile", {})
        followers = _safe_int(profile.get("follower_count"))
        total_followers += followers
        if profile.get("verified"):
            verified_count += 1
        if followers >= 1_000_000: tiers["mega"] += 1
        elif followers >= 100_000: tiers["macro"] += 1
        elif followers >= 10_000: tiers["micro"] += 1
        elif followers >= 1000: tiers["nano"] += 1
        else: tiers["regular"] += 1
    return {
        "available": True, "total_users": total_users,
        "total_streams_detected": total_streams, "total_snapshots": total_snapshots,
        "total_followers_aggregate": total_followers, "verified_accounts": verified_count,
        "influencer_distribution": tiers,
        "avg_followers_per_user": round(total_followers / total_users, 0) if total_users else 0,
    }


def sessions_stats() -> Dict[str, Any]:
    data_root = os.environ.get("DATA_ROOT", "data")
    sessions_dir = os.path.join(data_root, "sessions")
    local_dir = "/tmp/tiktok_sessions_local"
    gh = sum(1 for f in os.listdir(sessions_dir) if f.endswith(".json")) if os.path.exists(sessions_dir) else 0
    local = sum(1 for f in os.listdir(local_dir) if f.endswith(".json")) if os.path.exists(local_dir) else 0
    latest = None
    if os.path.exists(sessions_dir):
        for fname in os.listdir(sessions_dir):
            if fname.endswith(".json"):
                r = _read_json(os.path.join(sessions_dir, fname))
                if r and r.get("saved_at") and (not latest or r["saved_at"] > latest):
                    latest = r["saved_at"]
    return {"github_sessions": gh, "local_sessions": local, "total_sessions": gh + local, "latest_session_at": latest}


def deep_data_stats() -> Dict[str, Any]:
    if not DeepDataStorage:
        return {"available": False}
    storage = DeepDataStorage()
    users = storage.list_all_users()
    total_extractions = sum(u.get("live_count", 0) for u in users)
    total_files = total_size = webmssdk_count = 0
    for user in users:
        for ext in storage.list_user_extractions(user["unique_id"]):
            live_dir = ext["path"]
            if not os.path.isdir(live_dir):
                continue
            for root, dirs, files in os.walk(live_dir):
                for fname in files:
                    try:
                        size = os.path.getsize(os.path.join(root, fname))
                        total_files += 1; total_size += size
                        if fname == "webmssdk.js": webmssdk_count += 1
                    except Exception:
                        pass
    return {
        "available": True, "total_users": len(users), "total_extractions": total_extractions,
        "total_files": total_files, "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024*1024), 2),
        "webmssdk_downloaded_count": webmssdk_count,
        "avg_extractions_per_user": round(total_extractions / len(users), 1) if users else 0,
    }


def streams_stats() -> Dict[str, Any]:
    data_root = os.environ.get("DATA_ROOT", "data")
    users_dir = os.path.join(data_root, "users")
    if not os.path.exists(users_dir):
        return {"available": False, "total_streams": 0}
    total_streams = total_peak = total_enter = total_likes = longest = 0
    for fname in os.listdir(users_dir):
        if not fname.endswith(".json"):
            continue
        record = _read_json(os.path.join(users_dir, fname))
        if not isinstance(record, dict):
            continue
        for stream in record.get("stream_history", []):
            total_streams += 1
            total_peak += _safe_int(stream.get("peak_viewer_count"))
            total_enter += _safe_int(stream.get("enter_count"))
            total_likes += _safe_int(stream.get("live_likes"))
            started = _safe_int(stream.get("started_at"))
            last_seen = _safe_int(stream.get("last_seen_epoch"))
            if started and last_seen and last_seen > started:
                dur = (last_seen - started) // 60
                if dur > longest: longest = dur
    return {
        "available": total_streams > 0, "total_streams": total_streams,
        "total_peak_viewers": total_peak, "total_enter_count": total_enter,
        "total_live_likes": total_likes, "longest_stream_minutes": longest,
        "longest_stream_hours": round(longest / 60, 2),
        "avg_viewers_per_stream": round(total_peak / total_streams, 0) if total_streams else 0,
    }


def fans_stats() -> Dict[str, Any]:
    data_root = os.environ.get("DATA_ROOT", "data")
    users_dir = os.path.join(data_root, "users")
    if not os.path.exists(users_dir):
        return {"available": False, "total_fans_seen": 0}
    total_fans = 0
    fans_by_id = {}
    for fname in os.listdir(users_dir):
        if not fname.endswith(".json"):
            continue
        record = _read_json(os.path.join(users_dir, fname))
        if not isinstance(record, dict):
            continue
        for fan in record.get("top_fans_seen", []):
            total_fans += 1
            uid = fan.get("unique_id")
            if uid:
                fans_by_id[uid] = fans_by_id.get(uid, 0) + 1
    return {"available": total_fans > 0, "total_fans_records": total_fans, "unique_fans": len(fans_by_id)}


def full_stats() -> Dict[str, Any]:
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "version": "v5.1_statictor",
        "users_db": users_db_stats(),
        "sessions": sessions_stats(),
        "deep_data": deep_data_stats(),
        "streams": streams_stats(),
        "fans": fans_stats(),
    }

def headline_stats() -> Dict[str, Any]:
    s = full_stats()
    return {
        "generated_at": s["generated_at"],
        "total_users_tracked": s["users_db"].get("total_users", 0),
        "total_streams_detected": s["streams"].get("total_streams", 0),
        "total_extractions": s["deep_data"].get("total_extractions", 0),
        "total_files_stored": s["deep_data"].get("total_files", 0),
        "storage_mb": s["deep_data"].get("total_size_mb", 0),
        "webmssdk_files": s["deep_data"].get("webmssdk_downloaded_count", 0),
        "total_sessions_captured": s["sessions"].get("total_sessions", 0),
        "total_fans_seen": s["fans"].get("total_fans_records", 0),
        "verified_accounts": s["users_db"].get("verified_accounts", 0),
    }


if __name__ == "__main__":
    print(json.dumps(full_stats(), ensure_ascii=False, indent=2, default=str))
