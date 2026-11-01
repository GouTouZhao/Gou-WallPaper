# 参考开源项目源码与功能深度分析报告 (`reference_analysis.md`)

本文档记录了对工作区中已有的三个参考开源项目的源码审查、Git 元数据核验、许可证检查及核心功能模块的技术对比分析，作为 **WallFlow** 架构设计与工程实现的依据。

---

## 一、参考项目仓库元数据与环境核查

在对工作区 `c:\Users\Lenovo\Desktop\Desktop WallPaper` 进行检查后，确认三个参考项目均已存在于本地独立目录中。我们在不修改任何上游仓库源码的前提下完成了源码级审查：

| 项目名称 | 本地目录 | Git 远程地址 (`origin`) | 当前分支 | 最新提交 Commit | 许可证类型 | 技术栈 |
|---|---|---|---|---|---|---|
| **KokoroPaper** (`wallpaper-changer-windows`) | `wallpaper-changer-windows/` | `https://github.com/klysman08/wallpaper-changer-windows.git` | `master` | `0e8c6f38fba2a649ddcc93c16765cbd0ba3a0fd8` (`Turn the site's hero into a working Wallpaper Studio`) | MIT（在 `README.md` 与 `tauri.conf.json` 中声明） | Rust (`wallpaper-core` + Tauri v2) + React 19 / TypeScript 6 |
| **MultiScreenSaver** | `MultiScreenSaver/` | `https://github.com/openscopeproject/MultiScreenSaver.git` | `main` | `b5fa23390c076eac3451257ac1b24c1341c3f5c3` (`Suppress libpng warnings Issue #6`) | GPL-3.0-or-later (`COPYING`) | C++14 + wxWidgets (Direct2D) + `easyexif` + CMake |
| **Lively Wallpaper** (`lively`) | `lively/` | `https://github.com/lively-community/lively.git` | `core-separation` | `25a0a5f4ed1988ff42117d3a17feb321062237fe` (`Update package version`) | GPL-3.0 (`LICENSE`) | C# (.NET 9 WPF Core + WinUI 3 UI + .NET Framework 4.7.2 `.scr` 包装器) |

---

## 二、逐项功能源码研究与迁移决策

### 2.1 KokoroPaper (`wallpaper-changer-windows`) 深度分析

KokoroPaper v6.0.0 将早期 Python Sidecar 架构重构为 Rust 单进程引擎（`desktop/src-tauri/crates/wallpaper-core`）配合 Tauri v2 外壳（`desktop/src-tauri/src`）。

#### （1）图片目录扫描与格式验证 (`crates/wallpaper-core/src/images.rs`)
1. **源项目的实现方式**：
   - 定义 `SUPPORTED = &["jpg", "jpeg", "png", "bmp", "webp"]` 与 21 种 `UNSUPPORTED` 扩展名（如 `gif`, `heic`, `avif`, `tif`, `raw` 等），在 `list_folder_images(folder)` 中统计被跳过的格式及数量以便向前端展示诊断提示。
   - `list_images(folder)` 使用 `std::fs::read_dir` 执行单层目录遍历。
   - `open_image(path)` 使用 `image::ImageReader::open(path)?.with_guessed_format()?.decode()`，通过文件头 Magic Bytes 嗅探真实格式而非盲目信任文件扩展名。
