package com.enjaz.preview;

import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.Dialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.core.content.FileProvider;
import java.io.File;
import java.util.Locale;
import static android.os.Environment.DIRECTORY_DOWNLOADS;

final class UpdateScreen {
  private static final int GREEN = Color.rgb(15, 118, 110);
  private static final int DARK = Color.rgb(10, 48, 53);
  private static final int ORANGE = Color.rgb(242, 153, 74);
  private static final int MUTED = Color.rgb(71, 97, 101);

  private final MainActivity activity;
  private final DownloadManager downloads;
  private final SharedPreferences state;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private final Runnable refresh = this::readProgress;
  private Dialog screen;
  private TextView heading, percent, bytes, speed;
  private ProgressBar progress;
  private Button action;
  private long previousBytes = -1;
  private long previousTime;
  private double lastRate;
  private boolean foreground;

  UpdateScreen(MainActivity activity) {
    this.activity = activity;
    downloads = (DownloadManager) activity.getSystemService(Context.DOWNLOAD_SERVICE);
    state = activity.getSharedPreferences("enjaz_update", Context.MODE_PRIVATE);
  }

  void offer(String url, String asset, int version) {
    if (!foreground || activity.isFinishing() || activity.isDestroyed() ||
        state.getLong("id", -1) != -1) return;
    new AlertDialog.Builder(activity)
        .setTitle("يتوفر تحديث جديد لإنجاز")
        .setMessage("الإصدار " + version + " متوفر للتنزيل.")
        .setNegativeButton("لاحقاً", null)
        .setPositiveButton("تنزيل التحديث", (dialog, which) -> start(url, asset, version))
        .show();
  }

