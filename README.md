<div align="center">

# 🎬 Crunchyroller

**Production-ready Desktop App & CLI to download Crunchyroll anime in full quality.**  
Multi-threaded DASH downloads, multiple audio & subtitle tracks, Widevine DRM decryption, and auto-muxing to MKV.

[![Release](https://img.shields.io/github/v/release/Vure-sh/crunchyroller?color=black&style=for-the-badge)](https://github.com/Vure-sh/crunchyroller/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows%2064--bit-black?style=for-the-badge&logo=windows)](https://github.com/Vure-sh/crunchyroller/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-black?style=for-the-badge&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-black?style=for-the-badge)](LICENSE)

[**📥 Download Latest Executable (.zip)**](https://github.com/Vure-sh/crunchyroller/releases/latest) • [**✨ Features**](#-features) • [**🔑 Widevine Setup**](#-widevine-keys-required) • [**⚙️ Developer Setup**](#-developer-setup)

---

<img width="1816" alt="Crunchyroller Interface" src="https://github.com/user-attachments/assets/e064a2ad-f2c8-40d8-93a6-f32b9a72cb24" />

</div>

---

## ✨ Features

- 🖥️ **Native Desktop App**: Built-in minimalist glassmorphism interface powered by PyWebView. No terminal required.
- ⚡ **Multi-Threaded DASH Downloader**: Ultra-fast segmented downloads for episodes, full seasons, or complete series.
- 🔊 **Multi-Audio & Multi-Subtitles**: Choose any combination of dubs (Japanese, English, Latin Spanish, French, German, etc.) and soft subtitles.
- 🔑 **Widevine DRM Decryption**: Seamlessly decrypts CENC encrypted streams using your Widevine device keys (`.wvd` or `.bin` / `.pem`).
- 🌐 **In-App Browser Session Capturer**: Automatically captures your `etp_rt` session cookie via built-in web login or auto-detects browser cookies.
- 🎬 **FFmpeg Auto-Muxing**: Packs video, audio tracks, subtitles, fonts, and metadata directly into a single, clean `.mkv` file.
- 💻 **CLI Mode Available**: Prefer the terminal? Full command-line interface with batch file downloading support.

---

## 🚀 Quick Start (Portable Executable)

No Python installation required!

1. Download the latest **[`crunchyroller-v1.1.1-win64.zip`](https://github.com/Vure-sh/crunchyroller/releases/latest)** from Releases.
2. Extract the ZIP folder.
3. Place your **Widevine keys** (see below) inside the extracted `crunchyroller/` folder next to `crunchyroller.exe`.
4. Double-click `crunchyroller.exe` to launch!

---

## 🔑 Widevine Keys (Required)

Crunchyroll encrypts its video streams using Widevine DRM. To decrypt and save videos, you must provide your own Widevine device key.

Place **ONE** of the following inside your app folder next to `crunchyroller.exe` (or in the project root if running from source):

* **Option A**: A `*.wvd` file (easiest)
* **Option B**: `client_id.bin` AND `private_key.pem`

> ⚠️ *Note: Widevine device keys cannot be distributed with this project for legal reasons. Search for "ready to use CDMs" or check Android Studio tools if you need to generate your own.*

---

## ⚙️ Developer Setup (Run from Source)

### 1. Prerequisites
* Python 3.10+
* [FFmpeg](https://ffmpeg.org/) (ensure `ffmpeg.exe` is in your system `PATH` or placed in the project root).

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/Vure-sh/crunchyroller.git
cd crunchyroller

# Install Python dependencies
pip install -r requirements.txt
```

### 3. Launching
```bash
# Launch Native Desktop App
python main.py --gui

# Download a single episode via CLI
python main.py --etp-rt "YOUR_ETP_RT" --url "https://www.crunchyroll.com/watch/..." --video-quality 1080p

# Download an entire season
python main.py --etp-rt "YOUR_ETP_RT" --url "https://www.crunchyroll.com/series/..." --season 1

# Batch download URLs from a file
python main.py --etp-rt "YOUR_ETP_RT" --file urls.txt
```

---

## 📁 Repository Structure

```
crunchyroller/
├── crunchyroll/             # Core Downloader & API Logic
│   ├── api.py               # Crunchyroll API parser (Series, Seasons, Episodes)
│   ├── auth.py              # Auth handler & cookie capturer
│   ├── downloader.py        # Multi-threaded DASH downloader
│   ├── drm.py               # PyWidevine license exchange & decryption
│   ├── merger.py            # FFmpeg mkvmerge multiplexer
│   └── mpd.py               # DASH manifest XML parser
├── web/                     # Minimalist B&W Glassmorphism Web UI
│   ├── index.html
│   ├── css/
│   └── js/
├── main.py                  # CLI & App entry point
├── web_gui.py               # PyWebView window & HTTP REST API handler
├── build_exe.py             # PyInstaller standalone executable builder
└── requirements.txt
```

---

## 💬 Community & Support

* If you encounter an issue or have a feature request, please open an [**Issue**](https://github.com/Vure-sh/crunchyroller/issues).
* For setup questions, reach out on Discord: **`.vure`**

---

## ⚠️ Disclaimer

This project is intended strictly for personal backups and educational purposes. Downloading copyrighted content may violate Crunchyroll's Terms of Service. The maintainers take no responsibility for misuse of this software.
