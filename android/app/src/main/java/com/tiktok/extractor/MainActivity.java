package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import com.google.android.material.floatingactionbutton.FloatingActionButton;
import org.json.JSONArray;
import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * TikTok Extractor Pro v1.0.34
 * - 3 FABs: pink (open URL), orange (login), cyan (database/tools)
 * - Long-press cyan: Tools menu with 20 options
 * - In-app browser with Desktop mode + session monitor
 * - Copy All / Deep Extract / Save Local / Check Session buttons in browser
 * - All UI in English
 * - v1.0.34: Retry logic + loading indicator for Render free-tier wake-up
 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "TikTokExtractor";
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";
    private static final int MAX_LOAD_RETRIES = 3;
    private static final int LOAD_TIMEOUT_MS = 45000;  // 45 seconds per attempt

    private WebView webView;
    private WebView browserWebView;
    private ProgressBar progressBar;
    private TextView errorView;
    private TextView browserStatusBar;
    private FloatingActionButton fabDatabase, fabLogin, fabInteract;
    private SharedPreferences prefs;
    private boolean sessionCapturedInBrowser = false;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "v1.0.34 starting — with Render wake-up retry");
        prefs = getSharedPreferences("tiktok_session_prefs", Context.MODE_PRIVATE);
        try {
            setContentView(R.layout.activity_main);
            progressBar = findViewById(R.id.progressBar);
            errorView = findViewById(R.id.errorView);
            webView = findViewById(R.id.webview);
            fabDatabase = findViewById(R.id.fabDatabase);
            fabLogin = findViewById(R.id.fabLogin);
            fabInteract = findViewById(R.id.fabInteract);
            if (webView == null) { showError("Internal error."); return; }
            setupMainWebView();
            setupFABs();
            if (savedInstanceState != null) {
                webView.restoreState(savedInstanceState);
            } else {
                // Wake up the backend before loading the page (Render free tier sleeps)
                wakeUpBackendAndLoad();
            }
            hideAllFABs();
        } catch (Exception e) {
            Log.e(TAG, "onCreate crashed", e);
            showError("App startup error: " + e.getMessage());
        }
    }

    /**
     * Wake up the Render backend (which may be sleeping on the free tier)
     * before loading the URL. This shows a friendly "Waking up..." message
     * and retries up to MAX_LOAD_RETRIES times.
     */
    private void wakeUpBackendAndLoad() {
        showWakingUpMessage();
        new Thread(() -> {
            for (int attempt = 1; attempt <= MAX_LOAD_RETRIES; attempt++) {
                try {
                    Log.i(TAG, "Wake-up attempt " + attempt + "/" + MAX_LOAD_RETRIES);
                    java.net.URL url = new URL(PWA_URL + "api/health");
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(LOAD_TIMEOUT_MS);
                    conn.setReadTimeout(LOAD_TIMEOUT_MS);
                    conn.setRequestProperty("User-Agent",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");
                    int code = conn.getResponseCode();
                    if (code == 200) {
                        Log.i(TAG, "Backend awake on attempt " + attempt);
                        runOnUiThread(() -> {
                            hideWakingUpMessage();
                            webView.loadUrl(PWA_URL);
                        });
                        conn.disconnect();
                        return;
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    Log.w(TAG, "Wake-up attempt " + attempt + " failed: " + e.getMessage());
                }
            }
            // All retries exhausted — try loading the URL anyway
            Log.w(TAG, "Wake-up retries exhausted — loading URL directly");
            runOnUiThread(() -> {
                hideWakingUpMessage();
                webView.loadUrl(PWA_URL);
            });
        }).start();
    }

    private void showWakingUpMessage() {
        runOnUiThread(() -> {
            if (progressBar != null) {
                progressBar.setVisibility(View.VISIBLE);
                progressBar.setProgress(0);
            }
            if (errorView != null) {
                errorView.setText("⏳ Waking up the backend server...\nThis may take up to 60 seconds on first launch.\nPlease wait...");
                errorView.setTextColor(0xFF25f4ee);
                errorView.setVisibility(View.VISIBLE);
            }
            if (webView != null) webView.setVisibility(View.GONE);
        });
    }

    private void hideWakingUpMessage() {
        runOnUiThread(() -> {
            if (errorView != null) errorView.setVisibility(View.GONE);
            if (webView != null) webView.setVisibility(View.VISIBLE);
        });
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupMainWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setUserAgentString("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");

        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface public void openInTikTok(String url) { openInAppBrowserInternal(url); }
            @JavascriptInterface public void openInAppBrowser(String url) { openInAppBrowserInternal(url); }
        }, "Android");

        webView.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap f) {
                if (progressBar != null) { progressBar.setVisibility(View.VISIBLE); progressBar.setProgress(0); }
                if (errorView != null) errorView.setVisibility(View.GONE);
                if (webView != null) webView.setVisibility(View.VISIBLE);
            }
            @Override public void onPageFinished(WebView v, String url) {
                if (progressBar != null) progressBar.setVisibility(View.GONE);
                showAllFABs();
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req != null && req.isForMainFrame()) {
                    String d = err != null && err.getDescription() != null ? err.getDescription().toString() : "Unknown";
                    showError("Failed to load:\n" + d);
                }
            }
        });
        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override public void onProgressChanged(WebView v, int p) {
                if (progressBar != null) { progressBar.setProgress(p); if (p >= 100) progressBar.setVisibility(View.GONE); }
            }
            @Override public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) { Log.d(TAG, "JS: " + cm.message()); return true; }
        });
    }

    private void setupFABs() {
        if (fabDatabase != null) {
            fabDatabase.setOnClickListener(v -> {
                hideAllFABs();
                runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"database\"]'); if (t) t.click(); })();");
                toast("Database");
                v.postDelayed(this::showAllFABs, 800);
            });
            fabDatabase.setOnLongClickListener(v -> { showToolsMenu(); return true; });
        }
        if (fabLogin != null) {
            fabLogin.setOnClickListener(v -> openLoginCaptureWebView());
            fabLogin.setOnLongClickListener(v -> { checkSavedSession(); return true; });
        }
        if (fabInteract != null) {
            fabInteract.setOnClickListener(v -> {
                runJs("(() => { const i=document.getElementById('urlInput'); const u=i?i.value:''; if(u) Android.openInAppBrowser(u); else alert('Paste a TikTok URL first'); })();");
            });
            fabInteract.setOnLongClickListener(v -> { triggerDeepExtract(); return true; });
        }
    }

    // ═══ Tools Menu (20 options) ═══
    private void showToolsMenu() {
        String[] opts = {
            "Full Statistics", "Deep Extract", "Users List", "Streams Stats",
            "Fans Stats", "Session Tab", "Open URL In-App", "Sync GitHub",
            "Download webmssdk.js", "--- Interactions ---",
            "Send Like", "Follow User", "Send Comment", "Enter Live Room",
            "Interaction Stats", "--- Monitor ---",
            "Monitor Live", "Monitor Report", "--- Export ---",
            "Download All (ZIP)"
        };
        new AlertDialog.Builder(this).setTitle("Tools v5.2").setItems(opts, (d, w) -> {
            switch (w) {
                case 0: fetchAndShowJson("Statistics", "api/stats/headline"); break;
                case 1: triggerDeepExtract(); break;
                case 2: fetchAndShowJson("Users", "api/deep/users"); break;
                case 3: fetchAndShowJson("Streams", "api/stats/streams"); break;
                case 4: fetchAndShowJson("Fans", "api/stats/fans"); break;
                case 5: openSessionTab(); break;
                case 6: openInTikTokFromInput(); break;
                case 7: syncGitHub(); break;
                case 8: downloadLatestWebmssdk(); break;
                case 10: executeInteraction("send_like"); break;
                case 11: executeInteraction("follow_user"); break;
                case 12: executeInteraction("send_comment"); break;
                case 13: executeInteraction("enter_live_room"); break;
                case 14: fetchAndShowJson("Interaction Stats", "api/react/stats"); break;
                case 16: fetchAndShowJson("Live Monitor", "api/monitor/live/all"); break;
                case 17: fetchAndShowJson("Monitor Report", "api/monitor/report"); break;
                case 19: downloadZip("all"); break;
            }
        }).setNegativeButton("Close", null).show();
    }

    private void fetchAndShowJson(String title, String endpoint) {
        toast("Loading " + title + "...");
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + endpoint);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET"); conn.setConnectTimeout(15000); conn.setReadTimeout(15000);
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                if (code == 200) {
                    String display;
                    try { display = new JSONObject(resp).toString(2); } catch (Exception e) { display = resp; }
                    if (display.length() > 4000) display = display.substring(0, 4000) + "...";
                    final String fd = display;
                    mainHandler.post(() -> new AlertDialog.Builder(this).setTitle(title).setMessage(fd).setPositiveButton("OK", null).show());
                } else { mainHandler.post(() -> toast("Error: HTTP " + code)); }
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void executeInteraction(String action) {
        runJs("(() => { const i=document.getElementById('urlInput'); const u=i?i.value:''; if(!u){alert('Paste a URL first');return;}"
            + "fetch('/api/extract?url='+encodeURIComponent(u)).then(r=>r.json()).then(d=>{"
            + "if(!d.success){alert('Extraction failed');return;}"
            + "const body={action:'" + action + "'};"
            + "if(d.all_ids){body.room_id=d.all_ids.room_id||'';body.sec_uid=d.all_ids.sec_uid||'';body.user_id=d.all_ids.user_id||'';body.video_id=d.content_id||'';}"
            + "fetch('/api/react/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})"
            + ".then(r=>r.json()).then(r=>{alert('Result: '+(r.success?'SUCCESS':'FAILED')+'\\n'+JSON.stringify(r,null,2).substring(0,500));})"
            + ".catch(e=>alert('Error: '+e));}).catch(e=>alert('Extract error: '+e)); })();");
        toast("Running: " + action);
    }

    private void triggerDeepExtract() {
        runJs("(() => { const i=document.getElementById('urlInput'); const u=i?i.value:''; if(!u){alert('Paste a URL first');return;}"
            + "fetch('/api/deep/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u})})"
            + ".then(r=>r.json()).then(d=>{if(d.success){alert('Deep extract OK! @'+d.unique_id+' live'+d.live_number+' ('+d.files_saved.length+' files)');}else{alert('Failed: '+(d.error||'unknown'));}})"
            + ".catch(e=>alert('Error: '+e)); })();");
    }

    private void openSessionTab() { hideAllFABs(); runJs("(() => { const t=document.querySelector('.nav-tab[data-tab=\"session\"]'); if(t) t.click(); })();"); webView.postDelayed(this::showAllFABs, 800); }
    private void openInTikTokFromInput() { runJs("(() => { const i=document.getElementById('urlInput'); const u=i?i.value:''; if(u) Android.openInAppBrowser(u); else alert('Paste a URL first'); })();"); }

    private void syncGitHub() {
        toast("Syncing...");
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + "api/sync-db");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.getOutputStream().write("{}".getBytes("UTF-8"));
                JSONObject d = new JSONObject(readResponse(conn));
                mainHandler.post(() -> toast(d.optBoolean("success") ? "Synced " + d.optInt("pushed_files") + " files" : "Sync failed"));
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void downloadLatestWebmssdk() {
        toast("Searching...");
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + "api/deep/users");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET"); conn.setConnectTimeout(15000);
                JSONObject d = new JSONObject(readResponse(conn));
                JSONArray users = d.optJSONArray("users");
                if (users != null && users.length() > 0) {
                    JSONObject first = users.getJSONObject(0);
                    final String uid = first.optString("unique_id", "unknown");
                    final int lc = first.optInt("live_count", 1);
                    final String dl = PWA_URL + "api/deep/users/" + uid + "/live" + lc + "/download/webmssdk.js";
                    mainHandler.post(() -> { try { startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); toast("Downloading webmssdk.js"); } catch (Exception e) { toast("Download failed"); } });
                } else { mainHandler.post(() -> toast("No deep data")); }
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void downloadZip(String type) {
        toast("Preparing ZIP...");
        final String dl = PWA_URL + "api/export/" + type;
        mainHandler.post(() -> { try { startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); toast("Downloading ZIP: " + type); } catch (Exception e) { toast("Download failed"); } });
    }

    // ═══ In-App Browser with Desktop Mode ═══
    @SuppressLint("SetJavaScriptEnabled")
    private void openInAppBrowserInternal(String url) {
        mainHandler.post(() -> {
            try {
                toast("Opening browser...");
                sessionCapturedInBrowser = false;
                browserWebView = new WebView(this);
                WebSettings s = browserWebView.getSettings();
                s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true); s.setDatabaseEnabled(true);
                s.setCacheMode(WebSettings.LOAD_DEFAULT); s.setLoadWithOverviewMode(true); s.setUseWideViewPort(true);
                s.setSupportZoom(true); s.setBuiltInZoomControls(true); s.setDisplayZoomControls(false);
                s.setMediaPlaybackRequiresUserGesture(false); s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
                s.setUserAgentString("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");

                CookieManager cm = CookieManager.getInstance();
                cm.setAcceptCookie(true); cm.setAcceptThirdPartyCookies(browserWebView, true);

                webView.setVisibility(View.GONE); hideAllFABs();
                ViewGroup root = (ViewGroup) webView.getParent();
                root.addView(browserWebView, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

                // Status bar
                browserStatusBar = new TextView(this);
                browserStatusBar.setText("Browsing | Monitoring session...");
                browserStatusBar.setBackgroundColor(0xCC000000); browserStatusBar.setTextColor(0xFFFFFFFF);
                browserStatusBar.setPadding(24, 16, 24, 16); browserStatusBar.setTextSize(12);
                FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                sp.gravity = Gravity.TOP; root.addView(browserStatusBar, sp);

                // Close button
                TextView closeBtn = new TextView(this); closeBtn.setText("X Close");
                closeBtn.setBackgroundColor(0xCCFF2D55); closeBtn.setTextColor(0xFFFFFFFF);
                closeBtn.setPadding(24, 12, 24, 12); closeBtn.setTextSize(14);
                closeBtn.setOnClickListener(v -> closeInAppBrowser());
                FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                cp.gravity = Gravity.TOP | Gravity.RIGHT; cp.topMargin = 60; cp.rightMargin = 16; root.addView(closeBtn, cp);

                // Check Session button
                TextView chkBtn = new TextView(this); chkBtn.setText("Check Session");
                chkBtn.setBackgroundColor(0xCC25F4EE); chkBtn.setTextColor(0xFF000000);
                chkBtn.setPadding(24, 12, 24, 12); chkBtn.setTextSize(14);
                chkBtn.setOnClickListener(v -> checkAndCaptureSession());
                FrameLayout.LayoutParams kp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                kp.gravity = Gravity.TOP | Gravity.LEFT; kp.topMargin = 60; kp.leftMargin = 16; root.addView(chkBtn, kp);

                // Copy All button
                TextView cpBtn = new TextView(this); cpBtn.setText("Copy All");
                cpBtn.setBackgroundColor(0xCCFFB547); cpBtn.setTextColor(0xFF000000);
                cpBtn.setPadding(24, 12, 24, 12); cpBtn.setTextSize(14);
                cpBtn.setOnClickListener(v -> copyAllCookies());
                FrameLayout.LayoutParams cpp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                cpp.gravity = Gravity.TOP | Gravity.LEFT; cpp.topMargin = 110; cpp.leftMargin = 16; root.addView(cpBtn, cpp);

                // Deep Extract button
                TextView dpBtn = new TextView(this); dpBtn.setText("Deep Extract");
                dpBtn.setBackgroundColor(0xCC9C27B0); dpBtn.setTextColor(0xFFFFFFFF);
                dpBtn.setPadding(24, 12, 24, 12); dpBtn.setTextSize(14);
                dpBtn.setOnClickListener(v -> triggerDeepExtractInBrowser());
                FrameLayout.LayoutParams dpp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                dpp.gravity = Gravity.TOP | Gravity.LEFT; dpp.topMargin = 160; dpp.leftMargin = 16; root.addView(dpBtn, dpp);

                // Save Local button
                TextView svBtn = new TextView(this); svBtn.setText("Save Local");
                svBtn.setBackgroundColor(0xCC4CAF50); svBtn.setTextColor(0xFFFFFFFF);
                svBtn.setPadding(24, 12, 24, 12); svBtn.setTextSize(14);
                svBtn.setOnClickListener(v -> saveSessionLocally());
                FrameLayout.LayoutParams svp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                svp.gravity = Gravity.TOP | Gravity.LEFT; svp.topMargin = 210; svp.leftMargin = 16; root.addView(svBtn, svp);

                browserWebView.setWebViewClient(new WebViewClient() {
                    @Override public void onPageFinished(WebView v, String pageUrl) {
                        super.onPageFinished(v, pageUrl);
                        String cookies = cm.getCookie("https://www.tiktok.com/");
                        if (cookies != null) {
                            if (cookies.contains("sessionid") && !sessionCapturedInBrowser) {
                                sessionCapturedInBrowser = true;
                                if (browserStatusBar != null) { browserStatusBar.setText("Browsing | Session captured!"); browserStatusBar.setBackgroundColor(0xCC00D68F); }
                                toast("Session captured!");
                                captureAndUploadSession(cookies, pageUrl);
                            } else if (!sessionCapturedInBrowser && browserStatusBar != null) {
                                browserStatusBar.setText(cookies.contains("sid_tt") || cookies.contains("ttwid") ? "Browsing | Partial session — login" : "Browsing | Monitoring...");
                            }
                        }
                    }
                });
                browserWebView.loadUrl(url);
            } catch (Exception e) { Log.e(TAG, "browser crashed", e); toast("Browser error: " + e.getMessage()); }
        });
    }

    private void checkAndCaptureSession() {
        if (browserWebView == null) { toast("No active browser"); return; }
        CookieManager cm = CookieManager.getInstance();
        String cookies = cm.getCookie("https://www.tiktok.com/");
        if (cookies == null || cookies.isEmpty()) { toast("No cookies"); return; }
        Map<String, String> m = parseCookies(cookies);
        boolean hasSid = m.containsKey("sessionid") && !m.get("sessionid").isEmpty();
        StringBuilder sb = new StringBuilder("Session Check:\n\n");
        sb.append("sessionid: ").append(hasSid ? "YES" : "NO").append("\n");
        sb.append("sid_tt: ").append(m.containsKey("sid_tt") ? "YES" : "NO").append("\n");
        sb.append("ttwid: ").append(m.containsKey("ttwid") ? "YES" : "NO").append("\n");
        sb.append("msToken: ").append(m.containsKey("msToken") ? "YES" : "NO").append("\n");
        sb.append("Total cookies: ").append(m.size()).append("\n\n");
        if (hasSid) { sb.append("Session active!"); captureAndUploadSession(cookies, browserWebView.getUrl() != null ? browserWebView.getUrl() : ""); sb.append("\nSaved automatically"); }
        else if (m.containsKey("sid_tt")) sb.append("Partial — login for sessionid");
        else sb.append("No session — login to TikTok");
        new AlertDialog.Builder(this).setTitle("Session Check").setMessage(sb.toString()).setPositiveButton("OK", null).show();
    }

    private void copyAllCookies() {
        if (browserWebView == null) { toast("No active browser"); return; }
        String cookies = CookieManager.getInstance().getCookie("https://www.tiktok.com/");
        if (cookies == null || cookies.isEmpty()) { toast("No cookies"); return; }
        try {
            ((ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("TikTok Cookies", cookies));
            Map<String, String> m = parseCookies(cookies);
            toast("Copied " + m.size() + " cookies");
            StringBuilder sb = new StringBuilder("All Cookies (" + m.size() + "):\n\n");
            for (Map.Entry<String, String> e : m.entrySet()) { String v = e.getValue(); if (v.length() > 80) v = v.substring(0, 80) + "..."; sb.append(e.getKey()).append(": ").append(v).append("\n"); }
            String d = sb.toString(); if (d.length() > 4000) d = d.substring(0, 4000) + "...";
            new AlertDialog.Builder(this).setTitle("All Cookies (" + m.size() + ")").setMessage(d).setPositiveButton("OK", null).show();
        } catch (Exception e) { toast("Copy failed: " + e.getMessage()); }
    }

    private void triggerDeepExtractInBrowser() {
        if (browserWebView == null) { toast("No active browser"); return; }
        final String currentUrl = browserWebView.getUrl();
        if (currentUrl == null || currentUrl.isEmpty()) { toast("No URL loaded"); return; }
        toast("Running deep extract...");
        new Thread(() -> {
            try {
                JSONObject payload = new JSONObject(); payload.put("url", currentUrl);
                URL url = new URL(PWA_URL + "api/deep/extract");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(30000); conn.setReadTimeout(60000);
                conn.getOutputStream().write(payload.toString().getBytes("UTF-8"));
                int code = conn.getResponseCode(); String resp = readResponse(conn);
                if (code == 200) {
                    JSONObject d = new JSONObject(resp);
                    if (d.optBoolean("success")) {
                        String uid = d.optString("unique_id", "unknown"); int ln = d.optInt("live_number", 0);
                        int files = d.optJSONArray("files_saved") != null ? d.getJSONArray("files_saved").length() : 0;
                        boolean wm = d.optJSONObject("webmssdk") != null && d.getJSONObject("webmssdk").optBoolean("success");
                        mainHandler.post(() -> toast("Deep extract done! @" + uid + " live" + ln + " (" + files + " files, webmssdk: " + (wm ? "yes" : "no") + ")"));
                    } else { mainHandler.post(() -> toast("Deep extract failed: " + d.optString("error", "unknown"))); }
                } else { mainHandler.post(() -> toast("Deep extract HTTP " + code)); }
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void saveSessionLocally() {
        if (browserWebView == null) { toast("No active browser"); return; }
        String cookies = CookieManager.getInstance().getCookie("https://www.tiktok.com/");
        if (cookies == null || cookies.isEmpty()) { toast("No cookies"); return; }
        Map<String, String> m = parseCookies(cookies);
        SharedPreferences.Editor ed = prefs.edit();
        ed.putString("all_cookies", cookies);
        ed.putString("cookies_saved_at", new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(new Date()));
        for (String k : new String[]{"sessionid","ttwid","msToken","sid_tt","sid_guard","uid_tt","tt_chain_token","cmpl_token"}) { ed.putString(k, m.get(k)); }
        ed.putInt("cookies_count", m.size()); ed.apply();
        toast("Saved " + m.size() + " cookies locally");
        new AlertDialog.Builder(this).setTitle("Saved Locally").setMessage("Saved " + m.size() + " cookies to device.\n\nPersists across app restarts.").setPositiveButton("OK", null).show();
    }

    private void closeInAppBrowser() {
        if (browserWebView != null) {
            try { ViewGroup root = (ViewGroup) webView.getParent(); root.removeView(browserWebView); if (browserStatusBar != null) root.removeView(browserStatusBar); browserWebView.destroy(); browserWebView = null; browserStatusBar = null; } catch (Exception e) { Log.e(TAG, "close failed", e); }
        }
        webView.setVisibility(View.VISIBLE); showAllFABs(); toast("Back to main");
    }

    // ═══ Login WebView ═══
    @SuppressLint("SetJavaScriptEnabled")
    private void openLoginCaptureWebView() {
        toast("Open TikTok and login");
        openInAppBrowserInternal("https://www.tiktok.com/login/phone-or-email/email");
    }

    private void captureAndUploadSession(String cookieString, String currentUrl) {
        Log.i(TAG, "Capturing cookies, length=" + cookieString.length());
        Map<String, String> cookies = parseCookies(cookieString);
        String sessionid = cookies.get("sessionid");
        if (sessionid == null || sessionid.isEmpty()) { Log.w(TAG, "sessionid not found"); return; }
        Log.i(TAG, "Captured sessionid (length=" + sessionid.length() + ")");
        String uniqueId = cookies.getOrDefault("unique_id", cookies.getOrDefault("sid_tt", "user"));
        final String finalUid = uniqueId;
        final JSONObject payload = new JSONObject();
        try {
            payload.put("sessionid", sessionid);
            JSONObject extra = new JSONObject();
            for (String k : new String[]{"ttwid","msToken","sid_tt","passport_csrf_token","sid_guard","uid_tt","uid_tt_ss","tt_chain_token","cmpl_token"}) { String v = cookies.get(k); if (v != null && !v.isEmpty()) extra.put(k, v); }
            payload.put("extra_cookies", extra);
        } catch (Exception e) { Log.e(TAG, "payload failed", e); return; }
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + "api/session/capture");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(30000); conn.setReadTimeout(60000);
                conn.getOutputStream().write(payload.toString().getBytes("UTF-8"));
                int code = conn.getResponseCode(); String resp = readResponse(conn);
                Log.i(TAG, "Session upload: " + code);
                mainHandler.post(() -> {
                    if (code == 200) { toast("Session saved! @" + finalUid); prefs.edit().putString("last_unique_id", finalUid).putString("all_cookies", cookieString).putString("sessionid", sessionid).apply(); }
                    else { toast("Save failed — saved locally"); prefs.edit().putString("all_cookies", cookieString).putString("sessionid", sessionid).putString("last_unique_id", finalUid).apply(); }
                });
            } catch (Exception e) { Log.e(TAG, "upload failed", e); mainHandler.post(() -> { toast("Network error — saved locally"); prefs.edit().putString("all_cookies", cookieString).putString("sessionid", sessionid).putString("last_unique_id", finalUid).apply(); }); }
        }).start();
    }

    private void checkSavedSession() {
        String uid = prefs.getString("last_unique_id", "");
        if (uid.isEmpty()) { toast("No saved session"); return; }
        toast("Checking for " + uid);
        runJs("(() => { fetch('/api/session/" + uid + "/status').then(r=>r.json()).then(d=>{ alert(d.has_session ? 'Session saved! At: ' + d.saved_at : 'No saved session'); }).catch(e=>alert('Error: '+e)); })();");
    }

    // ═══ Utilities ═══
    private void runJs(String js) { if (webView != null) webView.post(() -> webView.evaluateJavascript(js, null)); }
    private void toast(String msg) { mainHandler.post(() -> { try { Toast.makeText(this, msg, Toast.LENGTH_LONG).show(); } catch (Exception e) {} }); }
    private void showError(String msg) { mainHandler.post(() -> { try { if (webView != null) webView.setVisibility(View.GONE); if (progressBar != null) progressBar.setVisibility(View.GONE); if (errorView != null) { errorView.setText(msg); errorView.setVisibility(View.VISIBLE); } } catch (Exception e) {} }); }
    private void hideAllFABs() { if (fabDatabase != null) fabDatabase.hide(); if (fabLogin != null) fabLogin.hide(); if (fabInteract != null) fabInteract.hide(); }
    private void showAllFABs() { if (fabDatabase != null) fabDatabase.show(); if (fabLogin != null) fabLogin.show(); if (fabInteract != null) fabInteract.show(); }

    private Map<String, String> parseCookies(String cs) {
        Map<String, String> m = new HashMap<>(); if (cs == null) return m;
        for (String p : cs.split(";")) { int eq = p.indexOf('='); if (eq > 0) m.put(p.substring(0, eq).trim(), p.substring(eq + 1).trim()); }
        return m;
    }

    private String readResponse(HttpURLConnection conn) {
        try {
            java.io.InputStream is = conn.getResponseCode() >= 400 ? conn.getErrorStream() : conn.getInputStream();
            java.io.BufferedReader r = new java.io.BufferedReader(new java.io.InputStreamReader(is, "UTF-8"));
            StringBuilder sb = new StringBuilder(); String l; while ((l = r.readLine()) != null) sb.append(l); r.close(); return sb.toString();
        } catch (Exception e) { return "(error: " + e.getMessage() + ")"; }
    }

    @Override public void onBackPressed() {
        if (browserWebView != null) { if (browserWebView.canGoBack()) browserWebView.goBack(); else closeInAppBrowser(); return; }
        try { if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed(); } catch (Exception e) { super.onBackPressed(); }
    }
    @Override protected void onPause() { super.onPause(); try { if (webView != null) webView.onPause(); } catch (Exception e) {} }
    @Override protected void onResume() { super.onResume(); try { if (webView != null) webView.onResume(); } catch (Exception e) {} }
    @Override protected void onDestroy() {
        try {
            if (browserWebView != null) { ((ViewGroup) browserWebView.getParent()).removeView(browserWebView); browserWebView.destroy(); browserWebView = null; }
            if (webView != null) { ((ViewGroup) webView.getParent()).removeView(webView); webView.destroy(); webView = null; }
        } catch (Exception e) {} super.onDestroy();
    }
    @Override protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); try { if (webView != null) webView.saveState(out); } catch (Exception e) {} }
}
