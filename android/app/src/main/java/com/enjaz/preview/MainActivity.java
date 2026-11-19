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
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.IntentFilter;
import android.os.Build;
import android.os.Environment;
import java.io.File;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONArray;
import org.json.JSONObject;
import androidx.core.content.FileProvider;

public class MainActivity extends Activity {
  private WebView view;
  private LinearLayout root;
  private LinearLayout errorPanel;
  private String homeUrl;
  private boolean loadError = false;

  private long updateDownloadId = -1;
  private String updateFileName;
  private final BroadcastReceiver updateReceiver = new BroadcastReceiver() {
    @Override public void onReceive(Context context, Intent intent) {
      if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) != updateDownloadId) return;
      DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
      android.database.Cursor cursor = manager.query(new DownloadManager.Query().setFilterById(updateDownloadId));
      if (cursor == null) return;
      try {
        if (cursor.moveToFirst() && cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) == DownloadManager.STATUS_SUCCESSFUL) {
          File apk = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), updateFileName);
          Uri uri = FileProvider.getUriForFile(thisActivity(), getPackageName() + ".fileprovider", apk);
          if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(thisActivity()).setTitle("السماح بتثبيت التحديث")
              .setMessage("اسمح لإنجاز بتثبيت التطبيقات من هذا المصدر، ثم افتح التحديث مرة أخرى.")
              .setPositiveButton("فتح الإعدادات", (dialog, which) -> {
                Intent settings = new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                  Uri.parse("package:" + getPackageName()));
                startActivity(settings);
              }).setNegativeButton("لاحقاً", null).show();
            return;
          }
          Intent install = new Intent(Intent.ACTION_VIEW);
          install.setDataAndType(uri, "application/vnd.android.package-archive");
          install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
          try { startActivity(install); } catch (Exception e) { updateMessage("تم تنزيل التحديث، لكن تعذر فتح شاشة التثبيت."); }
        } else updateMessage("فشل تنزيل التحديث. تحقق من اتصال الإنترنت وحاول مجدداً.");
      } finally { cursor.close(); }
    }
  };
  private Activity thisActivity() { return this; }
  private void updateMessage(String message) {
    runOnUiThread(() -> new AlertDialog.Builder(this).setTitle("تحديث إنجاز").setMessage(message).setPositiveButton("حسناً", null).show());
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
        long current = Build.VERSION.SDK_INT >= 28
          ? getPackageManager().getPackageInfo(getPackageName(), 0).getLongVersionCode()
          : getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
        if (newest <= current) return;
        String apkName = getPackageName().contains(".admin.") ? "Enjaz-Admin.apk" : "Enjaz-Student.apk";
        JSONArray assets = release.optJSONArray("assets");
        if (assets == null) return;
        for (int i = 0; i < assets.length(); i++) {
          JSONObject asset = assets.getJSONObject(i);
          if (!apkName.equals(asset.optString("name"))) continue;
          String downloadUrl = asset.optString("browser_download_url", "");
          if (!downloadUrl.startsWith("https://github.com/belalebrahimabas2007-bot/-/releases/download/")) return;
          runOnUiThread(() -> new AlertDialog.Builder(this)
            .setTitle("يتوفر تحديث جديد لإنجاز")
            .setMessage("الإصدار " + tag + " متوفر. هل تريد تنزيل التحديث؟")
            .setNegativeButton("لاحقاً", null)
            .setPositiveButton("تنزيل التحديث", (dialog, which) -> downloadUpdate(downloadUrl, apkName, newest))
            .show());
          return;
        }
      } catch (Exception ignored) { /* No internet or no release yet. */ }
    }).start();
  }
  private void downloadUpdate(String url, String apkName, int version) {
    try {
      updateFileName = "enjaz-" + version + "-" + apkName;
      File previous = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), updateFileName);
      if (previous.exists()) previous.delete();
      DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
      request.setTitle("تحديث إنجاز");
      request.setDescription("جارٍ تنزيل الإصدار الجديد");
      request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
      request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, updateFileName);
      DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
      updateDownloadId = manager.enqueue(request);
    } catch (Exception e) { updateMessage("تعذر بدء التنزيل."); }
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
    view.getSettings().setUseWideViewPort(true);
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
    IntentFilter updateFilter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
    if (Build.VERSION.SDK_INT >= 33) registerReceiver(updateReceiver, updateFilter, Context.RECEIVER_NOT_EXPORTED);
    else registerReceiver(updateReceiver, updateFilter);
    checkForUpdates();
    if (savedInstanceState != null) view.restoreState(savedInstanceState);
    else view.loadUrl(homeUrl);
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
    try { unregisterReceiver(updateReceiver); } catch (Exception ignored) {}
    if(view != null) { root.removeView(view); view.destroy(); }
    super.onDestroy();
  }
}
