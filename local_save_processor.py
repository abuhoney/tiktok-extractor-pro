#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
local_save_processor.py
=======================
Local-save processor that runs during extraction to save ALL artifacts
locally on the device, so all download buttons work without backend access.

Saves to:
  /tmp/tiktok_local_cache/<unique_id>/
    ├── extraction_result.json     (full extraction result)
    ├── session_values.json        (34 session values)
    ├── mssdk_analysis.json        (8-phase MSSDK analysis)
    ├── webmssdk.js                (downloaded from CDN)
    ├── all_ids.json               (extracted IDs only)
    ├── security_credentials.json  (csrf, wid, nonce, etc.)
    ├── stream_access.json         (all signed stream URLs)
    ├── interaction_payloads.json  (ready-to-use POST payloads)
    ├── images/                    (avatars, covers)
    └── metadata.json              (summary + paths)

This module is called from extractor.py during the enrichment pipeline.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


LOCAL_CACHE_ROOT = Path(os.environ.get("TIKTOK_LOCAL_CACHE", "/tmp/tiktok_local_cache"))
WEBMSSDK_CDN_URL = (
    "https://sf16-website-login.neutral.ttwstatic.com/obj/"
    "tiktok_web_login_static/webmssdk/1.0.0.417/webmssdk.js"
)


def _safe_uid(uid: str) -> str:
    """Sanitize a unique_id for use as a directory name."""
    return re.sub(r'[^a-zA-Z0-9_\.\-]', '_', str(uid or "unknown"))


def get_user_cache_dir(unique_id: str) -> Path:
    """Get the local cache directory for a specific user."""
    safe = _safe_uid(unique_id)
    user_dir = LOCAL_CACHE_ROOT / safe
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "images").mkdir(exist_ok=True)
    return user_dir


