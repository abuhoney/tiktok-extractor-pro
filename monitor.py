#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor.py — Unified Monitoring Engine v1.0
====================================================================
Merges monitoring functions from:
  - onlinetiktok.py (DeepDataStorage + extraction monitoring)
  - 2tikhtml.py (live monitoring helpers)
  - 3htmlboxtik.py (gift box monitoring)
  - mhmdz1.py (AdvancedIDExtractor + AdvancedDataProcessor)
  - extractor.py (interaction_fingerprint + webcast monitoring)

Functions:
  - Live stream real-time monitoring with snapshots
  - Session status monitoring
  - Deep data extraction monitoring
  - Gift box analysis tracking
  - Comprehensive monitoring reports
"""

from __future__ import annotations

import os, sys, json, re, time, logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("monitor")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from onlinetiktok import (
        TikTokSessionManager, get_session, DeepDataStorage,
        extract_room_id_from_html, extract_username_from_html,
        extract_user_id_from_html, extract_sec_uid_from_html,
        extract_extra_live_data_from_html,
        USER_AGENTS, DEFAULT_HEADERS,
    )
except ImportError:
    DeepDataStorage = None

try:
    from statictor import full_stats, headline_stats
    HAS_STATICTOR = True
except ImportError:
    HAS_STATICTOR = False


@dataclass
class LiveSnapshot:
    timestamp: str = ""
    room_id: str = ""
    viewer_count: int = 0
    like_count: int = 0
    enter_count: int = 0
    follow_count: int = 0
    share_count: int = 0
    comment_count: int = 0
    replay_viewers: int = 0
    live_status: str = ""
    is_live: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LiveMonitor:
    """Monitors a single live stream and records stats changes."""

    def __init__(self, room_id: str = None, url: str = None):
        self.room_id = room_id
        self.url = url
        self.snapshots: List[LiveSnapshot] = []
        self.max_snapshots = 1000
        self.owner_info: Dict[str, Any] = {}

    def _fetch_webcast_data(self) -> Optional[Dict[str, Any]]:
        if not self.room_id:
            return None
        url = f"https://webcast.tiktok.com/webcast/room/info/?room_id={self.room_id}"
        try:
            resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=15, verify=False)
            if resp.status_code == 200:
                return resp.json().get("data")
        except Exception as e:
            logger.warning(f"webcast fetch failed: {e}")
        return None

    def take_snapshot(self) -> LiveSnapshot:
        snapshot = LiveSnapshot(timestamp=datetime.utcnow().isoformat()+"Z")
        data = self._fetch_webcast_data()
        if not data:
            return snapshot

        snapshot.room_id = str(data.get("id_str") or data.get("room_id") or self.room_id or "")
        stats = data.get("stats") or {}
        snapshot.viewer_count = int(stats.get("viewer_count") or 0)
        snapshot.like_count = int(stats.get("like_count") or 0)
        snapshot.enter_count = int(stats.get("enter_count") or 0)
        snapshot.follow_count = int(stats.get("follow_count") or 0)
        snapshot.share_count = int(stats.get("share_count") or 0)
        snapshot.comment_count = int(stats.get("comment_count") or 0)
        snapshot.replay_viewers = int(stats.get("replay_viewers") or 0)
        live_status = data.get("live_status") or data.get("status")
        snapshot.live_status = str(live_status or "")
        snapshot.is_live = bool(live_status in (2, "live", "is_live", "live_status"))

        if not self.owner_info and data.get("owner"):
            owner = data["owner"]
            self.owner_info = {
                "unique_id": owner.get("unique_id"),
                "nickname": owner.get("nickname"),
                "user_id": str(owner.get("user_id") or owner.get("id_str") or ""),
                "follower_count": owner.get("follower_count"),
                "verified": bool(owner.get("verified")),
            }

        self.snapshots.append(snapshot)
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots:]
        return snapshot

    def get_deltas(self) -> List[Dict[str, Any]]:
        deltas = []
        for i in range(1, len(self.snapshots)):
            prev, curr = self.snapshots[i-1], self.snapshots[i]
            deltas.append({
                "timestamp": curr.timestamp,
                "viewer_delta": curr.viewer_count - prev.viewer_count,
                "like_delta": curr.like_count - prev.like_count,
                "enter_delta": curr.enter_count - prev.enter_count,
            })
        return deltas

    def get_summary(self) -> Dict[str, Any]:
        if not self.snapshots:
            return {"available": False, "reason": "No snapshots taken yet"}
        latest = self.snapshots[-1]
        return {
            "available": True, "room_id": self.room_id, "owner": self.owner_info,
            "total_snapshots": len(self.snapshots),
            "monitoring_started": self.snapshots[0].timestamp,
            "latest_snapshot": latest.to_dict(),
            "peak_viewer_count": max((s.viewer_count for s in self.snapshots), default=0),
            "peak_like_count": max((s.like_count for s in self.snapshots), default=0),
            "is_currently_live": latest.is_live,
        }


class SessionMonitor:
    """Monitors saved session status."""

    def __init__(self):
        self.checks: List[Dict[str, Any]] = []

    def check_session_status(self, unique_id: str) -> Dict[str, Any]:
        local_dir = "/tmp/tiktok_sessions_local"
        safe_uid = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', str(unique_id))
        local_path = os.path.join(local_dir, f"{safe_uid}.json")
        result = {
            "unique_id": unique_id,
            "checked_at": datetime.utcnow().isoformat()+"Z",
            "local_exists": os.path.exists(local_path),
        }
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                result["saved_at"] = record.get("saved_at")
                result["sessionid_preview"] = (record.get("sessionid") or "")[:20] + "..."
                result["extra_cookies_count"] = len(record.get("extra_cookies") or {})
            except Exception:
                pass
        self.checks.append(result)
        return result

    def check_all_sessions(self) -> Dict[str, Any]:
        local_dir = "/tmp/tiktok_sessions_local"
        sessions = []
        if os.path.exists(local_dir):
            for fname in os.listdir(local_dir):
                if fname.endswith(".json"):
                    sessions.append(self.check_session_status(fname[:-5]))
        return {"total_local_sessions": len(sessions), "sessions": sessions, "checked_at": datetime.utcnow().isoformat()+"Z"}


class DeepDataMonitor:
    """Monitors deep extraction data."""

    def __init__(self):
        self.storage = DeepDataStorage() if DeepDataStorage else None

    def monitor_all_extractions(self) -> Dict[str, Any]:
        if not self.storage:
            return {"available": False, "error": "DeepDataStorage not available"}
        users = self.storage.list_all_users()
        result = {"total_users": len(users), "total_extractions": sum(u.get("live_count", 0) for u in users),
                  "users_detail": [], "monitored_at": datetime.utcnow().isoformat()+"Z"}
        for user in users:
            uid = user["unique_id"]
            extractions = self.storage.list_user_extractions(uid)
            result["users_detail"].append({
                "unique_id": uid, "live_count": user["live_count"],
                "last_extraction": user.get("last_extraction"),
                "extractions_summary": [
                    {"live_number": e.get("live_number"), "saved_at": e.get("saved_at"),
                     "files_count": len(e.get("files", [])),
                     "has_webmssdk": "webmssdk.js" in e.get("files", [])}
                    for e in extractions
                ],
            })
        return result

    def monitor_single_extraction(self, unique_id: str, live_number: int) -> Dict[str, Any]:
        if not self.storage:
            return {"available": False, "error": "DeepDataStorage not available"}
        uid_safe = self.storage._sanitize_id(unique_id)
        live_dir = os.path.join(self.storage.base_dir, uid_safe, f"live{live_number}")
        if not os.path.exists(live_dir):
            return {"available": False, "error": "Extraction not found"}
        files_info, total_size = [], 0
        for root, dirs, files in os.walk(live_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                size = os.path.getsize(fpath)
                total_size += size
                files_info.append({"name": fname, "size_bytes": size})
        return {
            "available": True, "unique_id": unique_id, "live_number": live_number,
            "total_files": len(files_info), "total_size_bytes": total_size,
            "files": files_info, "monitored_at": datetime.utcnow().isoformat()+"Z",
        }


class GiftBoxMonitor:
    """Monitors gift boxes in streams."""

    def __init__(self):
        self.gift_history: List[Dict[str, Any]] = []

    def analyze_gift_boxes(self, html: str, webcast_data: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            from onlinetiktok import extract_all_gift_boxes
            result = extract_all_gift_boxes(html, webcast_data or {})
            self.gift_history.append({"analyzed_at": datetime.utcnow().isoformat()+"Z", "result": result})
            return result
        except ImportError:
            return {"available": False, "error": "onlinetiktok not available"}


# Singleton accessors
_live_monitors: Dict[str, LiveMonitor] = {}
_session_monitor: Optional[SessionMonitor] = None
_deep_monitor: Optional[DeepDataMonitor] = None
_gift_monitor: Optional[GiftBoxMonitor] = None

def get_live_monitor(room_id: str = None, url: str = None) -> LiveMonitor:
    key = room_id or url or "default"
    if key not in _live_monitors:
        _live_monitors[key] = LiveMonitor(room_id=room_id, url=url)
    return _live_monitors[key]

def get_session_monitor() -> SessionMonitor:
    global _session_monitor
    if _session_monitor is None:
        _session_monitor = SessionMonitor()
    return _session_monitor

def get_deep_monitor() -> DeepDataMonitor:
    global _deep_monitor
    if _deep_monitor is None:
        _deep_monitor = DeepDataMonitor()
    return _deep_monitor

def get_gift_monitor() -> GiftBoxMonitor:
    global _gift_monitor
    if _gift_monitor is None:
        _gift_monitor = GiftBoxMonitor()
    return _gift_monitor

def monitor_live(room_id: str = None, url: str = None) -> Dict[str, Any]:
    monitor = get_live_monitor(room_id=room_id, url=url)
    snapshot = monitor.take_snapshot()
    return {"success": True, "snapshot": snapshot.to_dict(), "summary": monitor.get_summary()}

def monitor_all_live() -> Dict[str, Any]:
    result = {"total_monitors": len(_live_monitors), "monitors": [], "monitored_at": datetime.utcnow().isoformat()+"Z"}
    for key, monitor in _live_monitors.items():
        summary = monitor.get_summary()
        summary["key"] = key
        result["monitors"].append(summary)
    return result

def monitor_sessions() -> Dict[str, Any]:
    return get_session_monitor().check_all_sessions()

def monitor_deep_data() -> Dict[str, Any]:
    return get_deep_monitor().monitor_all_extractions()

def monitor_deep_single(unique_id: str, live_number: int) -> Dict[str, Any]:
    return get_deep_monitor().monitor_single_extraction(unique_id, live_number)

def monitor_gift_boxes(html: str, webcast_data: Dict[str, Any] = None) -> Dict[str, Any]:
    return get_gift_monitor().analyze_gift_boxes(html, webcast_data)

def full_monitor_report() -> Dict[str, Any]:
    report = {
        "generated_at": datetime.utcnow().isoformat()+"Z",
        "version": "v5.1_monitor",
        "live_monitoring": monitor_all_live(),
        "sessions": monitor_sessions(),
        "deep_data": monitor_deep_data(),
    }
    if HAS_STATICTOR:
        report["statistics"] = headline_stats()
    return report


if __name__ == "__main__":
    print(json.dumps(full_monitor_report(), ensure_ascii=False, indent=2, default=str)[:2000])
