"""
crunchyroller discord bot — remote control for the downloader from your phone.
reuses the same download pipeline as web_gui.py, with interactive episode selection.
"""

import asyncio
import os
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Set

import discord
from discord import app_commands

from crunchyroll.api import (
    get_episode_info,
    get_season_episodes,
    get_seasons,
    get_series,
    parse_url_type,
)
from crunchyroll.auth import load_config
from crunchyroll.downloader import download_episode
from crunchyroll.http_client import CrunchyrollHttpClient
from crunchyroll.types import EpisodeInfo, EpisodeMetadata, Season, SeasonEpisode


# ── state ────────────────────────────────────────────────────────────────────

class DownloadState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"  # idle | running | completed | failed
        self.progress = 0.0
        self.episode = ""
        self.error = ""
        self.cancel_flag = False
        self.queue: deque = deque()  # list of (ep_id, label, vq, aq, a_langs, s_langs)
        self.log: list = []

    def _log(self, msg: str):
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


# ── download worker ──────────────────────────────────────────────────────────

def _worker_loop(etp_rt: str):
    """process queued items one by one in a background thread"""
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


def enqueue_episodes(items: List[tuple], etp_rt: str):
    """add items to queue and start worker if not already running"""
    with STATE.lock:
        for item in items:
            STATE.queue.append(item)
        already_running = STATE.status == "running"

    if not already_running:
        t = threading.Thread(target=_worker_loop, args=(etp_rt,), daemon=True)
        t.start()


# ── range parser helper ──────────────────────────────────────────────────────

def parse_episode_ranges(range_str: str, available_eps: List[SeasonEpisode]) -> List[SeasonEpisode]:
    """parse expressions like '1-5, 8, 10-12' and return matching episodes"""
    selected: List[SeasonEpisode] = []
    ep_map = {ep.episode_number: ep for ep in available_eps}

    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2:
                try:
                    start, end = int(sub[0].strip()), int(sub[1].strip())
                    for num in range(min(start, end), max(start, end) + 1):
                        if num in ep_map and ep_map[num] not in selected:
                            selected.append(ep_map[num])
                except ValueError:
                    pass
        else:
            try:
                num = int(part)
                if num in ep_map and ep_map[num] not in selected:
                    selected.append(ep_map[num])
            except ValueError:
                pass

    return sorted(selected, key=lambda e: e.episode_number)


# ── interactive ui views ─────────────────────────────────────────────────────

class CustomRangeModal(discord.ui.Modal, title="Select Episode Range"):
    range_input = discord.ui.TextInput(
        label="Episode Numbers or Ranges",
        placeholder="e.g. 1-5, 8, 11-13",
        required=True,
        max_length=100,
    )

    def __init__(self, picker_view: "EpisodePickerView"):
        super().__init__()
        self.picker_view = picker_view

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.range_input.value.strip()
        eps = parse_episode_ranges(raw, self.picker_view.current_episodes)
        if not eps:
            await interaction.response.send_message(
                f"❌ No matching episodes found for range `{raw}` in this season.",
                ephemeral=True,
            )
            return
        await self.picker_view.enqueue_and_finish(interaction, eps)


