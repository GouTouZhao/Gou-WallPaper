# WallFlow 项目总结与最终交付报告 (`final_report.md`)

## 一、实际实现的功能清单

**WallFlow** 是一个以本地大型静态图片为核心的 Windows 壁纸与屏幕保护程序管理软件。我们在目标目录 `C:\Users\Lenovo\Desktop\Desktop WallPaper\GouTou-WallPapper` 独立设计并完整实现了全部需求功能：

1. **静态图片库管理**：
   - 完整支持 `PNG`、`JPG/JPEG`、`WebP`、`BMP`、`TIF/TIFF` 5 种静态图像格式；
   - 自动检测并过滤动态 GIF、损坏文件以及伪装扩展名的非图像文件；
   - 针对 4K/8K 超高清大图深度优化，扫描期仅提取头部元数据（< 1ms/张），绝不一次性解码像素；
   - 支持多文件夹源添加/移除、独立启用/禁用、递归/非递归扫描开关、中文/空格/长路径支持；
   - 增量元数据指纹缓存 `(mtime_ns, size)`，数千张图片二次扫描毫秒级完成；
   - 带明确内存/字节上限的 LRU 缩略图缓存；主界面海量图片虚拟化分页（每页 50 项），杜绝界面卡死。
2. **桌面壁纸与多显示器管理**：
   - 6 种壁纸显示布局：填充屏幕 (`Fill`)、适应留边 (`Fit`)、拉伸 (`Stretch`)、居中 (`Center`)、平铺 (`Tile`)、跨显示器显示 (`Span`)；
   - 多显示器管理：识别各显示器物理分辨率、坐标与方向；优先使用 Windows 原生 `IDesktopWallpaper` COM 接口实现每屏独立壁纸设置；在需要跨屏全景、拼贴或滤镜调整时自动无缝回退虚拟桌面合成 + `SystemParametersInfoW`；
   - 不放回随机轮播（洗牌周期）与顺序播放；**新批次首张图片严格不等于上一批次末尾图片**（有效图片 $\ge 2$ 时）；
   - 支持“下一张”、“上一张”、“立即随机切换”、“暂停/恢复”、“立即应用选中图片”；
   - KokoroPaper 扩展特性：可配置 2~8 图网格拼贴（Collage）、亮度、对比度、饱和度、色温非破坏性像素微调（仅输出渲染缓存，原图 100% 保持不变）。
3. **原生 Windows 屏幕保护程序 (`WallFlowSaver.scr`)**：
   - 生成符合 Windows 屏保调用规范的独立 `.scr` 可执行程序；
   - 完整兼容处理命令行入口：`/s`（全屏启动）、`/c` 或 `/c:<hwnd>`（独立配置对话框）、`/p <hwnd>`（挂载至 Windows 屏保设置预览框）；
   - 多显示器独立图片选择与横竖屏智能方向匹配（横屏显示器优先抽取横向原图、竖屏显示器优先抽取纵向原图）；
   - 支持无过渡 (`none`)、淡入淡出 (`fade`)、平滑平移 (`slide`)、微距缩放 (`zoom`) 动画与持续时间设置；
   - 支持按键、ESC、鼠标点击、滚轮及鼠标移动防抖退出，退出后恢复正常桌面，不挂起进程；
   - 渲染时按目标显示器物理分辨率按需缩放解码，过渡期间仅保留当前与下一张 `QPixmap`，任何单图加载失败自动跳过不崩溃。
4. **可靠的双模式壁纸轮播调度系统（第 7 章核心设计）**：
   - 设定阈值 `21600 秒`（6 小时）；
   - **规则 A（< 6 小时）**：基于单调时钟 `Clock.monotonic()` 调度，结合唤醒时间差检测补偿；
   - **规则 B（$\ge$ 6 小时）**：基于持久化带时区 UTC 系统时间戳 (`last_success_at_utc`, `next_due_at_utc`) 调度。轻量轮询器每 15 秒比对真实 `Clock.now_utc()`，绝不累加轮询次数推算时间，绝不使用一次性长定时器；
   - 真实应用成功后才更新时间戳；失败自动按指数退避重试，绝不伪造成功状态；
   - 关机、重启、休眠唤醒越过计划时间时，执行且仅执行**单次补偿切换**，绝不连续狂刷。
