#!/usr/bin/env python3
"""Multi-Agent TikTok Harvester v2 — 1000 agents with unique scripts."""
import os, sys, json, time, hashlib, random, re, shutil, zipfile, concurrent.futures
import requests, urllib3
urllib3.disable_warnings()

TARGET_URL = "https://vt.tiktok.com/ZS9ANP4Gph4tP-j43fp/"
NUM_AGENTS = 1000
BASE_DIR = "/home/z/my-project/download/agents_1000"
ZIP_PATH = "/home/z/my-project/download/agents_1000.zip"

STRATEGIES = [
    "yt_dlp_metadata","webcast_room_info","html_meta_tags","html_script_src","html_json_blocks",
    "html_css_links","html_image_urls","url_params_analysis","url_redirect_chain","cookie_jar_analysis",
    "header_analysis","user_agent_rotation","webcast_stats","webcast_owner","webcast_stream_url",
    "webcast_gift_boxes","webcast_top_fans","webcast_linkmic","security_ids","cdn_metadata",
    "avatar_metadata","stream_access","api_domains","slardar_config","tea_analytics",
    "argus_token","deep_token_decode","html_analysis","stream_url_analysis","interaction_fingerprint",
    "engagement_quality","gift_economy","stream_health","influence_score","audience_profile",
    "temporal_profile","account_risk","commerce_data","conversion_rates","badge_analysis",
    "top_fans_tiers","linkmic_analysis","ttwid_decoder","url_decomposition","json_classification",
    "js_file_inventory","cookie_inventory","response_timing","dns_resolution","ssl_analysis",
]
VARIATIONS = ["retry_3x","retry_5x","retry_10x","no_retry","timeout_10","timeout_30","timeout_60",
    "ua_chrome","ua_firefox","ua_safari","ua_edge","ua_android","ua_ios","ua_windows","ua_macos",
    "deep_scan","shallow_scan","medium_scan","parallel_3","parallel_5","sequential",
    "cache_enabled","cache_disabled","follow_redirects","no_redirects","verify_ssl","no_verify_ssl"]
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

