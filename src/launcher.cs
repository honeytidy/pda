// pda.exe 原生启动器（C# / .NET Framework 4.x csc 编译，无需 SDK）
//
// - 无参数：毫秒级弹出 splash（420x240 居中圆角卡片，眼睛 logo），拉起同目录
//   main.exe，轮询到其主窗口出现后关闭。主窗口判定：属于子进程、可见、非最小化、
//   标题以"个人助理知识库"开头、物理宽度 >= 600（大于 splash 自身，排除 toast 等小窗）。
//   子进程弹出任何带标题的可见窗口（启动失败 / "正在启动或无响应"等提示框）也立即关闭，
//   splash 不置顶，避免挡住这些提示。
//   子进程退出（已有实例被激活）或主窗口留在托盘不出现时，最多 60 秒兜底关闭。
// - 带参数（--add 等）：不显示 splash，参数原样转发给 main.exe 并立即退出。
//
// 计时日志（默认关闭）：设环境变量 PDA_MENU_LOG=1 时写 %TEMP%\pda_launcher.log
// （t0/shown/main 毫秒），超过 1MB 自动截断。
//
// main.exe 定位顺序：自身目录\main.exe → 自身目录\pda\main.exe → 环境变量 PDA_APP_EXE。
// 注意：本文件用 .NET Framework 自带 csc（C# 5）编译，不能用字符串插值等新语法。
using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

static class NativeMethods {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hwnd, System.Text.StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}

class SplashForm : Form {
    static readonly Color Accent = Color.FromArgb(0x3B, 0x6E, 0xF6);
    static readonly Color TextDark = Color.FromArgb(0x1F, 0x23, 0x29);
    static readonly Color TextSubtle = Color.FromArgb(0x8F, 0x95, 0x9E);
    readonly float scale;

    public SplashForm(float scale) {
        this.scale = scale;
        int w = S(420), h = S(240);
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        Size = new Size(w, h);
        // 不置顶：main.exe 启动失败时的错误提示框不能被 splash 挡住
        ShowInTaskbar = false;
        DoubleBuffered = true;
        Text = "个人助理知识库";
        using (var path = RoundedRect(new Rectangle(0, 0, w, h), S(12)))
            Region = new Region(path);
    }

    protected override void OnLoad(EventArgs e) {
        base.OnLoad(e);
        // 居中放到 OnLoad 用最终实际尺寸计算（防 DPI 缩放在构造后改动窗口大小）；
        // 用 WorkingArea（任务栏在侧/顶部时也不会偏）
        var wa = Screen.PrimaryScreen.WorkingArea;
        Location = new Point(
            wa.Left + (wa.Width - Width) / 2,
            wa.Top + (wa.Height - Height) / 2);
    }

    int S(int v) { return (int)Math.Round(v * scale); }

    static GraphicsPath RoundedRect(Rectangle r, int radius) {
        var path = new GraphicsPath();
        int d = radius * 2;
        path.AddArc(r.Left, r.Top, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Top, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.Left, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Color.White);
        using (var pen = new Pen(Color.FromArgb(0xE5, 0xE7, 0xEB)))
        using (var path = RoundedRect(new Rectangle(0, 0, Width - 1, Height - 1), S(12)))
            g.DrawPath(pen, path);

        // 眼睛 logo：accent 圆角方块 + 白色 Segoe UI Symbol 粗体 θ 旋转 90°（笔画均匀最方正）
        int size = S(56), x = (Width - size) / 2, y = S(28);
        using (var brush = new SolidBrush(Accent))
        using (var path = RoundedRect(new Rectangle(x, y, size, size), size / 4))
            g.FillPath(brush, path);
        using (var font = new Font("Segoe UI Symbol", size * 0.72f, FontStyle.Bold, GraphicsUnit.Pixel)) {
            var state = g.Save();
            g.TranslateTransform(x + size / 2f, y + size / 2f);
            g.RotateTransform(90);
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
            var rect = new RectangleF(-size / 2f, -size / 2f, size, size);
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                g.DrawString("θ", font, Brushes.White, rect, sf);
            g.Restore(state);
        }

        DrawCentered(g, "个人助理知识库", S(20), FontStyle.Bold, TextDark, S(100));
        DrawCentered(g, "你的本地 AI 知识库", S(13), FontStyle.Regular, TextSubtle, S(130));
        DrawCentered(g, "正在启动…", S(12), FontStyle.Regular, TextSubtle, S(204));
    }

    void DrawCentered(Graphics g, string text, int px, FontStyle style, Color color, int top) {
        using (var font = new Font("Microsoft YaHei UI", px, style, GraphicsUnit.Pixel))
        using (var brush = new SolidBrush(color)) {
            var size = g.MeasureString(text, font);
            g.DrawString(text, font, brush, (Width - size.Width) / 2, top);
        }
    }
}

static class Program {
    static readonly string LogPath =
        Path.Combine(Path.GetTempPath(), "pda_launcher.log");
    static readonly Stopwatch Clock = Stopwatch.StartNew();

    const long MaxLogBytes = 1024 * 1024;
    static readonly bool LogEnabled =
        Environment.GetEnvironmentVariable("PDA_MENU_LOG") == "1";

    // 日志默认关闭（PDA_MENU_LOG=1 开启），超过 1MB 截断重写
    static void Log(string stage) {
        if (!LogEnabled) return;
        try {
            var fi = new FileInfo(LogPath);
            if (fi.Exists && fi.Length > MaxLogBytes) File.WriteAllText(LogPath, "");
            File.AppendAllText(LogPath, stage + "=" + Clock.ElapsedMilliseconds + "\n");
        }
        catch { }
    }

