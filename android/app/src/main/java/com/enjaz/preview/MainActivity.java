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

public class MainActivity extends Activity {
  private WebView view;
  private LinearLayout root;
  private LinearLayout errorPanel;
  private String homeUrl;

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
      @Override public void onReceivedError(WebView v, WebResourceRequest request, WebResourceError error) {
        if (request.isForMainFrame()) showConnectionError();
      }
      @Override public void onPageFinished(WebView v, String url) {
        if (url.startsWith(homeUrl)) hideConnectionError();
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
    if(view != null) { root.removeView(view); view.destroy(); }
    super.onDestroy();
  }
}