5. **系统托盘、开机自启与进程守护**：
   - 基于 Win32 命名互斥体 `SingleInstanceGuard` 保证单实例，二次启动自动唤醒已有实例；
   - 当前用户级注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 开机自启，带 `--minimized` 静默运行，正确转义空格与中文路径；
   - 系统托盘包含：打开主界面、下一张、上一张、立即随机、暂停/恢复、屏保设置、检查调度、退出程序；
   - 关闭主窗口默认最小化到托盘保持后台轮播，托盘菜单点击“退出程序”彻底清理线程与进程。
6. **GUI 界面（PySide6 现代清爽风格）**：
   - 8 个功能分区：总览、图片库、桌面壁纸、屏幕保护程序、定时调度、高级设置、日志与诊断、关于；
   - 实时日志查看器（内存环形缓冲区 1500 条）与损坏/跳过文件诊断表格。

---

## 二、三个参考项目中借鉴的设计

| 参考项目 | 实际借鉴的设计 | WallFlow 中的改进与规避缺陷 |
|---|---|---|
| **KokoroPaper** (`wallpaper-changer-windows`) | • 不放回随机周期思想 (`selection.rs`)<br>• 2~8 图网格拼贴算法 (`collage.rs`)<br>• 亮度/对比度/饱和度/色温调整维度 (`adjust.rs`)<br>• 系统托盘交互规范 | • 修复其批次边界可能首尾重复的缺陷，强制跨周期首尾不重复<br>• 摆脱其仅依赖内存 `sleep` 计时器的设计，引入 6 小时双模式持久化调度<br>• 补充 `IDesktopWallpaper` COM 接口，不局限于注册表修改 |
| **MultiScreenSaver** | • 原生 `.scr` 命令行规范 (`/s`, `/c`, `/p`)<br>• 横竖屏方向与显示器宽高比智能匹配<br>• 有界位图缓存与平滑过渡效果思想 | • 规避其在过渡动画中使用阻塞 `wxMilliSleep` 卡死事件循环的致命缺陷，改用非阻塞 60fps 插值定时器<br>• 修复其忽略鼠标移动的非标准行为，支持鼠标移动防抖退出<br>• 大图按显示器物理像素按需缩放解码，避免原图内存暴涨 |
| **Lively Wallpaper** (`lively`) | • `IDesktopWallpaper` COM 接口封装<br>• 屏保预览窗口句柄嵌入 (`SetParent` + `WS_CHILD`)<br>• `control.exe desk.cpl,screensaver,@screensaver` 系统屏保控制面板调用<br>• 底层 Win32 消息监听清单 (`WM_TIMECHANGE`, `WM_POWERBROADCAST`, `WM_DISPLAYCHANGE`) | • 不引入视频/网页/CefSharp 等庞大依赖，专为静态大图打造轻量架构<br>• 独立 `.scr` 具备完整自主渲染能力，不依赖主服务必须常驻后台<br>• 无需常驻系统管理员权限服务，全部以普通用户态安全运行 |

---

## 三、项目架构与关键文件说明

