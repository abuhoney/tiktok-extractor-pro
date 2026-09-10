# TikTok Extractor Pro v3.0 🎬

> استخراج جميع البيانات والصلاحيات والمفاتيح من أي رابط TikTok (فيديو / صورة / بث مباشر) — **استخراج حقيقي فقط بدون وضع تجريبي** — واجهة ويب احترافية + خادم Python + PWA + APK قابل للتثبيت على الأندرويد.

## 🆕 ما الجديد في v3.0

| التغيير | الوصف |
|---|---|
| ❌ **إزالة الوضع التجريبي** | الاستخراج الآن حقيقي فقط — لا مزيد من البيانات المزيفة |
| ✅ **yt-dlp كاستراتيجية أساسية** | يعمل من أي IP بما في ذلك خوادم Render المتأثرة بالحظر الجغرافي |
| 📱 **بناء APK عبر GitHub Actions** | اضغط tag → APK يُبنى ويُرفع إلى GitHub Releases تلقائياً |
| 🔗 **Deep-link support** | `/.well-known/assetlinks.json` endpoint + Digital Asset Links |
| 📊 **/api/info endpoint** | معلومات الإصدار والاستراتيجيات المدعومة |
| 🔓 **استخراج كامل للصلاحيات والمفاتيح** | `sec_uid` + `csrf_token` + `wid` + `nonce` + `request_id` + `room_id` + `stream_id` + `encrypted_webid` + `region` |

## ✨ المميزات

| الميزة | الوصف |
|---|---|
| 🔗 **كل صيغ الروابط** | `vm.tiktok.com`, `vt.tiktok.com`, `m.tiktok.com`, `www.tiktok.com/@user/video/...`, `/live`, `/photo`, `@username` |
| ⚡ **7 استراتيجيات حقيقية** | `yt-dlp` + `UNIVERSAL_DATA` + `SIGI_STATE` + Webcast API + `oEmbed` + Meta Tags + DOM |
| 🛡️ **لا وضع تجريبي** | عند الفشل، يُرجع الخطأ الفعلي بدلاً من بيانات مزيفة |
| 🌐 **دعم البروكسي** | `TIKTOK_PROXY` متغير بيئي يدعم HTTP/HTTPS/SOCKS5/SOCKS5h |
| 🔏 **X-Bogus الحقيقي** | عبر Playwright + `webmssdk.js` الحقيقي من TikTok (عند `ENABLE_PLAYWRIGHT=true`) |
| 📱 **PWA + APK** | قابل للتثبيت كتطبيق على الأندرويد عبر GitHub Actions (TWA) |
| 🎁 **تحميل مباشر** | فيديو MP4 + صوت MP3 + غلاف JPG + صور منشور، عبر وكيل CORS |
| 🔒 **أمان** | `/api/proxy` بقائمة بيضاء للمضيفين (TikTok CDN فقط) |
| 🚀 **إنتاج جاهز** | waitress WSGI + render.yaml + Dockerfile + Procfile + GitHub Actions |

## 📁 هيكل المشروع

```
tiktok-extractor-pro/
├── app.py                    # Flask backend v3.0 (no demo)
├── extractor.py              # 7-strategy extraction engine (yt-dlp first)
├── xbogus.py                 # Pure-Python X-Bogus signer
├── xbogus_playwright.py      # Playwright-backed X-Bogus signer
├── requirements.txt          # Python deps (yt-dlp included)
├── render.yaml               # Render Blueprint config
├── Dockerfile                # Container deploy
├── Procfile                  # Heroku/Render alt
├── .env.example              # Template env vars
├── .github/workflows/
│   ├── build-apk.yml         # GitHub Action: tag → build APK → Release
│   └── deploy-render.yml     # GitHub Action: push → trigger Render deploy
├── templates/
│   └── index.html            # Flask Jinja2 UI (RTL Arabic) with PWA meta
├── static/
│   ├── style.css             # TikTok-themed dark UI
│   ├── app.js                # Frontend logic (no demo banner)
│   ├── manifest.json         # PWA manifest
│   ├── sw.js                 # Service Worker (offline cache)
│   └── icons/                # PWA icons (192×192, 512×512, maskable, etc.)
├── android/                  # TWA APK build scaffold (used by GitHub Action)
│   ├── twa-manifest.json
│   ├── assetlinks.json
│   └── app/...
└── README.md
```

## 🚀 التشغيل محلياً

```bash
# 1) انسخ المتغيرات
cp .env.example .env
# عدّل القيم حسب الحاجة

# 2) ثبّت المتطلبات
pip install -r requirements.txt

# 3) شغّل الخادم
python app.py
# → افتح http://localhost:10000
```

## ☁️ النشر على Render

الخدمة منشورة بالفعل على: **https://tiktok-extractor-pro.onrender.com**

كل push إلى `main` يُطلق deploy تلقائياً عبر GitHub Action (`deploy-render.yml`).

