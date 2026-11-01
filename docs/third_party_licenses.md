# 许可证合规审查与第三方声明 (`third_party_licenses.md`)

本文档记录了参考项目的开源许可证义务审查结果，以及 **WallFlow** 所使用的 Python 运行时依赖库的许可证声明。

---

## 一、三个参考项目的许可证合规评估

在开发开始前，我们核查了工作区中三个参考项目的许可证文件与源码版权头：

| 参考项目 | 许可证类型 | 许可证位置 | 核心权限与义务分析 | WallFlow 采纳策略 |
|---|---|---|---|---|
| **KokoroPaper** (`wallpaper-changer-windows`) | **MIT License** | `README.md`、`desktop/src-tauri/tauri.conf.json` | 宽松开源许可证（Permissive）。允许商业/私人使用、修改和分发；若复用其代码需保留版权声明（`Copyright (c) klysman08`）与许可声明。 | **仅参考功能设计与算法思路**（如不放回随机周期思想、拼贴网格布局、色彩调整参数维度），使用 Python 独立编写实现，未复制其 Rust/TypeScript 源码或图标素材。 |
| **MultiScreenSaver** | **GPL-3.0-or-later** | `COPYING` 及各 `.cpp`/`.h` 文件头 (`Copyright (C) 2023 Andrew Lutsenko`) | 强著佐权许可证（Strong Copyleft）。如果直接复制、修改或静态/动态链接其 C++ 源码，衍生作品必须以 GPLv3 开源分发。但学习 Windows 标准 `.scr` 命令行规范（`/s`、`/c`、`/p`）及功能设计并在净室环境（Clean-room）下用 Python + PySide6 独立实现，不构成 GPL 衍生作品。 | **完全独立实现**。不复制任何 C++ 源码或 wxWidgets 布局文件；使用 Python + PySide6 重新设计非阻塞帧动画屏保引擎。 |
| **Lively Wallpaper** (`lively`) | **GPL-3.0** | `LICENSE`、`src/installer/License/License.txt` (`Copyright (C) 2026 rocksdanister`) | 强著佐权许可证（Strong Copyleft）。直接复制其 C# 源码同样会触发 GPLv3 传染性义务；而调用 Microsoft Windows 公开文档化的 COM 接口（`IDesktopWallpaper`）、Win32 API（`EnumDisplayMonitors`、`SetParent`、`WM_POWERBROADCAST`）以及 `desk.cpl` 控制面板命令属于操作系统标准接口调用，不受特定应用 GPL 约束。 | **完全独立实现**。仅参考其系统事件监听清单、Win32/COM 标准接口调用模式与屏保预览挂载机制，使用 Python 独立编码实现。 |

### 合规结论

1. **禁止跨许可证混用源码**：鉴于 `MultiScreenSaver` 与 `lively` 采用 GPL-3.0，而 `KokoroPaper` 采用 MIT，我们严格遵守知识产权与开源合规原则，**不从上述三个项目中复制任何源代码、UI 模板、图标或静态资源**。
2. **100% 原创 Python 实现**：**WallFlow** 的全部 Python 源码、PySide6 界面、测试套件、图标生成与打包脚本均为独立编写，版权清晰、无许可证污染风险。

---

## 二、WallFlow 第三方运行时与构建依赖声明

WallFlow 运行与打包所依赖的 Python 第三方库及其开源许可证如下：

| 依赖库 | 用途 | 许可证 | 兼容性说明 |
|---|---|---|---|
| **Python (CPython)** | 基础运行时与标准库 (`ctypes`, `winreg`, `sqlite3`, `json`, `logging`) | PSF License v2 | 宽松许可，允许商业与独立打包分发 |
| **PySide6 (Qt for Python)** | 图形用户界面、系统托盘、屏保渲染与原生消息过滤 | LGPL-3.0 | 采用动态链接方式（PyInstaller 标准 DLL 打包），未修改 Qt/PySide6 自身源码，符合 LGPLv3 分发要求 |
| **Pillow (PIL Fork)** | 静态图片元数据校验、EXIF 方向处理、多显示器壁纸合成、缩略图与滤镜处理 | MIT-CMU License | 宽松许可，完全兼容 |
| **comtypes** | 调用 Windows 原生 `IDesktopWallpaper` COM 接口 | MIT License | 宽松许可，完全兼容 |
| **pywin32** | Win32 API 辅助接口支持 | PSF License | 宽松许可，完全兼容 |
| **psutil** | 性能基准测量与进程内存监控诊断 | BSD-3-Clause License | 宽松许可，完全兼容 |
| **pytest** | 单元测试与集成测试框架（仅开发/测试阶段使用） | MIT License | 宽松许可，不打包进最终 EXE |
| **PyInstaller** | 构建 `WallFlow.exe` 与 `WallFlowSaver.scr` | GPL-2.0 with Bootloader Exception | PyInstaller 具有特殊的引导加载程序例外条款（Bootloader Exception），允许将其打包生成的独立可执行文件以任何许可证分发 |

---

## 三、致谢声明

本项目在功能规划与架构设计过程中参考了以下优秀开源项目的设计思想，特此致谢：

1. **KokoroPaper (`wallpaper-changer-windows`)** — 作者：`klysman08` (https://github.com/klysman08/wallpaper-changer-windows, MIT License)
2. **MultiScreenSaver** — 作者：`Andrew Lutsenko / OpenScopeProject` (https://github.com/openscopeproject/MultiScreenSaver, GPL-3.0 License)
3. **Lively Wallpaper** — 作者：`Dani John (rocksdanister) / lively-community` (https://github.com/lively-community/lively, GPL-3.0 License)