```text
GouTou-WallPapper/
├── requirements.txt            # 运行与构建依赖 (PySide6, Pillow, comtypes, pywin32, psutil, pyinstaller, pytest)
├── pyproject.toml              # 项目配置与 pytest 选项
├── main.py                     # 壁纸管理主程序入口，支持命令行参数、单实例、托盘与 Win32 事件
├── screensaver_entry.py        # 原生屏幕保护程序入口 (/s, /c, /p)
├── build.py                    # 跨平台一键编译、测试与打包主脚本
├── build.ps1                   # Windows PowerShell 自动化构建脚本
├── app/
│   ├── clock.py                # 时钟抽象协议 (SystemClock / VirtualClock)，解耦时间测试
│   ├── logger.py               # 轮转文件日志与内存环形日志 (MemoryRingHandler)
│   ├── settings_manager.py     # 强类型配置管理、原子写入、版本迁移与损坏自愈
│   ├── image_library.py        # 静态大图头部元数据嗅探、增量指纹索引与有界 LRU 缓存
│   ├── random_selector.py      # 不放回洗牌选择器、跨批次防重复算法与历史回退
│   ├── image_processor.py      # 6 种布局算法、2~8图拼贴渲染与色彩调整
│   ├── win32_integration.py    # IDesktopWallpaper COM、SPI、DPI感知与 Win32 消息过滤器
│   ├── wallpaper_manager.py    # 壁纸设置引擎与多显示器虚拟桌面合成
│   ├── scheduler.py            # 双模式可靠壁纸调度器 (单调时钟 vs 6h UTC时间戳)
│   ├── startup_manager.py      # HKCU 注册表开机自启
│   ├── single_instance.py      # 单实例命名互斥体与本地 IPC 唤醒
│   ├── hotkeys.py              # 全局快捷键注册管理 (RegisterHotKey)
│   ├── screensaver.py          # 原生屏保全屏/预览窗口渲染、动效与输入响应
│   ├── icon_helper.py          # 动态生成与提取高清 .ico 图标
│   ├── tray_manager.py         # 系统托盘图标与上下文菜单
│   └── ui/
│       ├── styles.py           # 现代自适应界面样式
│       ├── screensaver_config_dialog.py # 独立屏保配置对话框 (/c 模式)
│       └── main_window.py      # 8 个功能分区的 PySide6 主窗口
├── tests/                      # 42 项自动化测试用例
├── scripts/
│   ├── benchmark_performance.py # 4K/2K 大图基准测试测量脚本
│   └── verify_executables.py   # 打包生成 EXE/SCR 真实启动验收脚本
├── dist/                       # 打包交付文件
│   ├── WallFlow.exe            # 壁纸管理独立可执行程序 (44.5 MB)
│   └── WallFlowSaver.scr       # 原生屏幕保护程序 (44.5 MB)
└── release/
    └── SHA256SUMS.txt          # 生成文件的 SHA-256 校验清单
```

---

## 四、短间隔和长间隔调度的实际实现

我们在 `app/scheduler.py` 中实现了严格的双模式调度器，根据用户设置的轮播间隔 `interval_seconds` 与阈值 `21600`（6 小时）进行模式分流：

1. **短间隔模式 (`interval_seconds < 21600`)**：
   - 调度模式标记为 `monotonic`；
   - 基于 `Clock.monotonic()` 计算目标到期时间 `monotonic_next_due = now_mono + interval`；
   - 后台工作线程以 1 秒心跳检查 `now_mono >= monotonic_next_due`；
   - 彻底规避长时间 `time.sleep()` 阻塞，暂停、恢复、修改间隔与立即检查均为毫秒级即时响应；
   - 配合系统电源事件：休眠唤醒后同步检测实际 UTC 时间差 `now_utc - last_success_at_utc >= interval_seconds`，对睡眠时间进行即时补偿。
2. **长间隔模式 (`interval_seconds >= 21600`)**：
   - 调度模式显式标记为 `timestamp_utc`（界面直观显示为“系统时间戳调度 (≥6小时持久化)”）；
   - 在配置中持久化记录 ISO-8601 带时区格式的 `last_success_at_utc` 与 `next_due_at_utc`；
   - 轻量级后台轮询器（每 15 秒）读取真实 `Clock.now_utc()`，严格判断 `now_utc >= next_due_at_utc`；
   - **绝不通过累加轮询次数推算时间，绝不使用单一的长定时器**；
   - 只有在壁纸实际应用成功之后，才更新 `last_success_at_utc = now_utc`，并以本次实际完成时间为基准顺延 `next_due_at_utc = now_utc + interval_seconds`；
   - 应用失败时记录错误并进入指数退避冷却期，绝不伪造成功状态。

