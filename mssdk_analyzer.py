#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mssdk_analyzer.py
=================
Python port of the comprehensive MSSDK analyzer from `strong signature.html`.

Performs 8-phase analysis on any webmssdk.js (or similar obfuscated JS) file:

  Phase 1: Static analysis — extract variables, functions, hex vars, strings
  Phase 2: Decode strings — Base64 decode all string literals
  Phase 3: Runtime probe — evaluate the script in a sandboxed environment
  Phase 4: Base64 deep scan — find all base64-looking strings
  Phase 5: ZIP extraction — find ZIP-like binary blobs in decoded base64
  Phase 6: XOR decoding — try common XOR keys against hex strings
  Phase 7: ACrawler rebuild — extract the byted_acrawler module from the script
  Phase 8: Deobfuscation — rename _0x... identifiers to readable names

Generates signatures by executing webmssdk.js via Playwright (frontierSign),
producing real X-Bogus / X-Gnarly / X-Mssdk-Info / X-Mssdk-RC values.

API endpoints (when imported from app.py):
    analyze_webmssdk(code: str) -> dict
    generate_signatures(url, method, params, ticket, user_mode) -> dict

English-only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import string
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


# ════════════════════════════════════════════════════════════════════════════
#  Phase 1: Static analysis
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class StaticAnalysis:
    """Phase 1 result: static structure of the JS file."""
    variables: Dict[str, Any] = field(default_factory=dict)
    hex_variables: Dict[str, str] = field(default_factory=dict)
    functions: Dict[str, str] = field(default_factory=dict)
    string_literals: List[str] = field(default_factory=list)
    expressions: List[str] = field(default_factory=list)


