package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
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

/**
 * MainActivity — loads the TikTok Extractor Pro PWA inside an in-app WebView.
 *
 * v4.4: Adds two Floating Action Buttons (FABs):
 *   - fabDatabase: Opens the Users Database tab (monitoring saved users, streams, fans)
 *   - fabInteract: Quick access to auto-interact feature
 *
 * The FABs inject JavaScript into the WebView to switch tabs and trigger actions.
 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "TikTokExtractor";
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";

    private WebView webView;
    private ProgressBar progressBar;
    private TextView errorView;
    private FloatingActionButton fabDatabase;
    private FloatingActionButton fabInteract;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "MainActivity.onCreate() v4.4 starting");

        try {
            setContentView(R.layout.activity_main);

            progressBar = findViewById(R.id.progressBar);
            errorView = findViewById(R.id.errorView);
            webView = findViewById(R.id.webview);
            fabDatabase = findViewById(R.id.fabDatabase);
            fabInteract = findViewById(R.id.fabInteract);

            if (webView == null) {
                Log.e(TAG, "WebView is null after findViewById — layout issue");
                showError("خطأ داخلي: WebView غير متوفر.\nأعد تثبيت التطبيق.");
                return;
            }

            // Configure WebView settings
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

            // WebViewClient — handle page loading + errors
            webView.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                    Log.i(TAG, "onPageStarted: " + url);
                    if (progressBar != null) {
                        progressBar.setVisibility(View.VISIBLE);
                        progressBar.setProgress(0);
                    }
                    if (errorView != null) errorView.setVisibility(View.GONE);
                    if (webView != null) webView.setVisibility(View.VISIBLE);
                }

                @Override
                public void onPageFinished(WebView view, String url) {
                    Log.i(TAG, "onPageFinished: " + url);
                    if (progressBar != null) progressBar.setVisibility(View.GONE);
                    // إظهار أزرار FAB بعد تحميل الصفحة
                    if (fabDatabase != null) fabDatabase.show();
                    if (fabInteract != null) fabInteract.show();
                }

                @Override
                public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                    String failingUrl = request != null && request.getUrl() != null
                            ? request.getUrl().toString() : "(unknown)";
                    String desc = error != null && error.getDescription() != null
                            ? error.getDescription().toString() : "خطأ غير معروف";
                    Log.e(TAG, "onReceivedError: url=" + failingUrl + " desc=" + desc);
                    if (request != null && request.isForMainFrame()) {
                        showError("تعذّر تحميل الصفحة:\n" + desc + "\n\nتحقق من الإنترنت وأعد المحاولة.");
                    }
                }
            });

            // WebChromeClient — for progress + console messages
            webView.setWebChromeClient(new android.webkit.WebChromeClient() {
                @Override
                public void onProgressChanged(WebView view, int newProgress) {
                    if (progressBar != null) {
                        progressBar.setProgress(newProgress);
                        if (newProgress >= 100) {
                            progressBar.setVisibility(View.GONE);
                        }
                    }
                }

                @Override
                public boolean onConsoleMessage(android.webkit.ConsoleMessage consoleMessage) {
                    Log.d(TAG, "JS[" + consoleMessage.messageLevel() + "]: "
                            + consoleMessage.message()
                            + " (" + consoleMessage.sourceId() + ":" + consoleMessage.lineNumber() + ")");
                    return true;
                }
            });

            // ─── زر قاعدة البيانات: يفتح تبويب المراقبة ───
            if (fabDatabase != null) {
                fabDatabase.setOnClickListener(v -> {
                    Log.i(TAG, "FAB Database clicked — switching to database tab");
                    // إخفاء الأزرار مؤقتاً
                    fabDatabase.hide();
                    fabInteract.hide();
                    // تنفيذ JavaScript لتبديل التبويب
                    runJs("(() => {"
                        + "  const tab = document.querySelector('.nav-tab[data-tab=\"database\"]');"
                        + "  if (tab) { tab.click(); }"
                        + "  else {"
                        + "    // إذا كانت الواجهة قديمة، انتقل مباشرة لقسم /api/users"
                        + "    window.location.href = '/#database';"
                        + "  }"
                        + "})();");
                    toast("📊 فتح قاعدة البيانات");
                    // إعادة إظهار الزر بعد 800ms
                    v.postDelayed(() -> {
                        if (fabDatabase != null) fabDatabase.show();
                        if (fabInteract != null) fabInteract.show();
                    }, 800);
                });

                fabDatabase.setOnLongClickListener(v -> {
                    Log.i(TAG, "FAB Database long-clicked — refreshing users list");
                    runJs("(() => {"
                        + "  const btn = document.getElementById('dbRefreshBtn');"
                        + "  if (btn) btn.click();"
                        + "})();");
                    toast("🔄 تحديث قائمة المستخدمين");
                    return true;
                });
            }

            // ─── زر التفاعل التلقائي: يفتح نافذة الإدخال السريع ───
            if (fabInteract != null) {
                fabInteract.setOnClickListener(v -> {
                    Log.i(TAG, "FAB Interact clicked — extracting current URL");
                    // استخرج الرابط من حقل الإدخال
                    runJs("(() => {"
                        + "  const input = document.getElementById('urlInput');"
                        + "  const url = input ? input.value : '';"
                        + "  if (!url) {"
                        + "    alert('الصق رابط TikTok أولاً في حقل البحث');"
                        + "    return;"
                        + "  }"
                        + "  // انقر زر الاستخراج"
                        + "  const btn = document.getElementById('extractBtn');"
                        + "  if (btn) btn.click();"
                        + "})();");
                    toast("⚡ بدء الاستخراج والتحليل");
                });

                fabInteract.setOnLongClickListener(v -> {
                    Log.i(TAG, "FAB Interact long-clicked — triggering GitHub sync");
                    runJs("(() => {"
                        + "  const btn = document.getElementById('dbSyncBtn');"
                        + "  if (btn) {"
                        + "    // بدّل لتبويب قاعدة البيانات أولاً"
                        + "    const tab = document.querySelector('.nav-tab[data-tab=\"database\"]');"
                        + "    if (tab) tab.click();"
                        + "    setTimeout(() => btn.click(), 500);"
                        + "  } else {"
                        + "    alert('مزامنة GitHub متاحة من تبويب قاعدة البيانات');"
                        + "  }"
                        + "})();");
                    toast("🔄 مزامنة مع GitHub...");
                    return true;
                });
            }

            // Restore state if rotating, otherwise load fresh
            if (savedInstanceState != null) {
                webView.restoreState(savedInstanceState);
                Log.i(TAG, "Restored WebView state");
            } else {
                Log.i(TAG, "Loading URL: " + PWA_URL);
                webView.loadUrl(PWA_URL);
            }

            // إخفاء أزرار FAB في البداية حتى تُحمّل الصفحة
            if (fabDatabase != null) fabDatabase.hide();
            if (fabInteract != null) fabInteract.hide();

        } catch (Exception e) {
            Log.e(TAG, "onCreate() crashed", e);
            showError("خطأ في بدء التطبيق:\n" + e.getMessage()
                    + "\n\nأعد تثبيت التطبيق أو تواصل مع المطور.");
        }
    }

    /**
     * ينفذ JavaScript في WebView بأمان.
     */
    private void runJs(String js) {
        if (webView != null) {
            try {
                webView.post(() -> webView.evaluateJavascript(js, null));
            } catch (Exception e) {
                Log.e(TAG, "runJs failed", e);
            }
        }
    }

    /**
     * يعرض رسالة Toast قصيرة.
     */
    private void toast(String message) {
        runOnUiThread(() -> {
            try {
                Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
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

    @Override
    public void onBackPressed() {
        try {
            if (webView != null && webView.canGoBack()) {
                webView.goBack();
            } else {
                super.onBackPressed();
            }
        } catch (Exception e) {
            Log.e(TAG, "onBackPressed failed", e);
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        try {
            if (webView != null) webView.onPause();
        } catch (Exception e) {
            Log.e(TAG, "onPause failed", e);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        try {
            if (webView != null) webView.onResume();
        } catch (Exception e) {
            Log.e(TAG, "onResume failed", e);
        }
    }

    @Override
    protected void onDestroy() {
        try {
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
        try {
            if (webView != null) webView.saveState(outState);
        } catch (Exception e) {
            Log.e(TAG, "onSaveInstanceState failed", e);
        }
    }
}