## 📱 بناء APK عبر GitHub Actions

### الطريقة التلقائية (الموصى بها)

1. اذهب إلى **Settings → Secrets and variables → Actions** في GitHub repo
2. أضف الأسرار التالية:
   - `ANDROID_KEYSTORE_BASE64` — base64-encoded keystore (موصى به للإنتاج)
   - `KEYSTORE_PASSWORD` — كلمة مرور keystore (default: `android`)
   - `RENDER_API_KEY` — لتفعيل auto-deploy على Render
3. لإنشاء الـ keystore:
   ```bash
   keytool -genkey -v -keystore android.keystore -alias android \
     -keyalg RSA -keysize 2048 -validity 10000 \
     -storepass android -keypass android \
     -dname "CN=TikTok Extractor Pro, OU=Dev, O=abuhoney, L=Dubai, ST=Dubai, C=AE"
   base64 -i android.keystore | tr -d '\n'  # الصق الناتج في الـ secret
   ```
4. لتشغيل البناء:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
   أو يدوياً: اذهب إلى **Actions → Build Android APK (TWA) → Run workflow**
5. سيُبنى الـ APK ويُرفع تلقائياً إلى **GitHub Releases**
6. بعد أول بناء ناجح، انسخ `APK_SIGNING_SHA256` من سجل البناء واضبطها كمتغير بيئي على Render لتفعيل deep links

### الطريقة اليدوية (بديل)

1. حمّل الـ APK من **Releases**
2. فعّل "Install unknown apps" في إعدادات الأندرويد
3. اضغط على APK للتثبيت
4. افتح التطبيق — سيحمّل PWA من `https://tiktok-extractor-pro.onrender.com`

## 🔌 API endpoints

| Method | Path | الوصف |
|---|---|---|
| `GET` | `/` | واجهة الويب |
| `GET` | `/api/health` | فحص الصحة (يُرجع أيضاً `demo_mode: false`) |
| `GET` | `/api/info` | معلومات الإصدار والاستراتيجيات |
| `POST` | `/api/extract` | استخراج البيانات (body: `{"url": "..."}`) |
| `GET` | `/api/extract?url=...` | نفس الشيء بـ GET |
| `GET` | `/api/proxy?url=...` | وكيل وسائط (لتجاوز CORS) |
| `GET` | `/.well-known/assetlinks.json` | Digital Asset Links (للـ TWA) |

### مثال على `/api/extract`

```bash
curl -X POST https://tiktok-extractor-pro.onrender.com/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://vt.tiktok.com/ZS9Sf13WjyFx4-tTufg/"}'
```

الاستجابة (حقيقية — بدون وضع تجريبي):
```json
{
  "success": true,
  "kind": "live",
  "url": "https://vt.tiktok.com/ZS9Sf13WjyFx4-tTufg/",
  "final_url": "https://www.tiktok.com/@humixc/live",
  "title": "...",
  "author": {
    "unique_id": "humixc",
    "nickname": "...",
    "follower_count": 123456,
    "sec_uid": "MS4wLjABAAAA..."
  },
  "stats": {
    "play_count": 9805,
    "digg_count": 842
  },
  "all_ids": {
    "csrf_token": "yaS6NnXt-...",
    "wid": "7683683828103923222",
    "nonce": "S7AGzvfjw7ya...",
    "request_id": "24109791788997053532",
    "encrypted_webid": "1|0E__VK...",
    "region": "RO"
  },
  "raw_keys": ["yt_dlp", "enriched_from_html"],
  "extracted_at": "2026-09-10T..."
}
```

## 🔐 المتغيرات البيئية

انظر `.env.example` للقائمة الكاملة. الأهم:

| المتغير | مطلوب؟ | الوصف |
|---|---|---|
| `PORT` | نعم | منفذ الخادم (Render يضبطه تلقائياً) |
| `BACKEND_SECRET` | مُستحسن | مفتاح سري عشوائي للجلسات |
| `TIKTOK_PROXY` | اختياري | `http://` أو `socks5://` لتجاوز الحظر الجغرافي |
| `ENABLE_PLAYWRIGHT` | اختياري | `true` لتفعيل X-Bogus الحقيقي عبر Playwright (يتطلب خطة Render Standard) |
| `APK_SIGNING_SHA256` | اختياري | بصمة SHA-256 لمفتاح توقيع الـ APK (لتفعيل deep links) |

## 🛡️ الأمان

- لا يتم تخزين أي بيانات على الخادم (كل المعالجة in-memory)
- `/api/proxy` لديه قائمة بيضاء صارمة — لا يمكن استخدامه كـ open proxy
- `.env` في `.gitignore` — لا يتم رفع الأسرار إلى GitHub
- GitHub Actions secrets تُستخدم للـ keystore + Render API key — لا تُسجّل في السجلات

## 📝 الترخيص

MIT License — انظر `LICENSE`.