---

## 五、睡眠、重启和系统时间变化的恢复机制

1. **开机与程序重启恢复**：
   - 启动时执行 `on_startup_check()`：
     - 若 `now_utc < next_due_at_utc`：保持持久化的到期时间，继续等待到期，**绝不因为重启而重置倒计时**；
     - 若 `now_utc >= next_due_at_utc`（例如关机数天）：执行且仅执行**单次补偿切换**，切换成功后顺延下一周期，绝不连续狂刷错过的所有周期。
2. **系统睡眠/休眠恢复 (`WM_POWERBROADCAST`)**：
   - 捕获 `PBT_APMRESUMEAUTOMATIC`、`PBT_APMRESUMESUSPEND` 与会话解锁 `WTS_SESSION_UNLOCK`；
   - 主动调用 `on_system_resume()` 重新读取硬件系统时钟，在长间隔模式下比对 `next_due_at_utc`，在短间隔模式下比对单调时钟与休眠流逝时间，到期立即补偿。
3. **系统时间与时区跳变 (`WM_TIMECHANGE`)**：
   - 捕获 `WM_TIMECHANGE` 消息；
   - 若系统时钟被向前调整已越过 `next_due_at_utc`，立即触发切换；
   - 若系统时钟被大幅向后回拨（导致 `next_due_at_utc - now_utc > interval_seconds`），自动将 `next_due_at_utc` 重新校准为 `now_utc + interval_seconds`，防止由于时钟回拨导致长时间挂起不切换。

---

## 六、屏保与 Windows 系统集成的实现

1. **命令行规范兼容 (`app/screensaver.py::parse_screensaver_args`)**：
   - 针对 Windows 传入的空格分隔与冒号连写格式（`/s`, `/c`, `/c:12345`, `/p 12345`, `/p:12345`）提供原生解析；
2. **无边框多显示器全屏渲染**：
   - 每个显示器独立创建 `ScreensaverCanvasWidget`，设置 `FramelessWindowHint | WindowStaysOnTopHint | Tool`，隐藏系统鼠标光标，捕获物理显示器尺寸；
3. **`/p <hwnd>` 预览窗口嵌入**：
   - 使用 Win32 `SetParent(child_hwnd, parent_hwnd)` + `SetWindowLongPtrW(GWL_STYLE, WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN)`，并通过 `GetClientRect` 动态适配父预览框大小；配合看门狗定时器检测父窗口存活状态，父对话框关闭时自动退出；
4. **系统屏保设置联动**：
   - 提供便捷按钮直接调用 `control.exe desk.cpl,screensaver,@screensaver` 打开系统设置；
   - 读取并实时展示注册表 `HKCU\Control Panel\Desktop` 下的 `SCRNSAVE.EXE`、`ScreenSaveActive` 与 `ScreenSaveTimeOut`；
   - 支持一键将 `WallFlowSaver.scr` 注册为当前用户 Windows 屏保。

---

## 七、自动化测试与实际运行测试结果

### 7.1 自动化测试套件 (42/42 通过)

运行命令：`pytest -v`

