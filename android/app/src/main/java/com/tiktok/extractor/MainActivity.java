package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AppCompatActivity;
import com.google.android.material.floatingactionbutton.FloatingActionButton;
import org.json.JSONObject;
import java.net.URLEncoder;
import java.util.HashMap;
import java.util.Map;

/**
 * MainActivity — TikTok Extractor Pro v4.6
 *
 * 3 Floating Action Buttons:
 *   - fabDatabase (cyan): Monitor users/sessions database
 *   - fabLogin (orange): TikTok login capture — opens WebView, captures ALL cookies after manual login
 *   - fabInteract (pink): Quick extract current URL
 *
 * v4.6 KEY FEATURE: Login Event Capture
 *   When user taps fabLogin:
 *     1. Opens a full-screen WebView to tiktok.com/login
 *     2. User enters email/password MANUALLY (no automation)
 *     3. After successful login, captures ALL cookies: sessionid, ttwid, msToken, sid_tt, etc.
 *     4. Uploads them to GitHub via /api/session endpoint
 *     5. Returns to main PWA view
 *
 * This is legitimate: user performs the login themselves, we just capture the resulting cookies.
 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "TikTokExtractor";
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";
    private static final String TIKTOK_LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email";
    private static final String TIKTOK_HOME_URL = "https://www.tiktok.com/";
    private static final String PREFS_NAME = "tiktok_session_prefs";

    private WebView webView;
    private WebView loginWebView;  // Separate WebView for login capture
    private ProgressBar progressBar;
    private TextView errorView;
    private FloatingActionButton fabDatabase, fabLogin, fabInteract;
    private SharedPreferences prefs;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "MainActivity.onCreate() v4.6 starting");
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);

        try {
            setContentView(R.layout.activity_main);
            progressBar = findViewById(R.id.progressBar);
            errorView = findViewById(R.id.errorView);
            webView = findViewById(R.id.webview);
            fabDatabase = findViewById(R.id.fabDatabase);
            fabLogin = findViewById(R.id.fabLogin);
            fabInteract = findViewById(R.id.fabInteract);

            if (webView == null) {
                showError("خطأ داخلي: WebView غير متوفر.\nأعد تثبيت التطبيق.");
                return;
            }

            setupMainWebView();
            setupFABs();

            if (savedInstanceState != null) {
                webView.restoreState(savedInstanceState);
            } else {
                webView.loadUrl(PWA_URL);
            }

            // إخفاء أزرار FAB حتى تُحمّل الصفحة
            hideAllFABs();

        } catch (Exception e) {
            Log.e(TAG, "onCreate() crashed", e);
            showError("خطأ في بدء التطبيق:\n" + e.getMessage());
        }
    }

    /**
     * يُعدّد WebView الرئيسي (لوحة التحكم PWA)
     */
    @SuppressLint("SetJavaScriptEnabled")
    private void setupMainWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 14; TikTokExtractorPro) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
        );

        // ─── v5.2: JavascriptInterface لفتح الروابط داخل التطبيق ───
        webView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void openInTikTok(String url) {
                openInAppBrowserInternal(url);
            }
            @android.webkit.JavascriptInterface
            public void openInAppBrowser(String url) {
                openInAppBrowserInternal(url);
            }
        }, "Android");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                if (progressBar != null) {
                    progressBar.setVisibility(View.VISIBLE);
                    progressBar.setProgress(0);
                }
                if (errorView != null) errorView.setVisibility(View.GONE);
                if (webView != null) webView.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (progressBar != null) progressBar.setVisibility(View.GONE);
                showAllFABs();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    String desc = error != null && error.getDescription() != null
                            ? error.getDescription().toString() : "خطأ غير معروف";
                    showError("تعذّر تحميل الصفحة:\n" + desc);
                }
            }
        });

        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (progressBar != null) {
                    progressBar.setProgress(newProgress);
                    if (newProgress >= 100) progressBar.setVisibility(View.GONE);
                }
            }
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
                Log.d(TAG, "JS[" + cm.messageLevel() + "]: " + cm.message());
                return true;
            }
        });
    }

    /**
     * يُعدّد أزرار FAB الثلاثة
     */
    private void setupFABs() {
        // ─── زر قاعدة البيانات ───
        if (fabDatabase != null) {
            fabDatabase.setOnClickListener(v -> {
                hideAllFABs();
                runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"database\"]'); if (t) t.click(); })();");
                toast("📊 فتح قاعدة البيانات");
                v.postDelayed(this::showAllFABs, 800);
            });
            fabDatabase.setOnLongClickListener(v -> {
                showToolsMenu();
                return true;
            });
        }

        // ─── زر تسجيل الدخول (التقاط الأحداث) ───
        if (fabLogin != null) {
            fabLogin.setOnClickListener(v -> {
                Log.i(TAG, "FAB Login clicked — opening TikTok login WebView");
                openLoginCaptureWebView();
            });
            fabLogin.setOnLongClickListener(v -> {
                // ضغطة طويلة: فحص الجلسة المحفوظة سابقاً
                checkSavedSession();
                return true;
            });
        }

        // ─── زر التفاعل: يفتح الرابط داخل WebView داخل التطبيق ───
        if (fabInteract != null) {
            fabInteract.setOnClickListener(v -> {
                Log.i(TAG, "FAB Interact clicked — opening URL in in-app WebView");
                runJs("(() => {"
                    + "  const input = document.getElementById('urlInput');"
                    + "  const url = input ? input.value : '';"
                    + "  if (url && url.length > 0) {"
                    + "    Android.openInAppBrowser(url);"
                    + "  } else {"
                    + "    alert('الصق رابط TikTok أولاً في حقل البحث');"
                    + "  }"
                    + "})();");
            });
            fabInteract.setOnLongClickListener(v -> {
                // ضغطة طويلة: استخراج عميق (يحفظ page.html + webmssdk.js + json/)
                runJs("(() => {"
                    + "  const input = document.getElementById('urlInput');"
                    + "  const url = input ? input.value : '';"
                    + "  if (!url) { alert('الصق رابطاً أولاً'); return; }"
                    + "  fetch('/api/deep/extract', {"
                    + "    method: 'POST',"
                    + "    headers: {'Content-Type': 'application/json'},"
                    + "    body: JSON.stringify({url: url})"
                    + "  }).then(r => r.json()).then(d => {"
                    + "    if (d.success) {"
                    + "      alert('✅ استخراج عميق ناجح!\\n👤 @' + d.unique_id + '\\n📁 live' + d.live_number + '\\n📦 ' + d.files_saved.length + ' ملفات\\n🔑 webmssdk: ' + (d.webmssdk.success ? 'نعم' : 'لا'));"
                    + "    } else {"
                    + "      alert('❌ فشل: ' + (d.error || 'unknown'));"
                    + "    }"
                    + "  }).catch(e => alert('خطأ: ' + e));"
                    + "})();");
                toast("🔬 استخراج عميق");
                return true;
            });
        }
    }

    // ════════════════════════════════════════════════════════════════
    //  v4.6: Login Event Capture — يفتح WebView لتسجيل دخول يدوي
    //         ويلتقط كل الكوكيز تلقائياً بعد النجاح
    // ════════════════════════════════════════════════════════════════

    /**
     * يفتح WebView مستقل لتسجيل الدخول إلى TikTok.
     * المستخدم يُدخل بريده/كلمة مروره بنفسه (لا أتمتة).
     * بعد النجاح، يلتقط النظام كل الكوكيز ويُرسلها إلى /api/session.
     */
    @SuppressLint("SetJavaScriptEnabled")
    private void openLoginCaptureWebView() {
        toast("🔐 افتح TikTok وسجّل دخولك بنفسك");

        // إنشاء WebView مستقل بشكل برمجي
        loginWebView = new WebView(this);
        WebSettings settings = loginWebView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        );

        // تفعيل cookies (مهم جداً)
        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(loginWebView, true);

        // إخفاء الـ WebView الرئيسي وإظهار login WebView
        webView.setVisibility(View.GONE);
        hideAllFABs();
        // إضافة login WebView إلى الـ layout
        android.view.ViewGroup root = (android.view.ViewGroup) webView.getParent();
        root.addView(loginWebView, new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT
        ));

        // مُراقب لالتقاط الكوكيز بعد كل تحميل صفحة
        loginWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                Log.i(TAG, "Login page loaded: " + url);

                // افحص الكوكيز بعد كل تنقل
                String cookies = cookieManager.getCookie("https://www.tiktok.com/");
                if (cookies != null && cookies.contains("sessionid")) {
                    Log.i(TAG, "✓ sessionid detected in cookies — capturing!");
                    captureAndUploadSession(cookies, url);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                Log.e(TAG, "Login WebView error: " + (error != null ? error.getDescription() : "unknown"));
            }
        });

        // زر إغلاق (يظهر كـ overlay)
        TextView closeBtn = new TextView(this);
        closeBtn.setText("✕ إغلاق");
        closeBtn.setBackgroundColor(0xCC000000);
        closeBtn.setTextColor(0xFFFFFFFF);
        closeBtn.setPadding(24, 12, 24, 12);
        closeBtn.setTextSize(14);
        closeBtn.setOnClickListener(v -> closeLoginWebView());
        android.widget.FrameLayout.LayoutParams closeParams = new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT,
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT
        );
        closeParams.gravity = android.view.Gravity.TOP | android.view.Gravity.RIGHT;
        closeParams.topMargin = 40;
        closeParams.rightMargin = 20;
        ((android.view.ViewGroup) webView.getParent()).addView(closeBtn, closeParams);

        // تعليمات للمستخدم
        TextView hint = new TextView(this);
        hint.setText("📝 سجّل دخولك إلى TikTok أدناه.\nسيتم التقاط الجلسة تلقائياً عند النجاح.");
        hint.setBackgroundColor(0xCC000000);
        hint.setTextColor(0xFFFFFFFF);
        hint.setPadding(24, 16, 24, 16);
        hint.setTextSize(12);
        android.widget.FrameLayout.LayoutParams hintParams = new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT
        );
        hintParams.gravity = android.view.Gravity.BOTTOM;
        hintParams.bottomMargin = 20;
        ((android.view.ViewGroup) webView.getParent()).addView(hint, hintParams);

        // تحميل صفحة تسجيل الدخول
        loginWebView.loadUrl(TIKTOK_LOGIN_URL);
    }

    /**
     * يلتقط كل الكوكيز من CookieManager ويُرسلها إلى /api/session على Render.
     */
    private void captureAndUploadSession(String cookieString, String currentUrl) {
        Log.i(TAG, "Capturing cookies, length=" + cookieString.length());

        // تحليل الكوكيز إلى خريطة
        Map<String, String> cookies = parseCookies(cookieString);
        String sessionid = cookies.get("sessionid");
        String uniqueId = prefs.getString("last_unique_id", "");

        if (sessionid == null || sessionid.isEmpty()) {
            Log.w(TAG, "sessionid not found in cookies — skipping");
            return;
        }

        Log.i(TAG, "✓ Captured sessionid (length=" + sessionid.length() + ")");

        // إذا لم يكن لدينا unique_id، اسأل المستخدم
        if (uniqueId.isEmpty()) {
            // استخرج من الكوكيز إن أمكن (مثل unique_id cookie أو sid_tt)
            uniqueId = cookies.getOrDefault("unique_id", cookies.getOrDefault("sid_tt", "user"));
        }
        final String finalCaptureUniqueId = uniqueId;

        // بناء JSON payload
        try {
            JSONObject extraCookies = new JSONObject();
            for (String key : new String[]{"ttwid", "msToken", "sid_tt", "passport_csrf_token",
                                            "passport_csrf_token_default", "sid_guard", "uid_tt",
                                            "uid_tt_ss", "tt_chain_token", "cmpl_token"}) {
                String val = cookies.get(key);
                if (val != null && !val.isEmpty()) {
                    extraCookies.put(key, val);
                }
            }

            JSONObject payload = new JSONObject();
            payload.put("unique_id", uniqueId);
            payload.put("sessionid", sessionid);
            payload.put("extra_cookies", extraCookies);

            Log.i(TAG, "Uploading session for: " + uniqueId);

            // إرسال إلى /api/session عبر thread خلفي
            new Thread(() -> {
                try {
                    java.net.URL url = new java.net.URL(PWA_URL + "api/session");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(30000);

                    byte[] body = payload.toString().getBytes("UTF-8");
                    java.io.OutputStream os = conn.getOutputStream();
                    os.write(body);
                    os.close();

                    int code = conn.getResponseCode();
                    String response = readResponse(conn);
                    Log.i(TAG, "Session upload response: " + code + " — " + response.substring(0, Math.min(200, response.length())));

                    runOnUiThread(() -> {
                        if (code == 200) {
                            toast("✅ تم التقاط الجلسة وحفظها في GitHub!");
                            // احفظ unique_id للاستخدام لاحقاً
                            prefs.edit().putString("last_unique_id", finalCaptureUniqueId).apply();
                            // أغلق WebView بعد نجاح الالتقاط
                            closeLoginWebView();
                        } else {
                            toast("⚠️ فشل حفظ الجلسة: HTTP " + code);
                        }
                    });
                } catch (Exception e) {
                    Log.e(TAG, "Session upload failed", e);
                    runOnUiThread(() -> toast("❌ خطأ شبكي: " + e.getMessage()));
                }
            }).start();

        } catch (Exception e) {
            Log.e(TAG, "Failed to build session payload", e);
        }
    }

    /**
     * يُحلّل سلسلة الكوكيز (key=value; key=value) إلى خريطة.
     */
    private Map<String, String> parseCookies(String cookieString) {
        Map<String, String> cookies = new HashMap<>();
        if (cookieString == null) return cookies;
        String[] parts = cookieString.split(";");
        for (String part : parts) {
            int eq = part.indexOf('=');
            if (eq > 0) {
                String key = part.substring(0, eq).trim();
                String value = part.substring(eq + 1).trim();
                cookies.put(key, value);
            }
        }
        return cookies;
    }

    /**
     * يقرأ استجابة HTTP كنص.
     */
    private String readResponse(java.net.HttpURLConnection conn) {
        try {
            java.io.InputStream is = conn.getResponseCode() >= 400
                    ? conn.getErrorStream() : conn.getInputStream();
            java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.InputStreamReader(is, "UTF-8"));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            return sb.toString();
        } catch (Exception e) {
            return "(error reading response: " + e.getMessage() + ")";
        }
    }

    /**
     * يُغلق WebView تسجيل الدخول ويعيد الواجهة الرئيسية.
     */
    private void closeLoginWebView() {
        if (loginWebView != null) {
            try {
                android.view.ViewGroup root = (android.view.ViewGroup) webView.getParent();
                root.removeView(loginWebView);
                loginWebView.destroy();
                loginWebView = null;
            } catch (Exception e) {
                Log.e(TAG, "closeLoginWebView failed", e);
            }
        }
        webView.setVisibility(View.VISIBLE);
        showAllFABs();
    }

    /**
     * يفحص ما إذا كانت هناك جلسة محفوظة للمستخدم.
     */
    private void checkSavedSession() {
        String uniqueId = prefs.getString("last_unique_id", "");
        if (uniqueId.isEmpty()) {
            toast("ℹ️ لا توجد جلسة محفوظة. استخدم زر تسجيل الدخول أولاً.");
            return;
        }
        toast("🔍 فحص الجلسة المحفوظة لـ " + uniqueId);
        runJs("(() => { fetch('/api/session/" + uniqueId + "/status').then(r=>r.json()).then(d=>{ alert(d.has_session ? '✅ جلسة محفوظة!\\nبتاريخ: ' + d.saved_at : 'ℹ️ لا توجد جلسة محفوظة'); }).catch(e=>alert('خطأ: '+e)); })();");
    }

    // ════════════════════════════════════════════════════════════════
    //  أدوات مساعدة عامة
    // ════════════════════════════════════════════════════════════════

    // ════════════════════════════════════════════════════════════════
    //  v5.2: In-App Browser — فتح الروابط داخل WebView داخل التطبيق
    //         مع مراقبة الجلسة والتقاط الكوكيز تلقائياً
    // ════════════════════════════════════════════════════════════════

    private WebView browserWebView;
    private TextView browserStatusBar;
    private boolean sessionCapturedInBrowser = false;

    @SuppressLint("SetJavaScriptEnabled")
    private void openInAppBrowserInternal(String url) {
        Log.i(TAG, "Opening in-app browser: " + url);
        toast("🌐 فتح داخل التطبيق...");

        sessionCapturedInBrowser = false;

        // إنشاء WebView مستقل للتصفح
        browserWebView = new WebView(this);
        WebSettings settings = browserWebView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        settings.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        );

        // تفعيل cookies
        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(browserWebView, true);

        // إخفاء الـ WebView الرئيسي وإظهار browser WebView
        webView.setVisibility(View.GONE);
        hideAllFABs();

        android.view.ViewGroup root = (android.view.ViewGroup) webView.getParent();
        root.addView(browserWebView, new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT
        ));

        // شريط حالة الجلسة (يظهر في الأعلى)
        browserStatusBar = new TextView(this);
        browserStatusBar.setText("🌐 تصفح TikTok | 🔍 جاري مراقبة الجلسة...");
        browserStatusBar.setBackgroundColor(0xCC000000);
        browserStatusBar.setTextColor(0xFFFFFFFF);
        browserStatusBar.setPadding(24, 16, 24, 16);
        browserStatusBar.setTextSize(12);
        android.widget.FrameLayout.LayoutParams statusParams = new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT
        );
        statusParams.gravity = android.view.Gravity.TOP;
        root.addView(browserStatusBar, statusParams);

        // زر إغلاق
        TextView closeBtn = new TextView(this);
        closeBtn.setText("✕ إغلاق");
        closeBtn.setBackgroundColor(0xCCFF2D55);
        closeBtn.setTextColor(0xFFFFFFFF);
        closeBtn.setPadding(24, 12, 24, 12);
        closeBtn.setTextSize(14);
        closeBtn.setOnClickListener(v -> closeInAppBrowser());
        android.widget.FrameLayout.LayoutParams closeParams = new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT,
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT
        );
        closeParams.gravity = android.view.Gravity.TOP | android.view.Gravity.RIGHT;
        closeParams.topMargin = 60;
        closeParams.rightMargin = 16;
        root.addView(closeBtn, closeParams);

        // زر فحص الجلسة
        TextView checkSessionBtn = new TextView(this);
        checkSessionBtn.setText("🔍 فحص الجلسة");
        checkSessionBtn.setBackgroundColor(0xCC25F4EE);
        checkSessionBtn.setTextColor(0xFF000000);
        checkSessionBtn.setPadding(24, 12, 24, 12);
        checkSessionBtn.setTextSize(14);
        checkSessionBtn.setOnClickListener(v -> checkAndCaptureSession());
        android.widget.FrameLayout.LayoutParams checkParams = new android.widget.FrameLayout.LayoutParams(
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT,
            android.widget.FrameLayout.LayoutParams.WRAP_CONTENT
        );
        checkParams.gravity = android.view.Gravity.TOP | android.view.Gravity.LEFT;
        checkParams.topMargin = 60;
        checkParams.leftMargin = 16;
        root.addView(checkSessionBtn, checkParams);

        // مُراقب لالتقاط الكوكيز بعد كل تحميل صفحة
        browserWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                Log.i(TAG, "Browser page loaded: " + url);

                // افحص الكوكيز بعد كل تنقل
                String cookies = cookieManager.getCookie("https://www.tiktok.com/");
                if (cookies != null) {
                    if (cookies.contains("sessionid") && !sessionCapturedInBrowser) {
                        sessionCapturedInBrowser = true;
                        Log.i(TAG, "✓ sessionid detected in browser cookies!");
                        if (browserStatusBar != null) {
                            browserStatusBar.setText("🌐 تصفح TikTok | ✅ تم التقاط الجلسة! (sessionid مكتشف)");
                            browserStatusBar.setBackgroundColor(0xCC00D68F);
                        }
                        toast("✅ تم اكتشاف sessionid! الجلسة نشطة");
                        // التقط وحفظ الجلسة
                        captureAndUploadSession(cookies, url);
                    } else if (!sessionCapturedInBrowser) {
                        // حدّث الشريط
                        if (browserStatusBar != null) {
                            boolean hasSid = cookies.contains("sid_tt");
                            boolean hasTtwid = cookies.contains("ttwid");
                            if (hasSid || hasTtwid) {
                                browserStatusBar.setText("🌐 تصفح TikTok | 🟡 جلسة جزئية (لا sessionid بعد) — سجّل دخولك");
                            } else {
                                browserStatusBar.setText("🌐 تصفح TikTok | 🔍 جاري مراقبة الجلسة...");
                            }
                        }
                    }
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                Log.e(TAG, "Browser error: " + (error != null ? error.getDescription() : "unknown"));
            }
        });

        // تحميل الرابط
        browserWebView.loadUrl(url);
    }

    /**
     * يفحص الجلسة الحالية في الـ browser WebView ويعرض حالتها.
     */
    private void checkAndCaptureSession() {
        if (browserWebView == null) {
            toast("ℹ️ لا يوجد متصفح نشط");
            return;
        }

        CookieManager cookieManager = CookieManager.getInstance();
        String cookies = cookieManager.getCookie("https://www.tiktok.com/");

        if (cookies == null || cookies.isEmpty()) {
            toast("ℹ️ لا توجد كوكيز — تصفّح صفحة TikTok أولاً");
            return;
        }

        // تحليل الكوكيز
        Map<String, String> cookieMap = parseCookies(cookies);
        boolean hasSessionid = cookieMap.containsKey("sessionid") && !cookieMap.get("sessionid").isEmpty();
        boolean hasSidTt = cookieMap.containsKey("sid_tt") && !cookieMap.get("sid_tt").isEmpty();
        boolean hasTtwid = cookieMap.containsKey("ttwid") && !cookieMap.get("ttwid").isEmpty();
        boolean hasMsToken = cookieMap.containsKey("msToken") && !cookieMap.get("msToken").isEmpty();

        StringBuilder status = new StringBuilder();
        status.append("🔍 فحص الجلسة الحالية:\n\n");
        status.append("🔑 sessionid: ").append(hasSessionid ? "✅ موجود" : "❌ غير موجود").append("\n");
        status.append("🔑 sid_tt: ").append(hasSidTt ? "✅ موجود" : "❌ غير موجود").append("\n");
        status.append("🔑 ttwid: ").append(hasTtwid ? "✅ موجود" : "❌ غير موجود").append("\n");
        status.append("🔑 msToken: ").append(hasMsToken ? "✅ موجود" : "❌ غير موجود").append("\n");
        status.append("\n📊 إجمالي الكوكيز: ").append(cookieMap.size()).append("\n\n");

        if (hasSessionid) {
            status.append("✅ الجلسة كاملة ونشطة!");
            if (browserStatusBar != null) {
                browserStatusBar.setText("🌐 تصفح TikTok | ✅ جلسة نشطة (sessionid موجود)");
                browserStatusBar.setBackgroundColor(0xCC00D68F);
            }
            // التقط وحفظ
            captureAndUploadSession(cookies, browserWebView.getUrl() != null ? browserWebView.getUrl() : "");
            status.append("\n💾 تم حفظ الجلسة تلقائياً");
        } else if (hasSidTt || hasTtwid) {
            status.append("🟡 جلسة جزئية — سجّل دخولك للحصول على sessionid");
            if (browserStatusBar != null) {
                browserStatusBar.setText("🌐 تصفح TikTok | 🟡 جلسة جزئية — سجّل دخولك");
                browserStatusBar.setBackgroundColor(0xCCFFB547);
            }
        } else {
            status.append("❌ لا توجد جلسة — سجّل دخولك إلى TikTok");
            if (browserStatusBar != null) {
                browserStatusBar.setText("🌐 تصفح TikTok | ❌ لا جلسة — سجّل دخولك");
                browserStatusBar.setBackgroundColor(0xCCFF4D6D);
            }
        }

        final String finalStatus = status.toString();
        runOnUiThread(() -> {
            new android.app.AlertDialog.Builder(this)
                .setTitle("🔍 فحص الجلسة")
                .setMessage(finalStatus)
                .setPositiveButton("حسناً", null)
                .show();
        });
    }

    /**
     * يُغلق متصفح الـ in-app browser ويعيد الواجهة الرئيسية.
     */
    private void closeInAppBrowser() {
        if (browserWebView != null) {
            try {
                android.view.ViewGroup root = (android.view.ViewGroup) webView.getParent();
                root.removeView(browserWebView);
                root.removeView(browserStatusBar);
                browserWebView.destroy();
                browserWebView = null;
                browserStatusBar = null;
            } catch (Exception e) {
                Log.e(TAG, "closeInAppBrowser failed", e);
            }
        }
        webView.setVisibility(View.VISIBLE);
        showAllFABs();
        toast("✅ عودة للواجهة الرئيسية");
    }

    // ════════════════════════════════════════════════════════════════
    //  v5.1: قائمة الأدوات الشاملة — 20 خيار
    // ════════════════════════════════════════════════════════════════

    private void showToolsMenu() {
        String[] options = {
            "📊 الإحصائيات الشاملة",
            "🔬 استخراج عميق",
            "📋 قائمة المستخدمين",
            "📈 إحصائيات البثوث",
            "👥 إحصائيات المعجبين",
            "🔐 تبويب الجلسة",
            "🌐 فتح الرابط في المتصفح الداخلي",
            "🔄 مزامنة GitHub",
            "📥 تنزيل webmssdk.js",
            "─── تفاعلات ───",
            "👍 إعجاب بالبث",
            "👥 متابعة المستخدم",
            "💬 إرسال تعليق",
            "🚪 دخول غرفة بث",
            "📊 إحصائيات التفاعل",
            "─── مراقبة ───",
            "📡 مراقبة بث مباشر",
            "🔍 تقرير المراقبة الشامل",
            "─── تنزيل ZIP ───",
            "📦 تنزيل كل شيء (ZIP)"
        };

        android.app.AlertDialog.Builder builder = new android.app.AlertDialog.Builder(this);
        builder.setTitle("🛠️ أدوات v5.1")
               .setItems(options, (dialog, which) -> {
                   switch (which) {
                       case 0: fetchAndShowJson("الإحصائيات", "/api/stats/headline"); break;
                       case 1: triggerDeepExtract(); break;
                       case 2: fetchAndShowJson("المستخدمون", "/api/deep/users"); break;
                       case 3: fetchAndShowJson("البثوث", "/api/stats/streams"); break;
                       case 4: fetchAndShowJson("المعجبون", "/api/stats/fans"); break;
                       case 5: openSessionTab(); break;
                       case 6: openInTikTokFromInput(); break;
                       case 7: syncGitHub(); break;
                       case 8: downloadLatestWebmssdk(); break;
                       case 9: break; // separator
                       case 10: executeInteraction("send_like"); break;
                       case 11: executeInteraction("follow_user"); break;
                       case 12: executeInteraction("send_comment"); break;
                       case 13: executeInteraction("enter_live_room"); break;
                       case 14: fetchAndShowJson("إحصائيات التفاعل", "/api/react/stats"); break;
                       case 15: break; // separator
                       case 16: fetchAndShowJson("مراقبة البث", "/api/monitor/live/all"); break;
                       case 17: fetchAndShowJson("تقرير المراقبة", "/api/monitor/report"); break;
                       case 18: break; // separator
                       case 19: downloadZip("all"); break;
                   }
               })
               .setNegativeButton("إغلاق", null)
               .show();
    }

    private void fetchAndShowJson(String title, String endpoint) {
        toast("⏳ جاري جلب " + title + "...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + (endpoint.startsWith("/") ? endpoint.substring(1) : endpoint));
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    // نسّق JSON
                    try {
                        JSONObject d = new JSONObject(resp);
                        final String formatted = d.toString(2);
                        runOnUiThread(() -> {
                            new android.app.AlertDialog.Builder(MainActivity.this)
                                .setTitle("📊 " + title)
                                .setMessage(formatted.length() > 4000 ? formatted.substring(0, 4000) + "..." : formatted)
                                .setPositiveButton("حسناً", null)
                                .show();
                        });
                    } catch (Exception e) {
                        final String raw = resp;
                        runOnUiThread(() -> {
                            new android.app.AlertDialog.Builder(MainActivity.this)
                                .setTitle("📊 " + title)
                                .setMessage(raw.length() > 4000 ? raw.substring(0, 4000) : raw)
                                .setPositiveButton("حسناً", null)
                                .show();
                        });
                    }
                } else {
                    runOnUiThread(() -> toast("❌ HTTP " + code));
                }
            } catch (Exception e) {
                Log.e(TAG, "fetchAndShowJson failed", e);
                runOnUiThread(() -> toast("❌ " + e.getMessage()));
            }
        }).start();
    }

    private void executeInteraction(String action) {
        // استخرج room_id/sec_uid من حقل الإدخال أولاً
        runJs("(() => {"
            + "  const input = document.getElementById('urlInput');"
            + "  const url = input ? input.value : '';"
            + "  if (!url) { alert('الصق رابطاً أولاً'); return; }"
            + "  fetch('/api/extract?url=' + encodeURIComponent(url))"
            + "    .then(r => r.json()).then(d => {"
            + "      if (!d.success) { alert('فشل الاستخراج'); return; }"
            + "      const body = {action: '" + action + "'};"
            + "      if (d.all_ids) {"
            + "        body.room_id = d.all_ids.room_id || '';"
            + "        body.sec_uid = d.all_ids.sec_uid || '';"
            + "        body.user_id = d.all_ids.user_id || '';"
            + "        body.video_id = d.content_id || '';"
            + "      }"
            + "      fetch('/api/react/execute', {"
            + "        method: 'POST',"
            + "        headers: {'Content-Type': 'application/json'},"
            + "        body: JSON.stringify(body)"
            + "      }).then(r => r.json()).then(result => {"
            + "        alert('نتيجة: ' + (result.success ? '✅ نجح' : '❌ فشل') + '\\n' + JSON.stringify(result, null, 2).substring(0, 500));"
            + "      }).catch(e => alert('خطأ: ' + e));"
            + "    }).catch(e => alert('خطأ استخراج: ' + e));"
            + "})();");
        toast("⚡ " + action);
    }

    private void triggerDeepExtract() {
        runJs("(() => {"
            + "  const input = document.getElementById('urlInput');"
            + "  const url = input ? input.value : '';"
            + "  if (!url) { alert('الصق رابطاً أولاً'); return; }"
            + "  fetch('/api/deep/extract', {"
            + "    method: 'POST',"
            + "    headers: {'Content-Type': 'application/json'},"
            + "    body: JSON.stringify({url: url})"
            + "  }).then(r => r.json()).then(d => {"
            + "    if (d.success) {"
            + "      alert('✅ استخراج ناجح!\\n👤 @' + d.unique_id + '\\n📁 live' + d.live_number + '\\n📦 ' + d.files_saved.length + ' ملفات');"
            + "    } else {"
            + "      alert('❌ فشل: ' + (d.error || 'unknown'));"
            + "    }"
            + "  }).catch(e => alert('خطأ: ' + e));"
            + "})();");
    }

    private void openSessionTab() {
        hideAllFABs();
        runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"session\"]'); if (t) t.click(); })();");
        webView.postDelayed(this::showAllFABs, 800);
    }

    private void openInTikTokFromInput() {
        runJs("(() => {"
            + "  const input = document.getElementById('urlInput');"
            + "  const url = input ? input.value : '';"
            + "  if (url) { Android.openInAppBrowser(url); }"
            + "  else { alert('الصق رابطاً أولاً'); }"
            + "})();");
    }

    private void syncGitHub() {
        toast("⏳ جاري المزامنة...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/sync-db");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.getOutputStream().write("{}".getBytes("UTF-8"));
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                final JSONObject d = new JSONObject(resp);
                runOnUiThread(() -> toast(d.optBoolean("success") ? "✅ تمت مزامنة " + d.optInt("pushed_files") + " ملف" : "⚠️ " + d.optString("error")));
            } catch (Exception e) {
                runOnUiThread(() -> toast("❌ " + e.getMessage()));
            }
        }).start();
    }

    private void downloadLatestWebmssdk() {
        toast("⏳ البحث عن webmssdk.js...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/deep/users");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                String resp = readResponse(conn);
                final JSONObject d = new JSONObject(resp);
                final JSONArray users = d.optJSONArray("users");
                if (users != null && users.length() > 0) {
                    JSONObject first = users.getJSONObject(0);
                    final String uid = first.optString("unique_id", "unknown");
                    final int liveCount = first.optInt("live_count", 1);
                    final String dlUrl = PWA_URL + "api/deep/users/" + uid + "/live" + liveCount + "/download/webmssdk.js";
                    runOnUiThread(() -> {
                        try {
                            startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dlUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                            toast("📥 تنزيل webmssdk.js من @" + uid);
                        } catch (Exception e) { toast("❌ تعذّر التنزيل"); }
                    });
                } else {
                    runOnUiThread(() -> toast("ℹ️ لا توجد بيانات عميقة"));
                }
            } catch (Exception e) {
                runOnUiThread(() -> toast("❌ " + e.getMessage()));
            }
        }).start();
    }

    private void downloadZip(String type) {
        toast("⏳ تجهيز ZIP...");
        new Thread(() -> {
            final String dlUrl = PWA_URL + "api/export/" + type;
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dlUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    toast("📦 تنزيل ZIP: " + type);
                } catch (Exception e) { toast("❌ تعذّر التنزيل"); }
            });
        }).start();
    }

    private void runJs(String js) {
        if (webView != null) {
            try {
                webView.post(() -> webView.evaluateJavascript(js, null));
            } catch (Exception e) {
                Log.e(TAG, "runJs failed", e);
            }
        }
    }

    private void toast(String message) {
        runOnUiThread(() -> {
            try {
                Toast.makeText(this, message, Toast.LENGTH_LONG).show();
            } catch (Exception e) {
                Log.e(TAG, "toast failed", e);
            }
        });
    }

    private void showError(String message) {
        runOnUiThread(() -> {
            try {
                if (webView != null) webView.setVisibility(View.GONE);
                if (progressBar != null) progressBar.setVisibility(View.GONE);
                if (errorView != null) {
                    errorView.setText(message);
                    errorView.setVisibility(View.VISIBLE);
                }
            } catch (Exception e) {
                Log.e(TAG, "showError() failed", e);
            }
        });
    }

    private void hideAllFABs() {
        if (fabDatabase != null) fabDatabase.hide();
        if (fabLogin != null) fabLogin.hide();
        if (fabInteract != null) fabInteract.hide();
    }

    private void showAllFABs() {
        if (fabDatabase != null) fabDatabase.show();
        if (fabLogin != null) fabLogin.show();
        if (fabInteract != null) fabInteract.show();
    }

    @Override
    public void onBackPressed() {
        if (browserWebView != null) {
            // إذا كان متصفح الـ in-app مفتوحاً، ارجع للصفحة السابقة أو أغلق
            if (browserWebView.canGoBack()) {
                browserWebView.goBack();
            } else {
                closeInAppBrowser();
            }
            return;
        }
        if (loginWebView != null) {
            // إذا كان WebView تسجيل الدخول مفتوحاً، أغلقه بدلاً من الخروج
            closeLoginWebView();
            return;
        }
        try {
            if (webView != null && webView.canGoBack()) {
                webView.goBack();
            } else {
                super.onBackPressed();
            }
        } catch (Exception e) {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        try { if (webView != null) webView.onPause(); } catch (Exception e) {}
    }

    @Override
    protected void onResume() {
        super.onResume();
        try { if (webView != null) webView.onResume(); } catch (Exception e) {}
    }

    @Override
    protected void onDestroy() {
        try {
            if (loginWebView != null) {
                ((android.view.ViewGroup) loginWebView.getParent()).removeView(loginWebView);
                loginWebView.destroy();
                loginWebView = null;
            }
            if (webView != null) {
                ((android.view.ViewGroup) webView.getParent()).removeView(webView);
                webView.destroy();
                webView = null;
            }
        } catch (Exception e) {
            Log.e(TAG, "onDestroy failed", e);
        }
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        try { if (webView != null) webView.saveState(outState); } catch (Exception e) {}
    }
}
