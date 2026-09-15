#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reactor.py — موحّد دوال التفاعل والاستجابة v1.0
====================================================================
يدمج جميع دوال التفاعل من:
  - Interactiontik.py (TikTokInteractionBot + AccountManager + SessionManager)
  - 3htmlboxtik.py (gift_boxes analysis + interaction helpers)
  - extractor.py (PRELOADED_ACCOUNTS + execute_interaction)

الوظائف:
  - إدارة الحسابات (AccountManager)
  - تنفيذ التفاعلات (send_like, follow, like_video, enter_room)
  - إحصائيات التفاعل
  - مراقبة نتائج التفاعل
  - بناء جلسات تفاعل موحّدة

لا يكرّر الدوال الموجودة في extractor.py — يعمل بالتكامل معها.
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import logging
import random
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger("reactor")

# استيراد onlinetiktok (نفس المجلد)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from onlinetiktok import TikTokSessionManager, get_session, USER_AGENTS, DEFAULT_HEADERS
except ImportError:
    pass

# استيراد extractor للوصول لـ PRELOADED_ACCOUNTS و execute_interaction
try:
    from extractor import PRELOADED_ACCOUNTS, TARGET_ACCOUNT, INTERACTION_ENDPOINTS
    HAS_EXTRACTOR = True
except ImportError:
    HAS_EXTRACTOR = False
    PRELOADED_ACCOUNTS = []
    TARGET_ACCOUNT = {}
    INTERACTION_ENDPOINTS = {}


# ════════════════════════════════════════════════════════════════════════════
#  PART 1: Data Classes (من Interactiontik.py)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class TikTokAccount:
    """حساب TikTok للتفاعل."""
    unique_id: str = ""
    nickname: str = ""
    user_id: str = ""
    sec_uid: str = ""
    csrf_token: str = ""
    wid: str = ""
    nonce: str = ""
    sessionid: str = ""
    ttwid: str = ""
    msToken: str = ""
    room_id: str = ""
    follower_count: int = 0
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_valid(self) -> bool:
        return bool(self.csrf_token and self.wid and self.nonce)


@dataclass
class InteractionResult:
    """نتيجة تفاعل واحد."""
    action: str = ""
    success: bool = False
    status_code: Optional[int] = None
    response: str = ""
    error: str = ""
    timestamp: str = ""
    account_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════════════════════
#  PART 2: SessionManager (مدمج من Interactiontik.py + mhmdz1.py)
# ════════════════════════════════════════════════════════════════════════════

class SessionManager:
    """مدير جلسة HTTP موحّد للتفاعلات."""

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._cookies: Dict[str, str] = {}
        self._init_session()

    def _init_session(self):
        s = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.verify = False
        s.headers.update(DEFAULT_HEADERS)
        s.headers["User-Agent"] = random.choice(USER_AGENTS)
        self._session = s

    def _update_headers(self, additional_headers: Dict = None):
        self._session.headers.update(DEFAULT_HEADERS)
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)
        if additional_headers:
            self._session.headers.update(additional_headers)

    def get(self, url: str, **kwargs) -> requests.Response:
        self._update_headers(kwargs.pop("headers", None))
        return self._session.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        self._update_headers(kwargs.pop("headers", None))
        return self._session.post(url, **kwargs)

    def set_cookies(self, cookies: Dict):
        self._cookies.update(cookies)
        for k, v in cookies.items():
            self._session.cookies.set(k, v, domain=".tiktok.com")


# ════════════════════════════════════════════════════════════════════════════
#  PART 3: AccountManager (من Interactiontik.py)
# ════════════════════════════════════════════════════════════════════════════

