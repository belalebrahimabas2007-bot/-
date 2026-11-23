package com.enjaz.preview;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceError;
import android.content.Intent;
import android.net.Uri;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Button;
import android.graphics.Color;
import android.os.Build;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONArray;
import org.json.JSONObject;

public class MainActivity extends Activity {
  private WebView view;
  private LinearLayout root;
  private LinearLayout errorPanel;
  private String homeUrl;
  private boolean loadError = false;

  private UpdateScreen updater;
  private boolean checkedUpdates;

  private long installedVersion() {
    try {
      android.content.pm.PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
      return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    } catch (android.content.pm.PackageManager.NameNotFoundException e) {
      return Long.MAX_VALUE;
    }
  }

  private void checkForUpdates() {
    new Thread(() -> {
      try {
        HttpURLConnection connection = (HttpURLConnection) new URL("https://api.github.com/repos/belalebrahimabas2007-bot/-/releases/latest").openConnection();
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(8000);
        connection.setRequestProperty("Accept", "application/vnd.github+json");
        if (connection.getResponseCode() != 200) { connection.disconnect(); return; }
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
        try (java.io.InputStream stream = connection.getInputStream()) {
          byte[] buffer = new byte[4096]; int count;
          while ((count = stream.read(buffer)) > 0 && output.size() < 200000) output.write(buffer, 0, count);
        } finally { connection.disconnect(); }
        JSONObject release = new JSONObject(output.toString("UTF-8"));
        String tag = release.optString("tag_name", "");
        if (!tag.matches("v[0-9]+")) return;
        int newest = Integer.parseInt(tag.substring(1));
        if (newest <= installedVersion()) return;
        String apkName = getPackageName().contains(".admin.") ? "Enjaz-Admin.apk" : "Enjaz-Student.apk";
        JSONArray assets = release.optJSONArray("assets");
        if (assets == null) return;
        for (int i = 0; i < assets.length(); i++) {
          JSONObject asset = assets.getJSONObject(i);
          if (!apkName.equals(asset.optString("name"))) continue;
          String downloadUrl = asset.optString("browser_download_url", "");
          if (!downloadUrl.startsWith("https://github.com/belalebrahimabas2007-bot/-/releases/download/")) return;
          runOnUiThread(() -> updater.offer(downloadUrl, apkName, newest));
          return;
        }
      } catch (Exception ignored) { /* No internet or no release yet. */ }
    }).start();
  }
  private void showConnectionError() {
    if (errorPanel != null) errorPanel.setVisibility(View.VISIBLE);
    if (view != null) view.setVisibility(View.GONE);
  }

  private void hideConnectionError() {
    if (errorPanel != null) errorPanel.setVisibility(View.GONE);
    if (view != null) view.setVisibility(View.VISIBLE);
  }

  @Override public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    homeUrl = getString(R.string.site_url);
    root = new LinearLayout(this);
    root.setOrientation(LinearLayout.VERTICAL);
    root.setBackgroundColor(Color.rgb(243,248,246));
    view = new WebView(this);
    view.getSettings().setJavaScriptEnabled(true);
    view.getSettings().setDomStorageEnabled(true);
    view.getSettings().setUseWideViewPort(false);
    view.getSettings().setLoadWithOverviewMode(false);
    view.getSettings().setTextZoom(100);
    view.getSettings().setSupportZoom(false);
    view.getSettings().setBuiltInZoomControls(false);
    view.getSettings().setDisplayZoomControls(false);
    view.getSettings().setCacheMode(WebSettings.LOAD_DEFAULT);
    view.setHorizontalScrollBarEnabled(false);
    view.setVerticalScrollBarEnabled(false);
    CookieManager.getInstance().setAcceptCookie(true);
    view.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    view.setWebChromeClient(new WebChromeClient());
    view.setWebViewClient(new WebViewClient() {
      @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
        Uri u = request.getUrl();
        if ("https".equals(u.getScheme()) && u.getHost()!=null && u.getHost().endsWith("onrender.com")) return false;
        try { startActivity(new Intent(Intent.ACTION_VIEW,u)); } catch (Exception ignored) { }
        return true;
      }
      @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap favicon) {
        loadError = false;
      }
      @Override public void onReceivedError(WebView v, WebResourceRequest request, WebResourceError error) {
        if (request.isForMainFrame()) { loadError = true; showConnectionError(); }
      }
      @Override public void onPageFinished(WebView v, String url) {
        if (!loadError && url.startsWith(homeUrl)) hideConnectionError();
      }
    });
    errorPanel = new LinearLayout(this);
    errorPanel.setOrientation(LinearLayout.VERTICAL);
    errorPanel.setPadding(32, 60, 32, 32);
    errorPanel.setBackgroundColor(Color.rgb(243,248,246));
    TextView message = new TextView(this);
    message.setText("تعذر الاتصال بإنجاز. تأكد من الإنترنت ثم حاول مجدداً.");
    message.setTextColor(Color.rgb(15,118,110));
    message.setTextSize(19);
    message.setGravity(android.view.Gravity.CENTER);
    errorPanel.addView(message, new LinearLayout.LayoutParams(-1,-2));
    Button retry = new Button(this);
    retry.setText("إعادة المحاولة");
    retry.setOnClickListener(v -> { hideConnectionError(); view.loadUrl(homeUrl); });
    errorPanel.addView(retry, new LinearLayout.LayoutParams(-1,-2));
    errorPanel.setVisibility(View.GONE);
    root.addView(view,new LinearLayout.LayoutParams(-1,0,1));
    root.addView(errorPanel,new LinearLayout.LayoutParams(-1,0,1));
    setContentView(root);
    updater = new UpdateScreen(this);
    if (savedInstanceState != null) view.restoreState(savedInstanceState);
    else view.loadUrl(homeUrl);
  }
  @Override protected void onResume() {
    super.onResume();
    updater.resume(installedVersion());
    if (!checkedUpdates) {
      checkedUpdates = true;
      checkForUpdates();
    }
  }

  @Override protected void onPause() {
    updater.pause();
    super.onPause();
  }

  @Override protected void onSaveInstanceState(Bundle outState) {
    view.saveState(outState);
    super.onSaveInstanceState(outState);
  }
  @Override public void onBackPressed() {
    if (view.canGoBack()) view.goBack();
    else super.onBackPressed();
  }
  @Override protected void onDestroy() {
    updater.close();
    if(view != null) { root.removeView(view); view.destroy(); }
    super.onDestroy();
  }
}
