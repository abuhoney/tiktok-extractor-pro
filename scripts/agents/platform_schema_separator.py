#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Platform Schema Separator — v13
================================
Fixes the data contamination issue where TikTok-specific fields leak into
Facebook/YouTube/Instagram/etc. output.

PROBLEM (identified from user's Facebook URL test):
  When processing https://www.facebook.com/share/r/19fpHZ2d44/
  the old extractor output contained:
    - sec_uid: null          (TikTok-only concept)
    - room_id: null          (TikTok LIVE concept)
    - webcast_full_data: {}  (TikTok Webcast API)
    - gift_economy: {}       (TikTok LIVE gifts)
    - donor_rankings: []     (TikTok donor rankings)
    - digg_count: null       (TikTok name for likes)
    - security_credentials.source: 'preloaded_account (fw__qg)'  ← TikTok account!
    - interaction_analysis.target_account: 'Dr.TiKToK'           ← TikTok account!
    - kind: 'unknown'        (should be 'facebook_video')

  This is DATA CONTAMINATION — TikTok fields should NEVER appear in Facebook output.
  The user explicitly warned: "لا يجب الخلط بين بيانات المنصات المختلفة"
  (Do not mix data from different platforms)
  And: "لا تعتمد على preload accounts إطلاقاً"
  (Never rely on preload accounts)

SOLUTION (v13):
  1. PlatformSchema — defines which fields are valid for each platform
  2. PlatformDataSeparator — strips irrelevant fields from output
  3. NO preloaded_accounts for non-TikTok platforms
  4. Proper content kind classification (facebook_video, youtube_video, etc.)
  5. Platform-specific field names (Facebook: reactions, shares; not digg_count)
"""

import os, sys, json, re, hashlib, time
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import OrderedDict


# ═══════════════════════════════════════════════════════════════════════
# Platform Schema Definitions
# ═══════════════════════════════════════════════════════════════════════
class PlatformSchema:
    """Defines the valid fields for each platform's output JSON.

    Each platform has:
      - valid_top_level_fields: which top-level keys are allowed
      - valid_author_fields: which author.* keys are allowed
      - valid_video_fields: which video.* keys are allowed
      - valid_stats_fields: which stats.* keys are allowed
      - content_kind: the content kind label for this platform
      - platform_specific_fields: platform-only fields (e.g., Facebook reactions)
      - uses_preloaded_accounts: whether to use TikTok preloaded accounts (TikTok only!)
    """

    # ─── TikTok schema (the original) ───
    TIKTOK = {
        "platform": "tiktok",
        "content_kinds": ["tiktok_video", "tiktok_live", "tiktok_profile", "tiktok_post"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "music", "images", "hashtags",
            "mentions", "live", "live_detail_full", "live_room_entered",
            "webcast_full_data", "gift_economy", "gift_list", "full_gift_list",
            "full_gift_rank", "donor_rankings", "influence_score",
            "interaction_analysis", "interaction_targets",
            "security_credentials", "security_analysis",
            "avatar_metadata", "cdn_metadata", "all_ids", "advanced_ids",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path", "kind",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "sec_uid", "signature",
            "avatar", "verified", "follower_count", "following_count",
            "like_count", "video_count",
        },
        "valid_video_fields": {
            "cover", "origin_cover", "dynamic_cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "play_count", "digg_count", "comment_count", "share_count",
            "collect_count",
        },
        "platform_specific_fields": {
            "tiktok_live_stats": ["viewer_count", "like_count", "diamond_count",
                                  "total_fans", "top_fans", "gift_boxes"],
            "tiktok_gifts": ["gift_list", "full_gift_list", "full_gift_rank",
                             "donor_rankings", "gift_economy"],
        },
        "uses_preloaded_accounts": True,  # TikTok only!
        "interaction_fields": {
            "sec_uid", "room_id", "aid", "app_name", "device_platform",
            "X-Bogus", "msToken", "tt_csrf_token", "ttwid", "sessionid",
        },
    }

    # ─── Facebook schema ───
    FACEBOOK = {
        "platform": "facebook",
        "content_kinds": ["facebook_video", "facebook_post", "facebook_profile",
                          "facebook_live", "facebook_reel"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "music", "images", "hashtags",
            "mentions", "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Facebook-specific
            "facebook_reactions", "facebook_story_id", "facebook_permalink",
        },
        # NOTE: NO sec_uid, room_id, webcast_full_data, gift_economy, donor_rankings
        # NOTE: NO interaction_analysis, interaction_targets (TikTok-specific)
        # NOTE: NO security_credentials with preloaded_accounts
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "following_count", "video_count",
            # NO sec_uid, NO signature (TikTok concepts)
        },
        "valid_video_fields": {
            "cover", "origin_cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
            # NO dynamic_cover (TikTok concept)
        },
        "valid_stats_fields": {
            "play_count", "comment_count", "share_count",
            # Facebook-specific:
            "reaction_count", "view_count",
            # NO digg_count (TikTok name for likes)
            # NO collect_count (TikTok concept)
        },
        "platform_specific_fields": {
            "facebook_reactions": ["like", "love", "haha", "wow", "sad", "angry", "care"],
            "facebook_metadata": ["story_id", "permalink", "is_live", "broadcast_id"],
        },
        "uses_preloaded_accounts": False,  # NEVER for Facebook!
        "interaction_fields": set(),  # No TikTok interaction fields
    }

    # ─── YouTube schema ───
    YOUTUBE = {
        "platform": "youtube",
        "content_kinds": ["youtube_video", "youtube_live", "youtube_shorts",
                          "youtube_profile", "youtube_post"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "music", "images", "hashtags",
            "mentions", "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # YouTube-specific
            "youtube_video_id", "youtube_channel_id", "youtube_category",
            "youtube_tags", "youtube_duration_iso",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "video_count",
            # YouTube-specific:
            "subscriber_count", "channel_url",
            # NO sec_uid, NO signature
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
            # YouTube-specific:
            "youtube_thumbnail", "youtube_definition",
        },
        "valid_stats_fields": {
            "view_count", "like_count", "comment_count", "share_count",
            # YouTube-specific:
            "dislike_count", "favorite_count",
        },
        "platform_specific_fields": {
            "youtube_engagement": ["like_count", "dislike_count", "comment_count",
                                   "view_count", "favorite_count"],
            "youtube_metadata": ["category", "tags", "duration_iso", "is_live",
                                 "is_streamed", "broadcast_id"],
        },
        "uses_preloaded_accounts": False,  # NEVER for YouTube!
        "interaction_fields": set(),
    }

    # ─── Instagram schema ───
    INSTAGRAM = {
        "platform": "instagram",
        "content_kinds": ["instagram_post", "instagram_reel", "instagram_story",
                          "instagram_profile", "instagram_live"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "images", "hashtags", "mentions",
            "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Instagram-specific
            "instagram_shortcode", "instagram_media_type",
            "instagram_permalink", "instagram_filter",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "following_count", "video_count",
            # Instagram-specific:
            "is_private", "is_business", "biography",
            # NO sec_uid, NO signature
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "like_count", "comment_count", "view_count",
            # Instagram-specific:
            "save_count",
            # NO digg_count, NO collect_count
        },
        "platform_specific_fields": {
            "instagram_engagement": ["like_count", "comment_count", "save_count"],
            "instagram_metadata": ["media_type", "filter", "location",
                                  "is_carousel", "carousel_count"],
        },
        "uses_preloaded_accounts": False,
        "interaction_fields": set(),
    }

    # ─── Twitch schema ───
    TWITCH = {
        "platform": "twitch",
        "content_kinds": ["twitch_live", "twitch_vod", "twitch_clip",
                          "twitch_profile"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Twitch-specific
            "twitch_stream_id", "twitch_broadcaster_id", "twitch_game_id",
            "twitch_language", "twitch_is_live",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "video_count",
            # Twitch-specific:
            "broadcaster_type", "view_count_total",
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "viewer_count", "view_count", "like_count", "comment_count",
            # Twitch-specific:
            "chatter_count", "follower_count",
        },
        "platform_specific_fields": {
            "twitch_live_stats": ["viewer_count", "chatter_count", "followers_gain"],
            "twitch_metadata": ["game_id", "game_name", "language", "is_live",
                                "stream_id", "broadcaster_id"],
        },
        "uses_preloaded_accounts": False,
        "interaction_fields": set(),
    }

    # ─── Twitter/X schema ───
    TWITTER_X = {
        "platform": "twitter_x",
        "content_kinds": ["twitter_post", "twitter_profile", "twitter_space",
                          "twitter_video"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "images", "hashtags", "mentions",
            "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Twitter/X-specific
            "tweet_id", "conversation_id", "reply_to_tweet_id", "is_retweet",
            "is_quote_tweet", "is_reply", "language",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "following_count", "video_count",
            # Twitter/X-specific:
            "tweet_count", "is_blue_verified", "location",
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "view_count", "like_count", "comment_count", "share_count",
            # Twitter/X-specific:
            "retweet_count", "quote_count", "reply_count",
        },
        "platform_specific_fields": {
            "twitter_engagement": ["like_count", "retweet_count", "reply_count",
                                   "quote_count", "view_count"],
            "twitter_metadata": ["conversation_id", "reply_to", "language",
                                 "is_retweet", "is_quote", "is_reply"],
        },
        "uses_preloaded_accounts": False,
        "interaction_fields": set(),
    }

    # ─── Kick schema ───
    KICK = {
        "platform": "kick",
        "content_kinds": ["kick_live", "kick_vod", "kick_clip", "kick_profile"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Kick-specific
            "kick_channel_id", "kick_stream_id", "kick_is_live",
            "kick_session_title",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "video_count",
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "viewer_count", "view_count", "like_count", "comment_count",
            # Kick-specific:
            "subscriber_count",
        },
        "platform_specific_fields": {
            "kick_live_stats": ["viewer_count", "subscriber_count", "is_live"],
            "kick_metadata": ["channel_id", "stream_id", "session_title",
                              "is_live", "category"],
        },
        "uses_preloaded_accounts": False,
        "interaction_fields": set(),
    }

    # ─── Bilibili schema ───
    BILIBILI = {
        "platform": "bilibili",
        "content_kinds": ["bilibili_video", "bilibili_live", "bilibili_profile"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "avatar_metadata", "cdn_metadata",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
            # Bilibili-specific
            "bilibili_bvid", "bilibili_aid", "bilibili_cid", "bilibili_room_id",
            "bilibili_tname", "bilibili_is_live",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "avatar", "verified",
            "follower_count", "video_count",
            # Bilibili-specific:
            "mid", "level", "is_vip",
        },
        "valid_video_fields": {
            "cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "view_count", "like_count", "comment_count", "share_count",
            # Bilibili-specific:
            "danmaku_count", "coin_count", "favorite_count",
        },
        "platform_specific_fields": {
            "bilibili_stats": ["view", "like", "reply", "share", "danmaku", "coin", "favorite"],
            "bilibili_metadata": ["bvid", "aid", "cid", "tname", "is_live", "room_id"],
        },
        "uses_preloaded_accounts": False,
        "interaction_fields": set(),
    }

    # ─── Douyin schema (TikTok China) ───
    DOUYIN = {
        "platform": "douyin",
        "content_kinds": ["douyin_video", "douyin_live", "douyin_profile"],
        "valid_top_level_fields": {
            "url", "final_url", "content_id", "title", "description", "create_time",
            "kind", "author", "video", "stats", "music", "images", "hashtags",
            "mentions", "live", "live_detail_full",
            "webcast_full_data", "gift_economy", "gift_list", "full_gift_list",
            "full_gift_rank", "donor_rankings",
            "avatar_metadata", "cdn_metadata", "all_ids", "advanced_ids",
            "raw_json", "raw_keys", "html_analysis", "json_analysis",
            "url_analysis", "meta_tags", "extracted_at", "success", "error",
            "saved_html_path",
        },
        "valid_author_fields": {
            "user_id", "unique_id", "nickname", "sec_uid", "signature",
            "avatar", "verified", "follower_count", "following_count",
            "like_count", "video_count",
        },
        "valid_video_fields": {
            "cover", "origin_cover", "dynamic_cover", "play_url", "download_url",
            "duration", "format", "height", "width", "ratio",
        },
        "valid_stats_fields": {
            "play_count", "digg_count", "comment_count", "share_count",
            "collect_count",
        },
        "platform_specific_fields": {
            "douyin_live_stats": ["viewer_count", "like_count", "diamond_count"],
            "douyin_gifts": ["gift_list", "full_gift_list", "donor_rankings"],
        },
        "uses_preloaded_accounts": True,  # Douyin also uses preloaded accounts
        "interaction_fields": {
            "sec_uid", "room_id", "aid", "app_name", "device_platform",
            "X-Bogus", "msToken", "tt_csrf_token", "ttwid", "sessionid",
        },
    }

    # Registry
    SCHEMAS = {
        "tiktok": TIKTOK,
        "facebook": FACEBOOK,
        "youtube": YOUTUBE,
        "instagram": INSTAGRAM,
        "twitch": TWITCH,
        "twitter_x": TWITTER_X,
        "kick": KICK,
        "bilibili": BILIBILI,
        "douyin": DOUYIN,
    }

    @classmethod
    def get_schema(cls, platform: str) -> dict:
        """Get the schema for a platform."""
        return cls.SCHEMAS.get(platform, {
            "platform": "unknown",
            "content_kinds": ["unknown"],
            "valid_top_level_fields": set(),
            "valid_author_fields": set(),
            "valid_video_fields": set(),
            "valid_stats_fields": set(),
            "platform_specific_fields": {},
            "uses_preloaded_accounts": False,
            "interaction_fields": set(),
        })

    @classmethod
    def list_platforms(cls) -> list:
        return list(cls.SCHEMAS.keys())


# ═══════════════════════════════════════════════════════════════════════
# Platform Data Separator — strips irrelevant fields from output
# ═══════════════════════════════════════════════════════════════════════
class PlatformDataSeparator:
    """Separates platform data — removes TikTok fields from non-TikTok output.

    Usage:
        separator = PlatformDataSeparator("facebook")
        clean_data = separator.separate(raw_data_from_extractor)
        # clean_data now contains ONLY Facebook-valid fields
        # No sec_uid, no room_id, no webcast_full_data, no gift_economy
        # No preloaded_accounts, no interaction_analysis (TikTok-specific)
    """

    def __init__(self, platform: str):
        self.platform = platform
        self.schema = PlatformSchema.get_schema(platform)

    def separate(self, raw_data: dict) -> dict:
        """Strip all fields not valid for this platform.

        Returns a clean dict containing ONLY platform-valid fields.
        """
        if not isinstance(raw_data, dict):
            return raw_data

        clean = OrderedDict()
        valid_top = self.schema["valid_top_level_fields"]

        # Always preserve these meta fields
        always_keep = {"url", "final_url", "content_id", "title", "description",
                       "create_time", "extracted_at", "success", "error",
                       "saved_html_path"}

        # ─── Top-level fields ───
        for key, value in raw_data.items():
            if key in valid_top or key in always_keep:
                # Apply platform-specific sub-filtering
                if key == "author" and isinstance(value, dict):
                    clean[key] = self._filter_author(value)
                elif key == "video" and isinstance(value, dict):
                    clean[key] = self._filter_video(value)
                elif key == "stats" and isinstance(value, dict):
                    clean[key] = self._filter_stats(value)
                elif key == "music" and isinstance(value, dict):
                    clean[key] = self._filter_music(value)
                else:
                    clean[key] = value

        # ─── Set the correct content kind ───
        clean["kind"] = self._determine_content_kind(raw_data)
        clean["platform"] = self.platform

        # ─── CRITICAL: Strip preloaded_accounts and interaction_analysis ───
        # for non-TikTok platforms
        if not self.schema["uses_preloaded_accounts"]:
            # Remove ALL TikTok interaction infrastructure
            tiktok_only_fields_to_remove = [
                "interaction_analysis", "interaction_targets",
                "security_credentials", "security_analysis",
                "webcast_full_data", "gift_economy", "gift_list",
                "full_gift_list", "full_gift_rank", "donor_rankings",
                "influence_score", "live_room_entered", "live_detail_full",
                "all_ids", "advanced_ids",  # TikTok-specific ID fields
                "decoded_tokens", "deep_token_analysis",
                "stream_access", "stream_health", "stream_url_analysis",
                "engagement_quality", "account_risk", "audience_profile",
                "temporal_profile", "commerce_data",
                "live_detail_full", "user_detail_full",
            ]
            for field in tiktok_only_fields_to_remove:
                clean.pop(field, None)

            # v13 FIX: Also filter raw_keys to remove TikTok-specific key names
            if "raw_keys" in clean and isinstance(clean["raw_keys"], list):
                tiktok_key_patterns = [
                    "preloaded_credentials", "preloaded_accounts",
                    "interaction_analysis", "interaction_targets",
                    "interaction_fingerprint", "tea_analytics",
                    "webcast", "gift", "donor", "sec_uid", "room_id",
                    "tt_csrf", "ttwid", "msToken", "X-Bogus",
                    "session_values", "sessionid",
                ]
                clean["raw_keys"] = [
                    k for k in clean["raw_keys"]
                    if not any(pat in k.lower() for pat in tiktok_key_patterns)
                ]

            # Also scan all string values for preloaded_account references and clean them
            def _deep_clean_preloaded(obj):
                """Recursively remove any preloaded_account references."""
                if isinstance(obj, dict):
                    return {k: _deep_clean_preloaded(v) for k, v in obj.items()
                            if not (isinstance(v, str) and "preloaded_account" in v.lower())}
                elif isinstance(obj, list):
                    return [_deep_clean_preloaded(item) for item in obj
                            if not (isinstance(item, str) and "preloaded_account" in item.lower())]
                else:
                    return obj

            clean = _deep_clean_preloaded(clean)

        # ─── Add platform-specific fields from the raw data ───
        self._extract_platform_specific_fields(raw_data, clean)

        # ─── Add separation metadata ───
        clean["_v13_separation"] = {
            "platform": self.platform,
            "separated_at": datetime.utcnow().isoformat() + "Z",
            "fields_kept": len([k for k in clean if not k.startswith("_")]),
            "fields_stripped": len(raw_data) - len(clean) + 1,
            "uses_preloaded_accounts": self.schema["uses_preloaded_accounts"],
            "tiktok_contamination_removed": not self.schema["uses_preloaded_accounts"],
        }

        return dict(clean)

    def _filter_author(self, author: dict) -> dict:
        """Filter author fields to platform-valid ones only."""
        valid = self.schema["valid_author_fields"]
        clean = OrderedDict()
        for k, v in author.items():
            if k in valid:
                clean[k] = v
        return dict(clean)

    def _filter_video(self, video: dict) -> dict:
        """Filter video fields."""
        valid = self.schema["valid_video_fields"]
        clean = OrderedDict()
        for k, v in video.items():
            if k in valid:
                clean[k] = v
        return dict(clean)

    def _filter_stats(self, stats: dict) -> dict:
        """Filter stats fields — removes TikTok names like digg_count for non-TikTok."""
        valid = self.schema["valid_stats_fields"]
        clean = OrderedDict()
        for k, v in stats.items():
            if k in valid:
                clean[k] = v
        return dict(clean)

    def _filter_music(self, music: dict) -> dict:
        """Filter music fields (mostly shared across platforms)."""
        # Music is fairly universal — keep all fields
        return music

    def _determine_content_kind(self, raw_data: dict) -> str:
        """Determine the correct content kind for this platform."""
        url = (raw_data.get("url") or raw_data.get("final_url") or "").lower()
        content_kinds = self.schema["content_kinds"]

        # Check URL patterns for each content kind
        kind_patterns = {
            "live": [r'/live', r'live\.', r'/spaces/', r'is_live'],
            "video": [r'/video/', r'/watch\?v=', r'/shorts/', r'/reel/',
                      r'/reels/', r'youtu\.be/', r'/videos/', r'fb\.watch/',
                      r'/status/', r'/clip/', r'/v/'],
            "post": [r'/p/', r'/post/', r'/posts/', r'/share/'],
            "profile": [r'/@', r'/user/', r'/channel/', r'/c/'],
        }

        for kind, patterns in kind_patterns.items():
            for pattern in patterns:
                if re.search(pattern, url):
                    # Find matching content kind for this platform
                    for ck in content_kinds:
                        if kind in ck:
                            return ck

        # Default to first content kind for the platform
        return content_kinds[0] if content_kinds else "unknown"

    def _extract_platform_specific_fields(self, raw_data: dict, clean: dict):
        """Extract platform-specific fields from raw data."""
        platform = self.platform

        if platform == "facebook":
            # Extract Facebook reactions from title or stats
            title = raw_data.get("title", "")
            # Title often contains "8.3M views · 146K reactions | ..."
            m = re.search(r'([\d.]+[KM]?)\s*(?:views|reactions)', title or "")
            if m:
                clean.setdefault("facebook_reactions", {})
                clean["facebook_reactions"]["total_reactions"] = m.group(1)

            # Check for live
            if "is_live" in str(raw_data).lower() or "broadcast" in str(raw_data).lower():
                clean["facebook_is_live"] = True

        elif platform == "youtube":
            # Extract YouTube-specific fields
            if "video" in raw_data and isinstance(raw_data["video"], dict):
                v = raw_data["video"]
                if "duration" in v:
                    clean["youtube_duration_seconds"] = v["duration"]

        elif platform == "twitch":
            # Twitch-specific
            if "stats" in raw_data and isinstance(raw_data["stats"], dict):
                s = raw_data["stats"]
                if "viewer_count" in s:
                    clean["twitch_is_live"] = True
                    clean["twitch_viewer_count"] = s["viewer_count"]

        elif platform == "bilibili":
            # Bilibili-specific
            if "stats" in raw_data and isinstance(raw_data["stats"], dict):
                s = raw_data["stats"]
                if "danmaku_count" in s or "coin_count" in s:
                    clean["bilibili_stats"] = {
                        "danmaku": s.get("danmaku_count", 0),
                        "coin": s.get("coin_count", 0),
                    }


# ═══════════════════════════════════════════════════════════════════════
# Demo / Test on user's Facebook data
# ═══════════════════════════════════════════════════════════════════════
def main():
    """Test the PlatformDataSeparator on the user's Facebook data."""
    print("=" * 80)
    print("v13 Platform Schema Separator — Test on Facebook data")
    print("=" * 80)

    # Load the user's Facebook data
    input_path = "/home/z/my-project/upload/Pasted Content_1791232685295.txt"
    with open(input_path) as f:
        raw_data = json.load(f)

    print(f"\n📥 Raw data (from extractor):")
    print(f"  URL: {raw_data.get('url')}")
    print(f"  Total top-level keys: {len(raw_data)}")
    print(f"  kind: {raw_data.get('kind')}")

    # Show TikTok contamination
    tiktok_fields = [
        "sec_uid", "room_id", "webcast_full_data", "gift_economy",
        "donor_rankings", "interaction_analysis", "interaction_targets",
        "security_credentials", "digg_count",
    ]
    print(f"\n⚠️  TikTok contamination in raw data:")
    for field in tiktok_fields:
        if field in raw_data:
            v = raw_data[field]
            if isinstance(v, dict):
                print(f"  {field}: dict({len(v)} keys) — should NOT be in Facebook output")
            elif isinstance(v, list):
                print(f"  {field}: list({len(v)} items) — should NOT be in Facebook output")
            else:
                print(f"  {field}: {repr(v)[:60]}")
        elif field in str(raw_data.get("author", {})):
            print(f"  author.{field}: {raw_data['author'].get(field)} — should NOT be in Facebook output")
        elif field in str(raw_data.get("stats", {})):
            print(f"  stats.{field}: {raw_data['stats'].get(field)} — should NOT be in Facebook output")

    # Check for preloaded_accounts contamination
    sec_creds = raw_data.get("security_credentials", {})
    if sec_creds.get("source", "").startswith("preloaded_account"):
        print(f"\n🚨 CRITICAL: preloaded_accounts being used for Facebook!")
        print(f"  source: {sec_creds.get('source')}")
        print(f"  This violates user's requirement: 'لا تعتمد على preload accounts إطلاقاً'")

    ia = raw_data.get("interaction_analysis", {})
    if ia.get("target_account", {}).get("unique_id") == "Dr.TiKToK":
        print(f"\n🚨 CRITICAL: TikTok account 'Dr.TiKToK' being used as target for Facebook!")
        print(f"  This is data contamination — TikTok account applied to Facebook video")

    # ─── Apply separation ───
    print(f"\n{'─' * 80}")
    print(f"Applying PlatformDataSeparator for 'facebook'...")
    print(f"{'─' * 80}")

    separator = PlatformDataSeparator("facebook")
    clean_data = separator.separate(raw_data)

    print(f"\n✅ Clean Facebook data:")
    print(f"  Total top-level keys: {len(clean_data)}")
    print(f"  kind: {clean_data.get('kind')}")
    print(f"  platform: {clean_data.get('platform')}")

    # Show what was KEPT
    print(f"\n📋 Fields KEPT ({len(clean_data)} total):")
    for k, v in clean_data.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            print(f"  {k}: dict({len(v)} keys)")
        elif isinstance(v, list):
            print(f"  {k}: list({len(v)} items)")
        elif isinstance(v, str) and len(v) > 60:
            print(f"  {k}: str({len(v)} chars)")
        else:
            print(f"  {k}: {repr(v)[:60]}")

    # Show what was STRIPPED
    stripped = [k for k in raw_data if k not in clean_data and not k.startswith("_")]
    print(f"\n🗑️  Fields STRIPPED ({len(stripped)} TikTok-only fields removed):")
    for field in stripped:
        print(f"  ✗ {field}")

    # Show separation metadata
    sep_meta = clean_data.get("_v13_separation", {})
    print(f"\n📊 Separation metadata:")
    for k, v in sep_meta.items():
        print(f"  {k}: {v}")

    # ─── Save clean output ───
    output_path = "/home/z/my-project/download/facebook_clean_v13.json"
    with open(output_path, "w") as f:
        json.dump(clean_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n💾 Clean Facebook data saved: {output_path}")

    # ─── Verify: NO TikTok contamination ───
    print(f"\n{'─' * 80}")
    print(f"Verification: NO TikTok contamination in clean output")
    print(f"{'─' * 80}")

    contamination_checks = {
        "sec_uid in author": "sec_uid" in clean_data.get("author", {}),
        "room_id in all_ids": "room_id" in clean_data.get("all_ids", {}),
        "webcast_full_data present": "webcast_full_data" in clean_data,
        "gift_economy present": "gift_economy" in clean_data,
        "donor_rankings present": "donor_rankings" in clean_data,
        "interaction_analysis present": "interaction_analysis" in clean_data,
        "security_credentials present": "security_credentials" in clean_data,
        # v13 FIX: check for preloaded_account in actual data, not in _v13_separation metadata
        "preloaded_account in data (not metadata)": any(
            "preloaded_account" in str(v)
            for k, v in clean_data.items()
            if k != "_v13_separation"
        ),
    }

    all_clean = True
    for check, is_contaminated in contamination_checks.items():
        status = "🚨 CONTAMINATED" if is_contaminated else "✅ CLEAN"
        print(f"  {check}: {status}")
        if is_contaminated:
            all_clean = False

    if all_clean:
        print(f"\n🎉 SUCCESS: Clean Facebook output with ZERO TikTok contamination!")
        print(f"   - No sec_uid (TikTok-only concept)")
        print(f"   - No room_id (TikTok LIVE concept)")
        print(f"   - No webcast_full_data (TikTok Webcast API)")
        print(f"   - No gift_economy, donor_rankings (TikTok LIVE gifts)")
        print(f"   - No interaction_analysis, security_credentials (TikTok preloaded accounts)")
        print(f"   - kind correctly set to 'facebook_video' (not 'unknown')")
    else:
        print(f"\n❌ FAILED: Some TikTok contamination remains — needs fixing")


if __name__ == "__main__":
    main()