def remove_comments(code: str) -> str:
    """Strip JS comments (// and /* */) preserving strings."""
    out = []
    i = 0
    n = len(code)
    in_str = None  # ', ", `
    while i < n:
        c = code[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in "\"'`":
            in_str = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            # line comment
            while i < n and code[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            i += 2
            while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_full_value(code: str, start_idx: int) -> Tuple[str, int]:
    """Parse a full JS value (string, number, object, array, function) starting at start_idx."""
    i = start_idx
    n = len(code)
    while i < n and code[i] in " \t\n":
        i += 1
    if i >= n:
        return "", i
    c = code[i]
    if c in "\"'`":
        # string literal
        end = c
        j = i + 1
        while j < n:
            if code[j] == "\\" and j + 1 < n:
                j += 2
                continue
            if code[j] == end:
                j += 1
                break
            j += 1
        return code[i:j], j
    if c == "[":
        # array
        depth = 0
        j = i
        while j < n:
            if code[j] == "[":
                depth += 1
            elif code[j] == "]":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            elif code[j] in "\"'`":
                end = code[j]
                j += 1
                while j < n:
                    if code[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if code[j] == end:
                        break
                    j += 1
            j += 1
        return code[i:j], j
    if c == "{":
        # object
        depth = 0
        in_str = None
        j = i
        while j < n:
            ch = code[j]
            if in_str:
                if ch == "\\" and j + 1 < n:
                    j += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in "\"'`":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        return code[i:j], j
    # number or identifier
    j = i
    while j < n and (code[j].isalnum() or code[j] in "._-$"):
        j += 1
    return code[i:j], j


def extract_string_literals(code: str) -> List[str]:
    """Extract all string literals from JS code."""
    out = []
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c in "\"'`":
            end = c
            j = i + 1
            buf = []
            while j < n:
                if code[j] == "\\" and j + 1 < n:
                    nxt = code[j + 1]
                    if nxt == "n": buf.append("\n")
                    elif nxt == "t": buf.append("\t")
                    elif nxt == "r": buf.append("\r")
                    elif nxt == "\\": buf.append("\\")
                    elif nxt == "'": buf.append("'")
                    elif nxt == '"': buf.append('"')
                    elif nxt == "`": buf.append("`")
                    elif nxt == "x" and j + 3 < n:
                        try:
                            buf.append(chr(int(code[j+2:j+4], 16)))
                            j += 4
                            continue
                        except Exception:
                            buf.append(code[j+1])
                    elif nxt == "u" and j + 5 < n:
                        try:
                            buf.append(chr(int(code[j+2:j+6], 16)))
                            j += 6
                            continue
                        except Exception:
                            buf.append(code[j+1])
                    else:
                        buf.append(nxt)
                    j += 2
                    continue
                if code[j] == end:
                    j += 1
                    break
                buf.append(code[j])
                j += 1
            out.append("".join(buf))
            i = j
        else:
            i += 1
    return out


def phase1_static_analysis(code: str) -> StaticAnalysis:
    """Phase 1: Extract static structure (variables, functions, strings)."""
    code_clean = remove_comments(code)
    out = StaticAnalysis()

    # Function declarations: function NAME(args) {
    for m in re.finditer(r"function\s+([a-zA-Z_$][\w$]*|_0x[0-9a-f]+)\s*\([^)]*\)\s*\{", code_clean):
        name = m.group(1)
        if name not in out.functions:
            # Find matching closing brace
            start = m.end() - 1  # position of {
            depth = 0
            i = start
            while i < len(code_clean):
                if code_clean[i] == "{":
                    depth += 1
                elif code_clean[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.functions[name] = code_clean[m.start():i + 1]

    # Assigned functions: var NAME = function(args) {
    for m in re.finditer(r"(?:var|let|const)\s+([a-zA-Z_$][\w$]*|_0x[0-9a-f]+)\s*=\s*(?:async\s+)?function\s*\([^)]*\)\s*\{", code_clean):
        name = m.group(1)
        if name not in out.functions:
            start = m.end() - 1
            depth = 0
            i = start
            while i < len(code_clean):
                if code_clean[i] == "{":
                    depth += 1
                elif code_clean[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.functions[name] = code_clean[m.start():i + 1]

    # Arrow functions: var NAME = (args) => {
    for m in re.finditer(r"(?:var|let|const)\s+([a-zA-Z_$][\w$]*|_0x[0-9a-f]+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[a-zA-Z_$][\w$]*)\s*=>\s*\{", code_clean):
        name = m.group(1)
        if name not in out.functions:
            start = m.end() - 1
            depth = 0
            i = start
            while i < len(code_clean):
                if code_clean[i] == "{":
                    depth += 1
                elif code_clean[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.functions[name] = code_clean[m.start():i + 1]

    # String literals
    out.string_literals = extract_string_literals(code_clean)

    # Hex variables: _0x[0-9a-f]+
    hex_var_re = re.compile(r"_0x[0-9a-f]{3,}\b")
    for m in hex_var_re.finditer(code_clean):
        name = m.group(0)
        if name not in out.hex_variables:
            out.hex_variables[name] = name

    # Variable declarations with values
    for m in re.finditer(r"(?:var|let|const)\s+([a-zA-Z_$][\w$]*|_0x[0-9a-f]+)\s*=\s*", code_clean):
        name = m.group(1)
        if name not in out.variables:
            value, _ = parse_full_value(code_clean, m.end())
            out.variables[name] = value[:500]  # truncate long values

    return out


# ════════════════════════════════════════════════════════════════════════════
#  Phase 2: Decode strings (Base64 attempts)
# ════════════════════════════════════════════════════════════════════════════
def phase2_decode_strings(static: StaticAnalysis) -> Dict[str, Any]:
    """Phase 2: Try to decode each string literal as base64 (standard + URL-safe)."""
    decoded = {}
    for i, s in enumerate(static.string_literals):
        if not s or len(s) < 4:
            continue
        info: Dict[str, Any] = {"raw_length": len(s)}
        for variant in ("standard", "urlsafe"):
            try:
                if variant == "standard":
                    decoded_bytes = base64.b64decode(s + "=" * (-len(s) % 4), validate=False)
                else:
                    decoded_bytes = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
                if decoded_bytes and len(decoded_bytes) > 0:
                    info[f"{variant}_length"] = len(decoded_bytes)
                    info[f"{variant}_hex"] = decoded_bytes.hex()[:200]
                    try:
                        utf8_preview = decoded_bytes.decode("utf-8", errors="replace")[:100]
                        info[f"{variant}_utf8"] = utf8_preview
                    except Exception:
                        pass
                    # Check if PNG
                    if decoded_bytes.startswith(b"\x89PNG"):
                        info[f"{variant}_is_png"] = True
                    # Check if ZIP
                    if decoded_bytes.startswith(b"PK"):
                        info[f"{variant}_is_zip"] = True
            except Exception as e:
                info[f"{variant}_error"] = str(e)[:100]
        if len(info) > 1:
            decoded[str(i)] = info
    return decoded


# ════════════════════════════════════════════════════════════════════════════
#  Phase 3: Runtime probe via Playwright (when available)
# ════════════════════════════════════════════════════════════════════════════
def phase3_runtime_probe(code: str) -> Dict[str, Any]:
    """Phase 3: Execute the script in Playwright and probe window.byted_acrawler."""
    result = {"tried": False, "ok": False, "byted_acrawler": None, "frontierSign": None, "error": None}
    try:
        from playwright.sync_api import sync_playwright
        result["tried"] = True
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_content("""<!doctype html><html><head><meta charset="utf-8"></head>
<body><script>
  let _cookies = '__ac_testid=verify_stub_abc123; ttwid=1|stub|123|abc';
  try { Object.defineProperty(document, 'cookie', { get: () => _cookies, set: (v) => { _cookies = v; } }); } catch(e) {}
</script></body></html>""")
            page.evaluate("""(code) => { try { (0, eval)(code); return { ok: true }; } catch(e) { return { ok: false, error: String(e) }; } }""", code)
            time.sleep(2)
            info = page.evaluate("""() => {
                const a = window.byted_acrawler;
                if (!a) return { found: false };
                return {
                    found: true,
                    ownProperties: Object.getOwnPropertyNames(a),
                    has_frontierSign: typeof a.frontierSign === 'function',
                    has_setTTWebid: typeof a.setTTWebid,
                    has_setTTWebidV2: typeof a.setTTWebidV2,
                    has_setTTWid: typeof a.setTTWid,
                    has_init: typeof a.init,
                    has_setUserMode: typeof a.setUserMode,
                    has_report: typeof a.report,
                    has_getReferer: typeof a.getReferer,
                    isWebmssdk: a.isWebmssdk,
                };
            }""")
            result["byted_acrawler"] = info
            result["ok"] = info.get("found", False)
            if info.get("has_frontierSign"):
                result["frontierSign"] = "function"
            browser.close()
    except ImportError:
        result["error"] = "playwright not installed"
    except Exception as e:
        result["error"] = str(e)
    return result


# ════════════════════════════════════════════════════════════════════════════
#  Phase 4: Base64 deep scan
# ════════════════════════════════════════════════════════════════════════════
def phase4_base64_scan(code: str) -> Dict[str, Any]:
    """Phase 4: Find all base64-looking strings (>= 20 chars)."""
    entries = []
    for m in re.finditer(r"[A-Za-z0-9+/_=]{20,}", code):
        s = m.group(0)
        info: Dict[str, Any] = {"index": len(entries), "raw": s[:80], "raw_length": len(s)}
        try:
            decoded = base64.b64decode(s + "=" * (-len(s) % 4), validate=False)
            info["decoded_length"] = len(decoded)
            info["decoded_hex"] = decoded.hex()[:200]
            info["is_zip"] = decoded.startswith(b"PK")
            info["is_png"] = decoded.startswith(b"\x89PNG")
        except Exception:
            info["decode_error"] = True
        entries.append(info)
    return {"stats": {"count": len(entries)}, "entries": entries[:50]}


# ════════════════════════════════════════════════════════════════════════════
#  Phase 5: ZIP extraction (manual ZIP parsing)
# ════════════════════════════════════════════════════════════════════════════
def phase5_zip_extraction(b64_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Phase 5: Extract ZIP files from base64-decoded blobs."""
    zips = []
    files = []
    for entry in b64_entries:
        if entry.get("is_zip"):
            try:
                decoded = base64.b64decode(entry["raw"] + "=" * (-len(entry["raw"]) % 4))
                # Minimal ZIP central directory parsing
                # Find EOCD signature: 0x06054b50
                eocd_idx = decoded.rfind(b"PK\x05\x06")
                if eocd_idx >= 0:
                    zips.append({"raw_length": len(decoded), "eocd_offset": eocd_idx})
                    files.append({
                        "zip_index": len(zips) - 1,
                        "size": len(decoded),
                        "raw_preview": decoded[:100].hex(),
                    })
            except Exception as e:
                zips.append({"error": str(e)[:100]})
    return {"stats": {"file_count": len(files)}, "zips": zips, "files": files}


# ════════════════════════════════════════════════════════════════════════════
#  Phase 6: XOR decoding
# ════════════════════════════════════════════════════════════════════════════
def phase6_xor_decode(static: StaticAnalysis) -> Dict[str, Any]:
    """Phase 6: Try XOR decoding against hex variables with common keys."""
    entries = []
    common_keys = [0x5A, 0x37, 0x42, 0x55, 0xFF]
    for name, val in list(static.hex_variables.items())[:100]:
        # Take a sample of the variable usage in code
        for key in common_keys:
            try:
                sample = val[:64].encode()
                decoded = bytes(b ^ key for b in sample)
                if all(0x20 <= b < 0x7f for b in decoded):
                    text = decoded.decode("ascii")
                    if any(c.isalpha() for c in text):
                        entries.append({
                            "var": name,
                            "key": hex(key),
                            "preview": text[:50],
                        })
                        break
            except Exception:
                pass
    return {"stats": {"count": len(entries)}, "entries": entries[:50]}


# ════════════════════════════════════════════════════════════════════════════
#  Phase 7: ACrawler rebuild (extract byted_acrawler module)
# ════════════════════════════════════════════════════════════════════════════
def phase7_rebuild_acrawler(static: StaticAnalysis) -> Dict[str, Any]:
    """Phase 7: Rebuild the byted_acrawler module from extracted functions."""
    # Find the main module wrapper function (the largest function in the file)
    if not static.functions:
        return {"stats": {"total": 0, "rebuilt": 0}, "functions": {}, "rebuiltCode": ""}
    largest = max(static.functions.items(), key=lambda kv: len(kv[1]))
    rebuilt_code = f"""// Rebuilt byted_acrawler module
// Total functions: {len(static.functions)}
// Main module: {largest[0]} ({len(largest[1])} chars)

(function(window, globalThis) {{
"""
    for name, body in list(static.functions.items())[:30]:
        rebuilt_code += f"\n// Function: {name} ({len(body)} chars)\n{body[:500]}\n"
    rebuilt_code += "})(window, globalThis);\n"
    return {
        "stats": {"total": len(static.functions), "rebuilt": min(30, len(static.functions))},
        "functions": dict(list(static.functions.items())[:30]),
        "rebuiltCode": rebuilt_code,
    }


# ════════════════════════════════════════════════════════════════════════════
#  Phase 8: Deobfuscation (rename _0x... identifiers)
# ════════════════════════════════════════════════════════════════════════════
def phase8_deobfuscate(code: str) -> Dict[str, Any]:
    """Phase 8: Rename _0x[0-9a-f]+ identifiers to readable names."""
    counter = [0]
    rename_map: Dict[str, str] = {}

    def make_name(idx: int) -> str:
        # var_001, var_002, fn_001, etc.
        return f"var_{idx:04d}"

    def replacer(m: re.Match) -> str:
        ident = m.group(0)
        if ident not in rename_map:
            counter[0] += 1
            rename_map[ident] = make_name(counter[0])
        return rename_map[ident]

    processed = re.sub(r"\b_0x[0-9a-f]{3,}\b", replacer, code)
    return {
        "stats": {"renamedCount": len(rename_map)},
        "renamed": rename_map,
        "processed_length": len(processed),
        "processed_preview": processed[:2000],
    }


# ════════════════════════════════════════════════════════════════════════════
#  Signature generation via Playwright
# ════════════════════════════════════════════════════════════════════════════
def generate_signatures_via_playwright(
    webmssdk_code: str,
    url: str,
    method: str = "GET",
    params: Dict[str, Any] = None,
    ticket: str = "",
    user_mode: int = 0,
) -> Dict[str, Any]:
    """Generate real X-Bogus / X-Gnarly / X-Mssdk-Info by executing webmssdk.js.

    Args:
        webmssdk_code: Full source code of webmssdk.js
        url: Request URL to sign
        method: HTTP method (GET, POST, etc.)
        params: Request parameters dict
        ticket: Optional msToken to use
        user_mode: Optional user mode (0=normal, 1=incognito, etc.)

    Returns:
        Dict with X-Bogus, X-Gnarly, X-Mssdk-Info, X-Mssdk-RC, raw response
    """
    result = {
        "ok": False,
        "x-bogus": None,
        "x-gnarly": None,
        "x-mssdk-info": None,
        "x-mssdk-rc": None,
        "raw": None,
        "error": None,
        "generatedAt": None,
        "request": {"url": url, "method": method, "params": params, "ticket": ticket, "userMode": user_mode},
    }
    params = params or {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_content("""<!doctype html><html><head><meta charset="utf-8"></head>
<body><script>
  let _cookies = '__ac_testid=verify_stub_abc123; ttwid=1|stub|123|abc';
  try { Object.defineProperty(document, 'cookie', { get: () => _cookies, set: (v) => { _cookies = v; } }); } catch(e) {}
</script></body></html>""")
            page.evaluate("""(code) => { try { (0, eval)(code); return { ok: true }; } catch(e) { return { ok: false, error: String(e) }; } }""", webmssdk_code)
            time.sleep(2)

            if user_mode > 0:
                page.evaluate(f"""(mode) => {{ try {{ window.byted_acrawler.setUserMode(mode); }} catch(e) {{}} }}""", user_mode)

            sign_params = {
                "X-MS-STUB": "",
                "url": url,
                "method": method,
                "params": json.dumps(params),
                "headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json"
                }
            }
            if ticket:
                sign_params["ticket"] = ticket

            sign_result = page.evaluate("""(signParams) => {
                try {
                    const r = window.byted_acrawler.frontierSign(signParams);
                    let out = { ok: true, raw: r };
                    if (typeof r === 'string') {
                        out['x-bogus'] = r;
                    } else if (r && typeof r === 'object') {
                        for (const [k, v] of Object.entries(r)) {
                            const lk = k.toLowerCase();
                            if (lk.includes('bogus')) out['x-bogus'] = v;
                            else if (lk.includes('gnarly')) out['x-gnarly'] = v;
                            else if (lk.includes('mssdk-info') || lk.includes('mssdk_info')) out['x-mssdk-info'] = v;
                            else if (lk.includes('mssdk-rc') || lk.includes('mssdk_rc')) out['x-mssdk-rc'] = v;
                        }
                    }
                    return out;
                } catch(e) { return { ok: false, error: String(e) }; }
            }""", sign_params)
            result.update(sign_result)
            result["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            browser.close()
    except ImportError:
        result["error"] = "playwright not installed"
    except Exception as e:
        result["error"] = str(e)
    return result


# ════════════════════════════════════════════════════════════════════════════
#  Full 8-phase analysis
# ════════════════════════════════════════════════════════════════════════════
def analyze_webmssdk(code: str, do_runtime: bool = False) -> Dict[str, Any]:
    """Run all 8 analysis phases on a webmssdk.js (or similar) source.

    Args:
        code: The full JS source code to analyze
        do_runtime: Whether to run Phase 3 (Playwright runtime probe)

    Returns:
        Complete analysis dict with all 8 phases + summary.
    """
    # Phase 1
    static = phase1_static_analysis(code)
    # Phase 2
    decoded = phase2_decode_strings(static)
    # Phase 4
    b64 = phase4_base64_scan(code)
    # Phase 5
    zip_data = phase5_zip_extraction(b64.get("entries", []))
    # Phase 6
    xor = phase6_xor_decode(static)
    # Phase 7
    acrawler = phase7_rebuild_acrawler(static)
    # Phase 8
    deob = phase8_deobfuscate(code)
    # Phase 3 (optional)
    runtime = phase3_runtime_probe(code) if do_runtime else {"tried": False, "skipped": True}

    return {
        "_meta": {
            "analyzedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "sourceCodeLength": len(code),
            "analysisVersion": "python-port-v1.0",
        },
        "_phase1_static": {
            "variables": static.variables,
            "hex_variables": list(static.hex_variables.keys())[:200],
            "functions": dict(list(static.functions.items())[:50]),
            "string_literals": static.string_literals,
            "expressions": static.expressions,
        },
        "_phase2_decoded": decoded,
        "_phase3_runtime": runtime,
        "_phase4_base64": b64,
        "_phase5_zip": zip_data,
        "_phase6_xor": xor,
        "_phase7_acrawler": acrawler,
        "_phase8_deobfuscation": deob,
        "_summary": {
            "totalVariables": len(static.variables),
            "totalHexVars": len(static.hex_variables),
            "totalFunctions": len(static.functions),
            "totalStringLiterals": len(static.string_literals),
            "runtimeExecuted": runtime.get("ok", False),
            "totalB64": b64.get("stats", {}).get("count", 0),
            "totalZipFiles": zip_data.get("stats", {}).get("file_count", 0),
            "totalXOR": xor.get("stats", {}).get("count", 0),
            "totalRenamed": deob.get("stats", {}).get("renamedCount", 0),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Analyze webmssdk.js (8 phases) and generate real signatures")
    parser.add_argument("input", help="Path to webmssdk.js (or similar JS file)")
    parser.add_argument("--output", "-o", default="analysis.json", help="Output JSON path")
    parser.add_argument("--runtime", action="store_true", help="Run Phase 3 runtime probe")
    parser.add_argument("--sign-url", help="Generate signatures for this URL via Playwright")
    args = parser.parse_args()

    code = Path(args.input).read_text(encoding="utf-8")
    print(f"Analyzing {len(code)} chars from {args.input}...")

    analysis = analyze_webmssdk(code, do_runtime=args.runtime)
    print(f"Phase 1: {analysis['_summary']['totalVariables']} vars, {analysis['_summary']['totalFunctions']} functions")
    print(f"Phase 2: {len(analysis['_phase2_decoded'])} strings decoded")
    print(f"Phase 4: {analysis['_summary']['totalB64']} base64 blobs")
    print(f"Phase 8: {analysis['_summary']['totalRenamed']} identifiers renamed")

    if args.sign_url:
        print(f"\nGenerating signatures for: {args.sign_url}")
        sig = generate_signatures_via_playwright(code, args.sign_url)
        analysis["signatures"] = sig
        print(f"X-Bogus: {sig.get('x-bogus', '(failed)')}")

    Path(args.output).write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ Saved to {args.output}")