2. **是否适合迁移到 Python**：
   - **适合**。魔数校验（Magic-byte verification）与损坏/不支持文件统计机制非常适合迁移至 Python，并且需要在 WallFlow 中进一步扩充支持 `TIF/TIFF`、多文件夹源管理以及可选子目录递归扫描。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/image_library.py` 中支持多文件夹源配置（每个源具备 `path`、`enabled`、`recursive` 属性）。
   - 支持静态图片格式：`PNG`、`JPG/JPEG`、`WebP`、`BMP`、`TIF/TIFF`。
   - 扫描阶段结合扩展名预筛与 `PIL.Image.open()` / `QImageReader` 头部元数据快速校验（检查 `is_animated` 排除动态 GIF/动态 WebP，读取宽高与文件大小，**绝不在扫描阶段解码整张高分辨率 PNG 的像素缓冲**）。
   - 扫描过程在后台工作线程执行，通过信号向 GUI 报告真实进度，避免阻塞主线程。
4. **需要规避的缺陷**：
   - KokoroPaper 的 `list_images` 仅扫描单层目录且仅支持单一文件夹源，不支持多目录启用/禁用切换与递归扫描。
   - 若在扫描阶段调用完整 `.decode()` 会导致数千张大型 4K/8K PNG 耗尽内存或长时间卡死。WallFlow 在扫描期仅读取图片头部（Header）元数据。
5. **相应的测试方法**：
   - 编写单元测试覆盖：多目录混合扫描、递归/非递归开关、扩展名伪装文件（如文本文件改名为 `.png`）、损坏 PNG、动态 GIF 过滤、包含中文/空格/特殊字符的路径。

#### （2）不放回随机轮播与顺序选择 (`crates/wallpaper-core/src/selection.rs`, `session.rs`)
1. **源项目的实现方式**：
   - `pick_random` 在 `%APPDATA%\WallpaperChanger\state.json` 中按规范化路径 `<canonical_folder>:random_history` 记录已展示文件名列表 `shown`。每次从 `available - shown` 中抽取图片；当剩余未展示图片不足时清空 `shown` 开启新一轮。
   - `Session` 内部维护容量为 50 的 `History` 环形缓冲区以支持 `apply_previous_wallpaper` 回退。
   - 预览操作传入临时状态文件 `%TEMP%\wallpaper_changer_preview_state.json`，防止预览污染正式轮播历史。
2. **是否适合迁移到 Python**：
   - **适合**，但其新旧批次边界存在首尾重复的隐患，需要在 Python 中改进。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/random_selector.py` 中实现 `ImageSelector`，同时支持不放回随机模式（`random`）和顺序模式（`sequential`）。
   - 随机模式将有效图片列表洗牌（Fisher-Yates / `random.shuffle`），逐张出队；当前批次耗尽重洗牌时，强制检查**新批次首张图片是否与上一批次末张图片相同**，若相同且有效图片数 $\ge 2$，则将首张图片与后续位置交换，严格保证跨批次边界不重复。
   - 维护历史栈与游标，完整支持“上一张”“下一张”“随机切换”“立即应用”以及候选集变更/文件删除后的自愈恢复。
4. **需要规避的缺陷**：
   - KokoroPaper 清空 `shown` 后重新调用 `choose_multiple` 时，未约束新周期第一张图片不能等于上一周期最后一张图片。
5. **相应的测试方法**：
   - 对包含 2 张、3 张、100 张图片的集合连续抽取 $N \times 10$ 次，断言每个批次内无重复，且相邻两次抽取永远不相等；测试中途删除当前图片或重启反序列化后的状态恢复。

#### （3）多显示器壁纸合成、布局、拼贴与色彩调整 (`monitor.rs`, `collage.rs`, `compose.rs`, `adjust.rs`, `apply.rs`)
1. **源项目的实现方式**：
   - 调用 `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` 开启每显示器 DPI 感知，使用 `EnumDisplayMonitors` + `GetMonitorInfoW` 获取所有显示器的物理像素坐标，计算虚拟桌面包围盒 `virtual_desktop`。
   - `collage.rs` 支持每台显示器 1 张或 2~8 张网格拼贴（`grid_layout`），支持 `fill`、`fit`、`stretch`、`center` 四种缩放裁剪方式。
   - `adjust.rs` 支持亮度（Brightness）、对比度（Contrast）、饱和度（Saturation）、色温（Warmth/Temperature）等像素调整。
   - `apply.rs` 将合成后的虚拟桌面图像保存为 BMP，写入注册表 `WallpaperStyle = "22"`（Span 跨屏模式）并调用 `SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE)`。