class SingleEpisodeView(discord.ui.View):
    def __init__(
        self,
        ep_id: str,
        label: str,
        vq: str,
        aq: str,
        a_langs: List[str],
        s_langs: List[str],
        etp_rt: str,
    ):
        super().__init__(timeout=300)
        self.ep_id = ep_id
        self.label = label
        self.vq = vq
        self.aq = aq
        self.a_langs = a_langs
        self.s_langs = s_langs
        self.etp_rt = etp_rt

    @discord.ui.button(label="📥 Download Episode", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        enqueue_episodes(
            [(self.ep_id, self.label, self.vq, self.aq, self.a_langs, self.s_langs)],
            self.etp_rt,
        )
        self.clear_items()
        await interaction.response.edit_message(
            content=f"📥 **Added to queue:**\n`1.` {self.label} `[{self.vq}/{self.aq}]`",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.clear_items()
        await interaction.response.edit_message(
            content="❌ Download cancelled.",
            embed=None,
            view=None,
        )


class EpisodePickerView(discord.ui.View):
    def __init__(
        self,
        series_title: str,
        seasons: List[Season],
        season_episodes_map: Dict[str, List[SeasonEpisode]],
        vq: str,
        aq: str,
        a_langs: List[str],
        s_langs: List[str],
        etp_rt: str,
    ):
        super().__init__(timeout=300)
        self.series_title = series_title
        self.seasons = seasons
        self.season_episodes_map = season_episodes_map
        self.current_season_idx = 0
        self.selected_ep_ids: Set[str] = set()
        self.vq = vq
        self.aq = aq
        self.a_langs = a_langs
        self.s_langs = s_langs
        self.etp_rt = etp_rt
        self.update_components()

    @property
    def current_season(self) -> Season:
        return self.seasons[self.current_season_idx]

    @property
    def current_episodes(self) -> List[SeasonEpisode]:
        sn = self.current_season
        return self.season_episodes_map.get(sn.id, [])

    def update_components(self):
        self.clear_items()

        # 1. Season selector if multiple seasons
        if len(self.seasons) > 1:
            season_options = [
                discord.SelectOption(
                    label=f"Season {s.season_number}: {s.title or 'Season ' + str(s.season_number)}"[:100],
                    value=str(i),
                    default=(i == self.current_season_idx),
                )
                for i, s in enumerate(self.seasons[:25])
            ]
            season_select = discord.ui.Select(
                placeholder="Switch Season...",
                options=season_options,
                row=0,
            )
            season_select.callback = self.on_season_change
            self.add_item(season_select)

        # 2. Episode multi-select dropdown (up to 25)
        eps = self.current_episodes
        if eps:
            ep_options = [
                discord.SelectOption(
                    label=f"E{ep.episode_number:02d}: {ep.title or 'Episode ' + str(ep.episode_number)}"[:100],
                    description=f"Season {ep.season_number} Episode {ep.episode_number}"[:50],
                    value=ep.id,
                    default=(ep.id in self.selected_ep_ids),
                )
                for ep in eps[:25]
            ]
            ep_select = discord.ui.Select(
                placeholder="Check episodes to download...",
                min_values=1,
                max_values=len(ep_options),
                options=ep_options,
                row=1,
            )
            ep_select.callback = self.on_episode_select
            self.add_item(ep_select)

        # 3. Action buttons
        btn_download_selected = discord.ui.Button(
            label="📥 Download Selected",
            style=discord.ButtonStyle.success,
            row=2,
        )
        btn_download_selected.callback = self.on_download_selected
        self.add_item(btn_download_selected)

        btn_download_all = discord.ui.Button(
            label="📦 Download All (Season)",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        btn_download_all.callback = self.on_download_all
        self.add_item(btn_download_all)

        btn_range = discord.ui.Button(
            label="🔢 Custom Range",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        btn_range.callback = self.on_custom_range
        self.add_item(btn_range)

        btn_cancel = discord.ui.Button(
            label="❌ Cancel",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        btn_cancel.callback = self.on_cancel
        self.add_item(btn_cancel)

    def build_embed(self) -> discord.Embed:
        eps = self.current_episodes
        sn = self.current_season
        embed = discord.Embed(
            title=f"🎬 {self.series_title}",
            description=(
                f"**Season {sn.season_number}** ({len(eps)} episodes)\n"
                f"Quality: `{self.vq}` / `{self.aq}`\n\n"
                f"• Check specific episodes in the dropdown below\n"
                f"• Or click **Download All** / **Custom Range**"
            ),
            color=0x5865F2,
        )
        if self.selected_ep_ids:
            embed.add_field(
                name="Selected",
                value=f"**{len(self.selected_ep_ids)}** episode(s) checked",
                inline=False,
            )
        return embed

    async def on_season_change(self, interaction: discord.Interaction):
        self.current_season_idx = int(interaction.data["values"][0])
        self.selected_ep_ids.clear()
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_episode_select(self, interaction: discord.Interaction):
        self.selected_ep_ids = set(interaction.data["values"])
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_download_selected(self, interaction: discord.Interaction):
        if not self.selected_ep_ids:
            await interaction.response.send_message(
                "❌ Select at least one episode from the dropdown first!",
                ephemeral=True,
            )
            return
        ep_map = {ep.id: ep for ep in self.current_episodes}
        selected_eps = [ep_map[eid] for eid in self.selected_ep_ids if eid in ep_map]
        await self.enqueue_and_finish(interaction, selected_eps)

    async def on_download_all(self, interaction: discord.Interaction):
        await self.enqueue_and_finish(interaction, self.current_episodes)

    async def on_custom_range(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CustomRangeModal(self))

    async def on_cancel(self, interaction: discord.Interaction):
        self.clear_items()
        await interaction.response.edit_message(
            content="❌ Download cancelled.",
            embed=None,
            view=None,
        )

    async def enqueue_and_finish(
        self, interaction: discord.Interaction, episodes: List[SeasonEpisode]
    ):
        if not episodes:
            await interaction.response.send_message(
                "❌ No episodes selected.", ephemeral=True
            )
            return

        queue_items = []
        for ep in episodes:
            lbl = f"{self.series_title} S{ep.season_number:02d}E{ep.episode_number:02d} — {ep.title}"
            queue_items.append((ep.id, lbl, self.vq, self.aq, self.a_langs, self.s_langs))

        enqueue_episodes(queue_items, self.etp_rt)

        lines = [
            f"📥 **Added {len(queue_items)} episode(s) to queue** `[{self.vq}/{self.aq}]`"
        ]
        for i, (_, lbl, *_) in enumerate(queue_items[:10]):
            lines.append(f"`{i+1}.` {lbl}")
        if len(queue_items) > 10:
            lines.append(f"*...and {len(queue_items) - 10} more*")

        self.clear_items()
        if interaction.response.is_done():
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content="\n".join(lines),
                embed=None,
                view=None,
            )
        else:
            await interaction.response.edit_message(
                content="\n".join(lines),
                embed=None,
                view=None,
            )


# ── discord bot ──────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    await tree.sync()
    print(f"bot ready as {bot.user}")


@tree.command(name="download", description="submit a crunchyroll url to select and download episodes")
@app_commands.describe(url="crunchyroll episode, season, or series url")
async def cmd_download(interaction: discord.Interaction, url: str):
    cfg = load_config()
    etp_rt = cfg.get("etp_rt", "")
    if not etp_rt:
        await interaction.response.send_message(
            "❌ no etp_rt token found in config.json. log in via the desktop app first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=False)

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
            label = (
                f"{info.episode_metadata.series_title} "
                f"S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d} — {info.title}"
            )
            embed = discord.Embed(
                title=f"📺 {info.title}",
                description=(
                    f"**Series:** {info.episode_metadata.series_title}\n"
                    f"**Episode:** S{info.episode_metadata.season_number:02d}E{info.episode_metadata.episode_number:02d}\n"
                    f"**Quality:** `{vq}` / `{aq}`"
                ),
                color=0x5865F2,
            )
            view = SingleEpisodeView(
                ep_id=cid,
                label=label,
                vq=vq,
                aq=aq,
                a_langs=a_langs,
                s_langs=s_langs,
                etp_rt=etp_rt,
            )
            await interaction.followup.send(embed=embed, view=view)

        elif kind == "series":
            series = get_series(client, cid, a_langs[0], s_langs[0])
            title = series.get("title", cid)
            seasons = series.get("seasons", [])
            all_eps = series.get("episodes", [])

            if not seasons or not all_eps:
                await interaction.followup.send("❌ No seasons or episodes found for this series.")
                return

            season_episodes_map: Dict[str, List[SeasonEpisode]] = {}
            for s in seasons:
                season_episodes_map[s.id] = [e for e in all_eps if e.season_number == s.season_number]

            view = EpisodePickerView(
                series_title=title,
                seasons=seasons,
                season_episodes_map=season_episodes_map,
                vq=vq,
                aq=aq,
                a_langs=a_langs,
                s_langs=s_langs,
                etp_rt=etp_rt,
            )
            await interaction.followup.send(embed=view.build_embed(), view=view)

        elif kind == "season":
            eps = get_season_episodes(client, cid, a_langs[0], s_langs[0])
            if not eps:
                await interaction.followup.send("❌ No episodes found for this season.")
                return

            title = eps[0].series_title if eps else "Crunchyroll Season"
            sn_num = eps[0].season_number if eps else 1
            pseudo_season = Season(
                id=cid,
                season_number=sn_num,
                audio_locale=a_langs[0],
                title=f"Season {sn_num}",
            )
            view = EpisodePickerView(
                series_title=title,
                seasons=[pseudo_season],
                season_episodes_map={cid: eps},
                vq=vq,
                aq=aq,
                a_langs=a_langs,
                s_langs=s_langs,
                etp_rt=etp_rt,
            )
            await interaction.followup.send(embed=view.build_embed(), view=view)

        else:
            await interaction.followup.send(f"❌ unsupported url type: {kind}")

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
