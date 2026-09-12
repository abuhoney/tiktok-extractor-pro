# TikTok Extractor Pro — Target Data

This directory contains extracted TikTok target data for monitoring and interaction.

## Files

| File | Description |
|---|---|
| `dr_tiktok_profile.json` | Compact target profile — user_id, sec_uid, room_id, csrf_token, wid, nonce, ready_payloads |
| `dr_tiktok_live_fest2026.json` | Full extraction result (237 KB) — all fields from the extraction API |

## Target: @Dr.TiKToK (live_fest2026)

- **unique_id:** `live_fest2026`
- **nickname:** `@Dr.TiKToK`
- **user_id:** `7123696644457743366`
- **sec_uid:** `MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT`
- **room_id:** `7683963746938555152`
- **stream_id:** `1849444191476121704`
- **follower_count:** 476
- **following_count:** 868
- **signature:** "الهدف 10k متابع 🫣 تابعني ربي يحقق لك كل احلامك ويجعل كل أيامك سعادة وهناء 🤲💓."
- **interaction_ready:** `true`
- **credentials:** csrf_token + wid + nonce from PRELOADED_ACCOUNTS (fw__qg)

## Usage

These JSON files can be loaded by the extractor for:
1. Quick reference to the target's IDs and credentials
2. Pre-built interaction payloads (ready_payloads)
3. Monitoring changes in follower_count, viewer_count, etc.
4. Future automation (auto-like, auto-follow when the target goes live)

## Interaction API

```bash
# Follow Dr.TiKToK
curl -X POST https://tiktok-extractor-pro.onrender.com/api/interact \
  -H "Content-Type: application/json" \
  -d '{"action":"follow_user","csrf_token":"uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk","wid":"7658084697712657940","nonce":"6vaarBpVsrfBDm_nl5DbJ","user_id":"7123696644457743366","sec_uid":"MS4wLjABAAAArwnWOJMKRjJ0LdfJTV4iaG8D45YJaja624wz9v_8t25W0M7EbYKXx1Tz_MYPJfPT","unique_id":"live_fest2026"}'

# Like Dr.TiKToK's live
curl -X POST https://tiktok-extractor-pro.onrender.com/api/interact \
  -H "Content-Type: application/json" \
  -d '{"action":"send_like","csrf_token":"uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk","wid":"7658084697712657940","nonce":"6vaarBpVsrfBDm_nl5DbJ","room_id":"7683963746938555152","user_id":"7123696644457743366","count":1,"unique_id":"live_fest2026"}'

# Like Dr.TiKToK's video
curl -X POST https://tiktok-extractor-pro.onrender.com/api/interact \
  -H "Content-Type: application/json" \
  -d '{"action":"like_video","csrf_token":"uQUYBmKB-84NfiEAC1JSNQg3hAsfbjS3Xrpk","wid":"7658084697712657940","nonce":"6vaarBpVsrfBDm_nl5DbJ","video_id":"7683963746938555152","unique_id":"live_fest2026"}'
```