2. **是否适合迁移到 Python**：
   - **适合**，但需要结合 Windows 原生 `IDesktopWallpaper` COM 接口进行增强。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/win32_integration.py` 和 `app/wallpaper_manager.py` 中：
     - 优先使用 Windows `IDesktopWallpaper` COM 接口（CLSID `{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}`，IID `{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}`）实现原生每显示器独立壁纸设置、位置模式（Fill / Fit / Stretch / Center / Tile / Span）及显示器设备路径管理。
     - 同时实现虚拟桌面合成回退引擎（当处于拼贴模式、应用了亮度/对比度/饱和度/色温滤镜、或 `IDesktopWallpaper` 在特定系统环境下不可用时，按各显示器物理坐标合成虚拟桌面图像并通过 `SystemParametersInfoW` 应用）。
   - 在 `app/image_processor.py` 中使用 Pillow 实现非破坏性的 `Fill`、`Fit`、`Stretch`、`Center`、`Tile`、`Span` 布局计算、2~8 图网格拼贴合成，以及亮度、对比度、饱和度、色温调整（仅输出到 `%LOCALAPPDATA%\WallFlow\cache\rendered\`，**绝不修改用户原始 PNG 文件**）。
4. **需要规避的缺陷**：
   - KokoroPaper 仅使用 `WallpaperStyle=22` + `SystemParametersInfoW`，完全未使用 `IDesktopWallpaper` COM 接口；且省略了 `SPIF_SENDWININICHANGE`，在纯静态壁纸场景下可能导致部分 Windows 桌面刷新不及时。WallFlow 在 `SystemParametersInfoW` 回退路径中传递 `SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE`。
5. **相应的测试方法**：
   - 测试 6 种显示布局（Fill / Fit / Stretch / Center / Tile / Span）的宽高比保持与黑边填充；测试 2~8 张拼贴与色彩调整不改动源文件 SHA256；测试多显示器不同图片与同图模式。

#### （4）定时调度、系统托盘、全局快捷键与开机自启 (`session.rs`, `tray.rs`, `hotkeys.rs`, `startup.rs`)
1. **源项目的实现方式**：
   - 调度器：仅使用 `tokio::time::sleep(Duration::from_secs(interval))` 内存计时器。
   - 托盘与单实例：使用 Win32 命名互斥体 `Global\KokoroPaper_SingleInstance`，关闭窗口时隐藏到系统托盘。
   - 全局快捷键：支持下一张、上一张、立即应用、暂停/恢复、显示/隐藏窗口。
   - 开机自启：读写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，传入 `--minimized` / `--startup` 参数。
2. **是否适合迁移到 Python**：
   - 托盘、单实例互斥体、全局快捷键（Win32 `RegisterHotKey`）、注册表开机自启非常适合迁移到 Python；但其**单纯依赖内存 sleep 的定时器不能满足 6 小时以上长间隔调度要求**。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 重新设计双模式调度器（见 `docs/scheduler_design.md`）：`< 6 小时` 采用单调时钟（`Clock.monotonic()`），`>= 6 小时` 采用持久化 UTC 时间戳（`last_success_at_utc`、`next_due_at_utc`）结合轻量级轮询与系统电源/时间事件恢复。
   - 使用 Win32 `CreateMutexW` 实现单实例保护，通过命名管道/本地 Socket 唤醒已有实例主窗口。
   - 使用 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 管理开机自启，正确为含空格和中文的路径添加引号并附带 `--minimized` 参数。
4. **需要规避的缺陷**：
   - 彻底规避 KokoroPaper 在进程重启或系统休眠后丢失长间隔倒计时的问题。
5. **相应的测试方法**：
   - 虚拟时钟（`FakeClock`）测试 5h59m 与 6h/12h/24h 边界、休眠补偿、重启恢复；注册表自启命令包含中文与空格路径解析测试。

---

### 2.2 MultiScreenSaver 深度分析

MultiScreenSaver 是一个基于 C++14 与 wxWidgets (Direct2D) 编写的多显示器原生 Windows 屏保程序。

#### （1）原生 `.scr` 命令行参数与预览窗口句柄集成 (`main.cpp`)
1. **源项目的实现方式**：
   - CMake 将输出后缀设为 `.scr`（`WIN32` 子系统可执行文件）。
   - 使用 `wxCmdLineParser` 解析 `/` 或 `-` 前缀的参数：
     - `/s` 或 `/S`：启动全屏屏保。
     - `/p <hwnd>` 或 `/P <hwnd>`：解析 Windows 屏保设置对话框传入的预览父窗口句柄 `m_previewHwnd`，通过 `wnd->SetHWND((WXHWND)m_previewHwnd)` 将渲染窗口嵌入 `desk.cpl` 的小预览框。
     - `/c` 或无参数：打开屏保配置对话框（`CONFIG_DIALOG`）。
2. **是否适合迁移到 Python**：
   - **完全适合**。Windows `.scr` 本质上就是支持特定命令行约定（`/s`、`/c`、`/c:<hwnd>`、`/p <hwnd>`、`/p:<hwnd>`）的 PE GUI 可执行文件。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 提供独立屏保入口 `screensaver_entry.py`，通过 PyInstaller 打包生成 `dist/WallFlowSaver.scr`。
   - 编写专门的参数解析器 `parse_screensaver_args(argv)`，兼容 Windows 传入的各种形式：
     - `/s`, `-s` -> `mode="screensaver"`
     - `/c`, `-c`, `/c:123456`, `-c:123456` 或无参数 -> `mode="config"`
     - `/p 123456`, `/p:123456`, `-p 123456`, `-p:123456` -> `mode="preview", preview_hwnd=123456`
   - 在 `/p` 预览模式下，校验 `IsWindow(preview_hwnd)`，通过 `GetClientRect` 获取预览框尺寸，并使用 Win32 `SetParent` + `SetWindowLongPtrW(GWL_STYLE, WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN)` 将 PySide6 预览组件嵌入 Windows 屏保预览小窗，同时监听父窗口销毁状态自动退出。
4. **需要规避的缺陷**：
   - MultiScreenSaver 的 `wxCmdLineParser` 对带冒号连写的 `/p:12345` 兼容性有限；WallFlow 的自定义解析器同时支持空格分隔和冒号连写。
5. **相应的测试方法**：
   - 自动化测试覆盖 `/s`、`/c`、`/c:98765`、`/p 12345`、`/p:12345`、`-S`、空参数等全部变体；对打包后的 `WallFlowSaver.scr` 实测启动、预览嵌入、配置弹窗与退出。

#### （2）目录扫描、EXIF 方向、屏幕方向匹配与过渡渲染 (`main.cpp`, `saverframe.cpp`)
1. **源项目的实现方式**：
   - `ImageScanner` 遍历目录（支持 `recursive` 开关），仅支持 `jpg`、`jpeg`、`png`。
   - `rotatingJPEGHandler` 读取 EXIF Orientation 标签旋转图片。
   - 根据每个显示器的宽高比（`width > height`）区分横屏与竖屏，分别从 `landscapeDir` 或 `portraitDir` 取图。
   - 每个 `RenderWindow` 维护容量为 3 的环形位图缓存 `m_bitmaps[3]`（上一张、当前张、下一张），支持 `NONE` 与 `BLEND`（Direct2D 图层透明度混合）过渡效果。
2. **是否适合迁移到 Python**：
   - 3 槽位/有界缓存思想、屏幕方向匹配、平滑过渡动画非常适合迁移到 Python（PySide6 `QPainter` + `QTimer` 动画）。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/screensaver.py` 中：
     - 支持共用桌面壁纸图片库或使用独立屏保图片库。
     - 支持横屏/竖屏方向智能匹配（可选开启：横屏显示器优先抽取横向图片，竖屏显示器优先抽取纵向图片）及 EXIF `ImageOps.exif_transpose` 自动纠正。
     - 渲染时**按目标显示器的物理像素分辨率进行按需缩放解码**，并采用有明确内存/数量上限的 LRU 缓存；过渡期间仅保留当前帧和目标帧两张缩放后的 `QPixmap`。
     - 支持多种过渡效果：**无过渡（None）**、**淡入淡出（Fade）**、**平滑平移（Slide）**、**微距缩放（Zoom）**，并允许自定义过渡持续时间（毫秒）。
