package com.enjaz.preview;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.CookieManager;
import android.content.Intent;
import android.net.Uri;

public class MainActivity extends Activity {
  private WebView view;
  @Override public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    view = new WebView(this);
    view.getSettings().setJavaScriptEnabled(true);
    view.getSettings().setUseWideViewPort(true);
    view.getSettings().setLoadWithOverviewMode(true);
    view.getSettings().setTextZoom(100);
    view.getSettings().setDomStorageEnabled(true);
    view.getSettings().setCacheMode(WebSettings.LOAD_NO_CACHE);
    view.clearCache(true);
    CookieManager.getInstance().setAcceptCookie(true);
    view.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    view.setWebChromeClient(new WebChromeClient());
    view.setWebViewClient(new WebViewClient() {
      @Override public boolean shouldOverrideUrlLoading(WebView v, android.webkit.WebResourceRequest request) {
        Uri u=request.getUrl();
        if ("https".equals(u.getScheme()) && u.getHost()!=null && u.getHost().endsWith("onrender.com")) return false;
        startActivity(new Intent(Intent.ACTION_VIEW,u)); return true;
      }
    });
    setContentView(view);
    view.loadUrl(getString(R.string.site_url) + "?androidBuild=2");
  }
  @Override public void onBackPressed() { if(view.canGoBack()) view.goBack(); else super.onBackPressed(); }
  @Override protected void onDestroy(){view.destroy();super.onDestroy();}
}
