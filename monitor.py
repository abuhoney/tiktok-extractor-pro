#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor.py — موحّد دوال المراقبة v1.0
====================================================================
يدمج جميع دوال المراقبة من:
  - onlinetiktok.py (DeepDataStorage + extraction monitoring)
  - 2tikhtml.py (live monitoring helpers)
  - 3htmlboxtik.py (gift box monitoring)
  - mhmdz1.py (AdvancedIDExtractor + AdvancedDataProcessor)
  - tiktokjson.py (live_details monitoring)
  - extractor.py (interaction_fingerprint + webcast monitoring)

الوظائف:
  - مراقبة البثوث المباشرة في الوقت الفعلي
  - تتبّع تغيّر الإحصائيات (viewer_count, like_count, etc.)
  - مراقبة صناديق الهدايا
  - مراقبة حالة الجلسات
  - مراقبة عمق الاستخراج
  - مراقبة تفاعل المستخدمين
  - توليد تقارير دورية

لا يكرّر الدوال الموجودة في onlinetiktok.py أو statictor.py — يعمل بالتكامل معهما.
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("monitor")

# استيراد الوحدات الشقيقة
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from onlinetiktok import (
        TikTokSessionManager, get_session, DeepDataStorage,
        extract_room_id_from_html, extract_username_from_html,
        extract_user_id_from_html, extract_sec_uid_from_html,
        extract_extra_live_data_from_html,
        extract_js_files_from_html, extract_json_blocks_from_html,
        USER_AGENTS, DEFAULT_HEADERS,
    )
except ImportError:
    pass

try:
    from statictor import full_stats, headline_stats
    HAS_STATICTOR = True
except ImportError:
    HAS_STATICTOR = False


# ════════════════════════════════════════════════════════════════════════════
#  PART 1: LiveMonitor — مراقبة البث المباشر في الوقت الفعلي
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class LiveSnapshot:
    """لقطة لحظية لحالة البث المباشر."""
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
    """يراقب بثاً مباشراً واحداً ويسجّل تغيّر إحصائياته."""

    def __init__(self, room_id: str = None, url: str = None):
        self.room_id = room_id
        self.url = url
        self.snapshots: List[LiveSnapshot] = []
        self.max_snapshots = 1000
        self.owner_info: Dict[str, Any] = {}

    def _fetch_webcast_data(self) -> Optional[Dict[str, Any]]:
        """يجلب بيانات البث من Webcast API."""
        if not self.room_id:
            return None
        url = f"https://webcast.tiktok.com/webcast/room/info/?room_id={self.room_id}"
        session = get_session()
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                d = resp.json()
                return d.get("data")
        except Exception as e:
            logger.warning(f"webcast fetch failed: {e}")
        return None

    def take_snapshot(self) -> LiveSnapshot:
        """يلتقط لقطة لحظية لحالة البث."""
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

        # احفظ owner_info إن لم تكن محفوظة
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
        """يحسب التغيّر بين اللقطات المتتالية."""
        deltas = []
        for i in range(1, len(self.snapshots)):
            prev = self.snapshots[i-1]
            curr = self.snapshots[i]
            delta = {
                "timestamp": curr.timestamp,
                "viewer_delta": curr.viewer_count - prev.viewer_count,
                "like_delta": curr.like_count - prev.like_count,
                "enter_delta": curr.enter_count - prev.enter_count,
                "follow_delta": curr.follow_count - prev.follow_count,
                "minutes_elapsed": 0,
            }
            deltas.append(delta)
        return deltas

    def get_summary(self) -> Dict[str, Any]:
        """يُرجع ملخّص مراقبة البث."""
        if not self.snapshots:
            return {"available": False, "reason": "No snapshots taken yet"}
        latest = self.snapshots[-1]
        first = self.snapshots[0] if self.snapshots else latest
        return {
            "available": True,
            "room_id": self.room_id,
            "owner": self.owner_info,
            "total_snapshots": len(self.snapshots),
            "monitoring_started": first.timestamp,
            "latest_snapshot": latest.to_dict(),
            "peak_viewer_count": max((s.viewer_count for s in self.snapshots), default=0),
            "peak_like_count": max((s.like_count for s in self.snapshots), default=0),
            "peak_enter_count": max((s.enter_count for s in self.snapshots), default=0),
            "is_currently_live": latest.is_live,
            "deltas_count": len(self.get_deltas()),
        }


# ════════════════════════════════════════════════════════════════════════════
#  PART 2: SessionMonitor — مراقبة حالة الجلسات
# ════════════════════════════════════════════════════════════════════════════