def save_extraction_local(result, original_url: str) -> Dict[str, Any]:
    """Save the full extraction result + all artifacts locally.

    Args:
        result: ExtractionResult object from extractor.extract()
        original_url: the URL that was extracted

    Returns:
        dict of saved file paths + summary
    """
    try:
        result_dict = result.to_dict() if hasattr(result, 'to_dict') else dict(result)
    except Exception:
        result_dict = {"error": "could not serialize result"}

    unique_id = (
        result_dict.get("author", {}).get("unique_id")
        or result_dict.get("all_ids", {}).get("unique_id")
        or "unknown"
    )
    user_dir = get_user_cache_dir(unique_id)
    saved_files: Dict[str, str] = {}
    summary = {
        "unique_id": unique_id,
        "url": original_url,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }

    # 1. Full extraction result
    extraction_path = user_dir / "extraction_result.json"
    try:
        extraction_path.write_text(json.dumps(result_dict, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["extraction_result"] = str(extraction_path)
    except Exception as e:
        saved_files["extraction_result_error"] = str(e)

    # 2. all_ids (subset for quick access)
    all_ids = result_dict.get("all_ids", {})
    if all_ids:
        path = user_dir / "all_ids.json"
        path.write_text(json.dumps(all_ids, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["all_ids"] = str(path)

    # 3. security_credentials
    sec_creds = result_dict.get("security_credentials", {})
    if sec_creds:
        path = user_dir / "security_credentials.json"
        path.write_text(json.dumps(sec_creds, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["security_credentials"] = str(path)

    # 4. stream_access (signed stream URLs)
    stream_access = result_dict.get("stream_access", {})
    if stream_access and isinstance(stream_access, dict):
        path = user_dir / "stream_access.json"
        path.write_text(json.dumps(stream_access, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["stream_access"] = str(path)

    # 5. interaction_analysis (ready-to-use payloads)
    interaction = result_dict.get("interaction_analysis", {})
    if interaction and isinstance(interaction, dict):
        path = user_dir / "interaction_payloads.json"
        path.write_text(json.dumps(interaction, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["interaction_payloads"] = str(path)

    # 6. deep_token_analysis
    deep_tokens = result_dict.get("deep_token_analysis", {})
    if deep_tokens:
        path = user_dir / "deep_token_analysis.json"
        path.write_text(json.dumps(deep_tokens, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        saved_files["deep_token_analysis"] = str(path)

    # 7. session_values.json (from session_values_path if present)
    sv_path = sec_creds.get("session_values_path") if sec_creds else None
    if sv_path and Path(sv_path).exists():
        # Copy / link to local cache
        local_sv = user_dir / "session_values.json"
        try:
            local_sv.write_text(Path(sv_path).read_text(encoding="utf-8"), encoding="utf-8")
            saved_files["session_values"] = str(local_sv)
        except Exception:
            pass

    # 8. Download webmssdk.js from CDN
    webmssdk_path = user_dir / "webmssdk.js"
    if not webmssdk_path.exists() or webmssdk_path.stat().st_size < 1000:
        try:
            r = requests.get(WEBMSSDK_CDN_URL, timeout=30, verify=False)
            if r.status_code == 200 and len(r.text) > 1000:
                webmssdk_path.write_text(r.text, encoding="utf-8")
                saved_files["webmssdk"] = str(webmssdk_path)
        except Exception as e:
            saved_files["webmssdk_error"] = str(e)
    else:
        saved_files["webmssdk"] = str(webmssdk_path)

    # 9. Download avatar + cover images
    images_dir = user_dir / "images"
    images_dir.mkdir(exist_ok=True)
    downloaded_images: List[str] = []

    # Author avatar
    author = result_dict.get("author", {})
    avatar_url = author.get("avatar") or author.get("avatar_url")
    if avatar_url:
        try:
            img_path = images_dir / "avatar.jpg"
            r = requests.get(avatar_url, timeout=15, verify=False)
            if r.status_code == 200:
                img_path.write_bytes(r.content)
                downloaded_images.append(str(img_path))
        except Exception:
            pass

    # Cover from video
    video = result_dict.get("video", {})
    cover_url = video.get("cover_url") or video.get("thumbnail")
    if cover_url:
        try:
            img_path = images_dir / "cover.jpg"
            r = requests.get(cover_url, timeout=15, verify=False)
            if r.status_code == 200:
                img_path.write_bytes(r.content)
                downloaded_images.append(str(img_path))
        except Exception:
            pass

    if downloaded_images:
        saved_files["images"] = downloaded_images

    # 10. metadata.json (summary + all paths)
    summary["files"] = saved_files
    summary["total_files"] = sum(1 for v in saved_files.values() if isinstance(v, str))
    summary["cache_dir"] = str(user_dir)
    metadata_path = user_dir / "metadata.json"
    metadata_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    saved_files["metadata"] = str(metadata_path)

    return {
        "success": True,
        "unique_id": unique_id,
        "cache_dir": str(user_dir),
        "saved_files": saved_files,
        "total_files": summary["total_files"],
        "metadata_path": str(metadata_path),
    }


def list_local_cache(unique_id: Optional[str] = None) -> Dict[str, Any]:
    """List all locally-cached extractions.

    Args:
        unique_id: if provided, returns details for that user only

    Returns:
        dict with list of cached extractions or details for one user
    """
    if not LOCAL_CACHE_ROOT.exists():
        return {"success": True, "cached_users": [], "total": 0}

    if unique_id:
        user_dir = get_user_cache_dir(unique_id)
        metadata_path = user_dir / "metadata.json"
        if not metadata_path.exists():
            return {"success": False, "error": f"No cache for user '{unique_id}'"}
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        return {"success": True, "user": unique_id, "metadata": meta}

    users = []
    for entry in LOCAL_CACHE_ROOT.iterdir():
        if entry.is_dir():
            meta_path = entry / "metadata.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    users.append({
                        "unique_id": meta.get("unique_id"),
                        "url": meta.get("url"),
                        "saved_at": meta.get("saved_at"),
                        "total_files": meta.get("total_files", 0),
                        "cache_dir": str(entry),
                    })
                except Exception:
                    pass
    return {"success": True, "cached_users": users, "total": len(users)}


def get_local_file(unique_id: str, filename: str) -> Optional[Path]:
    """Get a specific cached file path for a user.

    Args:
        unique_id: TikTok username
        filename: e.g. 'extraction_result.json', 'session_values.json', 'webmssdk.js'

    Returns:
        Path to the file if it exists, else None
    """
    user_dir = get_user_cache_dir(unique_id)
    file_path = user_dir / filename
    return file_path if file_path.exists() else None


def build_local_zip(unique_id: str) -> Path:
    """Build a ZIP file containing all locally-cached files for a user.

    Args:
        unique_id: TikTok username

    Returns:
        Path to the generated ZIP file
    """
    import io
    import zipfile
    user_dir = get_user_cache_dir(unique_id)
    zip_path = user_dir / f"{_safe_uid(unique_id)}_local_cache.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(user_dir):
            for fname in files:
                if fname.endswith('.zip'):
                    continue
                fpath = Path(root) / fname
                arcname = fpath.relative_to(user_dir)
                zf.write(fpath, arcname)
    return zip_path


if __name__ == "__main__":
    # Self-test
    print("=== Local Save Processor — self-test ===")
    print(f"Cache root: {LOCAL_CACHE_ROOT}")
    print(f"Cache exists: {LOCAL_CACHE_ROOT.exists()}")
    if LOCAL_CACHE_ROOT.exists():
        cached = list_local_cache()
        print(f"Cached users: {cached.get('total', 0)}")
        for u in cached.get("cached_users", []):
            print(f"  - @{u['unique_id']}: {u['total_files']} files (saved {u['saved_at'][:19]})")