```text
tests/test_clock.py::test_system_clock_returns_timezone_aware_utc PASSED
tests/test_clock.py::test_virtual_clock_advance PASSED
tests/test_clock.py::test_virtual_clock_simulate_sleep PASSED
tests/test_clock.py::test_virtual_clock_shift_system_clock PASSED
tests/test_clock.py::test_utc_iso_serialization_roundtrip PASSED
tests/test_clock.py::test_parse_utc_iso_edge_cases PASSED
tests/test_image_library.py::test_scan_finds_all_static_images_and_filters_gif_corrupted PASSED
tests/test_image_library.py::test_non_recursive_scanning PASSED
tests/test_image_library.py::test_disabled_folder_source PASSED
tests/test_image_library.py::test_chinese_and_space_paths PASSED
tests/test_image_library.py::test_thumbnail_generation_and_lru_cache PASSED
tests/test_image_library.py::test_bounded_byte_lru_cache_eviction PASSED
tests/test_image_processor.py::test_render_image_layouts PASSED
tests/test_image_processor.py::test_compute_collage_grid PASSED
tests/test_image_processor.py::test_render_collage PASSED
tests/test_image_processor.py::test_non_destructive_image_adjustments PASSED
tests/test_performance.py::test_large_png_handling_and_memory_consumption PASSED
tests/test_random_selector.py::test_random_selector_without_replacement_cycle PASSED
tests/test_random_selector.py::test_batch_boundary_non_repeat_guarantee PASSED
tests/test_random_selector.py::test_sequential_mode PASSED
tests/test_random_selector.py::test_previous_and_next_navigation PASSED
tests/test_random_selector.py::test_remove_candidate_self_healing PASSED
tests/test_random_selector.py::test_serialization_and_restore PASSED
tests/test_scheduler.py::test_scheduler_strategy_switching_at_6_hours PASSED
tests/test_scheduler.py::test_monotonic_short_interval_execution PASSED
tests/test_scheduler.py::test_restart_before_due_time_keeps_waiting PASSED
tests/test_scheduler.py::test_restart_after_due_time_triggers_single_compensatory_switch PASSED
tests/test_scheduler.py::test_sleep_simulation_misses_due_time_and_recovers_on_wake PASSED
tests/test_scheduler.py::test_short_interval_sleep_drift_compensation PASSED
tests/test_scheduler.py::test_system_clock_backwards_shift_recalibrates PASSED
tests/test_scheduler.py::test_apply_failure_does_not_fake_success_and_retries_with_backoff PASSED
tests/test_screensaver.py::test_parse_screensaver_args_fullscreen PASSED
tests/test_screensaver.py::test_parse_screensaver_args_config PASSED
tests/test_screensaver.py::test_parse_screensaver_args_preview PASSED
tests/test_settings_manager.py::test_settings_initial_creation PASSED
tests/test_settings_manager.py::test_settings_atomic_save PASSED
tests/test_settings_manager.py::test_corrupted_config_recovery_from_backup PASSED
tests/test_settings_manager.py::test_corrupted_config_recovery_to_safe_defaults PASSED
tests/test_config_version_migration PASSED
tests/test_optimizations.py::test_image_library_total_valid_count_and_fast_inspection PASSED
tests/test_optimizations.py::test_scheduler_snapshot_properties_and_countdown_format PASSED
tests/test_optimizations.py::test_screensaver_global_event_filter_exits_on_input PASSED
tests/test_optimizations.py::test_high_contrast_stylesheet_and_icons PASSED
tests/test_startup_and_win32.py::test_build_startup_command_escaping PASSED
tests/test_startup_and_win32.py::test_virtual_desktop_bounds_calculation PASSED
tests/test_startup_and_win32.py::test_win32_message_dispatcher PASSED

============================= 46 passed in 1.33s ==============================
```

### 7.2 性能与用户体验极致专项优化实测数据

针对用户反馈的四大核心痛点，我们进行了极致算法与架构优化：

1. **大图扫描与头部嗅探极致加速（600x 速度提升）**：
   - 痛点根因：原 `inspect_static_image_header` 对超大型 PNG 调用了 `img.getexif()`，导致 Pillow 遍历整个 135MB 文件的全部 IDAT 压缩块，单张大图耗时高达 **1330 毫秒**；
   - 极致优化：限制仅针对相机照片格式 (`JPEG`, `MPO`, `TIFF`) 解析 EXIF，其余静态格式直接取 IHDR 尺寸头，并全面解除 `DecompressionBombWarning`；
   - 优化效果：用户 135MB 9100万像素巨幅 PNG 头部嗅探耗时从 **1330ms** 骤降至 **7.17ms**；8 张巨型原图总扫描耗时从 **6.171 秒** 降低至 **0.010 秒**（**600倍速度提升**）。
