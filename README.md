# Crunchyroll Downloader (Python Edition)

A high-performance Python port of [crunchyroll-downloader](https://github.com/CuteTenshii/crunchyroll-downloader) supporting Widevine DRM decryption, multi-threaded segment downloading, multi-audio/subtitle track multiplexing, series/season batch downloads, credential authentication, quality selection, and a modern Web GUI.

---

## Features

- 🌟 **Modern Web GUI**: Interactive glassmorphism dashboard (`python main.py --gui`) with live progress bars, speed metrics, episode batch selector trees, and quality dropdowns.
- 🔑 **Credential Login**: Log in with your Crunchyroll Email & Password or Session Token (`etp_rt`). Credentials and session tokens are saved securely in `config.json`.
- 📺 **Full Season & Series Downloading**: Download single episodes (`/watch/`), entire seasons (`/season/`), or full anime series (`/series/`).
- 🎬 **Quality Selector**: Choose video quality (`1080p`, `720p`, `480p`, `360p`, `240p`) and audio quality (`192k`, `96k`).
- ⚡ **Concurrent Downloader**: Download DASH fragments simultaneously with 10 workers for maximum speed.
- 🔒 **Zero-Error Decryption**: Native FFmpeg `-decryption_key` demuxing and per-sample CENC decryption for clean, error-free playback.
- 🌐 **Multi-Audio & Multi-Subtitles**: Mux Japanese / English audio and ASS subtitles into Matroska MKV files with proper language tags.

---

## Installation & Setup

1. **Clone or navigate to the project directory**:
   ```powershell
   cd C:\Users\vure\.gemini\antigravity\scratch\crunchyroll-downloader-python
   ```

2. **Install Python dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

3. **FFmpeg Setup**:
   `ffmpeg.exe` is already included directly in the project directory.

4. **Widevine CDM Device Keys**:
   Ensure your Widevine device keys (`client_id.bin` and `private_key.pem` or `.wvd` file) are placed in the project root directory.

---

## Usage

### 1. Launch Modern Web GUI (Recommended)

```powershell
python main.py --gui
```
*Or simply run `python main.py` without arguments. This opens http://localhost:8000 in your browser.*

---

### 2. Command Line Interface (CLI)

#### Download with Email & Password Credentials:
```powershell
python main.py --email "user@example.com" --password "your_password" --url "https://www.crunchyroll.com/series/GEXH3W407/demon-slayer-kimetsu-no-yaiba"
```

#### Download Full Series (All Seasons):
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/series/GEXH3W407/demon-slayer-kimetsu-no-yaiba"
```

#### Fast 360p Test Download:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/watch/GE00198973JAJP/dawn-and-confusion" --video-quality 360p --audio-quality 96k
```

#### Maximum 1080p Quality Download:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/watch/GE00198973JAJP/dawn-and-confusion" --video-quality 1080p --audio-quality 192k
```

#### Download Multiple URLs from File:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --file urls.txt
```

---

## License & Disclaimer

Educational tool created for personal backup purposes. Respect Crunchyroll's terms of service and content copyrights.
