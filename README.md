# Crunchyroller

A high-performance Crunchyroll video downloader with Widevine DRM decryption, multi-threaded segment downloading, multi-audio/subtitle track multiplexing, series/season batch downloads, quality selection, and a clean dark Web GUI.

---

## Features

- 🌟 **Clean Web GUI**: Interactive dark dashboard (`python main.py --gui`) with episode batch selection, quality controls, and live progress logs.
- ⚡ **Auto-Detect Session**: Automatically extracts `etp_rt` session cookies from installed browsers (Chrome, Edge, Firefox, Brave).
- 📺 **Full Series & Season Downloads**: Download single episodes (`/watch/`), entire seasons (`/season/`), or full series (`/series/`).
- 🎬 **Quality Selector**: Choose video quality (`1080p`, `720p`, `480p`, `360p`, `240p`) and audio quality (`192k`, `96k`).
- ⚡ **Concurrent Downloader**: Download DASH fragments simultaneously with 10 workers for maximum speed.
- 🌐 **Multi-Audio & Multi-Subtitles**: Mux Japanese / English audio and ASS subtitles into Matroska MKV files with proper language tags.

---

## Setup

1. **Install Python dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

2. **Widevine CDM Keys**:
   Ensure Widevine device files (`client_id.bin` and `private_key.pem` or `.wvd` file) are present in the root directory.

---

## Usage

### 1. Web GUI (Recommended)

```powershell
python main.py --gui
```
*Opens http://localhost:8000 in your browser.*

---

### 2. Command Line Interface (CLI)

#### Download Series:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/series/GEXH3W407/demon-slayer-kimetsu-no-yaiba" --season 1
```

#### Fast 360p Download:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/watch/GE00198973JAJP/dawn-and-confusion" --video-quality 360p --audio-quality 96k
```

#### Download URLs from File:
```powershell
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --file urls.txt
```

---

## License & Disclaimer

Educational tool created for personal backup purposes. Respect content copyrights.