4. **需要规避的缺陷**：
   - **关键缺陷 1**：MultiScreenSaver 在 `App::switchImage` 中使用同步阻塞循环 `for (double tick = 0.02; tick < 1.0; tick += .02) { wxMilliSleep(16); }` 驱动过渡动画，期间阻塞主事件循环！WallFlow 改用非阻塞的 `QTimer`（60fps 帧时间戳插值），在过渡动画期间仍能即时响应键盘/鼠标退出事件。
   - **关键缺陷 2**：MultiScreenSaver 忽略鼠标移动，仅在鼠标左键点击或按 ESC 时退出，不符合原生 Windows 屏保规范。WallFlow 同时支持**鼠标移动（带防抖动阈值，如 `>= 12px`）、鼠标点击、滚轮及任意键盘按键（含 ESC）立即退出**，并彻底清理所有显示器的全屏窗口与进程。
   - **关键缺陷 3**：MultiScreenSaver 启动时直接解码原图尺寸位图，遇到 8K 大型 PNG 时内存暴涨。WallFlow 严格按显示器分辨率降采样后再生成显示位图。
5. **相应的测试方法**：
   - 测试屏保在单屏/多屏下的窗口覆盖、按显示器分辨率缩放内存上限、损坏图片自动跳过不崩溃、以及鼠标移动/按键/ESC 触发立即退出。