  private void start(String url, String asset, int version) {
    String filename = "enjaz-" + version + "-" + asset;
    try {
      File previous = new File(activity.getExternalFilesDir(DIRECTORY_DOWNLOADS), filename);
      if (previous.exists() && !previous.delete()) throw new IllegalStateException();
      DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url))
          .setTitle("تحديث " + activity.getString(R.string.app_name))
          .setDescription("جارٍ تنزيل الإصدار الجديد")
          .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
          .setDestinationInExternalFilesDir(activity, DIRECTORY_DOWNLOADS, filename);
      long id = downloads.enqueue(request);
      state.edit().putLong("id", id).putString("filename", filename).putInt("version", version)
          .putString("url", url).putString("asset", asset).apply();
      previousBytes = -1;
      lastRate = 0;
      display();
    } catch (RuntimeException e) {
      new AlertDialog.Builder(activity).setTitle("تحديث إنجاز")
          .setMessage("تعذر بدء التنزيل. تحقق من مساحة التخزين وحاول مجدداً.")
          .setPositiveButton("حسناً", null).show();
    }
  }

  void resume(long installedVersion) {
    foreground = true;
    if (state.getLong("id", -1) == -1) return;
    if (state.getInt("version", 0) <= installedVersion) {
      state.edit().clear().apply();
      return;
    }
    display();
  }

  void pause() {
    foreground = false;
    handler.removeCallbacks(refresh);
    previousBytes = -1;
    lastRate = 0;
  }

  void close() {
    pause();
    if (screen != null) screen.dismiss();
  }

  private void display() {
    if (screen == null || !screen.isShowing()) buildScreen();
    handler.removeCallbacks(refresh);
    readProgress();
  }

  private void readProgress() {
    if (!foreground || screen == null || !screen.isShowing()) return;
    long id = state.getLong("id", -1);
    int status = DownloadManager.STATUS_FAILED;
    long done = 0, total = -1;
    try (Cursor row = downloads.query(new DownloadManager.Query().setFilterById(id))) {
      if (row != null && row.moveToFirst()) {
        status = row.getInt(row.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
        done = row.getLong(row.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
        total = row.getLong(row.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
      }
    } catch (RuntimeException ignored) {
      // A stale or unavailable system download can be restarted from the same screen.
    }
    boolean finished = status == DownloadManager.STATUS_SUCCESSFUL;
    int value = finished ? 100 : total > 0 ? (int) Math.min(100, 100.0 * done / total) : 0;
    progress.setIndeterminate(!finished && total <= 0 && status != DownloadManager.STATUS_FAILED);
    progress.setProgress(value);
    percent.setText(finished || total > 0 ? value + "%" : "—");
    bytes.setText(total > 0 ? units(done) + " / " + units(total) : units(done));

    long now = SystemClock.elapsedRealtime();
    if (previousBytes >= 0 && now > previousTime) {
      lastRate = Math.max(0, (done - previousBytes) * 1000.0 / (now - previousTime));
    }
    previousBytes = done;
    previousTime = now;
    boolean running = status == DownloadManager.STATUS_RUNNING;
    speed.setText(running ? units(lastRate) + "/s" : "—");

    if (finished) {
      heading.setText("التحديث جاهز للتثبيت");
      action.setText("تثبيت التحديث");
      action.setVisibility(View.VISIBLE);
      action.setOnClickListener(view -> install());
    } else if (status == DownloadManager.STATUS_FAILED) {
      heading.setText("تعذر تنزيل التحديث");
      action.setText("إعادة التنزيل");
      action.setVisibility(View.VISIBLE);
      action.setOnClickListener(view -> start(state.getString("url", ""),
          state.getString("asset", ""), state.getInt("version", 0)));
    } else {
      heading.setText(running ? "جارٍ تنزيل التحديث" : "بانتظار بدء التنزيل");
      action.setVisibility(View.GONE);
      handler.postDelayed(refresh, 650);
    }
  }

  private void install() {
    File apk = new File(activity.getExternalFilesDir(DIRECTORY_DOWNLOADS), state.getString("filename", ""));
    if (!apk.isFile()) {
      downloads.remove(state.getLong("id", -1));
      readProgress();
      return;
    }
    if (Build.VERSION.SDK_INT >= 26 && !activity.getPackageManager().canRequestPackageInstalls()) {
      new AlertDialog.Builder(activity).setTitle("السماح بتثبيت التحديث")
          .setMessage("اسمح لإنجاز بالتثبيت من هذا المصدر، ثم ارجع واضغط تثبيت التحديث.")
          .setPositiveButton("فتح الإعدادات", (dialog, which) -> activity.startActivity(new Intent(
              Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.getPackageName()))))
          .setNegativeButton("لاحقاً", null).show();
      return;
    }
    try {
      Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".fileprovider", apk);
      Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
          .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
      activity.startActivity(intent);
    } catch (RuntimeException e) {
      heading.setText("تعذر فتح شاشة التثبيت");
    }
  }

  private void buildScreen() {
    Dialog dialog = new Dialog(activity);
    dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
    ScrollView scroller = new ScrollView(activity);
    scroller.setFillViewport(true);
    scroller.setBackground(new GradientDrawable(GradientDrawable.Orientation.TL_BR,
        new int[]{DARK, Color.rgb(13, 75, 76), GREEN}));
    LinearLayout frame = column(22);
    frame.setGravity(Gravity.CENTER);
    frame.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
    scroller.addView(frame, new ScrollView.LayoutParams(-1, -1));
    ImageView logo = new ImageView(activity);
    logo.setImageResource(R.drawable.enjaz_launcher);
    logo.setContentDescription(activity.getString(R.string.app_name));
    logo.setBackground(round(Color.WHITE, 25));
    logo.setPadding(dp(8), dp(8), dp(8), dp(8));
    frame.addView(logo, new LinearLayout.LayoutParams(dp(74), dp(74)));
    add(frame, text("تحديث التطبيق", 28, Color.WHITE, true), 14);
    add(frame, text(activity.getString(R.string.app_name) + "  ·  الإصدار " +
        state.getInt("version", 0), 14, Color.rgb(214, 237, 233), false), 4);

    LinearLayout card = column(23);
    card.setBackground(round(Color.WHITE, 25));
    LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(-1, -2);
    cardParams.topMargin = dp(24);
    frame.addView(card, cardParams);
    heading = text("", 22, DARK, true);
    add(card, heading, 0);
    percent = text("", 36, GREEN, true);
    percent.setTextDirection(View.TEXT_DIRECTION_LTR);
    add(card, percent, 10);
    progress = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
    progress.setMax(100);
    progress.setProgressTintList(ColorStateList.valueOf(GREEN));
    progress.setIndeterminateTintList(ColorStateList.valueOf(ORANGE));
    add(card, progress, 4);
    bytes = text("", 14, MUTED, false);
    bytes.setTextDirection(View.TEXT_DIRECTION_LTR);
    add(card, bytes, 7);

    LinearLayout network = column(12);
    network.setBackground(round(Color.rgb(235, 247, 244), 16));
    add(card, network, 17);
    add(network, text("سرعة التنزيل", 14, MUTED, true), 0);
    speed = text("", 24, GREEN, true);
    speed.setTextDirection(View.TEXT_DIRECTION_LTR);
    add(network, speed, 2);
    action = new Button(activity);
    action.setAllCaps(false);
    action.setBackgroundTintList(ColorStateList.valueOf(ORANGE));
    action.setTextColor(DARK);
    action.setVisibility(View.GONE);
    add(card, action, 18);

    Button later = new Button(activity);
    later.setText("العودة إلى إنجاز");
    later.setAllCaps(false);
    later.setBackgroundTintList(ColorStateList.valueOf(Color.rgb(223, 243, 238)));
    later.setTextColor(DARK);
    later.setOnClickListener(view -> dialog.dismiss());
    add(frame, later, 14);
    dialog.setOnDismissListener(unused -> handler.removeCallbacks(refresh));
    dialog.setContentView(scroller);
    dialog.show();
    Window window = dialog.getWindow();
    if (window != null) {
      window.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
      window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
      window.setStatusBarColor(DARK);
      window.setNavigationBarColor(GREEN);
      window.setLayout(-1, -1);
    }
    screen = dialog;
  }

  private LinearLayout column(int padding) {
    LinearLayout layout = new LinearLayout(activity);
    layout.setOrientation(LinearLayout.VERTICAL);
    int px = dp(padding);
    layout.setPadding(px, px, px, px);
    return layout;
  }

  private TextView text(String label, int size, int color, boolean bold) {
    TextView view = new TextView(activity);
    view.setText(label);
    view.setTextSize(size);
    view.setTextColor(color);
    view.setGravity(Gravity.CENTER);
    if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
    return view;
  }

  private void add(LinearLayout parent, View child, int top) {
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
    params.topMargin = dp(top);
    parent.addView(child, params);
  }

  private GradientDrawable round(int color, int radius) {
    GradientDrawable shape = new GradientDrawable();
    shape.setColor(color);
    shape.setCornerRadius(dp(radius));
    return shape;
  }

  private int dp(int value) {
    return Math.round(value * activity.getResources().getDisplayMetrics().density);
  }

  private static String units(double size) {
    if (size >= 1048576) return String.format(Locale.US, "%.1f MB", size / 1048576);
    return String.format(Locale.US, "%.1f KB", size / 1024);
  }
}
