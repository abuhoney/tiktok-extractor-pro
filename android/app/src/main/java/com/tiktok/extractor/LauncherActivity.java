package com.tiktok.extractor;

import android.net.Uri;
import android.os.Bundle;
import androidx.appcompat.app.AppCompatActivity;
import androidx.browser.customtabs.CustomTabsIntent;

/**
 * Launcher Activity for the TWA (Trusted Web Activity).
 * Opens https://tiktok-extractor-pro.onrender.com/ in a Chrome Custom Tab.
 * When the matching Digital Asset Links file is published at
 * /.well-known/assetlinks.json on the website, the URL bar is hidden
 * and the PWA runs as if it were a native app.
 */
public class LauncherActivity extends AppCompatActivity {
    private static final String TWA_URL = "https://tiktok-extractor-pro.onrender.com/";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Open the PWA URL in a Chrome Custom Tab
        CustomTabsIntent builder = new CustomTabsIntent.Builder()
                .setShowTitle(false)
                .setUrlBarHidingEnabled(true)
                .build();
        builder.intent.setPackage("com.android.chrome");
        builder.launchUrl(this, Uri.parse(TWA_URL));
        finish();  // close the launcher so only the PWA tab is visible
    }
}