class SessionMonitor:
    """يراقب حالة الجلسات المحفوظة."""

    def __init__(self):
        self.checks: List[Dict[str, Any]] = []

    def check_session_status(self, unique_id: str) -> Dict[str, Any]:
        """يفحص حالة جلسة محددة."""
        # تحقق محلياً
        local_dir = "/tmp/tiktok_sessions_local"
        safe_uid = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', str(unique_id))
        local_path = os.path.join(local_dir, f"{safe_uid}.json")
        result = {
            "unique_id": unique_id,
            "checked_at": datetime.utcnow().isoformat()+"Z",
            "local_exists": os.path.exists(local_path),
            "local_path": local_path if os.path.exists(local_path) else None,
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
        """يفحص كل الجلسات المحفوظة محلياً."""
        local_dir = "/tmp/tiktok_sessions_local"
        sessions = []
        if os.path.exists(local_dir):
            for fname in os.listdir(local_dir):
                if not fname.endswith(".json"):
                    continue
                uid = fname[:-5]
                status = self.check_session_status(uid)
                sessions.append(status)
        return {
            "total_local_sessions": len(sessions),
            "sessions": sessions,
            "checked_at": datetime.utcnow().isoformat()+"Z",
        }


# ════════════════════════════════════════════════════════════════════════════
#  PART 3: DeepDataMonitor — مراقبة عمق الاستخراج
# ════════════════════════════════════════════════════════════════════════════

class DeepDataMonitor:
    """يراقب البيانات العميقة المستخرجة."""

    def __init__(self):
        self.storage = DeepDataStorage() if 'DeepDataStorage' in dir() else None

    def monitor_all_extractions(self) -> Dict[str, Any]:
        """يراقب كل الاستخراجات العميقة المحفوظة."""
        if not self.storage:
            return {"available": False, "error": "DeepDataStorage not available"}
        users = self.storage.list_all_users()
        result = {
            "total_users": len(users),
            "total_extractions": sum(u.get("live_count", 0) for u in users),
            "users_detail": [],
            "monitored_at": datetime.utcnow().isoformat()+"Z",
        }
        for user in users:
            uid = user["unique_id"]
            extractions = self.storage.list_user_extractions(uid)
            user_detail = {
                "unique_id": uid,
                "live_count": user["live_count"],
                "last_extraction": user.get("last_extraction"),
                "extractions_summary": [
                    {
                        "live_number": e.get("live_number"),
                        "saved_at": e.get("saved_at"),
                        "files_count": len(e.get("files", [])),
                        "has_webmssdk": "webmssdk.js" in e.get("files", []),
                        "has_page_html": "page.html" in e.get("files", []),
                    }
                    for e in extractions
                ],
            }
            result["users_detail"].append(user_detail)
        return result

    def monitor_single_extraction(self, unique_id: str, live_number: int) -> Dict[str, Any]:
        """يراقب استخراجاً واحداً محدداً."""
        if not self.storage:
            return {"available": False, "error": "DeepDataStorage not available"}
        uid_safe = self.storage._sanitize_id(unique_id)
        live_dir = os.path.join(self.storage.base_dir, uid_safe, f"live{live_number}")
        if not os.path.exists(live_dir):
            return {"available": False, "error": "Extraction not found"}

        files_info = []
        total_size = 0
        for entry in os.listdir(live_dir):
            path = os.path.join(live_dir, entry)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                total_size += size
                files_info.append({
                    "name": entry,
                    "size_bytes": size,
                    "size_kb": round(size / 1024, 1),
                })
            elif os.path.isdir(path):
                # مجلد json/
                subdir_files = []
                for sub_entry in os.listdir(path):
                    sub_path = os.path.join(path, sub_entry)
                    if os.path.isfile(sub_path):
                        sub_size = os.path.getsize(sub_path)
                        total_size += sub_size
                        subdir_files.append({
                            "name": sub_entry,
                            "size_bytes": sub_size,
                        })
                files_info.append({
                    "name": entry + "/",
                    "is_directory": True,
                    "files": subdir_files,
                })

        # اقرأ complete_data.json للحصول على معلومات إضافية
        complete_data = None
        cd_path = os.path.join(live_dir, "complete_data.json")
        if os.path.exists(cd_path):
            try:
                with open(cd_path, "r", encoding="utf-8") as f:
                    complete_data = json.load(f)
            except Exception:
                pass

        return {
            "available": True,
            "unique_id": unique_id,
            "live_number": live_number,
            "live_dir": live_dir,
            "total_files": len(files_info),
            "total_size_bytes": total_size,
            "total_size_kb": round(total_size / 1024, 1),
            "files": files_info,
            "complete_data_summary": {
                "saved_at": complete_data.get("saved_at") if complete_data else None,
                "user_info": complete_data.get("user_info") if complete_data else None,
                "live_details": complete_data.get("live_details") if complete_data else None,
                "webmssdk": complete_data.get("webmssdk") if complete_data else None,
            } if complete_data else None,
            "monitored_at": datetime.utcnow().isoformat()+"Z",
        }


# ════════════════════════════════════════════════════════════════════════════
#  PART 4: GiftBoxMonitor — مراقبة صناديق الهدايا
# ════════════════════════════════════════════════════════════════════════════

class GiftBoxMonitor:
    """يراقب صناديق الهدايا في البثوث."""

    def __init__(self):
        self.gift_history: List[Dict[str, Any]] = []

    def analyze_gift_boxes(self, html: str, webcast_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """يحلل صناديق الهدايا من HTML + Webcast data."""
        # استخدم دوال onlinetiktok إن كانت متاحة
        try:
            from onlinetiktok import extract_all_gift_boxes
            result = extract_all_gift_boxes(html, webcast_data or {})
            self.gift_history.append({
                "analyzed_at": datetime.utcnow().isoformat()+"Z",
                "result": result,
            })
            return result
        except ImportError:
            return {"available": False, "error": "onlinetiktok not available"}

    def get_gift_history(self) -> List[Dict[str, Any]]:
        """يُرجع سجل تحليلات صناديق الهدايا."""
        return self.gift_history


# ════════════════════════════════════════════════════════════════════════════
#  PART 5: Unified Monitor API (للـ app.py)
# ════════════════════════════════════════════════════════════════════════════

_live_monitors: Dict[str, LiveMonitor] = {}
_session_monitor: Optional[SessionMonitor] = None
_deep_monitor: Optional[DeepDataMonitor] = None
_gift_monitor: Optional[GiftBoxMonitor] = None


def get_live_monitor(room_id: str = None, url: str = None) -> LiveMonitor:
    """يُرجع LiveMonitor singleton لكل room_id."""
    key = room_id or url or "default"
    if key not in _live_monitors:
        _live_monitors[key] = LiveMonitor(room_id=room_id, url=url)
    return _live_monitors[key]


def get_session_monitor() -> SessionMonitor:
    """يُرجع SessionMonitor singleton."""
    global _session_monitor
    if _session_monitor is None:
        _session_monitor = SessionMonitor()
    return _session_monitor


def get_deep_monitor() -> DeepDataMonitor:
    """يُرجع DeepDataMonitor singleton."""
    global _deep_monitor
    if _deep_monitor is None:
        _deep_monitor = DeepDataMonitor()
    return _deep_monitor


def get_gift_monitor() -> GiftBoxMonitor:
    """يُرجع GiftBoxMonitor singleton."""
    global _gift_monitor
    if _gift_monitor is None:
        _gift_monitor = GiftBoxMonitor()
    return _gift_monitor


def monitor_live(room_id: str = None, url: str = None) -> Dict[str, Any]:
    """يلتقط لقطة بث مباشر ويعرضها."""
    monitor = get_live_monitor(room_id=room_id, url=url)
    snapshot = monitor.take_snapshot()
    return {
        "success": True,
        "snapshot": snapshot.to_dict(),
        "summary": monitor.get_summary(),
    }


def monitor_all_live() -> Dict[str, Any]:
    """يراقب كل البثوث النشطة."""
    result = {
        "total_monitors": len(_live_monitors),
        "monitors": [],
        "monitored_at": datetime.utcnow().isoformat()+"Z",
    }
    for key, monitor in _live_monitors.items():
        summary = monitor.get_summary()
        summary["key"] = key
        result["monitors"].append(summary)
    return result


def monitor_sessions() -> Dict[str, Any]:
    """يفحص كل الجلسات المحفوظة."""
    sm = get_session_monitor()
    return sm.check_all_sessions()


def monitor_deep_data() -> Dict[str, Any]:
    """يراقب كل البيانات العميقة."""
    dm = get_deep_monitor()
    return dm.monitor_all_extractions()


def monitor_deep_single(unique_id: str, live_number: int) -> Dict[str, Any]:
    """يراقب استخراجاً واحداً."""
    dm = get_deep_monitor()
    return dm.monitor_single_extraction(unique_id, live_number)


def monitor_gift_boxes(html: str, webcast_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """يحلل صناديق الهدايا."""
    gm = get_gift_monitor()
    return gm.analyze_gift_boxes(html, webcast_data)


def full_monitor_report() -> Dict[str, Any]:
    """يجمع كل التقارير في تقرير واحد شامل."""
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


# ════════════════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Monitor module loaded successfully")
    print(json.dumps(full_monitor_report(), ensure_ascii=False, indent=2, default=str)[:2000])
