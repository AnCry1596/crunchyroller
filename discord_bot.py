"""
crunchyroller discord bot — remote control for the downloader from your phone.
reuses the same download pipeline as web_gui.py, no duplicate logic.
"""

import asyncio
import os
import threading
import time
from collections import deque
from typing import Optional

import discord
from discord import app_commands

from crunchyroll.api import get_episode_info, get_season_episodes, get_series, parse_url_type
from crunchyroll.auth import load_config
from crunchyroll.downloader import download_episode
from crunchyroll.http_client import CrunchyrollHttpClient
from crunchyroll.types import EpisodeInfo, EpisodeMetadata


# ── state ────────────────────────────────────────────────────────────────────

class DownloadState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"          # idle | running | completed | failed
        self.progress = 0.0
        self.episode = ""
        self.error = ""
        self.cancel_flag = False
        self.queue: deque = deque()   # list of (ep_id, label, vq, aq, a_langs, s_langs)
        self.log: list = []

    def _log(self, msg):
        with self.lock:
            self.log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            if len(self.log) > 100:
                self.log.pop(0)

    def reset(self):
        with self.lock:
            self.status = "idle"
            self.progress = 0.0
            self.episode = ""
            self.error = ""
            self.cancel_flag = False

STATE = DownloadState()


# ── download worker (runs in a thread, mirrors web_gui._run_download) ───────

def _worker_loop(etp_rt: str):
    """process queued items one by one"""
    while True:
        with STATE.lock:
            if not STATE.queue:
                STATE.status = "idle"
                return
            item = STATE.queue.popleft()

        ep_id, label, vq, aq, a_langs, s_langs = item

        with STATE.lock:
            STATE.status = "running"
            STATE.progress = 0.0
            STATE.episode = label
            STATE.error = ""
            STATE.cancel_flag = False

        STATE._log(f"downloading: {label}")

        try:
            client = CrunchyrollHttpClient(etp_rt)
            info = get_episode_info(client, ep_id)

            def _cb(title, cur, tot, speed, status):
                with STATE.lock:
                    STATE.progress = round((cur / tot) * 100, 1) if tot > 0 else 0
                    STATE.episode = f"{label} ({cur}/{tot})"

            download_episode(
                client=client,
                base_content_id=ep_id,
                info=info,
                audio_langs=a_langs,
                subs_langs=s_langs,
                video_quality=vq,
                audio_quality=aq,
                progress_cb=_cb,
            )

            with STATE.lock:
                STATE.status = "completed"
                STATE.progress = 100.0
            STATE._log(f"done: {label}")

        except Exception as e:
            with STATE.lock:
                STATE.status = "failed"
                STATE.error = str(e)
            STATE._log(f"failed: {label} — {e}")


def enqueue_episodes(items, etp_rt):
    """add items to queue and start worker if not already running"""
    with STATE.lock:
        for item in items:
            STATE.queue.append(item)
        already_running = STATE.status == "running"

    if not already_running:
        t = threading.Thread(target=_worker_loop, args=(etp_rt,), daemon=True)
        t.start()


# ── discord bot ──────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    await tree.sync()
    print(f"bot ready as {bot.user}")


