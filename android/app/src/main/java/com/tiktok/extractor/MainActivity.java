package com.tiktok.extractor;

import android.annotation.SuppressLint;
import android.app.Activity;
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
 * TikTok Extractor Pro v5.2 — APK v1.0.26
 * All UI text in English. Desktop mode for in-app browser.
 */
public class MainActivity extends AppCompatActivity {
    private static final String TAG = "TikTokExtractor";
    private static final String PWA_URL = "https://tiktok-extractor-pro.onrender.com/";

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
        Log.i(TAG, "MainActivity v1.0.26 starting");
        prefs = getSharedPreferences("tiktok_session_prefs", Context.MODE_PRIVATE);
        try {
            setContentView(R.layout.activity_main);
            progressBar = findViewById(R.id.progressBar);
            errorView = findViewById(R.id.errorView);
            webView = findViewById(R.id.webview);
            fabDatabase = findViewById(R.id.fabDatabase);
            fabLogin = findViewById(R.id.fabLogin);
            fabInteract = findViewById(R.id.fabInteract);
            if (webView == null) { showError("Internal error: WebView not found."); return; }
            setupMainWebView();
            setupFABs();
            if (savedInstanceState != null) { webView.restoreState(savedInstanceState); }
            else { webView.loadUrl(PWA_URL); }
            hideAllFABs();
        } catch (Exception e) {
            Log.e(TAG, "onCreate crashed", e);
            showError("App startup error: " + e.getMessage());
        }
    }

    // ════════════════════════════════════════════════════════════════
    //  Main WebView setup
    // ════════════════════════════════════════════════════════════════
    @SuppressLint("SetJavaScriptEnabled")
    private void setupMainWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString("Mozilla/5.0 (Linux; Android 14; TikTokExtractorPro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36");

        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface public void openInTikTok(String url) { openInAppBrowserInternal(url); }
            @JavascriptInterface public void openInAppBrowser(String url) { openInAppBrowserInternal(url); }
        }, "Android");

        webView.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap favicon) {
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
                    String desc = err != null && err.getDescription() != null ? err.getDescription().toString() : "Unknown error";
                    showError("Failed to load page:\n" + desc);
                }
            }
        });
        webView.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override public void onProgressChanged(WebView v, int p) {
                if (progressBar != null) { progressBar.setProgress(p); if (p >= 100) progressBar.setVisibility(View.GONE); }
            }
            @Override public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
                Log.d(TAG, "JS[" + cm.messageLevel() + "]: " + cm.message()); return true;
            }
        });
    }

    // ════════════════════════════════════════════════════════════════
    //  FAB setup
    // ════════════════════════════════════════════════════════════════
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
                runJs("(() => { const i = document.getElementById('urlInput'); const u = i ? i.value : ''; if (u) Android.openInAppBrowser(u); else alert('Paste a TikTok URL first'); })();");
            });
            fabInteract.setOnLongClickListener(v -> { triggerDeepExtract(); return true; });
        }
    }

    // ════════════════════════════════════════════════════════════════
    //  Tools Menu (20 options, all English)
    // ════════════════════════════════════════════════════════════════
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
                    try { display = new JSONObject(resp).toString(2); }
                    catch (Exception e) { display = resp; }
                    if (display.length() > 4000) display = display.substring(0, 4000) + "...";
                    final String finalDisplay = display;
                    mainHandler.post(() -> new AlertDialog.Builder(this).setTitle(title).setMessage(finalDisplay).setPositiveButton("OK", null).show());
                } else { mainHandler.post(() -> toast("Error: HTTP " + code)); }
            } catch (Exception e) {
                Log.e(TAG, "fetchAndShowJson failed", e);
                mainHandler.post(() -> toast("Error: " + e.getMessage()));
            }
        }).start();
    }

    private void executeInteraction(String action) {
        runJs("(() => { const i = document.getElementById('urlInput'); const u = i ? i.value : ''; if (!u) { alert('Paste a URL first'); return; }"
            + "fetch('/api/extract?url=' + encodeURIComponent(u)).then(r => r.json()).then(d => {"
            + "if (!d.success) { alert('Extraction failed'); return; }"
            + "const body = {action: '" + action + "'};"
            + "if (d.all_ids) { body.room_id = d.all_ids.room_id || ''; body.sec_uid = d.all_ids.sec_uid || ''; body.user_id = d.all_ids.user_id || ''; body.video_id = d.content_id || ''; }"
            + "fetch('/api/react/execute', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})"
            + ".then(r => r.json()).then(r => { alert('Result: ' + (r.success ? 'SUCCESS' : 'FAILED') + '\\n' + JSON.stringify(r, null, 2).substring(0, 500)); })"
            + ".catch(e => alert('Error: ' + e)); }).catch(e => alert('Extract error: ' + e)); })();");
        toast("Running: " + action);
    }

    private void triggerDeepExtract() {
        runJs("(() => { const i = document.getElementById('urlInput'); const u = i ? i.value : ''; if (!u) { alert('Paste a URL first'); return; }"
            + "fetch('/api/deep/extract', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: u})})"
            + ".then(r => r.json()).then(d => { if (d.success) { alert('Deep extract OK! @' + d.unique_id + ' live' + d.live_number + ' (' + d.files_saved.length + ' files)'); } else { alert('Failed: ' + (d.error || 'unknown')); } })"
            + ".catch(e => alert('Error: ' + e)); })();");
    }

    private void openSessionTab() {
        hideAllFABs();
        runJs("(() => { const t = document.querySelector('.nav-tab[data-tab=\"session\"]'); if (t) t.click(); })();");
        webView.postDelayed(this::showAllFABs, 800);
    }

    private void openInTikTokFromInput() {
        runJs("(() => { const i = document.getElementById('urlInput'); const u = i ? i.value : ''; if (u) Android.openInAppBrowser(u); else alert('Paste a URL first'); })();");
    }

    private void syncGitHub() {
        toast("Syncing...");
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + "api/sync-db");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.getOutputStream().write("{}".getBytes("UTF-8"));
                String resp = readResponse(conn);
                JSONObject d = new JSONObject(resp);
                boolean ok = d.optBoolean("success", false);
                int pushed = d.optInt("pushed_files", 0);
                mainHandler.post(() -> toast(ok ? "Synced " + pushed + " files" : "Sync failed: " + d.optString("error")));
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void downloadLatestWebmssdk() {
        toast("Searching for webmssdk.js...");
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
                    final int liveCount = first.optInt("live_count", 1);
                    final String dlUrl = PWA_URL + "api/deep/users/" + uid + "/live" + liveCount + "/download/webmssdk.js";
                    mainHandler.post(() -> {
                        try { startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dlUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); toast("Downloading webmssdk.js from @" + uid); }
                        catch (Exception e) { toast("Download failed"); }
                    });
                } else { mainHandler.post(() -> toast("No deep data found")); }
            } catch (Exception e) { mainHandler.post(() -> toast("Error: " + e.getMessage())); }
        }).start();
    }

    private void downloadZip(String type) {
        toast("Preparing ZIP...");
        final String dlUrl = PWA_URL + "api/export/" + type;
        mainHandler.post(() -> {
            try { startActivity(new Intent(Intent.ACTION_VIEW, android.net.Uri.parse(dlUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); toast("Downloading ZIP: " + type); }
            catch (Exception e) { toast("Download failed"); }
        });
    }

    // ════════════════════════════════════════════════════════════════
    //  In-App Browser with Desktop Mode (FIXED: no crash)
    // ════════════════════════════════════════════════════════════════
    @SuppressLint("SetJavaScriptEnabled")
    private void openInAppBrowserInternal(String url) {
        Log.i(TAG, "Opening in-app browser: " + url);
        // MUST run on UI thread — JavascriptInterface calls from background
        mainHandler.post(() -> {
            try {
                toast("Opening in-app browser...");
                sessionCapturedInBrowser = false;
                browserWebView = new WebView(this);
                WebSettings s = browserWebView.getSettings();
                s.setJavaScriptEnabled(true);
                s.setDomStorageEnabled(true);
                s.setDatabaseEnabled(true);
                s.setCacheMode(WebSettings.LOAD_DEFAULT);
                s.setLoadWithOverviewMode(true);
                s.setUseWideViewPort(true);
                s.setSupportZoom(true);
                s.setBuiltInZoomControls(true);
                s.setDisplayZoomControls(false);
                s.setMediaPlaybackRequiresUserGesture(false);
                s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
                // DESKTOP MODE — forces TikTok to show desktop layout
                s.setUserAgentString("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");

                CookieManager cm = CookieManager.getInstance();
                cm.setAcceptCookie(true);
                cm.setAcceptThirdPartyCookies(browserWebView, true);

                webView.setVisibility(View.GONE);
                hideAllFABs();

                ViewGroup root = (ViewGroup) webView.getParent();
                root.addView(browserWebView, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

                // Status bar
                browserStatusBar = new TextView(this);
                browserStatusBar.setText("Browsing TikTok | Monitoring session...");
                browserStatusBar.setBackgroundColor(0xCC000000);
                browserStatusBar.setTextColor(0xFFFFFFFF);
                browserStatusBar.setPadding(24, 16, 24, 16);
                browserStatusBar.setTextSize(12);
                FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                sp.gravity = Gravity.TOP;
                root.addView(browserStatusBar, sp);

                // Close button
                TextView closeBtn = new TextView(this);
                closeBtn.setText("X Close");
                closeBtn.setBackgroundColor(0xCCFF2D55);
                closeBtn.setTextColor(0xFFFFFFFF);
                closeBtn.setPadding(24, 12, 24, 12);
                closeBtn.setTextSize(14);
                closeBtn.setOnClickListener(v -> closeInAppBrowser());
                FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                cp.gravity = Gravity.TOP | Gravity.RIGHT; cp.topMargin = 60; cp.rightMargin = 16;
                root.addView(closeBtn, cp);

                // Check session button
                TextView checkBtn = new TextView(this);
                checkBtn.setText("Check Session");
                checkBtn.setBackgroundColor(0xCC25F4EE);
                checkBtn.setTextColor(0xFF000000);
                checkBtn.setPadding(24, 12, 24, 12);
                checkBtn.setTextSize(14);
                checkBtn.setOnClickListener(v -> checkAndCaptureSession());
                FrameLayout.LayoutParams kp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                kp.gravity = Gravity.TOP | Gravity.LEFT; kp.topMargin = 60; kp.leftMargin = 16;
                root.addView(checkBtn, kp);

                browserWebView.setWebViewClient(new WebViewClient() {
                    @Override public void onPageFinished(WebView v, String pageUrl) {
                        super.onPageFinished(v, pageUrl);
                        Log.i(TAG, "Browser page loaded: " + pageUrl);
                        String cookies = cm.getCookie("https://www.tiktok.com/");
                        if (cookies != null) {
                            if (cookies.contains("sessionid") && !sessionCapturedInBrowser) {
                                sessionCapturedInBrowser = true;
                                if (browserStatusBar != null) { browserStatusBar.setText("Browsing | Session captured! (sessionid found)"); browserStatusBar.setBackgroundColor(0xCC00D68F); }
                                toast("Session captured!");
                                captureAndUploadSession(cookies, pageUrl);
                            } else if (!sessionCapturedInBrowser && browserStatusBar != null) {
                                if (cookies.contains("sid_tt") || cookies.contains("ttwid"))
                                    browserStatusBar.setText("Browsing | Partial session — please login");
                                else
                                    browserStatusBar.setText("Browsing | Monitoring session...");
                            }
                        }
                    }
                });
                browserWebView.loadUrl(url);
            } catch (Exception e) {
                Log.e(TAG, "openInAppBrowserInternal crashed", e);
                toast("Browser error: " + e.getMessage());
            }
        });
    }

    private void checkAndCaptureSession() {
        if (browserWebView == null) { toast("No active browser"); return; }
        CookieManager cm = CookieManager.getInstance();
        String cookies = cm.getCookie("https://www.tiktok.com/");
        if (cookies == null || cookies.isEmpty()) { toast("No cookies — browse TikTok first"); return; }
        Map<String, String> cm2 = parseCookies(cookies);
        boolean hasSid = cm2.containsKey("sessionid") && !cm2.get("sessionid").isEmpty();
        boolean hasSidTt = cm2.containsKey("sid_tt") && !cm2.get("sid_tt").isEmpty();
        boolean hasTtwid = cm2.containsKey("ttwid") && !cm2.get("ttwid").isEmpty();
        boolean hasMs = cm2.containsKey("msToken") && !cm2.get("msToken").isEmpty();
        StringBuilder sb = new StringBuilder();
        sb.append("Session Check:\n\n");
        sb.append("sessionid: ").append(hasSid ? "YES" : "NO").append("\n");
        sb.append("sid_tt: ").append(hasSidTt ? "YES" : "NO").append("\n");
        sb.append("ttwid: ").append(hasTtwid ? "YES" : "NO").append("\n");
        sb.append("msToken: ").append(hasMs ? "YES" : "NO").append("\n");
        sb.append("Total cookies: ").append(cm2.size()).append("\n\n");
        if (hasSid) {
            sb.append("Session is active and complete!");
            if (browserStatusBar != null) { browserStatusBar.setText("Browsing | Session active"); browserStatusBar.setBackgroundColor(0xCC00D68F); }
            captureAndUploadSession(cookies, browserWebView.getUrl() != null ? browserWebView.getUrl() : "");
            sb.append("\nSession saved automatically");
        } else if (hasSidTt || hasTtwid) {
            sb.append("Partial session — login to get sessionid");
        } else {
            sb.append("No session — login to TikTok");
        }
        String status = sb.toString();
        new AlertDialog.Builder(this).setTitle("Session Check").setMessage(status).setPositiveButton("OK", null).show();
    }

    private void closeInAppBrowser() {
        if (browserWebView != null) {
            try {
                ViewGroup root = (ViewGroup) webView.getParent();
                root.removeView(browserWebView);
                if (browserStatusBar != null) root.removeView(browserStatusBar);
                browserWebView.destroy(); browserWebView = null; browserStatusBar = null;
            } catch (Exception e) { Log.e(TAG, "closeInAppBrowser failed", e); }
        }
        webView.setVisibility(View.VISIBLE);
        showAllFABs();
        toast("Back to main");
    }

    // ════════════════════════════════════════════════════════════════
    //  Login WebView (session capture)
    // ════════════════════════════════════════════════════════════════
    @SuppressLint("SetJavaScriptEnabled")
    private void openLoginCaptureWebView() {
        toast("Open TikTok and login");
        mainHandler.post(() -> {
            try {
                browserWebView = new WebView(this);
                WebSettings s = browserWebView.getSettings();
                s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true); s.setDatabaseEnabled(true);
                s.setUserAgentString("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");
                CookieManager cm = CookieManager.getInstance(); cm.setAcceptCookie(true); cm.setAcceptThirdPartyCookies(browserWebView, true);
                webView.setVisibility(View.GONE); hideAllFABs();
                ViewGroup root = (ViewGroup) webView.getParent();
                root.addView(browserWebView, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
                // Close button
                TextView closeBtn = new TextView(this); closeBtn.setText("X Close"); closeBtn.setBackgroundColor(0xCCFF2D55); closeBtn.setTextColor(0xFFFFFFFF); closeBtn.setPadding(24, 12, 24, 12); closeBtn.setTextSize(14);
                closeBtn.setOnClickListener(v -> closeInAppBrowser());
                FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT);
                cp.gravity = Gravity.TOP | Gravity.RIGHT; cp.topMargin = 40; cp.rightMargin = 16;
                root.addView(closeBtn, cp);
                browserWebView.setWebViewClient(new WebViewClient() {
                    private boolean alreadyCaptured = false;
                    @Override public void onPageFinished(WebView v, String pageUrl) {
                        if (!alreadyCaptured) {
                            String cookies = cm.getCookie("https://www.tiktok.com/");
                            if (cookies != null && cookies.contains("sessionid")) {
                                alreadyCaptured = true;
                                toast("Session captured!");
                                captureAndUploadSession(cookies, pageUrl);
                            }
                        }
                    }
                });
                browserWebView.loadUrl("https://www.tiktok.com/login/phone-or-email/email");
            } catch (Exception e) { Log.e(TAG, "login WebView crashed", e); toast("Error: " + e.getMessage()); }
        });
    }

    private void captureAndUploadSession(String cookieString, String currentUrl) {
        Log.i(TAG, "Capturing cookies, length=" + cookieString.length());
        Map<String, String> cookies = parseCookies(cookieString);
        String sessionid = cookies.get("sessionid");
        if (sessionid == null || sessionid.isEmpty()) { Log.w(TAG, "sessionid not found"); return; }
        Log.i(TAG, "Captured sessionid (length=" + sessionid.length() + ")");
        final JSONObject payload = new JSONObject();
        try {
            payload.put("sessionid", sessionid);
            JSONObject extra = new JSONObject();
            for (String k : new String[]{"ttwid","msToken","sid_tt","passport_csrf_token","sid_guard","uid_tt","uid_tt_ss","tt_chain_token","cmpl_token"}) {
                String val = cookies.get(k); if (val != null && !val.isEmpty()) extra.put(k, val);
            }
            payload.put("extra_cookies", extra);
        } catch (Exception e) { Log.e(TAG, "payload build failed", e); return; }
        new Thread(() -> {
            try {
                URL url = new URL(PWA_URL + "api/session/capture");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(30000); conn.setReadTimeout(60000);
                conn.getOutputStream().write(payload.toString().getBytes("UTF-8"));
                int code = conn.getResponseCode();
                String resp = readResponse(conn);
                Log.i(TAG, "Session upload: " + code + " — " + resp.substring(0, Math.min(200, resp.length())));
                try {
                    JSONObject r = new JSONObject(resp);
                    if (r.optBoolean("success")) { mainHandler.post(() -> { toast("Session saved! @" + r.optString("unique_id", "me")); closeInAppBrowser(); }); }
                    else { mainHandler.post(() -> toast("Save failed: " + r.optString("error", ""))); }
                } catch (Exception e) { mainHandler.post(() -> toast("Unexpected response")); }
            } catch (Exception e) { Log.e(TAG, "upload failed", e); mainHandler.post(() -> toast("Network error: " + e.getMessage())); }
        }).start();
    }

    private void checkSavedSession() {
        String uid = prefs.getString("last_unique_id", "");
        if (uid.isEmpty()) { toast("No saved session. Use login button first."); return; }
        toast("Checking session for " + uid);
        runJs("(() => { fetch('/api/session/" + uid + "/status').then(r=>r.json()).then(d=>{ alert(d.has_session ? 'Session saved! Saved at: ' + d.saved_at : 'No saved session'); }).catch(e=>alert('Error: '+e)); })();");
    }

    // ════════════════════════════════════════════════════════════════
    //  Utilities
    // ════════════════════════════════════════════════════════════════
    private void runJs(String js) { if (webView != null) webView.post(() -> webView.evaluateJavascript(js, null)); }
    private void toast(String msg) { mainHandler.post(() -> { try { Toast.makeText(this, msg, Toast.LENGTH_LONG).show(); } catch (Exception e) { Log.e(TAG, "toast failed", e); } }); }
    private void showError(String msg) { mainHandler.post(() -> { try { if (webView != null) webView.setVisibility(View.GONE); if (progressBar != null) progressBar.setVisibility(View.GONE); if (errorView != null) { errorView.setText(msg); errorView.setVisibility(View.VISIBLE); } } catch (Exception e) { Log.e(TAG, "showError failed", e); } }); }
    private void hideAllFABs() { if (fabDatabase != null) fabDatabase.hide(); if (fabLogin != null) fabLogin.hide(); if (fabInteract != null) fabInteract.hide(); }
    private void showAllFABs() { if (fabDatabase != null) fabDatabase.show(); if (fabLogin != null) fabLogin.show(); if (fabInteract != null) fabInteract.show(); }

    private Map<String, String> parseCookies(String cookieString) {
        Map<String, String> m = new HashMap<>();
        if (cookieString == null) return m;
        for (String part : cookieString.split(";")) {
            int eq = part.indexOf('=');
            if (eq > 0) m.put(part.substring(0, eq).trim(), part.substring(eq + 1).trim());
        }
        return m;
    }

    private String readResponse(HttpURLConnection conn) {
        try {
            java.io.InputStream is = conn.getResponseCode() >= 400 ? conn.getErrorStream() : conn.getInputStream();
            java.io.BufferedReader r = new java.io.BufferedReader(new java.io.InputStreamReader(is, "UTF-8"));
            StringBuilder sb = new StringBuilder(); String line;
            while ((line = r.readLine()) != null) sb.append(line);
            r.close(); return sb.toString();
        } catch (Exception e) { return "(error: " + e.getMessage() + ")"; }
    }

    @Override public void onBackPressed() {
        if (browserWebView != null) { if (browserWebView.canGoBack()) browserWebView.goBack(); else closeInAppBrowser(); return; }
        try { if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed(); }
        catch (Exception e) { super.onBackPressed(); }
    }
    @Override protected void onPause() { super.onPause(); try { if (webView != null) webView.onPause(); } catch (Exception e) {} }
    @Override protected void onResume() { super.onResume(); try { if (webView != null) webView.onResume(); } catch (Exception e) {} }
    @Override protected void onDestroy() {
        try {
            if (browserWebView != null) { ((ViewGroup) browserWebView.getParent()).removeView(browserWebView); browserWebView.destroy(); browserWebView = null; }
            if (webView != null) { ((ViewGroup) webView.getParent()).removeView(webView); webView.destroy(); webView = null; }
        } catch (Exception e) { Log.e(TAG, "onDestroy failed", e); }
        super.onDestroy();
    }
    @Override protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); try { if (webView != null) webView.saveState(out); } catch (Exception e) {} }
}
