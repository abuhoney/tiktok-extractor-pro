# TikTok Extractor Pro 🎬

> استخراج جميع البيانات والصلاحيات والمفاتيح من أي رابط TikTok (فيديو / صورة / بث مباشر) — واجهة ويب احترافية + خادم Python + PWA قابلة للتثبيت كـ APK على الأندرويد.

## ✨ المميزات

| الميزة | الوصف |
|---|---|
| 🔗 **كل صيغ الروابط** | `vm.tiktok.com`, `vt.tiktok.com`, `m.tiktok.com`, `www.tiktok.com/@user/video/...`, `/live`, `/photo`, `@username` |
| ⚡ **7 استراتيجيات** | `__UNIVERSAL_DATA_FOR_REHYDRATION__` + `SIGI_STATE` + Webcast API + oEmbed + Meta Tags + DOM + yt-dlp fallback |
| 🛡️ **تفعيل تلقائي للوضع التجريبي** | عند حظر الخادم جغرافياً، يُولّد بيانات تجريبية واقعية مع لافتة صفراء توضح الأمر |
| 🌐 **دعم البروكسي** | `TIKTOK_PROXY` متغير بيئي يدعم HTTP/HTTPS/SOCKS5/SOCKS5h (لتجاوز الحظر الجغرافي) |
| 🔏 **موقّع X-Bogus** | نسختان: Python خالص + Playwright (للحصول على توقيع حقيقي عبر webmssdk.js) |
| 📱 **PWA + APK** | قابل للتثبيت كتطبيق على الأندرويد عبر TWA (Trusted Web Activity) |
| 🎁 **تحميل مباشر** | فيديو MP4 + صوت MP3 + غلاف JPG + صور منشور، عبر وكيل CORS |
| 🔒 **أمان** | `/api/proxy` بقائمة بيضاء للمضيفين (TikTok CDN فقط) |
| 🚀 **إنتاج جاهز** | waitress WSGI + render.yaml + Dockerfile + Procfile |

## 📁 هيكل المشروع

```
tiktok-extractor-pro/
├── app.py                    # Flask backend (waitress WSGI)
├── extractor.py              # 7-strategy extraction engine
├── xbogus.py                 # Pure-Python X-Bogus signer
├── xbogus_playwright.py      # Playwright-backed X-Bogus signer
├── requirements.txt          # Python deps
├── render.yaml               # Render Blueprint config
├── Dockerfile                # Container deploy
├── Procfile                  # Heroku/Render alt
├── .env.example              # Template env vars
├── templates/
│   └── index.html            # Flask Jinja2 UI (RTL Arabic)
├── static/
│   ├── style.css             # TikTok-themed dark UI
│   ├── app.js                # Frontend logic (fetch + render)
│   ├── manifest.json         # PWA manifest
│   ├── sw.js                 # Service Worker (offline cache)
│   └── icons/                # PWA icons (192×192, 512×512)
├── android/                  # TWA APK build scaffold
│   ├── app/build.gradle
│   ├── settings.gradle
│   ├── gradle.properties
│   └── assetlinks.json       # Digital Asset Links
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

الطريقة الأسهل: اربط GitHub repo بـ Render Blueprint.

1. ارفع الكود إلى GitHub repo جديد
2. في Render Dashboard → New → Blueprint
3. اختر الـ repo → سيقرأ `render.yaml` تلقائياً
4. أضف الأسرار (`BACKEND_SECRET`, `TIKTOK_PROXY` إن وُجد) في إعدادات الخدمة

أو عبر Render API:

```bash
curl -X POST https://api.render.com/v1/services \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  -d @render-service-payload.json
```

## 📱 بناء APK

### الطريقة 1: PWA Builder (الأسهل)
1. انشر الخدمة على Render واحصل على الرابط العام (e.g., `https://tiktok-extractor-pro.onrender.com`)
2. افتح https://www.pwabuilder.com/
3. أدخل الرابط → اضغط "Build My PWA" → اختر Android
4. حمّل الـ APK الناتج

### الطريقة 2: Bubblewrap (محلياً)
```bash
npm install -g @bubblewrap/cli
bubblewrap init --manifest https://tiktok-extractor-pro.onrender.com/manifest.json
bubblewrap build
# → الناتج: app-release-signed.apk
```

### الطريقة 3: Gradle (متقدم)
استخدم الـ scaffold في مجلد `android/`:
```bash
cd android
./gradlew assembleRelease
```

## 🔌 API endpoints

| Method | Path | الوصف |
|---|---|---|
| `GET` | `/` | واجهة الويب |
| `GET` | `/api/health` | فحص الصحة |
| `POST` | `/api/extract` | استخراج البيانات (body: `{"url": "...", "demo": false}`) |
| `GET` | `/api/proxy?url=...` | وكيل وسائط (لتجاوز CORS) |

### مثال على `/api/extract`

```bash
curl -X POST https://your-app.onrender.com/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://vt.tiktok.com/ZS9Sf13WjyFx4-tTufg/"}'
```

الاستجابة:
```json
{
  "success": true,
  "kind": "live",
  "url": "https://vt.tiktok.com/ZS9Sf13WjyFx4-tTufg/",
  "final_url": "https://www.tiktok.com/@humixc/live",
  "author": {
    "unique_id": "humixc",
    "nickname": "...",
    "follower_count": 123456,
    "sec_uid": "MS4wLjABAAAA..."
  },
  "stats": { "play_count": 9805, "digg_count": 842 },
  "raw_keys": ["universal_data"],
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
| `ENABLE_PLAYWRIGHT` | اختياري | `true` لتفعيل X-Bogus الحقيقي عبر Playwright |

## 🛡️ الأمان

- لا يتم تخزين أي بيانات على الخادم (كل المعالجة in-memory)
- `/api/proxy` لديه قائمة بيضاء صارمة — لا يمكن استخدامه كـ open proxy
- `.env` في `.gitignore` — لا يتم رفع الأسرار إلى GitHub

## 📝 الترخيص

MIT License — انظر `LICENSE`.

## 🙏 شكر وتقدير

- TikTok's `webmssdk.js` team (للخوارزمية الأصلية)
- Playwright (للمتصفح المؤتمت)
- Flask + waitress (للخادم)
- Render (للاستضافة المجانية)