@tree.command(name="download", description="submit a crunchyroll url to download")
@app_commands.describe(url="crunchyroll episode, season, or series url")
async def cmd_download(interaction: discord.Interaction, url: str):
    cfg = load_config()
    etp_rt = cfg.get("etp_rt", "")
    if not etp_rt:
        await interaction.response.send_message("❌ no etp_rt token found in config.json. log in via the desktop app first.", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        kind, cid = parse_url_type(url)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return

    vq = cfg.get("video_quality", "1080p")
    aq = cfg.get("audio_quality", "192k")
    al = cfg.get("audio_lang", "ja-JP")
    sl = cfg.get("subs_lang", "en-US")
    a_langs = [x.strip() for x in al.split(",") if x.strip()] or ["ja-JP"]
    s_langs = [x.strip() for x in sl.split(",") if x.strip()] or ["en-US"]

    try:
        client = CrunchyrollHttpClient(etp_rt)

        if kind == "episode":
            info = get_episode_info(client, cid)
            label = f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} — {info.title}"
            episodes = [(cid, label, vq, aq, a_langs, s_langs)]

        elif kind == "series":
            series = get_series(client, cid, a_langs[0], s_langs[0])
            title = series.get("title", cid)
            eps = series.get("episodes", [])
            episodes = []
            for ep in eps:
                lbl = f"{title} S{ep.season_number:02d}E{ep.episode_number:02d} — {ep.title}"
                episodes.append((ep.id, lbl, vq, aq, a_langs, s_langs))

        elif kind == "season":
            eps = get_season_episodes(client, cid, a_langs[0], s_langs[0])
            episodes = []
            for ep in eps:
                lbl = f"{ep.series_title} S{ep.season_number:02d}E{ep.episode_number:02d} — {ep.title}"
                episodes.append((ep.id, lbl, vq, aq, a_langs, s_langs))

        else:
            await interaction.followup.send(f"❌ unsupported url type: {kind}")
            return

        if not episodes:
            await interaction.followup.send("❌ no episodes found for that url")
            return

        enqueue_episodes(episodes, etp_rt)

        lines = [f"📥 **Added {len(episodes)} episode(s) to queue** [{vq}/{aq}]"]
        for i, (_, lbl, *_) in enumerate(episodes[:10]):
            lines.append(f"`{i+1}.` {lbl}")
        if len(episodes) > 10:
            lines.append(f"*...and {len(episodes) - 10} more*")

        await interaction.followup.send("\n".join(lines))

    except Exception as e:
        await interaction.followup.send(f"❌ {e}")


@tree.command(name="status", description="show current download progress")
async def cmd_status(interaction: discord.Interaction):
    with STATE.lock:
        status = STATE.status
        progress = STATE.progress
        episode = STATE.episode
        error = STATE.error
        recent_log = list(STATE.log[-5:])

    if status == "idle":
        msg = "💤 idle — nothing downloading"
    elif status == "running":
        bar_len = 12
        filled = int(bar_len * progress / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        msg = f"⏳ **downloading**\n`{bar}` {progress:.1f}%\n📺 {episode}"
    elif status == "completed":
        msg = f"✅ **download complete**\n📺 {episode}"
    elif status == "failed":
        msg = f"❌ **download failed**\n📺 {episode}\n```{error}```"
    else:
        msg = f"status: {status}"

    if recent_log:
        msg += "\n\n**recent log:**\n" + "\n".join(f"`{l}`" for l in recent_log)

    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="queue", description="show queued downloads")
async def cmd_queue(interaction: discord.Interaction):
    with STATE.lock:
        status = STATE.status
        episode = STATE.episode
        q = list(STATE.queue)

    lines = []
    if status == "running":
        lines.append(f"▶️ **now:** {episode}")

    if q:
        lines.append(f"\n📋 **queued ({len(q)}):**")
        for i, (_, lbl, *_) in enumerate(q[:15]):
            lines.append(f"`{i+1}.` {lbl}")
        if len(q) > 15:
            lines.append(f"*...and {len(q) - 15} more*")
    elif not lines:
        lines.append("📋 queue is empty")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@tree.command(name="cancel", description="cancel current download and clear queue")
async def cmd_cancel(interaction: discord.Interaction):
    with STATE.lock:
        was_running = STATE.status == "running"
        STATE.cancel_flag = True
        STATE.queue.clear()
        STATE.status = "idle"
        STATE.episode = ""

    if was_running:
        await interaction.response.send_message("🛑 cancelled current download and cleared queue")
    else:
        await interaction.response.send_message("📋 queue cleared (nothing was running)")


# ── entry point ──────────────────────────────────────────────────────────────

def _load_env():
    """load .env file if it exists (no extra dependency needed)"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def start_bot(token: Optional[str] = None):
    _load_env()
    tk = token or os.environ.get("DISCORD_BOT_TOKEN", "")
    if not tk:
        print("error: set DISCORD_BOT_TOKEN in .env file or as environment variable")
        return
    bot.run(tk)


if __name__ == "__main__":
    start_bot()
