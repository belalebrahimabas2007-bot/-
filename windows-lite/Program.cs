using System;
using System.Drawing;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Web.WebView2.Core;

namespace EnjazWindows;
internal static class Program
{
    [STAThread]
    static void Main()
    {
        ApplicationConfiguration.Initialize();
#if ENJAZ_ADMIN
        const string name = "إنجاز الإدارة";
        const string url = "https://engaz-admin-preview.onrender.com/";
#else
        const string name = "إنجاز الطلاب";
        const string url = "https://engaz-student-preview.onrender.com/";
#endif
        Application.Run(new EnjazForm(name, url));
    }
}
public sealed class EnjazForm : Form
{
    private readonly WebView2 browser = new();
    private readonly string url;
    public EnjazForm(string title, string address)
    {
        Text = title;
        url = address;
        MinimumSize = new Size(900, 650);
        Size = new Size(1360, 860);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(243, 248, 246);
        browser.Dock = DockStyle.Fill;
        Controls.Add(browser);
        Shown += async (_, _) =>
        {
            try
            {
                await browser.EnsureCoreWebView2Async();
                browser.CoreWebView2.Settings.AreDevToolsEnabled = false;
                browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                browser.CoreWebView2.NewWindowRequested += (_, e) =>
                {
                    e.Handled = true;
                    if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var target) && target.Scheme == "https")
                        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(target.ToString()) { UseShellExecute = true });
                };
                browser.CoreWebView2.Navigate(url);
            }
            catch (Exception ex)
            {
                MessageBox.Show("تعذر تشغيل WebView2. ثبّت Microsoft Edge WebView2 Runtime ثم أعد المحاولة.\n" + ex.Message, "إنجاز", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        };
    }
}