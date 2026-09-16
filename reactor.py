#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reactor.py — Unified Interaction Engine v1.0
====================================================================
Merges interaction functions from:
  - Interactiontik.py (TikTokInteractionBot + AccountManager + SessionManager)
  - 3htmlboxtik.py (gift_boxes analysis + interaction helpers)
  - extractor.py (PRELOADED_ACCOUNTS + execute_interaction)

Functions:
  - Account management (AccountManager)
  - Interaction execution (send_like, follow, like_video, enter_room)
  - Interaction statistics
  - Unified interaction API

Works in integration with extractor.py — does not duplicate its functions.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from onlinetiktok import TikTokSessionManager, get_session, USER_AGENTS, DEFAULT_HEADERS
except ImportError:
    USER_AGENTS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"]
    DEFAULT_HEADERS = {"User-Agent": USER_AGENTS[0], "Accept": "application/json"}

try:
    from extractor import PRELOADED_ACCOUNTS, TARGET_ACCOUNT, INTERACTION_ENDPOINTS
    HAS_EXTRACTOR = True
except ImportError:
    HAS_EXTRACTOR = False
    PRELOADED_ACCOUNTS = []
    TARGET_ACCOUNT = {}
    INTERACTION_ENDPOINTS = {}


def _resolve_endpoint_url(action: str, default_url: str) -> str:
    """Safely resolve an endpoint URL from INTERACTION_ENDPOINTS.

    The INTERACTION_ENDPOINTS dict has values like:
        {"url": "https://...", "method": "POST", "payload_fields": [...], "requires": [...]}

    This helper extracts the URL string from the dict, or returns the default.

    Bug fixed in v1.0.35: previously, the whole dict was passed to requests.post(),
    causing "No connection adapters were found for {'url': '...', 'method': 'GET', ...}" errors.
    """
    if not HAS_EXTRACTOR:
        return default_url
    endpoint = INTERACTION_ENDPOINTS.get(action, default_url)
    if isinstance(endpoint, dict):
        return endpoint.get("url", default_url)
    return endpoint  # already a URL string


@dataclass
class TikTokAccount:
    """TikTok account for interactions."""
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
    """Result of a single interaction."""
    action: str = ""
    success: bool = False
    status_code: Optional[int] = None
    response: str = ""
    error: str = ""
    timestamp: str = ""
    account_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SessionManager:
    """Unified HTTP session manager for interactions."""

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._cookies: Dict[str, str] = {}
        self._init_session()

    def _init_session(self):
        s = requests.Session()
        retry = Retry(
            total=4, backoff_factor=0.8,
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


class AccountManager:
    """Manages TikTok accounts available for interaction."""

    def __init__(self):
        self.accounts: Dict[str, TikTokAccount] = {}
        self._load_preloaded_accounts()
        self._load_env_account()

    def _load_preloaded_accounts(self):
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

    def _load_env_account(self):
        sessionid = os.environ.get("sessionid") or os.environ.get("SESSIONID")
        if sessionid:
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
            logger.info(f"Loaded env account: {account.unique_id}")

    def add_account_from_data(self, account_data: Dict) -> TikTokAccount:
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
                "unique_id": a.unique_id, "nickname": a.nickname,
                "user_id": a.user_id,
                "has_csrf": bool(a.csrf_token), "has_wid": bool(a.wid),
                "has_nonce": bool(a.nonce), "has_sessionid": bool(a.sessionid),
                "is_valid": a.is_valid(),
            }
            for a in self.accounts.values()
        ]


