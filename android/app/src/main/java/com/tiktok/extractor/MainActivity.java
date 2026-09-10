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
import androidx.appcompat.app.AppCompatActivity;

/**
 * MainActivity — loads the TikTok Extractor Pro PWA inside an in-app WebView.
 *
 * Simple and robust: uses XML layout, try/catch around everything, and shows
 * a clear Arabic error message if anything goes wrong.
 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "TikTokExtractor";
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";

    private WebView webView;
    private ProgressBar progressBar;
    private TextView errorView;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "MainActivity.onCreate() starting");

        try {
            // Use the XML layout — more reliable than building UI programmatically
            setContentView(R.layout.activity_main);

            progressBar = findViewById(R.id.progressBar);
            errorView = findViewById(R.id.errorView);
            webView = findViewById(R.id.webview);

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
                }

                @Override
                public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                    String failingUrl = request != null && request.getUrl() != null
                            ? request.getUrl().toString() : "(unknown)";
                    String desc = error != null && error.getDescription() != null
                            ? error.getDescription().toString() : "خطأ غير معروف";
                    Log.e(TAG, "onReceivedError: url=" + failingUrl + " desc=" + desc);
                    // Only show error for main frame
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

            // Restore state if rotating, otherwise load fresh
            if (savedInstanceState != null) {
                webView.restoreState(savedInstanceState);
                Log.i(TAG, "Restored WebView state");
            } else {
                Log.i(TAG, "Loading URL: " + PWA_URL);
                webView.loadUrl(PWA_URL);
            }

        } catch (Exception e) {
            Log.e(TAG, "onCreate() crashed", e);
            showError("خطأ في بدء التطبيق:\n" + e.getMessage()
                    + "\n\nأعد تثبيت التطبيق أو تواصل مع المطور.");
        }
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
