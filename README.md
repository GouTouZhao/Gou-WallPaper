# Gou Wallpaper

<div align="center">

**Modern, ultra-lightweight, and rock-solid wallpaper and native screensaver manager for Windows 10 & 11.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)
[![GUI: PySide6](https://img.shields.io/badge/GUI-PySide6-brightgreen.svg)](https://doc.qt.io/qtforpython-6/)
[![Platform: Windows x64](https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-blue.svg)](https://www.microsoft.com/windows)

**[English](README.md)** | **[简体中文](README_zh.md)**

</div>

---

## 🌟 Overview

**Gou Wallpaper** is a professional, high-performance desktop wallpaper and native Windows screensaver manager designed for Windows 10 and Windows 11 (64-bit).

Engineered specifically for massive collections of high-resolution static images (4K / 8K in PNG, JPG, WebP, BMP, and TIFF formats), Gou Wallpaper overcomes the common pitfalls of bloated wallpaper utilities—such as memory leaks, timer drift after system sleep, missed schedules after reboot, and annoying screensaver interruptions while watching videos.

---

## 🚀 Key Advantages

### 1. Ultra-Low Memory Footprint & On-Demand Downsampling
- **Sub-Millisecond Metadata Scanning**: During folder indexing, only image headers are read (< 1 ms per file). Pixel data is never loaded in bulk into RAM.
- **Physical Resolution Downsampling**: High-resolution 4K/8K images are decoded and scaled with high-quality Lanczos antialiasing strictly to match the target monitor's physical dimensions, backed by an LRU cache.
- **Automatic Working Set Trimming**: When minimized to the system tray or running in background daemon mode, Gou Wallpaper automatically releases unneeded UI handles and trims its physical working set memory.

### 2. Dual-Mode Reliable Scheduling Engine
Guarantees rock-solid wallpaper transitions without timer drift or lost progress:
- **Short-Interval Mode (< 6 hours)**:
  - Powered by high-precision monotonic clock (`time.monotonic`) for exact millisecond triggers;
  - Includes system resume compensation: calculates elapsed real time during sleep and triggers an immediate catch-up switch if the scheduled interval has passed.
- **Long-Period Mode (≥ 6 hours, e.g., 6h, 12h, Daily, Weekly)**:
  - Tracks schedules using persistent, timezone-aware **UTC system timestamps** (`last_success_at_utc`, `next_due_at_utc`);
  - Lightweight periodic polling checks real clock state—never relying on fragile long timers or polling counts;
  - **Single Smooth Catch-up on Boot**: If the scheduled switch time passed while the PC was powered off, exactly one clean transition is performed on startup without rapid flashing;
  - **Atomic Persistence & Exponential Backoff**: Configurations are saved using atomic file replacement; failures retry gracefully without reporting false successes.

### 3. System-Level Media & Fullscreen Do-Not-Disturb
- **WASAPI Hardware Audio Detection**: Directly interfaces with Windows Core Audio to detect active audio output (video players, music apps, web browsers playing Douyin / Bilibili / YouTube, voice meetings);
- **25-Second Anti-Flicker Grace Period**: Short pauses or video transitions will not trigger accidental screensavers;
- **Fullscreen & DirectX Game Awareness**: Senses exclusive Direct3D games, PowerPoint presentations, and borderless fullscreen playback;
- **Zero Black-Screen Flashes**: When media is active, Gou Wallpaper proactively signals Windows power management (`ES_DISPLAY_REQUIRED`), continuously resetting the OS idle timer and preventing Windows DWM from switching to a black screensaver desktop.

### 4. Smart Power Management & Timed Sleep
- **Display Awake Locking**: Option to prevent Windows from turning off the display during screensaver playback;
- **Timed Auto-Sleep**: Configure the screensaver to automatically put the PC into **System Sleep** or **Turn Off the Display** after running for 10, 15, 30 minutes, 1 hour, or 2 hours.

### 5. Pure Local Offline & Privacy First
- **Zero Network Traffic & Zero Telemetry**: Fully offline. No user data, analytics, or file metadata is ever transmitted over the network;
- **Non-Destructive Processing**: All collage, brightness, contrast, and color temperature adjustments are rendered in memory without touching or modifying the original image files.

---

## 🎨 Feature Matrix

| Feature | Description |
| :--- | :--- |
| **Supported Image Formats** | Full support for `PNG`, `JPG/JPEG`, `WebP`, `BMP`, and `TIFF`. Automatically filters out animated GIFs and corrupted files. |
| **Folder Management** | Add multiple image folders with recursive scanning, individual enable/disable toggles, deduplication, and 50-item virtualized pagination. |
| **Playback Modes** | Non-repeating random shuffle and sequential playback. Intervals range from 10 seconds to 1 week. |
| **Multi-Monitor Display** | Independent wallpapers per monitor, virtual desktop Span mode, Fill, Fit, Stretch, Center, and Tile. |
| **Collage Generation** | Automatic aesthetic grid arrangement for 2 to 8 images with optional high-resolution image export. |
| **Non-Destructive Adjustments** | Real-time preview of brightness, contrast, saturation, and color temperature (warm/cool). |
| **Native Screensaver (`.scr`)** | Full standard Windows `.scr` compatibility: `/s` (fullscreen), `/c` (config dialog), and `/p` (preview window). |
| **Screensaver Animations** | Smooth transitions: Fade, Slide, Zoom, and Instant Cut with configurable durations. |
| **Minimalist Clock Overlay** | Customizable digital clock and date in screensaver (Small, Medium, Large, Extra Large; Bottom-Left, Center-Top, Center-Bottom). |
| **Persistent Bottom Action Bar** | One-click access to Previous, Next, Apply Current, Pause/Resume, Shuffle, and Screensaver Test. |
| **System Tray & Autostart** | Silent background startup (`--minimized`), user-level registry autostart (no admin rights required), and rich context menu. |
| **Global Hotkeys** | Register system hotkeys for quick wallpaper switching, hiding/showing desktop icons, etc. |

---

## 📖 User Guide

### 1. Quick Start
1. Run `GouWallpaper.exe`;
2. Navigate to the **Gallery / Folders** tab, click **Add Image Folder**, and select your wallpaper directories;
3. Go to the **Schedule** tab and choose your preferred interval (e.g., 10 minutes, 30 minutes, Daily);
4. Go to the **Layout** tab and choose your layout mode (default: **Fill**);
5. Click **Apply Current Wallpaper** in the bottom action bar.

### 2. Native Screensaver Setup
Gou Wallpaper integrates seamlessly with Windows:
- **Method 1 (In-App)**: Open the **Screensaver** tab and click **Register GouWallpaperSaver.scr as System Screensaver**;
- **Method 2 (Explorer Context Menu)**: Right-click `GouWallpaperSaver.scr` in File Explorer and select **Install**;
- **Method 3 (Independent Settings)**: Right-click `GouWallpaperSaver.scr` and select **Configure**, or launch it with `/c` to open the standalone screensaver dialog.

### 3. Command-Line Interface (CLI)

#### Main Application: `GouWallpaper.exe`
```text
GouWallpaper.exe [OPTIONS]
  --minimized        Start minimized to the system tray
  --startup          Invoked via Windows startup (silent background daemon)
  --next             Switch to the next wallpaper and exit
  --previous         Switch to the previous wallpaper and exit
  --random           Pick and apply a random wallpaper and exit
  --apply            Apply the currently selected wallpaper and exit
  --check            Check scheduler status and exit
  --auto-close-ms N  (Testing) Automatically exit after N milliseconds
```

#### Screensaver: `GouWallpaperSaver.scr`
```text
GouWallpaperSaver.scr [/s] [/c] [/p <HWND>]
  /s                 Launch borderless fullscreen screensaver (exits on input)
  /c[:HWND]          Open standalone configuration dialog
  /p <HWND>          Embed in the Windows Screensaver settings preview window
```

---

## 🛠️ Build from Source

### Requirements
- **OS**: Windows 10 / Windows 11 (64-bit)
- **Python**: Python 3.10 or newer

### Setup & Run
```powershell
# 1. Clone the repository
git clone <repository_url>
cd GouTou-WallPapper

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch main application
python main.py

# 5. Test screensaver
python screensaver_entry.py /s
```

### Running Automated Tests
```powershell
pytest -v
```

### Compile Standalone Executables
```powershell
python build.py
```
Upon completion, the distribution files will be located in `dist/`:
- `dist/GouWallpaper.exe` (Single-file standalone application, no console window)
- `dist/GouWallpaperSaver.scr` (Single-file native Windows screensaver)
- `release/SHA256SUMS.txt` (Cryptographic verification checksums)

---

## 📄 License

Gou Wallpaper is licensed under the **[MIT License](LICENSE)**.
You are free to use, modify, and distribute this software in accordance with the license conditions.
