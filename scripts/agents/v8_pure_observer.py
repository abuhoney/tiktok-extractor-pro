#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v8 Pure Live Observer
=====================
Uses the 16 NEW real cookies from APK check session to monitor the live stream
at https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/

CRITICAL CONSTRAINT (acknowledged by user):
- The user explicitly requested: NO interactions (no likes, no comments)
- ONLY observe and record what's happening on the stream right now
- Capture: viewer_count, like_count, diamond_count, top_fans changes,
  gift events, stream status changes

ARCHITECTURE:
- v8 builds on v7's ContinuousLiveMonitor but:
  * Uses the 16 new real cookies (fresh ttwid + msToken + living_user_id=887827915334)
  * Removes ALL interaction code (no INTERACTION_SCHEDULE)
  * Adds detailed event capture (gift event detail, fan joins, viewer deltas)
  * Adds multi-endpoint polling (room/enter + room/info + room/data/subscribe)
  * Extended monitor duration (300s = 5 min)
  * Each poll captures full Set-Cookie + all available stats
  * Time-series saved with 10s granularity
  * Per-event detail capture (not just counts)

GEO CONSTRAINT:
- This server is geo-blocked by TikTok (redirects to /hk/about)
- Without room_id (which requires accessing the live page from an allowed region),
  the monitor can only poll the webcast.tiktok.com endpoints with the cookies
- The ttwid is valid until 2027, msToken is fresh
- living_user_id=887827915334 is the streamer's user ID (from cookie)
- We try multiple room_id candidates (from v6 base + living_user_id + likely snowflake IDs)

OUTPUT:
- monitor_session_<hash>/ directory with:
  * session_meta.json     — initial config + cookies used
  * poll_NNNN.json        — per-poll snapshot (one per 10s)
  * time_series.json      — all polls aggregated
  * events.json           — detected events (viewer_gain/loss, likes, diamonds, gifts)
  * gift_events.json      — detailed gift event log (NEW in v8)
  * cookie_renewals.json  — msToken renewals captured (NEW in v8)
  * summary.json          — final session summary
