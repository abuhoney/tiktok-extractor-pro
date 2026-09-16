# TikTok Extractor Pro v5.0 — Integration README

## What's New in v5.0

This release integrates the comprehensive **session integrator** (`tiktok_session_integrator.py`)
and the **MSSDK analyzer** (`mssdk_analyzer.py`) into the main project. These modules run
**automatically** on every extraction, producing real session values and signatures.

## New Files

| File | Purpose |
|------|---------|
| `tiktok_session_integrator.py` | Generates all 34 TikTok session values via full integration: live HTML fetch + real webmssdk.js via Playwright + xbogus.py fallback + hashlib |
| `mssdk_analyzer.py` | Python port of the comprehensive 8-phase MSSDK analyzer from `strong signature.html`. Analyzes any webmssdk.js file and generates real signatures via Playwright |

## New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/session-values` | POST, GET | Generate all 34 session values for any URL |
| `/api/session-values/download` | POST, GET | Generate and download the session_values.json file |
| `/api/mssdk-analyze` | POST, GET | Run 8-phase analysis on any webmssdk.js file |
| `/api/mssdk-sign` | POST, GET | Generate real X-Bogus / X-Gnarly / X-Mssdk-Info via Playwright |

## New UI Tabs

The web UI now has 5 tabs:

1. **Extract Data** (existing) — main TikTok URL extraction
2. **Database** (existing) — saved users database
3. **Session Login** (existing) — TikTok session management
4. **Session Values (34)** — generate all 34 session values for any URL
5. **MSSDK Analyzer** — 8-phase analysis + signature generator

## Auto-Run Behavior

The integrator runs **automatically** on every successful extraction, saving its output to:

```
data/sessions/<unique_id>_session_values.json
```

The extraction result includes the path and key values in `security_credentials`:

```python
result.security_credentials["session_values_path"]   # /path/to/session_values.json
result.security_credentials["session_values_x_bogus"]    # real X-Bogus via Playwright
result.security_credentials["session_values_ms_token"]    # msToken
result.security_credentials["session_values_verify_fp"]  # verifyFp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_PLAYWRIGHT` | `false` | Set to `true` to enable real webmssdk.js execution via Playwright |
| `PORT` | `5000` | HTTP server port |
| `HOST` | `0.0.0.0` | HTTP bind host |

## Deployment (Render.com)

The `render.yaml` file is pre-configured for production deployment:

- Builds with: `pip install -r requirements.txt && playwright install chromium`
- Starts with: `python app.py`
- `ENABLE_PLAYWRIGHT=true` is set by default in render.yaml
- Health check at: `/api/health`

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the server
python app.py

# Test the endpoints
curl -X POST http://localhost:5000/api/session-values \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.tiktok.com/@tiktok","enable_playwright":true}'
```

## Integration Flow

```
URL input
   ↓
extractor.extract(url)
   ↓
[All enrichment processors run]
   ↓
_enrich_with_session_values(result, url)  ← NEW
   ↓
tiktok_session_integrator.generate_session_values(url)
   ↓
├── Live HTML fetch (msToken, verifyFp, csrf_token)
├── Playwright + real webmssdk.js (X-Bogus)
├── xbogus.py fallback (if Playwright disabled)
├── hashlib (MD5, SHA-256, SHA-512)
└── CSPRNG (msToken variants, verifyFp, etc.)
   ↓
data/sessions/<unique_id>_session_values.json
```