class AccountManager:
    """يدير حسابات TikTok المتاحة للتفاعل."""

    def __init__(self):
        self.accounts: Dict[str, TikTokAccount] = {}
        self._load_preloaded_accounts()

    def _load_preloaded_accounts(self):
        """يحمّل حسابات PRELOADED_ACCOUNTS من extractor.py."""
        if HAS_EXTRACTOR and PRELOADED_ACCOUNTS:
            for acc_data in PRELOADED_ACCOUNTS:
                account = TikTokAccount(
                    unique_id=acc_data.get("unique_id", ""),
                    nickname=acc_data.get("nickname", ""),
                    user_id=str(acc_data.get("user_id", "")),
                    csrf_token=acc_data.get("csrf_token", ""),
                    wid=str(acc_data.get("wid", "")),
                    nonce=acc_data.get("nonce", ""),
                    room_id=str(acc_data.get("room_id", "")),
                    follower_count=acc_data.get("follower_count", 0),
                )
                key = account.unique_id or f"account_{len(self.accounts)}"
                self.accounts[key] = account
            logger.info(f"Loaded {len(self.accounts)} preloaded accounts")

    def add_account_from_data(self, account_data: Dict) -> TikTokAccount:
        """يضيف حساباً من قاموس بيانات."""
        account = TikTokAccount(
            unique_id=account_data.get("unique_id", ""),
            nickname=account_data.get("nickname", ""),
            user_id=str(account_data.get("user_id", "")),
            sec_uid=account_data.get("sec_uid", ""),
            csrf_token=account_data.get("csrf_token", ""),
            wid=str(account_data.get("wid", "")),
            nonce=account_data.get("nonce", ""),
            sessionid=account_data.get("sessionid", ""),
            ttwid=account_data.get("ttwid", ""),
            msToken=account_data.get("msToken", ""),
            room_id=str(account_data.get("room_id", "")),
        )
        key = account.unique_id or f"account_{len(self.accounts)}"
        self.accounts[key] = account
        return account

    def add_account_from_env(self) -> Optional[TikTokAccount]:
        """يضيف حساباً من متغيرات البيئة (sessionid, ttwid, etc.)."""
        sessionid = os.environ.get("sessionid") or os.environ.get("SESSIONID")
        if not sessionid:
            return None
        account = TikTokAccount(
            unique_id=os.environ.get("TARGET_UNIQUE_ID", "env_account"),
            sessionid=sessionid,
            ttwid=os.environ.get("ttwid", ""),
            msToken=os.environ.get("msToken", ""),
            csrf_token=os.environ.get("csrf_token", ""),
            wid=os.environ.get("wid", ""),
            nonce=os.environ.get("nonce", ""),
        )
        self.accounts[account.unique_id] = account
        logger.info(f"Added account from env: {account.unique_id}")
        return account

    def get_account(self, key: str) -> Optional[TikTokAccount]:
        return self.accounts.get(key)

    def get_first_valid_account(self) -> Optional[TikTokAccount]:
        for acc in self.accounts.values():
            if acc.is_valid():
                return acc
        return None

    def list_accounts(self) -> List[Dict[str, Any]]:
        return [
            {
                "unique_id": a.unique_id,
                "nickname": a.nickname,
                "user_id": a.user_id,
                "has_csrf": bool(a.csrf_token),
                "has_wid": bool(a.wid),
                "has_nonce": bool(a.nonce),
                "has_sessionid": bool(a.sessionid),
                "is_valid": a.is_valid(),
            }
            for a in self.accounts.values()
        ]


# ════════════════════════════════════════════════════════════════════════════
#  PART 4: InteractionBot (من Interactiontik.py — مدمج ومبسّط)
# ════════════════════════════════════════════════════════════════════════════