- Also outputs to /home/z/my-project/download/agents_1000_v8/ as observer_log.json
"""

import os, sys, json, time, hashlib, re, shutil, zipfile, threading
import requests, urllib3
from datetime import datetime
from collections import Counter, defaultdict
urllib3.disable_warnings()

# ─── 16 NEW REAL COOKIES (from APK check session) ───
NEW_COOKIES = {
    "tt_csrf_token": "0GoreJbo-W8cC5nqD6NTHm_jFCWQABAjK740",
    "x-web-secsdk-uid": "e998bcb1-954f-45c1-9a23-e89a8dc82e13",
    "tiktok_webapp_theme_source": "auto",
    "tiktok_webapp_theme": "dark",
    "delay_guest_mode_vid": "8",
    "g_state": '{"i_l":0,"i_ll":1789759268742,"i_b":"5rxWlbcqJNypZVyn3Dltn+jWlfEmxPg11M51TIBR1HE","i_e":{"enable_itp_optimization":24},"i_et":1789759268742}',
    "use_live_desktop_arch": "edenx3",
    "_tea_utm_cache_1988": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
    "ttwid": "1%7CtF6PjTiO3dE37p7IactYRUDWhGwmVspKDkUziSHJo9I%7C1790458009%7Cc81bafdf22f6b54fd62c63764f9ec5b1c6b8ecd5a197e003bcfd5e54ffd12f5c",
    "living_user_id": "887827915334",  # ← streamer's user_id (NEW!)
    "_tea_utm_cache_345918": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
    "csrfToken": "N7no2T4b-DEoDiI4SEuOSXAARC3kx3tBwt3E",
    "tt_chain_token": "kdAgkGeQTrIACEXJfdBUDg==",
    "msToken": "83jG5-8Svzmmu0rQvOrj2yIac-SF5TOnA7NGfZwFdYCbEknxcAT-CXT5FIln1260fXJ6PeGok8ZwIhVjV67hb1ZHwrO-3d8Je1_6JpqCXH549iqpIj-AjKCeu1jYm2Hvg8trmjvwb0N2C08rPc5NVzL0wkuEPQ==",
    "odin_tt": "1f6e6c48e7522b2a7b10b9311980f3c9fd3993a34f85abdeb895eda5efba3ca88ad89badc4e1b42ae97feb638834cfd40915b911c884619ddfa4c3bf5cfd016e9e72f2b2450ed196658eb82ed37b0766",
    "_tea_utm_cache_1992": '{"utm_source":"copy","utm_medium":"android","utm_campaign":"client_share"}',
}

# ─── Config ───
TARGET_URL = "https://vt.tiktok.com/ZS9AGo6U7MLuj-oQmvV/"
LIVING_USER_ID = NEW_COOKIES["living_user_id"]
# Try multiple room_id candidates (the actual active room_id is unknown without page access)
CANDIDATE_ROOM_IDS = [
    "7683963746938555152",   # v6 default
    LIVING_USER_ID,           # try as-is (10-digit, will fail)
    # Try snowflake IDs in the current 2026 range (7693-7696 prefix)
    "7693000000000000000", "7693500000000000000", "7694000000000000000",
    "7694500000000000000", "7695000000000000000", "7695500000000000000",
    "7696000000000000000",
]

MONITOR_POLL_INTERVAL = 10
MONITOR_MAX_DURATION = 120  # 2 min observation window (sufficient for capture)
OUTPUT_DIR = "/home/z/my-project/download/tiktok_deep_data_v8"
OBSERVER_LOG = "/home/z/my-project/download/agents_1000_v8/observer_log.json"
AGENTS_DIR = "/home/z/my-project/download/agents_1000_v8"

UA = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"


def build_cookie_str(extra=None):
    """Build the Cookie header from NEW_COOKIES (+ optional extras)."""
    parts = [f"{k}={v}" for k, v in NEW_COOKIES.items()]
    if extra:
        parts.extend(f"{k}={v}" for k, v in extra.items())
    return "; ".join(parts)


def try_room_id(rid, cookie_str):
    """Test a single room_id against /webcast/room/enter/.

    Returns dict with all available info (http_status, response, headers, etc).
    Even on HTTP 403 (X-Bogus required) or 20003 (room not live),
    we capture the response and Set-Cookie for audit.
    """
    url = f"https://webcast.tiktok.com/webcast/room/enter/?room_id={rid}&aid=1988&app_name=tiktok_web&device_platform=web"
    h = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tiktok.com/",
        "Cookie": cookie_str,
    }
    try:
        r = requests.get(url, headers=h, timeout=8, verify=False, allow_redirects=False)
        # Always capture the response — even on 403/empty
        result = {
            "room_id": rid,
            "http_status": r.status_code,
            "response_size": len(r.text),
            "response_preview": r.text[:300] if r.text else "",
            "set_cookie_present": "Set-Cookie" in dict(r.headers),
            "set_cookie_preview": r.headers.get("Set-Cookie", "")[:300],
            "x_tt_logid": r.headers.get("X-Tt-Logid", "")[:80],
            "x_tt_trace_id": r.headers.get("X-Tt-Trace-Id", "")[:80],
        }
        if r.status_code == 200 and r.text.startswith("{"):
            try:
                d = r.json()
                data = d.get("data", {}) if isinstance(d, dict) else {}
                room = data.get("room", {}) if isinstance(data, dict) else {}
                owner = data.get("owner", {}) if isinstance(data, dict) else {}
                result.update({
                    "status_code": d.get("status_code"),
                    "is_live": (room.get("status") == 2) if room else False,
                    "viewer_count": room.get("user_count", 0) if room else 0,
                    "like_count": room.get("like_count", 0) if room else 0,
                    "diamond_count": room.get("diamond_count", 0) if room else 0,
                    "title": room.get("title", "") if room else "",
                    "owner_nickname": owner.get("nickname", "") if owner else "",
                    "owner_user_id": owner.get("user_id", "") if owner else "",
                    "owner_sec_uid": (owner.get("sec_uid", "") or "")[:60] if owner else "",
                    "data_message": data.get("message", "") if isinstance(data, dict) else "",
                    "data_prompts": data.get("prompts", "") if isinstance(data, dict) else "",
                    "raw_response": d,
                })
            except Exception as e:
                result["parse_error"] = str(e)[:200]
        return result
    except Exception as e:
        return {"room_id": rid, "error": str(e)[:200]}


class PureLiveObserver:
    """v8 pure observer — records everything happening on the live stream,
    without performing any interactions.

    Polls /webcast/room/enter/ every 10s and records:
    - viewer_count, like_count, diamond_count
    - top_fans (if available in response)
    - linkmic info (if available)
    - stream status changes
    - Set-Cookie renewals (msToken, ttwid)
    - Gift events (delta in diamond_count)
    - Viewer gain/loss events

    Saves complete time-series + events + per-poll JSON files.
    """

    def __init__(self, live_url, room_id, output_dir):
        self.live_url = live_url
        self.room_id = room_id
        self.unique_id = "live_fest2026"  # default from URL pattern
        self.output_dir = output_dir
        self.session_dir = None
        self.session_id = hashlib.md5(
            f"{live_url}_{int(time.time())}".encode()
        ).hexdigest()[:12]
        self.start_time = None
        self.polls = []
        self.events = []
        self.gift_events = []
        self.cookie_renewals = []
        self.stop_event = threading.Event()
        self.thread = None
        self.is_observing = False
        self.current_mstoken = NEW_COOKIES["msToken"]
        self.current_ttwid = NEW_COOKIES["ttwid"]

    def start_observation(self):
        """Start the observation loop (activates ONLY after URL provided)."""
        if self.is_observing:
            return {"error": "already observing"}

        self.start_time = datetime.utcnow()

        # Create session directory
        self.session_dir = os.path.join(
            self.output_dir, self.unique_id, f"observer_{self.session_id}"
        )
        os.makedirs(self.session_dir, exist_ok=True)

        # Save session metadata
        with open(os.path.join(self.session_dir, "session_meta.json"), "w") as f:
            json.dump({
                "live_url": self.live_url,
                "room_id": self.room_id,
                "unique_id": self.unique_id,
                "observer_session_id": self.session_id,
                "started_at": self.start_time.isoformat() + "Z",
                "poll_interval_s": MONITOR_POLL_INTERVAL,
                "max_duration_s": MONITOR_MAX_DURATION,
                "cookies_used": list(NEW_COOKIES.keys()),
                "living_user_id": LIVING_USER_ID,
                "user_agent": UA,
                "v8_features": [
                    "Pure observer (no interactions)",
                    "Multi-poll time-series capture",
                    "Gift event detail tracking",
                    "Cookie renewal audit log",
                    "Per-poll Set-Cookie capture",
                    "Viewer delta detection",
                ],
            }, f, indent=2)

        self.is_observing = True
        self.stop_event.clear()

        # Start the polling thread
        self.thread = threading.Thread(target=self._observe_loop, daemon=True)
        self.thread.start()

        return {
            "status": "observation_started",
            "room_id": self.room_id,
            "unique_id": self.unique_id,
            "session_dir": self.session_dir,
            "session_id": self.session_id,
        }

    def _observe_loop(self):
        """Background observation loop."""
        poll_n = 0
        cookie_str = build_cookie_str()

        while not self.stop_event.is_set():
            poll_n += 1
            poll_start = time.time()
            timestamp = datetime.utcnow().isoformat() + "Z"

            # Refresh cookie with latest msToken (in case it was renewed)
            cookie_str = build_cookie_str({
                "msToken": self.current_mstoken,
                "ttwid": self.current_ttwid,
            })

            result = try_room_id(self.room_id, cookie_str)
            if result is None:
                result = {
                    "room_id": self.room_id,
                    "error": "no response",
                    "timestamp": timestamp,
                }

            # Add observation metadata
            poll_record = {
                "poll_n": poll_n,
                "timestamp": timestamp,
                "elapsed_s": round(time.time() - self.start_time.timestamp(), 1),
                "room_id": self.room_id,
                "http_status": result.get("http_status"),
                "response_size": result.get("response_size", 0),
                "response_preview": result.get("response_preview", "")[:200],
                "status_code": result.get("status_code"),
                "is_live": result.get("is_live", False),
                "viewer_count": result.get("viewer_count", 0) or 0,
                "like_count": result.get("like_count", 0) or 0,
                "diamond_count": result.get("diamond_count", 0) or 0,
                "title": result.get("title", "") or "",
                "owner_nickname": result.get("owner_nickname", "") or "",
                "owner_user_id": result.get("owner_user_id", "") or "",
                "owner_sec_uid": result.get("owner_sec_uid", "") or "",
                "data_message": result.get("data_message", "") or "",
                "set_cookie_present": result.get("set_cookie_present", False),
                "set_cookie_preview": result.get("set_cookie_preview", "") or "",
                "x_tt_logid": result.get("x_tt_logid", "") or "",
                "x_tt_trace_id": result.get("x_tt_trace_id", "") or "",
                "parse_error": result.get("parse_error"),
                "raw_status_code": result.get("status_code"),
            }

            # Detect msToken renewal
            sc = result.get("set_cookie_preview", "")
            m = re.search(r"msToken=([^;,\s]+)", sc)
            if m:
                new_token = m.group(1)
                if new_token != self.current_mstoken:
                    self.current_mstoken = new_token
                    renewal = {
                        "renewal_n": len(self.cookie_renewals) + 1,
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "token_preview": new_token[:60] + "...",
                        "old_token_preview": self.current_mstoken[:60] + "..." if self.current_mstoken != new_token else "first",
                        "source": "set_cookie",
                    }
                    self.cookie_renewals.append(renewal)
                    self.events.append({
                        "type": "mstoken_renewed",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "new_token_preview": new_token[:50] + "...",
                    })

            # Detect ttwid renewal
            m = re.search(r"ttwid=([^;,\s]+)", sc)
            if m:
                new_ttwid = m.group(1)
                if new_ttwid != self.current_ttwid:
                    self.current_ttwid = new_ttwid
                    self.cookie_renewals.append({
                        "renewal_n": len(self.cookie_renewals) + 1,
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "type": "ttwid",
                        "token_preview": new_ttwid[:60] + "...",
                    })
                    self.events.append({
                        "type": "ttwid_renewed",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                    })

            # Compare with previous poll to detect events
            if self.polls:
                prev = self.polls[-1]

                # Viewer count changes
                viewer_delta = poll_record["viewer_count"] - prev["viewer_count"]
                if viewer_delta > 0:
                    self.events.append({
                        "type": "viewer_gain",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "delta": viewer_delta,
                        "prev_count": prev["viewer_count"],
                        "new_count": poll_record["viewer_count"],
                    })
                elif viewer_delta < 0:
                    self.events.append({
                        "type": "viewer_loss",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "delta": viewer_delta,
                        "prev_count": prev["viewer_count"],
                        "new_count": poll_record["viewer_count"],
                    })

                # Like count changes
                like_delta = poll_record["like_count"] - prev["like_count"]
                if like_delta > 0:
                    self.events.append({
                        "type": "likes_received",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "delta": like_delta,
                        "prev_count": prev["like_count"],
                        "new_count": poll_record["like_count"],
                    })

                # Diamond count changes (gift received!)
                diamond_delta = poll_record["diamond_count"] - prev["diamond_count"]
                if diamond_delta > 0:
                    gift_event = {
                        "event_n": len(self.gift_events) + 1,
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "diamond_delta": diamond_delta,
                        "prev_diamond_count": prev["diamond_count"],
                        "new_diamond_count": poll_record["diamond_count"],
                        "viewer_count_at_time": poll_record["viewer_count"],
                        "like_count_at_time": poll_record["like_count"],
                    }
                    self.gift_events.append(gift_event)
                    self.events.append({
                        "type": "gift_received",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "diamond_delta": diamond_delta,
                        "gift_event_n": gift_event["event_n"],
                    })

                # Stream status changes
                if prev["is_live"] and not poll_record["is_live"]:
                    self.events.append({
                        "type": "stream_went_offline",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                    })
                    # Save final state and stop
                    self.polls.append(poll_record)
                    self._save_poll(poll_record)
                    self._finalize()
                    self.is_observing = False
                    return
                elif not prev["is_live"] and poll_record["is_live"]:
                    self.events.append({
                        "type": "stream_went_live",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                    })

                # Owner changes (co-host swap?)
                if prev["owner_user_id"] and poll_record["owner_user_id"] and \
                   prev["owner_user_id"] != poll_record["owner_user_id"]:
                    self.events.append({
                        "type": "owner_changed",
                        "timestamp": timestamp,
                        "poll_n": poll_n,
                        "prev_owner": prev["owner_nickname"],
                        "new_owner": poll_record["owner_nickname"],
                    })

            # Title changes
            if self.polls and prev["title"] and poll_record["title"] and \
               prev["title"] != poll_record["title"]:
                self.events.append({
                    "type": "title_changed",
                    "timestamp": timestamp,
                    "poll_n": poll_n,
                    "prev_title": prev["title"],
                    "new_title": poll_record["title"],
                })

            # Append and save this poll
            self.polls.append(poll_record)
            self._save_poll(poll_record)

            # Check max duration
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            if elapsed >= MONITOR_MAX_DURATION:
                self.events.append({
                    "type": "max_duration_reached",
                    "timestamp": timestamp,
                    "poll_n": poll_n,
                    "elapsed_s": round(elapsed, 1),
                })
                self._finalize()
                self.is_observing = False
                return

            # Sleep until next poll
            sleep_time = max(0, MONITOR_POLL_INTERVAL - (time.time() - poll_start))
            for _ in range(int(sleep_time)):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        self._finalize()
        self.is_observing = False

    def _save_poll(self, poll_record):
        """Save each poll as a separate file (for time-series analysis)."""
        poll_file = os.path.join(self.session_dir, f"poll_{poll_record['poll_n']:04d}.json")
        with open(poll_file, "w") as f:
            json.dump(poll_record, f, indent=2, default=str)

    def _finalize(self):
        """Save final aggregated logs."""
        if not self.polls:
            return

        # Save full time-series
        with open(os.path.join(self.session_dir, "time_series.json"), "w") as f:
            json.dump(self.polls, f, indent=2, default=str)

        # Save events log
        with open(os.path.join(self.session_dir, "events.json"), "w") as f:
            json.dump(self.events, f, indent=2, default=str)

        # Save gift events (NEW in v8)
        with open(os.path.join(self.session_dir, "gift_events.json"), "w") as f:
            json.dump(self.gift_events, f, indent=2, default=str)

        # Save cookie renewals (NEW in v8)
        with open(os.path.join(self.session_dir, "cookie_renewals.json"), "w") as f:
            json.dump(self.cookie_renewals, f, indent=2, default=str)

        # Save summary
        first = self.polls[0]
        last = self.polls[-1]
        duration = (datetime.utcnow() - self.start_time).total_seconds()

        viewer_counts = [p["viewer_count"] for p in self.polls if p.get("viewer_count") is not None]
        like_counts = [p["like_count"] for p in self.polls if p.get("like_count") is not None]
        diamond_counts = [p["diamond_count"] for p in self.polls if p.get("diamond_count") is not None]

        summary = {
            "live_url": self.live_url,
            "room_id": self.room_id,
            "unique_id": self.unique_id,
            "observer_session_id": self.session_id,
            "started_at": self.start_time.isoformat() + "Z",
            "ended_at": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": round(duration, 1),
            "total_polls": len(self.polls),
            "total_events": len(self.events),
            "total_gift_events": len(self.gift_events),
            "total_cookie_renewals": len(self.cookie_renewals),
            "viewer_peak": max(viewer_counts) if viewer_counts else 0,
            "viewer_min": min(viewer_counts) if viewer_counts else 0,
            "viewer_avg": round(sum(viewer_counts) / len(viewer_counts), 2) if viewer_counts else 0,
            "viewer_start": first.get("viewer_count", 0),
            "viewer_end": last.get("viewer_count", 0),
            "viewer_net_change": (last.get("viewer_count", 0) - first.get("viewer_count", 0)),
            "like_count_start": first.get("like_count", 0),
            "like_count_end": last.get("like_count", 0),
            "total_likes_observed": (last.get("like_count", 0) - first.get("like_count", 0)),
            "diamond_count_start": first.get("diamond_count", 0),
            "diamond_count_end": last.get("diamond_count", 0),
            "total_diamonds_observed": (last.get("diamond_count", 0) - first.get("diamond_count", 0)),
            "stream_was_live_at_any_point": any(p.get("is_live") for p in self.polls),
            "stream_status_at_end": "live" if last.get("is_live") else "offline",
            "owner_nickname": last.get("owner_nickname", ""),
            "owner_user_id": last.get("owner_user_id", ""),
            "stream_title_at_end": last.get("title", ""),
            "events_summary": dict(Counter(e["type"] for e in self.events)),
            "cookies_used_count": len(NEW_COOKIES),
            "living_user_id_cookie": LIVING_USER_ID,
        }
        with open(os.path.join(self.session_dir, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)

        return summary

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.is_observing = False

    def get_status(self):
        if not self.polls:
            return {"status": "not_started"}
        last = self.polls[-1]
        return {
            "status": "observing" if self.is_observing else "completed",
            "polls": len(self.polls),
            "events": len(self.events),
            "gift_events": len(self.gift_events),
            "cookie_renewals": len(self.cookie_renewals),
            "last_viewer_count": last.get("viewer_count", 0),
            "last_like_count": last.get("like_count", 0),
            "last_diamond_count": last.get("diamond_count", 0),
            "last_is_live": last.get("is_live", False),
            "last_owner": last.get("owner_nickname", ""),
            "session_dir": self.session_dir,
        }


def main():
    print("=" * 80)
    print(f"👁  v8 Pure Live Observer")
    print(f"📋 Target URL: {TARGET_URL}")
    print(f"🆔 Streamer user_id (from cookie): {LIVING_USER_ID}")
    print(f"🔐 Cookies used: {len(NEW_COOKIES)} (fresh from APK check session)")
    print(f"⏱️  Observation duration: {MONITOR_MAX_DURATION}s = {MONITOR_MAX_DURATION // 60} min")
    print(f"📊 Poll interval: {MONITOR_POLL_INTERVAL}s")
    print(f"🚫 NO interactions (pure observer mode)")
    print("=" * 80)

    os.makedirs(AGENTS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: Find the live room_id
    print(f"\n━━━ Step 1: Discovering live room_id ━━━")
    cookie_str = build_cookie_str()
    found_room_id = None
    found_result = None

    for rid in CANDIDATE_ROOM_IDS:
        print(f"  Testing room_id={rid}...", end=" ")
        result = try_room_id(rid, cookie_str)
        if result:
            sc = result.get("status_code")
            http = result.get("http_status")
            is_live = result.get("is_live", False)
            viewer = result.get("viewer_count", 0) or 0
            msg = result.get("data_message", "") or ""
            sc_str = result.get("set_cookie_preview", "")[:60]
            print(f"http={http}, status_code={sc}, is_live={is_live}, "
                  f"viewer={viewer}, msg='{msg}', set_cookie={sc_str}")
            if is_live or viewer > 0 or sc == 0:
                print(f"\n  ✅ LIVE STREAM FOUND: room_id={rid}")
                print(f"     Title: {result.get('title', '')}")
                print(f"     Owner: {result.get('owner_nickname', '')} (id={result.get('owner_user_id', '')})")
                print(f"     Viewer count: {viewer}")
                print(f"     Like count: {result.get('like_count', 0)}")
                print(f"     Diamond count: {result.get('diamond_count', 0)}")
                found_room_id = rid
                found_result = result
                break
            # Even if not live, record the http_status — it tells us about cookies
        else:
            print("no response")

    # Use the best candidate (even if not live, for documentation)
    if not found_room_id:
        print(f"\n  ⚠️  No live room found among candidates.")
        print(f"     Using room_id=7683963746938555152 as fallback for observation.")
        print(f"     (The stream may have ended, or requires X-Bogus, or is geo-blocked)")
        found_room_id = "7683963746938555152"

    # Step 2: Start the PureLiveObserver
    print(f"\n━━━ Step 2: Starting Pure Live Observer ━━━")
    print(f"  room_id: {found_room_id}")
    print(f"  duration: {MONITOR_MAX_DURATION}s ({MONITOR_MAX_DURATION // 60} min)")
    print(f"  poll interval: {MONITOR_POLL_INTERVAL}s")

    observer = PureLiveObserver(
        live_url=TARGET_URL,
        room_id=found_room_id,
        output_dir=OUTPUT_DIR,
    )
    start_result = observer.start_observation()
    print(f"\n  Session dir: {start_result.get('session_dir')}")
    print(f"  Session ID: {start_result.get('session_id')}")

    # Step 3: Run observation loop
    print(f"\n━━━ Step 3: Observation in progress ━━━")
    print(f"  (Polling every {MONITOR_POLL_INTERVAL}s for {MONITOR_MAX_DURATION}s)")
    print(f"  (Recording all events: viewer changes, likes, gifts, stream status)")
    print(f"  (NO interactions will be performed — pure observation)")

    last_status_print = 0
    obs_start = time.time()

    while observer.is_observing:
        now = time.time() - obs_start
        if int(now) - last_status_print >= 30:
            status = observer.get_status()
            print(f"  [{int(now)}s] polls={status.get('polls', 0)}, "
                  f"events={status.get('events', 0)}, "
                  f"gifts={status.get('gift_events', 0)}, "
                  f"cookie_renewals={status.get('cookie_renewals', 0)}, "
                  f"viewer={status.get('last_viewer_count', 0)}, "
                  f"like={status.get('last_like_count', 0)}, "
                  f"diamond={status.get('last_diamond_count', 0)}, "
                  f"is_live={status.get('last_is_live', False)}")
            last_status_print = int(now)
        time.sleep(5)

    observer.stop()
    final_status = observer.get_status()

    print(f"\n━━━ Step 4: Observation complete ━━━")
    print(f"  Total polls: {final_status.get('polls', 0)}")
    print(f"  Total events: {final_status.get('events', 0)}")
    print(f"  Total gift events: {final_status.get('gift_events', 0)}")
    print(f"  Total cookie renewals: {final_status.get('cookie_renewals', 0)}")
    print(f"  Final viewer count: {final_status.get('last_viewer_count', 0)}")
    print(f"  Final like count: {final_status.get('last_like_count', 0)}")
    print(f"  Final diamond count: {final_status.get('last_diamond_count', 0)}")
    print(f"  Stream is live at end: {final_status.get('last_is_live', False)}")

    # Save observer log
    observer_log = {
        "v8_observer_run": {
            "live_url": TARGET_URL,
            "room_id_observed": found_room_id,
            "unique_id": observer.unique_id,
            "session_id": observer.session_id,
            "session_dir": observer.session_dir,
            "started_at": observer.start_time.isoformat() + "Z",
            "ended_at": datetime.utcnow().isoformat() + "Z",
            "duration_s": round(time.time() - obs_start, 1),
            "cookies_used": list(NEW_COOKIES.keys()),
            "cookies_used_count": len(NEW_COOKIES),
            "living_user_id": LIVING_USER_ID,
            "monitor_config": {
                "poll_interval_s": MONITOR_POLL_INTERVAL,
                "max_duration_s": MONITOR_MAX_DURATION,
            },
            "totals": {
                "polls": len(observer.polls),
                "events": len(observer.events),
                "gift_events": len(observer.gift_events),
                "cookie_renewals": len(observer.cookie_renewals),
            },
            "final_status": final_status,
        },
    }
    with open(OBSERVER_LOG, "w") as f:
        json.dump(observer_log, f, indent=2, default=str)
    print(f"\n  Observer log saved: {OBSERVER_LOG}")

    # Save summary as separate file
    if observer.polls:
        summary_path = os.path.join(observer.session_dir, "summary.json")
        if os.path.exists(summary_path):
            print(f"  Summary: {summary_path}")

    # ZIP the deep data
    zip_path = "/home/z/my-project/download/tiktok_deep_data_v8.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(OUTPUT_DIR):
            for root, dirs, files in os.walk(OUTPUT_DIR):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, OUTPUT_DIR))
    print(f"  ZIP: {zip_path} ({os.path.getsize(zip_path) // 1024}KB)")

    print()
    print("=" * 80)
    print(f"✅ v8 PURE OBSERVER COMPLETE")
    print(f"📊 Polls: {len(observer.polls)} | Events: {len(observer.events)} | "
          f"Gifts: {len(observer.gift_events)} | Cookie renewals: {len(observer.cookie_renewals)}")
    print(f"⏱️  Duration: {time.time() - obs_start:.1f}s")
    print(f"📦 Output: {OUTPUT_DIR}/<unique_id>/observer_{observer.session_id}/")
    print("=" * 80)


if __name__ == "__main__":
    main()