class InteractionBot:
    """Executes TikTok interactions using a specific account."""

    def __init__(self, account: TikTokAccount):
        self.account = account
        self.session_manager = SessionManager()
        self.stats = {
            "likes_sent": 0, "follows_sent": 0, "unfollows_sent": 0,
            "video_likes_sent": 0, "comments_sent": 0,
            "room_enters": 0, "errors": 0,
        }

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.account.csrf_token:
            headers["X-Csrf-Token"] = self.account.csrf_token
        if self.account.sessionid:
            headers["X-Tt-Token"] = self.account.sessionid
        return headers

    def _build_cookies(self) -> Dict[str, str]:
        cookies = {}
        for k in ["csrf_token", "wid", "nonce", "sessionid", "ttwid", "msToken"]:
            v = getattr(self.account, k, "")
            if v:
                cookies[k] = v
        return cookies

    def _make_request(self, url: str, payload: Dict, action: str) -> InteractionResult:
        try:
            resp = self.session_manager.post(
                url, data=payload, headers=self._build_headers(),
                cookies=self._build_cookies(), timeout=15
            )
            result = InteractionResult(
                action=action, status_code=resp.status_code,
                response=resp.text[:500], timestamp=datetime.utcnow().isoformat()+"Z",
                account_used=self.account.unique_id,
            )
            try:
                d = resp.json()
                result.success = d.get("status_code") == 0
            except:
                result.success = resp.status_code == 200
            if not result.success:
                self.stats["errors"] += 1
            return result
        except Exception as e:
            self.stats["errors"] += 1
            return InteractionResult(action=action, error=str(e), timestamp=datetime.utcnow().isoformat()+"Z")

    def send_like(self, room_id: str = None, count: int = 1) -> InteractionResult:
        room_id = room_id or self.account.room_id
        if not room_id:
            return InteractionResult(action="send_like", error="room_id required")
        url = _resolve_endpoint_url("send_like", "https://www.tiktok.com/api/live/digg/")
        payload = {"room_id": room_id, "user_id": self.account.user_id, "count": str(count),
                   "device_id": self.account.wid, "aid": "1233", "msToken": self.account.msToken}
        result = self._make_request(url, payload, "send_like")
        if result.success:
            self.stats["likes_sent"] += count
        return result

    def follow_user(self, target_user_id: str, target_sec_uid: str) -> InteractionResult:
        url = _resolve_endpoint_url("follow_user", "https://www.tiktok.com/api/relation/follow/")
        payload = {"user_id": target_user_id, "sec_user_id": target_sec_uid, "type": "1",
                   "from": "0", "device_id": self.account.wid, "aid": "1233", "msToken": self.account.msToken}
        result = self._make_request(url, payload, "follow_user")
        if result.success:
            self.stats["follows_sent"] += 1
        return result

    def unfollow_user(self, target_user_id: str, target_sec_uid: str) -> InteractionResult:
        url = "https://www.tiktok.com/api/relation/unfollow/"
        payload = {"user_id": target_user_id, "sec_user_id": target_sec_uid, "type": "0",
                   "device_id": self.account.wid, "aid": "1233"}
        result = self._make_request(url, payload, "unfollow_user")
        if result.success:
            self.stats["unfollows_sent"] += 1
        return result

    def like_video(self, video_id: str) -> InteractionResult:
        url = _resolve_endpoint_url("like_video", "https://www.tiktok.com/api/commit/item/digg/")
        payload = {"id": video_id, "type": "1", "device_id": self.account.wid, "aid": "1233", "msToken": self.account.msToken}
        result = self._make_request(url, payload, "like_video")
        if result.success:
            self.stats["video_likes_sent"] += 1
        return result

    def send_comment(self, video_id: str, comment: str) -> InteractionResult:
        if not self.account.sessionid:
            return InteractionResult(action="send_comment", error="sessionid required for comments")
        url = _resolve_endpoint_url("send_comment", "https://www.tiktok.com/api/comment/publish/")
        payload = {"aweme_id": video_id, "text": comment, "device_id": self.account.wid, "aid": "1233", "msToken": self.account.msToken}
        result = self._make_request(url, payload, "send_comment")
        if result.success:
            self.stats["comments_sent"] += 1
        return result

    def enter_live_room(self, room_id: str = None) -> InteractionResult:
        room_id = room_id or self.account.room_id
        if not room_id:
            return InteractionResult(action="enter_live_room", error="room_id required")
        url = _resolve_endpoint_url("enter_live_room", "https://webcast.tiktok.com/webcast/room/enter/")
        payload = {"room_id": room_id, "user_id": self.account.user_id, "device_id": self.account.wid, "aid": "1233", "msToken": self.account.msToken}
        result = self._make_request(url, payload, "enter_live_room")
        if result.success:
            self.stats["room_enters"] += 1
        return result

    def get_interaction_stats(self) -> Dict[str, Any]:
        return {"account": self.account.unique_id, "stats": self.stats, "timestamp": datetime.utcnow().isoformat()+"Z"}


# ════════════════════════════════════════════════════════════════════════════
#  Unified API
# ════════════════════════════════════════════════════════════════════════════

_account_manager: Optional[AccountManager] = None

def get_account_manager() -> AccountManager:
    global _account_manager
    if _account_manager is None:
        _account_manager = AccountManager()
    return _account_manager

def execute_reaction(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    mgr = get_account_manager()
    account_index = int(params.get("account_index", 0))
    accounts = list(mgr.accounts.values())
    if account_index >= len(accounts):
        return {"success": False, "error": f"account_index {account_index} out of range (have {len(accounts)})"}
    account = accounts[account_index]
    bot = InteractionBot(account)

    if action == "send_like":
        result = bot.send_like(room_id=params.get("room_id") or account.room_id, count=int(params.get("count", 1)))
    elif action == "follow_user":
        result = bot.follow_user(params.get("user_id", ""), params.get("sec_uid", ""))
    elif action == "unfollow_user":
        result = bot.unfollow_user(params.get("user_id", ""), params.get("sec_uid", ""))
    elif action == "like_video":
        result = bot.like_video(params.get("video_id", ""))
    elif action == "send_comment":
        result = bot.send_comment(params.get("video_id", ""), params.get("comment_text", ""))
    elif action == "enter_live_room":
        result = bot.enter_live_room(room_id=params.get("room_id") or account.room_id)
    else:
        return {"success": False, "error": f"Unknown action: {action}"}
    return result.to_dict()

def list_reactor_accounts() -> Dict[str, Any]:
    mgr = get_account_manager()
    return {"total_accounts": len(mgr.accounts), "accounts": mgr.list_accounts()}

def get_reactor_stats(account_key: str = None) -> Dict[str, Any]:
    mgr = get_account_manager()
    if account_key:
        account = mgr.get_account(account_key)
        if not account:
            return {"success": False, "error": "Account not found"}
        return InteractionBot(account).get_interaction_stats()
    all_stats = []
    for key, acc in mgr.accounts.items():
        all_stats.append(InteractionBot(acc).get_interaction_stats())
    return {"total_accounts": len(all_stats), "all_stats": all_stats}


if __name__ == "__main__":
    print("Reactor module loaded")
    mgr = get_account_manager()
    print(f"Accounts: {len(mgr.list_accounts())}")
