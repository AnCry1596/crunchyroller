<div align="center">

# Crunchyroller — Crunchyroll Downloader

**A fast, modern Crunchyroll downloader with a sleek Web GUI and CLI. Download Crunchyroll anime episodes, full seasons, and series with multi-track audio, soft subtitles, unthrottled speeds, and automated Widevine DRM decryption into MKV.**

[![Release](https://img.shields.io/github/v/release/Vure-sh/crunchyroller?color=black&style=for-the-badge)](https://github.com/Vure-sh/crunchyroller/releases/latest)
[![Stars](https://img.shields.io/github/stars/Vure-sh/crunchyroller?color=ffd700&style=for-the-badge)](https://github.com/Vure-sh/crunchyroller/stargazers)
[![Downloads & Clones](https://img.shields.io/badge/Downloads%20%26%20Clones-400%2B-black?style=for-the-badge)](https://github.com/Vure-sh/crunchyroller/releases)

---

<img width="837" height="726" alt="Screenshot 2026-09-20 201713" src="https://github.com/user-attachments/assets/f632405c-2cbf-4fe8-8ff3-d8095cdf32d1" />



</div>

---

### Plex & Jellyfin Ready Out-of-the-Box

Downloads automatically default to a dedicated `anime/` directory, sorted into standard `Series/Season 01/Series - S01E01 - Title.mkv` folders so home media servers like Jellyfin and Plex instantly match official posters, episode guides, and multi-track audio without manual renaming:

<img width="1851" height="1034" alt="Jellyfin Library Showcase" src="https://github.com/user-attachments/assets/c2b945a5-ce6b-4f4c-9bb9-abfff0db4bee" />

---

## Features

- **Download Queue & Batch Manager:** Queue up multiple episodes, full seasons, or series in the web GUI with live progress meters and a sliding queue drawer.
- **Pause, Resume & Cancel:** Pause and resume active downloads with dynamic rolling speed calculation, or cancel individual episodes on the fly without interrupting the rest of your queue.
- **Plex & Jellyfin Ready:** Automatically creates `Season XX` subfolders with standard scene naming (`Series - S01E01 - Title.mkv`), ensuring 100% instant metadata and poster matching in home media servers.
- **Uncapped Download Speeds:** Downloads aren't throttled at all — it maxes out whatever your internet connection can handle (can reach 60–70+ MB/s on fast connections).
- **Multiple Audio Dubs & Soft Subtitles:** Pick Japanese, English, or download all available dubs and subs in one go (with full English CC support) muxed cleanly into a single MKV.
- **Clean Desktop GUI & CLI:** Run it as a sleek desktop app, in your web browser, or straight from the command line.
- **Automated Widevine DRM Decryption:** Handles CENC decryption automatically once you provide your CDM keys (`.wvd` or `client_id.bin` + `private_key.pem`).
- **Easy Login:** Sign in directly with your email/password, your web browser session, or by pasting an `etp_rt` cookie.
- **Smart Session Pacing:** Automatic session cleanup and cooldown delays to prevent playback lockouts or rate limits.

---

## Getting Started

### Windows (Pre-built)
1. Download the latest release from [**Releases**](https://github.com/Vure-sh/crunchyroller/releases/latest).
2. Extract the zip.
3. Put your Widevine CDM files in the folder (see below).
4. Run `crunchyroller.exe`.

### Running from Source
Make sure you have **Python 3.10+** and [**FFmpeg**](https://ffmpeg.org/) installed.

```bash
git clone https://github.com/Vure-sh/crunchyroller.git
cd crunchyroller
pip install -r requirements.txt

# Run the app
python main.py --gui       # desktop window
python main.py --browser   # or in your browser
```

---

## Widevine Keys

Crunchyroll content is protected by Widevine DRM, so you need CDM keys to decrypt the video files.

Drop either of these into the app folder (next to `crunchyroller.exe` or in the project root):
- A `*.wvd` file, **or**
- Both `client_id.bin` and `private_key.pem`

*(Keys can't be bundled here for obvious reasons. You can dump them from an Android device or find them online).*

---

## Optional Download Engine: N_m3u8DL-RE

In addition to the built-in native Python downloader, Crunchyroller supports [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) as an alternative backend engine (thanks to [@AnCry1596](https://github.com/AnCry1596) for the implementation!).

### To use N_m3u8DL-RE (completely optional):
1. Download `N_m3u8DL-RE` and `mp4decrypt` (Bento4) for your platform and place them inside the `bin/` folder.
2. In `config.json`, enable the engine:
   ```json
   "use_n_m3u8dl_re": true
   ```

*(If set to `false`, Crunchyroller uses its built-in unthrottled pure-Python downloader).*

---

## CLI Usage

If you prefer using the terminal as a CLI downloader:

```bash
# Log in with your account (recommended for faster downloads and 192k audio)
python main.py --email "user@example.com" --password "your_password" --url "https://www.crunchyroll.com/watch/..."

# Download a single episode in 1080p
python main.py --url "https://www.crunchyroll.com/watch/..." --video-quality 1080p

# Download with specific audio dubs and subtitles
python main.py --url "https://www.crunchyroll.com/watch/..." --audio-lang "ja-JP,en-US" --subs-lang "en-US,es-419"

# Download all available dubs and subs
python main.py --url "https://www.crunchyroll.com/watch/..." --audio-lang all --subs-lang all

# List all available seasons and story arcs
python main.py --url "https://www.crunchyroll.com/series/..." --list-seasons

# Download a specific season or story arc
python main.py --url "https://www.crunchyroll.com/series/..." --season 1
python main.py --url "https://www.crunchyroll.com/series/..."

# Batch download from a file (one URL per line)
python main.py --file urls.txt
```

### Listing & Picking Seasons (No More Guessing)

Crunchyroll numbers seasons pretty weirdly behind the scenes — for example, *Demon Slayer's Mugen Train Arc* is actually listed as Season 4, and *Attack on Titan's OADs* are listed under Season 66.

Instead of guessing what number Crunchyroll gave to an arc or special, just use `--list-seasons` (or `-ls`):

```bash
python main.py --list-seasons https://www.crunchyroll.com/series/GY5P48XEY
```

You'll instantly get a clean breakdown with the real arc names and the exact `--season` flag to use:

```text
Seasons available for 'Demon Slayer: Kimetsu no Yaiba':
===================================================================
  --season 4   | Mugen Train Arc
  --season 5   | Entertainment District Arc
  --season 6   | Swordsmith Village Arc
  --season 7   | Hashira Training Arc
===================================================================
```

Then just download the arc you want:
```bash
python main.py https://www.crunchyroll.com/series/GY5P48XEY --season 4
```

*(You don't even need to type `--url`, you can pass the link directly! And yes, specials and movies listed under `--season 0` work too).*

Run `python main.py --help` to see all available flags.

---

## Logs & Troubleshooting

If you run into an error, want to inspect download details, or need to attach logs when opening an issue, Crunchyroller automatically writes clean, token-redacted logs here:

- **Windows:** `%LOCALAPPDATA%\crunchyroller\logs\crunchyroller.log`  
  *(Press `Win + R`, paste `%LOCALAPPDATA%\crunchyroller\logs`, and hit Enter).*
- **Linux:** `~/.config/crunchyroller/logs/crunchyroller.log` (or `$XDG_STATE_HOME/crunchyroller/logs/`)
- **macOS:** `~/Library/Logs/crunchyroller/crunchyroller.log`

> **Note:** Sensitive data like auth tokens and passwords are automatically scrubbed from log files. You can disable persistent file logging anytime via the GUI Settings tab or by passing `--no-log` in the CLI.

---

## FAQ

**Why do I get a lower bitrate version when downloading?**  
Not every title on Crunchyroll has a separate high-bitrate 1080p stream. Some releases (like *Gachiakuta* E1) only have a single standard 1080p stream on Crunchyroll's servers, while others (like *Chainsaw Man*) offer both 1080p low and 1080p high bitrate variants. If a high-bitrate stream exists, Crunchyroller downloads it by default (unless you select Data-Saver mode in settings). If Crunchyroll only provides one stream, that is what gets fetched.

**Does it download in 1080p?**  
Yes, it fetches the highest available stream quality (up to 1080p source) by default, or you can pick 720p/480p if you want smaller file sizes.

**Can I download multiple audio dubs and subtitles together?**  
Yes. You can select specific dubs and subs (e.g. Japanese + English audio and English subtitles) or choose "all" to bundle everything into a single `.mkv` with proper language tags.

**Why do I need Widevine CDM keys?**  
Crunchyroll streams are encrypted with Widevine DRM. Providing your own CDM files lets the app decrypt the video and audio streams directly on your machine.

---

## Star the Repo

If Crunchyroller helped you out, consider dropping a star on GitHub! It really helps more people discover the project.

---

## Disclaimer

This project is intended for personal backups and educational use only. Please support the official creators and license holders by keeping an active Crunchyroll subscription.