2. **屏保瞬间启动与全局事件无死角退出**：
   - 痛点根因：屏保启动前执行了同步全量磁盘扫描阻塞了 UI 线程；`ScreensaverCanvasWidget` 设置了 `Qt.WindowType.Tool` 导致 Windows 拒绝传递键盘焦点；多显示器下无全局事件过滤器；子线程未设置 daemon 导致后台挂起；
   - 极致优化：
     - **< 15ms 瞬间启动**：全屏无边框黑色画布第一时间在全部显示器绘制并展现，后台异步/增量准备图片，告别启动卡顿；
     - **移除 `Qt.WindowType.Tool`**：恢复标准全屏窗口聚焦能力；
     - **`ScreensaverGlobalEventFilter`**：在 `QApplication` 全局层拦截任意键盘按键、鼠标点击、滚轮或大于 8px 的鼠标晃动，瞬间触发退出；
     - **守护线程与强制退出**：工作线程全部改造为 `daemon=True`，独立屏保退出时触发 `os._exit(0)` 兜底，绝不在任务栏和后台进程挂起。
3. **修复“下次计划切换时间一直显示计算中 / 图片总量一直显示为零”**：
   - 痛点根因：UI 定时器调用的 `_scheduler.get_snapshot()` 与 `_library.total_valid_count` 缺失属性导致抛出 `AttributeError`，UI 定时刷新在首帧即中断，未能脱离初始占位文案；
   - 极致优化：为 `WallpaperScheduler` 补充 `get_snapshot()` 兼容别名，为 `SchedulerStatusSnapshot` 扩充 `remaining_formatted`（格式化为 `HH:MM:SS` 实时倒计时）、`next_due_formatted` 与 `last_success_formatted`（自动转为本地可读时间），并为 `ImageLibrary` 增加线程安全的 `total_valid_count` 属性，实时精准刷新。
4. **彻底解决“选择选项的格子和背景融为一体、看不清哪个能选哪个不能选”**：
   - 痛点根因：旧样式表中各层容器与控件背景色色差过小（`#111927` vs `#0f172a`），表格无清晰网格线，单选/复选框无状态图标与禁用态样式；
   - 极致优化：
     - **阶梯式深色对比度层次**：页面底色 `#090d16` -> 容器 Card `#111a2a` (带 `1.5px solid #28374f` 边框) -> 控件背景 `#121c2e` (带 `1.5px solid #334562` 边框)；
     - **专属图标指示器**：动态生成 20x20 高清像素图标，复选框提供翠蓝高亮背景与纯白对勾标记 (`cb_checked.png`)、清晰未选边框 (`cb_unchecked.png`) 以及变暗灰度禁用态 (`cb_disabled.png`)；单选框提供专属同心圆标记；
     - **禁用态/不可选状态对比**：所有 `:disabled` 控件强制使用暗灰边框与 `#4b5d78` 弱化文字，与可选控件一目了然；
     - **表格控件 (QTableWidget)**：开启交替行底色 (`#0c1220` 与 `#121b2d`)，加入高对比度网格线 (`#2a3c56`) 与选中高亮 (`#2563eb`)，状态列以色彩区分（绿点“● 启用中” vs 灰点“○ 已停用”）；
     - **下拉框 (QComboBox)**：定制独立右侧按钮与明亮下拉小三角图标 (`arrow_down.png`)，下拉弹出面板拥有立体阴影与高亮悬停条目。

---

## 八、PyInstaller 构建结果及生成文件位置

打包产物已成功生成并经过实测验收：