---

### 2.3 Lively Wallpaper (`lively`) 深度分析

Lively Wallpaper 是知名的开源桌面壁纸与屏保系统（C# / .NET 9）。根据项目定位，我们不引入其动态视频/网页/Unity 播放器，重点借鉴其 **Windows 屏保系统集成、多显示器 `IDesktopWallpaper` 拓扑管理、系统电源/显示/时间事件监听及生命周期恢复机制**。

#### （1）壁纸与屏保集成及调用系统屏保设置 (`Lively.Utility.Screensaver`, `ScreensaverService.cs`, `ScreensaverUtil.cs`)
1. **源项目的实现方式**：
   - 允许屏保复用当前壁纸配置或使用独立屏保配置。
   - 在 `.scr` 启动时检查系统是否已处于锁屏状态（检测前台窗口是否为 `LockApp`）。
   - 在 `/p <hwnd>` 预览模式中校验父窗口类名（如 `SSDemoParent`）并使用 `SetParent` + `WS_CHILD` 挂载预览窗口。
   - 通过 `rundll32.exe shell32.dll,Control_RunDLL desk.cpl,,1` 或 `control.exe desk.cpl,screensaver,@screensaver` 一键打开 Windows 原生“屏幕保护程序设置”对话框，并读取 `HKCU\Control Panel\Desktop\SCRNSAVE.EXE` 检测当前系统选中的屏保状态。
2. **是否适合迁移到 Python**：
   - **非常适合**。这些 Win32 系统调用在 Python 中通过 `subprocess`、`winreg` 和 `ctypes.windll.user32` 即可轻量、稳定地实现。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/win32_integration.py` 中提供：
     - `open_windows_screensaver_settings()`：调用 `control.exe desk.cpl,screensaver,@screensaver`（回退 `rundll32.exe shell32.dll,Control_RunDLL desk.cpl,,1`）打开 Windows 原生屏保控制面板。
     - `get_system_screensaver_info()`：读取 `HKCU\Control Panel\Desktop` 下的 `SCRNSAVE.EXE`、`ScreenSaveActive`、`ScreenSaveTimeOut`，在 GUI 中直观展示系统屏保状态。
     - 提供将 `WallFlowSaver.scr` 安装/注册到用户屏保配置或打开目录的便捷入口。
4. **需要规避的缺陷**：
   - Lively 的 `.scr` 包装器在主程序未运行时对 `/p` 预览直接退出不渲染；WallFlow 的 `WallFlowSaver.scr` 具备独立渲染能力，无论 `WallFlow.exe` 后台是否正在运行，`/s`、`/c`、`/p` 均能独立正常工作。
5. **相应的测试方法**：
   - 测试系统屏保注册表状态读取、命令行参数转发及独立进程运行能力。

#### （2）多显示器 `IDesktopWallpaper` COM 接口与显示器拓扑 (`DisplayManager.cs`, `IDesktopWallpaper.cs`)
1. **源项目的实现方式**：
   - 定义 `IDesktopWallpaper` COM 接口（GUID `{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}`），调用 `GetMonitorDevicePathCount`、`GetMonitorDevicePathAt`、`GetMonitorRECT`、`SetWallpaper(monitorID, path)`、`SetPosition`。
   - 结合 `EnumDisplayMonitors` 与 `GetMonitorInfoW` 获取显示器物理坐标、主副屏标识、横竖屏方向与设备名称。
2. **是否适合迁移到 Python**：
   - **适合**。Python 已安装 `comtypes`，可直接定义 `IDesktopWallpaper` COM 接口并调用，同时结合 `ctypes` 调用 `EnumDisplayMonitors` 作为完整拓扑感知与回退。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/win32_integration.py` 中完整实现基于 `comtypes` 的 `IDesktopWallpaper` 封装，支持按 `monitor_id` 单独设置壁纸，并在 COM 接口报错或不可用时自动无缝回退到虚拟桌面多屏合成 + `SystemParametersInfoW`。
