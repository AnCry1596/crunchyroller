# Crunchyroller

A Crunchyroll downloader.

Downloads single episodes, entire seasons, or complete series, decrypts Widevine streams, grabs multiple audio/subtitle tracks, and packs everything into clean MKV files.

---

# Features

* 📺 Download a single episode, an entire season, or a whole series.
* 🎬 Pick your video quality (1080p → 240p) and audio bitrate.
* 🚀 Multi-threaded DASH downloader so you're not waiting forever.
* 🌐 Multiple audio and subtitle tracks bundled into one MKV.
* 🍪 Browser session support (eventually).
* ⚙️ CLI if terminals are more your thing.

> **⚠️ Browser Session Auto-Detection**
>
> It's still under development and currently doesn't work. You'll have to provide your `etp_rt` cookie manually for now, and the "in-app browser login" option is still unstable

---

# Installation

Clone the repository and install the dependencies:

```bash
pip install -r requirements.txt
```

---

# Things You'll Need

## FFmpeg

If the download finishes but the final video refuses to exist, chances are FFmpeg is missing.

Either:

* Install `ffmpeg` and make sure it's in your system's PATH, or
* Throw `ffmpeg.exe` into the project's root folder.

One of those two is enough.

---

## Widevine

You'll also need valid Widevine CDM device files.

Supported formats:

* `client_id.bin` + `private_key.pem`
* `.wvd`

Drop them into the project's root directory.

I can't help you obtain these files or explain how to generate them.

Google is your friend.

Searching around Android Studio is a decent place to start.

If you're stuck setting up the project itself (not getting the keys), feel free to DM me on Discord:

**`.vure`**

---

# Usage

## Web UI

```bash
python main.py --gui
```

Then open:

```text
http://localhost:8000
```

---

## Download an Entire Series

```bash
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/series/..." --season 1
```

---

## Download a Single Episode

```bash
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --url "https://www.crunchyroll.com/watch/..." --video-quality 1080p
```

---

## Download URLs From a File

```bash
python main.py --etp-rt "YOUR_ETP_RT_TOKEN" --file urls.txt
```

---

# Screenshots

<img width="1823" height="960" alt="Screenshot 2026-08-04 041853" src="https://github.com/user-attachments/assets/1812cb7c-cb0d-4076-b247-959cda9ebabc" />


---

# Disclaimer

This project is intended for educational purposes and personal backups.

Please don't be the reason lawyers have another meeting.

---

If you find a bug, there's a decent chance I already know about it.

If you find a weird bug... please open an issue because now I'm curious.