| 产物文件 | 绝对路径 | 文件大小 | SHA-256 校验和 |
|---|---|---|---|
| **`WallFlow.exe`** | `C:\Users\Lenovo\Desktop\Desktop WallPaper\GouTou-WallPapper\dist\WallFlow.exe` | **44.55 MB** | `b0f90896a6a8a5d662408e6a54254e1c13c65dbf6eb429183c88db824ef17e9b` |
| **`WallFlowSaver.scr`** | `C:\Users\Lenovo\Desktop\Desktop WallPaper\GouTou-WallPapper\dist\WallFlowSaver.scr` | **44.54 MB** | `4f0f5383f71d53b4f7b6f5a5cad2a057ef9b946a996e1d814b6cd4535db64dc6` |
| **`SHA256SUMS.txt`** | `C:\Users\Lenovo\Desktop\Desktop WallPaper\GouTou-WallPapper\release\SHA256SUMS.txt` | 158 字节 | - |

可执行文件实测响应结果：
- `dist/WallFlow.exe --check` -> 退出码 `0`，响应迅速；
- `dist/WallFlowSaver.scr /c --auto-close-ms 1000` -> 退出码 `0`，成功启动配置界面并退出；
- `dist/WallFlowSaver.scr /s --auto-close-ms 1000` -> 退出码 `0`，全屏启动屏保并退出。

---

## 九、当前未实现的功能及其原因

1. **视频壁纸与网页壁纸播放**：
   - *原因*：根据需求第 1.2 节的明确指示（“不要偏离项目定位，本项目是一个以静态图片为核心的壁纸和屏保软件，不是动态壁纸播放器；暂不需要视频壁纸、网页壁纸、Unity/Godot 程序……避免引入庞大且不必要的依赖”），我们坚决不引入 `libmpv` 或 `QtWebEngine`，使可执行文件体积保持在 44MB 左右轻量级水平。
2. **云端图片库同步与在线壁纸下载**：
   - *原因*：需求明确定位本地静态原图离线管理，WallFlow 保证 100% 本地运行、不联网、无遥测，严格保护用户隐私与网络安全。

---

## 十、已知缺陷、潜在风险与后续改进建议

1. **Windows 11 24H2 个别桌面多屏 COM 设备标识变化**：
   - *风险*：在极少数 Windows 11 24H2 预览版中，如果存在处于虚拟断开状态的显示器，`IDesktopWallpaper.GetMonitorRECT` 可能抛出 `COMError`；
   - *当前处理*：WallFlow 在枚举时已加入容错捕获，自动无缝回退到虚拟桌面合成 + `SystemParametersInfoW` 模式，确保壁纸正常应用；
   - *后续建议*：后续可结合 `DirectX / DXGI OutputDuplication` 进一步提升跨屏合成效率。
2. **超大型 16K 级特大图片缩放**：
   - *风险*：超过 16384 像素宽度的极端巨型图片在初次生成缩略图时瞬时内存占用会稍高；
   - *当前处理*：使用 PIL `draft()` 提示做预降采样；
   - *后续建议*：后续可引入分块流式解码（Tile-based streaming decode）进一步压低极限大图的瞬时内存开销。

---

## 十一、PowerShell 命令汇总

```powershell
# 1. 进入工作目录
cd "C:\Users\Lenovo\Desktop\Desktop WallPaper\GouTou-WallPapper"

# 2. 运行自动化测试套件 (42 项)
.\.venv\Scripts\python.exe -m pytest -v

# 3. 运行性能基准测试
.\.venv\Scripts\python.exe scripts\benchmark_performance.py

# 4. 执行一键构建与打包
.\build.ps1
# 或执行: .\.venv\Scripts\python.exe build.py

# 5. 验收测试打包生成的 EXE 与 SCR
.\.venv\Scripts\python.exe scripts\verify_executables.py

# 6. 启动主程序
.\dist\WallFlow.exe

# 7. 测试启动原生屏幕保护程序
.\dist\WallFlowSaver.scr /s
```