    /// <summary>按 CommandLineToArgvW / MSVCRT 规则给单个参数加引号：
    /// 引号前的反斜杠翻倍再转义引号；结尾反斜杠在闭合引号前翻倍
    /// （C:\dir\ → "C:\dir\\"，否则 \" 会被当成字面引号吞掉后续参数）。</summary>
    internal static string QuoteArg(string arg) {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
            return arg;
        var sb = new System.Text.StringBuilder("\"");
        int backslashes = 0;
        foreach (char c in arg) {
            if (c == '\\') { backslashes++; continue; }
            if (c == '"') sb.Append('\\', backslashes * 2 + 1);
            else sb.Append('\\', backslashes);
            backslashes = 0;
            sb.Append(c);
        }
        sb.Append('\\', backslashes * 2);
        sb.Append('"');
        return sb.ToString();
    }

    internal static string JoinArgs(string[] args) {
        var parts = new string[args.Length];
        for (int i = 0; i < args.Length; i++) parts[i] = QuoteArg(args[i]);
        return string.Join(" ", parts);
    }

    /// <summary>定位应用本体 main.exe，找不到返回 null。顺序：
    /// 自身目录\main.exe（发布布局）→ 自身目录\pda\main.exe → 环境变量 PDA_APP_EXE。</summary>
    static string ResolveAppExe(string selfDir) {
        var candidates = new System.Collections.Generic.List<string>();
        candidates.Add(Path.Combine(selfDir, "main.exe"));
        candidates.Add(Path.Combine(Path.Combine(selfDir, "pda"), "main.exe"));
        string env = Environment.GetEnvironmentVariable("PDA_APP_EXE");
        if (!string.IsNullOrEmpty(env)) candidates.Add(env);
        foreach (string c in candidates) {
            try { if (File.Exists(c)) return c; } catch { }
        }
        return null;
    }

    [STAThread]
    static int Main(string[] args) {
        Log("t0");
        NativeMethods.SetProcessDPIAware();

        string selfDir = Path.GetDirectoryName(
            System.Reflection.Assembly.GetExecutingAssembly().Location);
        string appExe = ResolveAppExe(selfDir);
        if (appExe == null) {
            MessageBox.Show("找不到 main.exe。\n\n请确认 pda.exe 与 main.exe 在同一目录。",
                            "个人助理知识库",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        var psi = new ProcessStartInfo {
            FileName = appExe,
            Arguments = JoinArgs(args),
            WorkingDirectory = Path.GetDirectoryName(appExe),
            UseShellExecute = false,
        };
        // 让 python 侧跳过自己的 splash（外部已有）
        psi.EnvironmentVariables["PDA_NO_INTERNAL_SPLASH"] = "1";
        Process child;
        try { child = Process.Start(psi); }
        catch (Exception e) {
            MessageBox.Show("启动失败：" + e.Message, "个人助理知识库",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (args.Length > 0) {
            // --add 等参数路径：不显示 splash，秒退
            Log("forwarded");
            return 0;
        }

        using (var g = Graphics.FromHwnd(IntPtr.Zero)) {
            float scale = g.DpiY / 96f;
            Application.EnableVisualStyles();
            var form = new SplashForm(scale);
            form.Show();
            Log("shown");

            uint childPid = (uint)child.Id;
            var timer = new Timer { Interval = 200 };
            int elapsedMs = 0;
            timer.Tick += (s, e) => {
                elapsedMs += 200;
                int found = FindChildWindow(childPid);
                if (found == 1) {
                    Log("main");
                    form.Close();
                } else if (found == 2) {
                    Log("dialog");  // 错误/提示框：关掉 splash 让用户看到
                    form.Close();
                } else if (child.HasExited && elapsedMs >= 1500) {
                    // 子进程秒退（已有实例在跑，转发 activate 后退出）或启动失败
                    Log("child_exited");
                    form.Close();
                } else if (elapsedMs >= 60000) {
                    form.Close();  // 兜底超时
                }
            };
            timer.Start();
            Application.Run(form);
        }
        return 0;
    }

    // 与 pda/ui/main_window.py 的 setWindowTitle 保持一致（前缀匹配，容许后缀变化）
    const string MainTitle = "个人助理知识库";

    /// <summary>0 = 无；1 = 主窗口；2 = 其他带标题的可见窗口（提示框等）。
    /// 无标题的小窗（toast）不算。</summary>
    static int FindChildWindow(uint pid) {
        int found = 0;
        NativeMethods.EnumWindows((hwnd, lp) => {
            uint wpid;
            NativeMethods.GetWindowThreadProcessId(hwnd, out wpid);
            if (wpid != pid || !NativeMethods.IsWindowVisible(hwnd) || NativeMethods.IsIconic(hwnd)) return true;
            var sb = new System.Text.StringBuilder(256);
            NativeMethods.GetWindowTextW(hwnd, sb, 256);
            NativeMethods.RECT rc;
            NativeMethods.GetWindowRect(hwnd, out rc);
            string title = sb.ToString();
            if (title.StartsWith(MainTitle, StringComparison.Ordinal) && rc.Right - rc.Left >= 600) {
                found = 1;
                return false;
            }
            if (title.Length > 0) found = 2;  // 继续枚举：主窗口优先
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
