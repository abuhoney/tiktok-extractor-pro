#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
attached_file_processor.py
===========================
Error-handling processor for attached files (JSON, images, cookies).

This module provides:
  - validate_and_parse_json(file_path) — robust JSON parsing with multiple fallbacks
  - extract_cookies_from_text(text) — parse cookie strings into structured data
  - extract_ids_from_analysis(analysis_dict) — pull all IDs from analysis JSON
  - merge_multiple_jsons(file_paths) — merge several JSON files into one
  - analyze_screenshot_with_vlm(image_path) — use Z-AI VLM to analyze screenshots
  - save_uploaded_file(uploaded_bytes, filename, unique_id) — save and process

Used by:
  - app.py /api/process-uploads endpoint (new in v1.0.35)
  - extractor.py to merge user-provided data with extraction results
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


UPLOAD_DIR = Path(os.environ.get("TIKTOK_UPLOAD_DIR", "/tmp/tiktok_uploads"))


def validate_and_parse_json(file_path: Path) -> Dict[str, Any]:
    """Robustly parse a JSON file with multiple fallbacks.

    Returns a dict with:
        - success: bool
        - data: dict (if success)
        - error: str (if failed)
        - file_info: dict (size, mtime, etc.)
        - warnings: list of str
    """
    result = {
        "success": False,
        "data": None,
        "error": None,
        "file_info": {},
        "warnings": [],
    }

    if not file_path.exists():
        result["error"] = f"File not found: {file_path}"
        return result

    stat = file_path.stat()
    result["file_info"] = {
        "path": str(file_path),
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }

    if stat.st_size == 0:
        result["error"] = "File is empty"
        return result

    if stat.st_size > 50 * 1024 * 1024:  # 50 MB
        result["warnings"].append("File is larger than 50MB — parsing may be slow")

    raw = file_path.read_bytes()
    if not raw:
        result["error"] = "File content is empty"
        return result

    # Try UTF-8 first
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        result["error"] = "Could not decode file as any known encoding"
        return result

    # Strip BOM if present
    if text and text[0] == "\ufeff":
        text = text[1:]
        result["warnings"].append("BOM stripped from beginning of file")

    # Try direct JSON parse
    try:
        data = json.loads(text)
        result["success"] = True
        result["data"] = data
        return result
    except json.JSONDecodeError as e:
        result["warnings"].append(f"Direct JSON parse failed: {e}")

    # Try stripping trailing commas (common in JS-style JSON)
    cleaned = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        data = json.loads(cleaned)
        result["success"] = True
        result["data"] = data
        result["warnings"].append("Fixed trailing commas to parse")
        return result
    except json.JSONDecodeError:
        pass

    # Try extracting the first {...} or [...] block
    m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            result["success"] = True
            result["data"] = data
            result["warnings"].append("Extracted JSON from surrounding text")
            return result
        except json.JSONDecodeError:
            pass

    # Maybe it's a cookie string or URL-encoded data — try that
    if "=" in text and ";" in text:
        cookies = extract_cookies_from_text(text)
        if cookies:
            result["success"] = True
            result["data"] = {"_type": "cookies", "cookies": cookies}
            result["warnings"].append("Parsed as cookie string")
            return result

    result["error"] = "Could not parse file as JSON, cookies, or any known format"
    return result


def extract_cookies_from_text(text: str) -> Dict[str, str]:
    """Parse a cookie string (like document.cookie) into a dict.

    Handles formats:
      - "key1=val1; key2=val2"
      - "key1=val1;\n key2=val2"
      - URL-encoded values
    """
    cookies = {}
    # Split on ; or newline
    for part in re.split(r'[;\n]', text):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        # URL-decode the value
        from urllib.parse import unquote
        try:
            val = unquote(val)
        except Exception:
            pass
        cookies[key] = val
    return cookies


def extract_ids_from_analysis(analysis: Dict[str, Any]) -> Dict[str, str]:
    """Pull all useful IDs from an analysis JSON (like analysis 1.json or 2.json).

    Returns a flat dict of {id_name: value} for quick lookup.
    """
    ids = {}
    # Top-level all_ids
    all_ids = analysis.get("all_ids", {})
    if isinstance(all_ids, dict):
        for k, v in all_ids.items():
            if v and isinstance(v, (str, int)):
                ids[k] = str(v)

    # Security credentials
    sec = analysis.get("security_credentials", {})
    if isinstance(sec, dict):
        for k in ("csrf_token", "wid", "nonce", "ttwid", "sessionid", "msToken"):
            if sec.get(k):
                ids[k] = sec[k]

    # Author IDs
    author = analysis.get("author", {})
    if isinstance(author, dict):
        for k in ("user_id", "unique_id", "sec_uid", "nickname"):
            if author.get(k):
                ids[f"author_{k}"] = author[k]

    # Stream / room IDs
    for k in ("room_id", "stream_id", "content_id", "video_id"):
        v = analysis.get(k)
        if v:
            ids[k] = str(v)

    # Decoded tokens
    decoded = analysis.get("decoded_tokens", {})
    if isinstance(decoded, dict):
        for k, v in decoded.items():
            if isinstance(v, dict):
                if v.get("raw"):
                    ids[f"decoded_{k}"] = v["raw"]
            elif isinstance(v, str):
                ids[f"decoded_{k}"] = v

    return ids