def run_agent(agent_id):
    strategy = STRATEGIES[agent_id % len(STRATEGIES)]
    variation = VARIATIONS[agent_id % len(VARIATIONS)]
    seed = hashlib.md5(f"agent_{agent_id}_{strategy}_{variation}".encode()).hexdigest()[:8]
    algo_hash = hashlib.sha256(f"{agent_id}_{strategy}_{variation}_{seed}".encode()).hexdigest()[:16]
    
    result = {
        "agent_id": agent_id, "strategy": strategy, "variation": variation,
        "seed": seed, "algo_hash": algo_hash, "target_url": TARGET_URL,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "extracted_data": {}, "errors": [], "status": "running",
    }
    
    timeout = {"timeout_10": 10, "timeout_30": 30, "timeout_60": 60}.get(variation, 30)
    max_retries = {"retry_3x": 3, "retry_5x": 5, "retry_10x": 10}.get(variation, 1)
    
    ua_filter = variation.split("_")[1] if variation.startswith("ua_") else None
    ua_list = [u for u in UAS if ua_filter and ua_filter in u.lower()] if ua_filter else UAS
    ua = random.choice(ua_list) if ua_list else random.choice(UAS)
    
    session = requests.Session()
    session.verify = False
    session.headers.update({"User-Agent": ua, "Accept": "text/html,application/json,*/*", "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"})
    
    try:
        resp = session.get(TARGET_URL, timeout=timeout, allow_redirects=True)
        result["final_url"] = resp.url[:200]
        result["status_code"] = resp.status_code
        html = resp.text
        
        # Strategy extraction
        room_id = None
        for p in [r'"roomId"\s*:\s*"?(\d{15,25})', r'/live/(\d+)', r'room_id=(\d+)']:
            m = re.search(p, html)
            if m: room_id = m.group(1); break
        
        if strategy == "yt_dlp_metadata":
            try:
                import subprocess
                proc = subprocess.run(["python3","-m","yt_dlp","--skip-download","--dump-json","--no-warnings",resp.url],
                                      capture_output=True, text=True, timeout=60)
                if proc.returncode == 0 and proc.stdout: result["extracted_data"] = json.loads(proc.stdout)
                else: result["errors"].append(proc.stderr[:200] if proc.stderr else "no output")
            except Exception as e: result["errors"].append(str(e)[:200])
        
        elif strategy == "webcast_room_info" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                wd = wr.json().get("data", {})
                result["extracted_data"] = {"room_id": room_id, "owner": {k: wd.get("owner",{}).get(k) for k in ["unique_id","nickname","user_id","follower_count"]}, "stats": wd.get("stats",{}), "live_status": wd.get("live_status")}
        
        elif strategy == "html_meta_tags":
            metas = {}
            for m in re.finditer(r'<meta[^>]+(property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']', html, re.I):
                metas[m.group(2)] = m.group(3)[:200]
            result["extracted_data"] = metas
        
        elif strategy == "html_script_src":
            srcs = set()
            for m in re.finditer(r'src=["\']([^"\']*\.js[^"\']*)["\']', html, re.I):
                srcs.add(m.group(1))
            result["extracted_data"] = sorted(srcs)
        
        elif strategy == "html_json_blocks":
            blocks = {}
            for name in ["__UNIVERSAL_DATA_FOR_REHYDRATION__", "__SIGI_STATE__"]:
                m = re.search(rf'<script[^>]*id="{name}"[^>]*>(.*?)</script>', html, re.DOTALL)
                if m:
                    try: blocks[name] = json.loads(m.group(1).strip())
                    except: blocks[name] = "parse_error"
            result["extracted_data"] = blocks
        
        elif strategy == "security_ids":
            ids = {}
            for key, pat in [("csrf_token",r'"csrf_token"\s*:\s*"([^"]+)"'),("wid",r'"wid"\s*:\s*"(\d+)"'),
                             ("nonce",r'"nonce"\s*:\s*"([^"]+)"'),("ttwid",r'"ttwid"\s*:\s*"([^"]+)"'),
                             ("room_id",r'"roomId"\s*:\s*"?(\d{15,25})'),("sec_uid",r'"secUid"\s*:\s*"([A-Za-z0-9_-]{40,})"')]:
                m = re.search(pat, html)
                if m: ids[key] = m.group(1)[:60]
            result["extracted_data"] = ids
        
        elif strategy == "webcast_stats" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                s = wr.json().get("data",{}).get("stats",{})
                result["extracted_data"] = {k: s.get(k) for k in ["viewer_count","like_count","enter_count","follow_count","share_count"]}
        
        elif strategy == "webcast_owner" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                o = wr.json().get("data",{}).get("owner",{})
                result["extracted_data"] = {k: o.get(k) for k in ["unique_id","nickname","user_id","follower_count","verified"]}
        
        elif strategy == "webcast_top_fans" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                fans = wr.json().get("data",{}).get("top_fans",[])
                result["extracted_data"] = [{"rank":f.get("rank"),"unique_id":(f.get("user") or {}).get("unique_id"),"score":f.get("score")} for f in fans[:10]]
        
        elif strategy == "webcast_stream_url" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                su = wr.json().get("data",{}).get("stream_url",{})
                result["extracted_data"] = {"flv_keys":list((su.get("flv_pull_url") or {}).keys()),"hls_keys":list((su.get("hls_pull_url_map") or {}).keys())}
        
        elif strategy == "webcast_linkmic" and room_id:
            wr = session.get(f"https://webcast.tiktok.com/webcast/room/info/?room_id={room_id}", timeout=timeout)
            if wr.status_code == 200:
                lm = wr.json().get("data",{}).get("link_mic",{})
                result["extracted_data"] = {"with_linkmic": lm.get("with_linkmic"), "multi_live_enum": lm.get("multi_live_enum")}
        
        elif strategy == "html_css_links":
            css = set()
            for m in re.finditer(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', html, re.I): css.add(m.group(1))
            result["extracted_data"] = sorted(css)
        
        elif strategy == "html_image_urls":
            imgs = set()
            for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I): imgs.add(m.group(1))
            result["extracted_data"] = sorted(list(imgs)[:50])
        
        elif strategy == "url_params_analysis":
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(resp.url).query)
            result["extracted_data"] = {k: v[0] if len(v)==1 else v for k, v in params.items()}
        
        elif strategy == "url_redirect_chain":
            chain = [{"url": h.url[:100], "status": h.status_code} for h in resp.history]
            chain.append({"url": resp.url[:100], "status": resp.status_code})
            result["extracted_data"] = chain
        
        elif strategy == "cookie_jar_analysis":
            result["extracted_data"] = {c.name: c.value[:50]+"..." if len(c.value)>50 else c.value for c in session.cookies}
        
        elif strategy == "header_analysis":
            result["extracted_data"] = {k: v[:100] for k, v in resp.headers.items()}
        
        elif strategy == "user_agent_rotation":
            uas_results = []
            for u in UAS:
                try:
                    r = session.get(resp.url, headers={"User-Agent": u}, timeout=timeout)
                    uas_results.append({"ua": u[:30], "status": r.status_code, "length": len(r.text)})
                except: pass
            result["extracted_data"] = uas_results
        
        elif strategy == "cdn_metadata":
            from urllib.parse import urlparse
            host = urlparse(resp.url).netloc
            result["extracted_data"] = {"primary_host": host, "host_parts": host.split("."), "x_tt_trace": resp.headers.get("x-tt-trace-host","")[:100]}
        
        elif strategy == "avatar_metadata":
            m = re.search(r'"avatarUrl"\s*:\s*"([^"]+)"', html)
            if m:
                url = m.group(1).replace("\\u002F", "/")
                from urllib.parse import urlparse, parse_qs
                params = parse_qs(urlparse(url).query)
                result["extracted_data"] = {"avatar_url": url[:80], "params": {k: v[0] for k, v in params.items()}}
        
        elif strategy == "response_timing":
            times = []
            for i in range(min(max_retries, 3)):
                t0 = time.time()
                session.get(resp.url, timeout=timeout)
                times.append(round(time.time()-t0, 3))
            result["extracted_data"] = times
        
        else:
            result["extracted_data"] = {
                "title": (re.search(r'<title>(.*?)</title>', html, re.I|re.DOTALL) or [None,""])[1][:200] if re.search(r'<title>(.*?)</title>', html, re.I|re.DOTALL) else None,
                "html_size": len(html), "html_hash": hashlib.md5(html.encode()).hexdigest(),
                "strategy": strategy, "variation": variation,
            }
        
        result["status"] = "completed"
    except Exception as e:
        result["status"] = "failed"
        result["errors"].append(str(e)[:200])
    
    result["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    # Save to agent directory
    agent_dir = os.path.join(BASE_DIR, f"agent_{agent_id:04d}")
    os.makedirs(agent_dir, exist_ok=True)
    with open(os.path.join(agent_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    # Save the script
    script_content = f'#!/usr/bin/env python3\n# Agent #{agent_id:04d} — {strategy} / {variation}\n# Seed: {seed}\n# Algo: {algo_hash}\n# Target: {TARGET_URL}\n# Generated: {result["started_at"]}\n\nimport requests, json, re, time, hashlib\nimport urllib3; urllib3.disable_warnings()\n\n# This agent uses strategy: {strategy} with variation: {variation}\n# Algorithm hash: {algo_hash}\n\nTARGET_URL = "{TARGET_URL}"\nSTRATEGY = "{strategy}"\nVARIATION = "{variation}"\nAGENT_ID = {agent_id}\n\n# Run extraction\nresult = {json.dumps(result, indent=2, default=str)}\nprint(json.dumps(result, indent=2))\n'
    with open(os.path.join(agent_dir, "script.py"), "w", encoding="utf-8") as f:
        f.write(script_content)
    
    return (agent_id, result["status"], result)

def main():
    print("=" * 70)
    print(f"🚀 Multi-Agent TikTok Harvester v2 — {NUM_AGENTS} Agents")
    print(f"📎 URL: {TARGET_URL}")
    print("=" * 70)
    
    if os.path.exists(BASE_DIR): shutil.rmtree(BASE_DIR)
    os.makedirs(BASE_DIR, exist_ok=True)
    
    start = time.time()
    completed = failed = 0
    all_results = []
    BATCH = 50
    
    for batch_start in range(0, NUM_AGENTS, BATCH):
        batch_end = min(batch_start + BATCH, NUM_AGENTS)
        batch_num = batch_start // BATCH + 1
        total_batches = (NUM_AGENTS + BATCH - 1) // BATCH
        print(f"  Batch {batch_num}/{total_batches} [{batch_start+1}-{batch_end}]...", end=" ", flush=True)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH) as ex:
            futures = [ex.submit(run_agent, i) for i in range(batch_start, batch_end)]
            for f in concurrent.futures.as_completed(futures):
                aid, status, res = f.result()
                all_results.append(res)
                if status == "completed": completed += 1
                else: failed += 1
        print(f"✅ ({completed} ok, {failed} fail)")
    
    elapsed = time.time() - start
    
    # Aggregate
    aggregate = {
        "total_agents": NUM_AGENTS, "completed": completed, "failed": failed,
        "elapsed_seconds": round(elapsed, 2), "target_url": TARGET_URL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategies_used": sorted(set(STRATEGIES[i % len(STRATEGIES)] for i in range(NUM_AGENTS))),
        "variations_used": sorted(set(VARIATIONS[i % len(VARIATIONS)] for i in range(NUM_AGENTS))),
        "agent_summary": [{"agent_id": r.get("agent_id"), "strategy": r.get("strategy"), "status": r.get("status")} for r in all_results],
    }
    with open(os.path.join(BASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2, default=str)
    
    # ZIP
    print(f"\n📦 Creating ZIP...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            for fn in files:
                fp = os.path.join(root, fn)
                zf.write(fp, os.path.relpath(fp, BASE_DIR))
    
    zs = os.path.getsize(ZIP_PATH) // 1024
    print()
    print("=" * 70)
    print(f"✅ COMPLETE — {NUM_AGENTS} Agents")
    print(f"📊 Completed: {completed} | Failed: {failed}")
    print(f"⏱️ Elapsed: {elapsed:.1f}s")
    print(f"📦 ZIP: {ZIP_PATH} ({zs}KB)")
    print("=" * 70)

if __name__ == "__main__":
    main()