class InteractionBot:
    """ينفّذ تفاعلات TikTok باستخدام حساب محدد."""

    def __init__(self, account: TikTokAccount):
        self.account = account
        self.session_manager = SessionManager()
        self.stats = {
            "likes_sent": 0,
            "follows_sent": 0,
            "unfollows_sent": 0,
            "video_likes_sent": 0,
            "comments_sent": 0,
            "room_enters": 0,
            "errors": 0,
        }

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.account.csrf_token:
            headers["X-Csrf-Token"] = self.account.csrf_token
        if self.account.sessionid:
            headers["X-Tt-Token"] = self.account.sessionid
        return headers

    def _build_cookies(self) -> Dict[str, str]:
        cookies = {}
        if self.account.csrf_token:
            cookies["csrf_token"] = self.account.csrf_token
        if self.account.wid:
            cookies["wid"] = self.account.wid
        if self.account.nonce:
            cookies["nonce"] = self.account.nonce
        if self.account.sessionid:
            cookies["sessionid"] = self.account.sessionid
        if self.account.ttwid:
            cookies["ttwid"] = self.account.ttwid
        if self.account.msToken:
            cookies["msToken"] = self.account.msToken
        return cookies

    def send_like(self, room_id: str = None, count: int = 1) -> InteractionResult:
        """يُرسل إعجاباً للبث المباشر."""
        room_id = room_id or self.account.room_id
        if not room_id:
            return InteractionResult(action="send_like", error="room_id required", timestamp=datetime.utcnow().isoformat()+"Z")

        url = INTERACTION_ENDPOINTS.get("send_like", "https://webcast.tiktok.com/webcast/like/")
        payload = {
            "room_id": room_id,
            "user_id": self.account.user_id,
            "count": str(count),
            "device_id": self.account.wid,
            "aid": "1233",
            "msToken": self.account.msToken,
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="send_like",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = False
            if result.success:
                self.stats["likes_sent"] += count
            else:
                self.stats["errors"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="send_like", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def send_multi_likes(self, room_id: str = None, count: int = 10, delay: float = 0.5) -> List[InteractionResult]:
        """يُرسل عدة إعجابات بتأخير بين كل واحد."""
        results = []
        for i in range(count):
            r = self.send_like(room_id=room_id, count=1)
            results.append(r)
            if delay > 0 and i < count - 1:
                time.sleep(delay)
        return results

    def follow_user(self, target_user_id: str, target_sec_uid: str) -> InteractionResult:
        """يُتابع مستخدماً."""
        url = INTERACTION_ENDPOINTS.get("follow", "https://www.tiktok.com/api/v1/following/follow/")
        payload = {
            "user_id": target_user_id,
            "sec_user_id": target_sec_uid,
            "type": "1",
            "from": "0",
            "device_id": self.account.wid,
            "aid": "1233",
            "msToken": self.account.msToken,
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="follow_user",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if result.success:
                self.stats["follows_sent"] += 1
            else:
                self.stats["errors"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="follow_user", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def unfollow_user(self, target_user_id: str, target_sec_uid: str) -> InteractionResult:
        """يُلغي متابعة مستخدم."""
        url = "https://www.tiktok.com/api/v1/following/unfollow/"
        payload = {
            "user_id": target_user_id,
            "sec_user_id": target_sec_uid,
            "type": "0",
            "device_id": self.account.wid,
            "aid": "1233",
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="unfollow_user",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if result.success:
                self.stats["unfollows_sent"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="unfollow_user", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def like_video(self, video_id: str) -> InteractionResult:
        """يُعجب بفيديو."""
        url = INTERACTION_ENDPOINTS.get("like_video", "https://www.tiktok.com/api/v1/like/")
        payload = {
            "id": video_id,
            "type": "1",
            "device_id": self.account.wid,
            "aid": "1233",
            "msToken": self.account.msToken,
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="like_video",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if result.success:
                self.stats["video_likes_sent"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="like_video", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def send_comment(self, video_id: str, comment: str) -> InteractionResult:
        """يُرسل تعليقاً (يتطلب sessionid)."""
        if not self.account.sessionid:
            return InteractionResult(action="send_comment", error="sessionid required for comments", timestamp=datetime.utcnow().isoformat()+"Z")

        url = INTERACTION_ENDPOINTS.get("send_comment", "https://www.tiktok.com/api/v1/comment/publish/")
        payload = {
            "aweme_id": video_id,
            "text": comment,
            "device_id": self.account.wid,
            "aid": "1233",
            "msToken": self.account.msToken,
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="send_comment",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if result.success:
                self.stats["comments_sent"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="send_comment", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def enter_live_room(self, room_id: str = None) -> InteractionResult:
        """يدخل غرفة بث مباشر."""
        room_id = room_id or self.account.room_id
        if not room_id:
            return InteractionResult(action="enter_live_room", error="room_id required", timestamp=datetime.utcnow().isoformat()+"Z")

        url = INTERACTION_ENDPOINTS.get("enter_live_room", "https://webcast.tiktok.com/webcast/room/enter/")
        payload = {
            "room_id": room_id,
            "user_id": self.account.user_id,
            "device_id": self.account.wid,
            "aid": "1233",
            "msToken": self.account.msToken,
        }
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action="enter_live_room",
                status_code=resp.status_code,
                response=resp.text[:500],
                timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if result.success:
                self.stats["room_enters"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action="enter_live_room", error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def get_interaction_stats(self) -> Dict[str, Any]:
        """يُرجع إحصائيات التفاعل."""
        return {
            "account": self.account.unique_id,
            "stats": self.stats,
            "timestamp": datetime.utcnow().isoformat()+"Z",
        }


# ════════════════════════════════════════════════════════════════════════════
#  PART 5: Unified Interaction API (للـ app.py)
# ════════════════════════════════════════════════════════════════════════════

_account_manager: Optional[AccountManager] = None

def get_account_manager() -> AccountManager:
    """يُرجع AccountManager singleton."""
    global _account_manager
    if _account_manager is None:
        _account_manager = AccountManager()
    return _account_manager


def execute_reaction(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """ينفّذ تفاعلاً موحّداً.

    Args:
        action: send_like | follow_user | unfollow_user | like_video | send_comment | enter_live_room
        params: room_id, user_id, sec_uid, video_id, comment_text, count, account_index

    Returns:
        نتيجة التفاعل كقاموس
    """
    mgr = get_account_manager()
    account_index = int(params.get("account_index", 0))
    accounts = list(mgr.accounts.values())

    if account_index >= len(accounts):
        return {"success": False, "error": f"account_index {account_index} out of range (have {len(accounts)})"}

    account = accounts[account_index]
    bot = InteractionBot(account)

    if action == "send_like":
        room_id = params.get("room_id") or account.room_id
        count = int(params.get("count", 1))
        result = bot.send_like(room_id=room_id, count=count)
    elif action == "follow_user":
        target_user_id = params.get("user_id", "")
        target_sec_uid = params.get("sec_uid", "")
        result = bot.follow_user(target_user_id, target_sec_uid)
    elif action == "unfollow_user":
        target_user_id = params.get("user_id", "")
        target_sec_uid = params.get("sec_uid", "")
        result = bot.unfollow_user(target_user_id, target_sec_uid)
    elif action == "like_video":
        video_id = params.get("video_id", "")
        result = bot.like_video(video_id)
    elif action == "send_comment":
        video_id = params.get("video_id", "")
        comment = params.get("comment_text", "")
        result = bot.send_comment(video_id, comment)
    elif action == "enter_live_room":
        room_id = params.get("room_id") or account.room_id
        result = bot.enter_live_room(room_id=room_id)
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    return result.to_dict()


def list_reactor_accounts() -> Dict[str, Any]:
    """يُرجع قائمة الحسابات المتاحة للتفاعل."""
    mgr = get_account_manager()
    return {
        "total_accounts": len(mgr.accounts),
        "accounts": mgr.list_accounts(),
    }


def get_reactor_stats(account_key: str = None) -> Dict[str, Any]:
    """يُرجع إحصائيات التفاعل لحساب محدد أو الكل."""
    mgr = get_account_manager()
    if account_key:
        account = mgr.get_account(account_key)
        if not account:
            return {"success": False, "error": "Account not found"}
        bot = InteractionBot(account)
        return bot.get_interaction_stats()
    # كل الحسابات
    all_stats = []
    for key, acc in mgr.accounts.items():
        bot = InteractionBot(acc)
        all_stats.append(bot.get_interaction_stats())
    return {"total_accounts": len(all_stats), "all_stats": all_stats}


# ════════════════════════════════════════════════════════════════════════════
#  Main
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Reactor module loaded successfully")
    mgr = get_account_manager()
    print(f"Accounts: {len(mgr.list_accounts())}")
    print(json.dumps(mgr.list_accounts(), indent=2, default=str))