4. **需要规避的缺陷**：
   - Windows 中部分断开连接的显示器仍会保留在 `IDesktopWallpaper.GetMonitorDevicePathCount` 列表中（但 `GetMonitorRECT` 会返回空矩形或报错 `S_FALSE`/`E_FAIL`）。WallFlow 在枚举时过滤掉无效/未激活的断开显示器，并与 `EnumDisplayMonitors` 活动显示器列表交叉验证。
5. **相应的测试方法**：
   - 单元测试与模拟多显示器拓扑测试（验证单显示器、双横屏、一横一竖、显示器热插拔断开/重连后的壁纸分配与坐标计算）。

#### （3）系统消息监听与异常恢复 (`WndProcMsgWindow.xaml.cs`, `WinDesktopCore.cs`)
1. **源项目的实现方式**：
   - 创建隐藏消息窗口挂载 `WndProc` 钩子，监听 `WM_DISPLAYCHANGE` (`0x007E`)、`WM_SETTINGCHANGE` (`0x001A`)、`WM_QUERYENDSESSION`/`WM_ENDSESSION`、会话锁屏/解锁事件等。
2. **是否适合迁移到 Python**：
   - **适合**。在 PySide6 中可通过重写 `QWidget.nativeEvent(eventType, message)` 或隐藏 Win32 消息窗口直接捕获底层 `MSG` 结构体。
3. **在新项目 WallFlow 中采用的实现方式**：
   - 在 `app/win32_integration.py` 中实现 `SystemEventFilter`（继承 `QAbstractNativeEventFilter` 并注册到 `QApplication`），实时拦截：
     - `WM_TIMECHANGE` (`0x001E`)：系统时间或时区变更 -> 立即触发调度器重新核对 UTC 时间戳。
     - `WM_POWERBROADCAST` (`0x0218`，含 `PBT_APMRESUMEAUTOMATIC` `0x0012`、`PBT_APMRESUMESUSPEND` `0x0007`)：系统从睡眠或休眠恢复 -> 立即触发调度器恢复检查与补偿切换。
     - `WM_DISPLAYCHANGE` (`0x007E`)：显示器插拔、旋转或分辨率变化 -> 刷新显示器列表并重新检查壁纸布局。
     - `WM_WTSSESSION_CHANGE` (`0x02B1`，`WTS_SESSION_UNLOCK` `0x8`)：会话解锁恢复 -> 检查调度状态。
     - `WM_SETTINGCHANGE` (`0x001A`)：系统设置变更通知。
4. **需要规避的缺陷**：
   - 不引入任何需要管理员权限的 Windows Service 常驻服务，全部在普通用户态通过标准 Win32 消息实现。
5. **相应的测试方法**：
   - 编写事件分发单元测试，模拟注入 `WM_TIMECHANGE`、`WM_POWERBROADCAST (PBT_APMRESUMEAUTOMATIC)`、`WM_DISPLAYCHANGE` 消息，验证调度器与显示器管理器立即执行恢复逻辑。