def merge_multiple_jsons(file_paths: List[Path]) -> Dict[str, Any]:
    """Merge multiple JSON files into a single unified dict.

    Used when a user uploads analysis.json + values.json + cookies.json —
    combines all of them into one cohesive data structure.
    """
    merged = {
        "_meta": {
            "merged_at": datetime.now(timezone.utc).isoformat(),
            "source_files": [],
        },
        "ids": {},
        "cookies": {},
        "session_values": {},
        "analysis": {},
        "errors": [],
    }

    for fp in file_paths:
        if not fp.exists():
            merged["errors"].append(f"File not found: {fp}")
            continue
        file_info = {
            "path": str(fp),
            "name": fp.name,
            "size_bytes": fp.stat().st_size,
        }
        parsed = validate_and_parse_json(fp)
        if not parsed["success"]:
            file_info["error"] = parsed["error"]
            file_info["warnings"] = parsed["warnings"]
            merged["errors"].append(f"{fp.name}: {parsed['error']}")
            merged["_meta"]["source_files"].append(file_info)
            continue

        data = parsed["data"]
        file_info["size_human"] = parsed["file_info"].get("size_human", "")
        file_info["warnings"] = parsed["warnings"]
        merged["_meta"]["source_files"].append(file_info)

        # Categorize by content
        if data.get("_type") == "cookies" or all(k in ("cookies", "_type") for k in data.keys()):
            merged["cookies"].update(data.get("cookies", {}))
        elif "values" in data and "_playwright_attempt" in data:
            # session_values.json format
            merged["session_values"] = data.get("values", {})
            merged["session_values_metadata"] = data.get("values_with_metadata", {})
        elif "all_ids" in data or "author" in data:
            # analysis JSON format
            merged["analysis"][fp.name] = data
            ids = extract_ids_from_analysis(data)
            merged["ids"].update(ids)
        elif isinstance(data, dict):
            # Generic dict — merge into ids if it looks like IDs
            for k, v in data.items():
                if isinstance(v, (str, int)) and v:
                    merged["ids"][k] = str(v)
        elif isinstance(data, list):
            merged.setdefault("lists", {})[fp.stem] = data

    # Calculate totals
    merged["_meta"]["total_files"] = len(file_paths)
    merged["_meta"]["successful_parses"] = len(merged["_meta"]["source_files"]) - len(merged["errors"])
    merged["_meta"]["total_ids"] = len(merged["ids"])
    merged["_meta"]["total_cookies"] = len(merged["cookies"])
    merged["_meta"]["total_session_values"] = len(merged["session_values"])

    return merged


def save_uploaded_file(uploaded_bytes: bytes, filename: str, unique_id: str = "upload") -> Path:
    """Save an uploaded file (from multipart form) to the uploads directory.

    Args:
        uploaded_bytes: raw file bytes
        filename: original filename
        unique_id: subdirectory for this upload session

    Returns:
        Path to the saved file
    """
    safe_name = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', filename)
    upload_dir = UPLOAD_DIR / unique_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / safe_name
    file_path.write_bytes(uploaded_bytes)
    return file_path


def analyze_screenshot_with_vlm(image_path: Path, question: str = None) -> Dict[str, Any]:
    """Use the Z-AI VLM CLI to analyze a screenshot.

    Args:
        image_path: path to the JPEG/PNG screenshot
        question: optional custom question (default: describe what's visible)

    Returns:
        dict with success, description, and raw response
    """
    if not image_path.exists():
        return {"success": False, "error": f"Image not found: {image_path}"}

    if not question:
        question = (
            "Describe what's visible in this screenshot. Focus on: "
            "1) Is the page loading or blank? "
            "2) Any error messages? (transcribe them exactly) "
            "3) What UI elements are visible? "
            "4) Is there a loading indicator?"
        )

    try:
        result = subprocess.run(
            ["z-ai", "vision", "-p", question, "-i", str(image_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            # Try to extract the JSON response from the CLI output
            output = result.stdout
            try:
                # The CLI outputs a JSON response
                response = json.loads(output)
                description = response.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "success": True,
                    "description": description,
                    "raw": response,
                }
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "description": output.strip(),
                    "raw": {"stdout": output[:5000]},
                }
        else:
            return {
                "success": False,
                "error": result.stderr or result.stdout or f"CLI exit code {result.returncode}",
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "VLM analysis timed out (120s)"}
    except FileNotFoundError:
        return {"success": False, "error": "z-ai CLI not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _human_size(n: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


if __name__ == "__main__":
    # Self-test
    print("=== Attached File Processor — self-test ===")
    # Test cookie parsing
    cookies = extract_cookies_from_text(
        "tt_csrf_token=M4KRf1cq-nJ0Gh8FRbMd; msToken=bJI7WH3H64TJJ9RIafcqYdLBVwpCB07NCl6vE10xxm45; ttwid=1%7CqAW_03qUF37gdeFVcwYmhsqw0qOIAdfuBQ_1yQxD6h0%7C1789589898%7C5c640207a1d03bc668ddc11a76733978e8c58b48e51c07da18fe14cbbc1863b9"
    )
    print(f"Parsed {len(cookies)} cookies:")
    for k, v in list(cookies.items())[:5]:
        print(f"  - {k}: {v[:60]}{'...' if len(v) > 60 else ''}")
