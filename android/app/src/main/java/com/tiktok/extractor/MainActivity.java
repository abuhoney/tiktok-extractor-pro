package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import androidx.appcompat.app.AppCompatActivity;

/**
 * MainActivity — loads the TikTok Extractor Pro PWA inside an in-app WebView.
 *
 * Why WebView instead of Chrome Custom Tabs:
 *   - WebView doesn't depend on Chrome being installed
 *   - The PWA runs in-process so the app stays alive
 *   - We can show a loading spinner + error fallback
 *   - Back button navigates WebView history
 *
 * The PWA itself handles all the extraction logic (Flask backend on Render).
 */
public class MainActivity extends AppCompatActivity {
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";

    private WebView webView;
    private ProgressBar progressBar;
    private TextView errorView;
    private LinearLayout errorLayout;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Build the UI programmatically (no XML layout file needed)
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#07070d"));
        LinearLayout.LayoutParams rootParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT
        );
        root.setLayoutParams(rootParams);

        // Progress bar at top
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgress(0);
        progressBar.setVisibility(View.VISIBLE);
        LinearLayout.LayoutParams pbParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dpToPx(4)
        );
        progressBar.setLayoutParams(pbParams);
        root.addView(progressBar);

        // WebView (fills the screen)
        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#07070d"));
        LinearLayout.LayoutParams wvParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1.0f  // weight=1 so it takes all remaining space
        );
        webView.setLayoutParams(wvParams);
        root.addView(webView);

        // Error layout (hidden by default)
        errorLayout = new LinearLayout(this);
        errorLayout.setOrientation(LinearLayout.VERTICAL);
        errorLayout.setVisibility(View.GONE);
        LinearLayout.LayoutParams errParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT
        );
        errorLayout.setLayoutParams(errParams);
        errorLayout.setGravity(android.view.Gravity.CENTER);

        errorView = new TextView(this);
        errorView.setTextColor(Color.parseColor("#ff4d6d"));
        errorView.setTextSize(16);
        errorView.setPadding(dpToPx(24), dpToPx(24), dpToPx(24), dpToPx(24));
        errorView.setGravity(android.view.Gravity.CENTER);
        errorView.setText("تعذّر تحميل التطبيق. تحقق من اتصالك بالإنترنت ثم أعد المحاولة.");
        errorLayout.addView(errorView);
        root.addView(errorLayout);

        setContentView(root);

        // Configure WebView
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);          // required for PWA
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setUserAgentString(
                "Mozilla/5.0 (Linux; Android 14; TikTokExtractorPro) " +
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
        );

        // WebViewClient — handles page loading + errors
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                progressBar.setVisibility(View.VISIBLE);
                progressBar.setProgress(0);
                errorLayout.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progressBar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                // Only show error for main frame
                if (request.isForMainFrame()) {
                    String msg = error.getDescription() != null ? error.getDescription().toString() : "خطأ غير معروف";
                    showError("تعذّر تحميل الصفحة:\n" + msg + "\n\nتحقق من اتصالك بالإنترنت ثم أعد المحاولة.");
                }
            }
        });

        // WebChromeClient — handles progress + console messages
        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                if (newProgress >= 100) {
                    progressBar.setVisibility(View.GONE);
                }
            }

            @Override
            public void onConsoleMessage(String message, int lineNumber, String sourceID) {
                android.util.Log.d("TikTokExtractor", "JS: " + message + " (" + sourceID + ":" + lineNumber + ")");
            }

            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage consoleMessage) {
                android.util.Log.d("TikTokExtractor", "JS[" + consoleMessage.messageLevel() + "]: "
                        + consoleMessage.message() + " ("
                        + consoleMessage.sourceId() + ":" + consoleMessage.lineNumber() + ")");
                return true;
            }
        });

        // Load the PWA
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(PWA_URL);
        }
    }

    private void showError(String message) {
        runOnUiThread(() -> {
            webView.setVisibility(View.GONE);
            progressBar.setVisibility(View.GONE);
            errorView.setText(message);
            errorLayout.setVisibility(View.VISIBLE);
        });
    }

    private int dpToPx(int dp) {
        float density = getResources().getDisplayMetrics().density;
        return (int) (dp * density + 0.5f);
    }

    @Override
    public void onBackPressed() {
        // If WebView can go back, do that; otherwise exit the app
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) webView.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            // Properly destroy WebView to prevent memory leaks
            ((LinearLayout) webView.getParent()).removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null) webView.saveState(outState);
    }
}
