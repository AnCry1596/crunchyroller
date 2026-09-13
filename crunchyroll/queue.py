"""
crunchyroll.queue — thread-safe download queue manager for batch and sequential downloads.
Supports FIFO queuing, job removal, queue clearing, pause/resume, skip, cancel, and live progress reporting.
"""

from collections import deque
from dataclasses import dataclass, field
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from .api import get_episode_info
from .downloader import download_episode
from .http_client import CrunchyrollHttpClient
from .session_pool import ConcurrencyConfig
from .types import EpisodeInfo


@dataclass
class QueueItem:
    id: str
    ep_id: str
    title: str = ""
    season_number: int = 0
    episode_number: int = 0
    series_title: str = ""
    video_quality: str = "1080p"
    audio_quality: str = "192k"
    audio_langs: List[str] = field(default_factory=lambda: ["ja-JP"])
    subs_langs: List[str] = field(default_factory=lambda: ["en-US"])
    force_download: bool = False
    status: str = "queued"  # queued | running | paused | completed | failed | canceled
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output_file: Optional[str] = None
    file_size_mb: float = 0.0

    @property
    def label(self) -> str:
        if self.season_number > 0 and self.episode_number > 0:
            prefix = f"S{self.season_number:02d}E{self.episode_number:02d}"
            return f"{prefix} — {self.title}" if self.title else prefix
        if self.title:
            return self.title
        return self.ep_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ep_id": self.ep_id,
            "title": self.title,
            "label": self.label,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
            "series_title": self.series_title,
            "video_quality": self.video_quality,
            "audio_quality": self.audio_quality,
            "audio_langs": self.audio_langs,
            "subs_langs": self.subs_langs,
            "force_download": self.force_download,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_file": self.output_file,
            "file_size_mb": round(self.file_size_mb, 1),
        }


def normalize_langs(val: Any) -> List[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()] or ["ja-JP"]
    if isinstance(val, str):
        return [str(x).strip() for x in val.split(",") if str(x).strip()] or ["ja-JP"]
    return ["ja-JP"]


class DownloadQueue:
    def __init__(
        self,
        client_factory: Optional[Callable[[], CrunchyrollHttpClient]] = None,
        pause_event: Optional[threading.Event] = None,
        cancel_event: Optional[threading.Event] = None,
        lock: Optional[threading.RLock] = None,
        cooldown_range: tuple = (1.5, 2.5),
        max_history: int = 50,
    ):
        self.lock = lock or threading.RLock()
        self.pause_event = pause_event or threading.Event()
        if pause_event is None:
            self.pause_event.set()
        self.cancel_event = cancel_event or threading.Event()

        self.client_factory = client_factory or (lambda: CrunchyrollHttpClient())
        self.cooldown_range = cooldown_range
        self.max_history = max_history

        self.queue: deque[QueueItem] = deque()
        self.active_job: Optional[QueueItem] = None
        self.history: List[QueueItem] = []
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_all_flag: bool = False

        # Live progress metrics
        self.current_seg: int = 0
        self.total_segs: int = 0
        self.speed: str = ""
        self.track: str = ""
        self.track_pct: float = 0.0
        self.overall_pct: float = 0.0
        self.complete_file: bool = False
        self.log_messages: List[str] = []

        # Batch counts for progress tracking
        self.completed_batch_count: int = 0
        self.total_batch_count: int = 0
        self.last_status: str = "idle"

    def log(self, msg: str):
        with self.lock:
            self.log_messages.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            if len(self.log_messages) > 200:
                self.log_messages.pop(0)

    @property
    def queued_count(self) -> int:
        with self.lock:
            return len(self.queue)

    def enqueue(
        self,
        item: Any,
        default_options: Optional[Dict[str, Any]] = None,
    ) -> Optional[QueueItem]:
        """Enqueue a single episode item. Returns the created QueueItem or None if duplicate."""
        opts = default_options or {}
        if isinstance(item, dict):
            ep_id = str(item.get("id") or item.get("ep_id") or "").strip()
            title = str(item.get("title") or "").strip()
            series_title = str(item.get("series_title") or "").strip()
            season_num = int(item.get("season_number") or 0)
            ep_num = int(item.get("episode_number") or 0)
            vq = str(item.get("video_quality") or opts.get("video_quality") or "1080p")
            aq = str(item.get("audio_quality") or opts.get("audio_quality") or "192k")
            al = normalize_langs(item.get("audio_langs") or item.get("audio_lang") or opts.get("audio_lang") or ["ja-JP"])
            sl = normalize_langs(item.get("subs_langs") or item.get("subs_lang") or opts.get("subs_lang") or ["en-US"])
            fd = bool(item.get("force_download", opts.get("force_download", False)))
        else:
            ep_id = str(item).strip()
            title = ""
            series_title = ""
            season_num = 0
            ep_num = 0
            vq = str(opts.get("video_quality") or "1080p")
            aq = str(opts.get("audio_quality") or "192k")
            al = normalize_langs(opts.get("audio_lang") or ["ja-JP"])
            sl = normalize_langs(opts.get("subs_lang") or ["en-US"])
            fd = bool(opts.get("force_download", False))

        if not ep_id:
            return None

        with self.lock:
            # Deduplicate if already queued or active
            active_id = self.active_job.ep_id if self.active_job else None
            queued_ids = {j.ep_id for j in self.queue}
            if ep_id == active_id or ep_id in queued_ids:
                return None

            job_id = f"job-{uuid.uuid4().hex[:8]}"
            job = QueueItem(
                id=job_id,
                ep_id=ep_id,
                title=title,
                season_number=season_num,
                episode_number=ep_num,
                series_title=series_title,
                video_quality=vq,
                audio_quality=aq,
                audio_langs=al,
                subs_langs=sl,
                force_download=fd,
                status="queued",
            )
            self.queue.append(job)

            if self.active_job is None and len(self.queue) == 1:
                # Reset counters for a fresh batch
                self.completed_batch_count = 0
                self.total_batch_count = 1
                self.last_status = "running"
                self.overall_pct = 0.0
                self.cancel_event.clear()
                self.pause_event.set()
                self.cancel_all_flag = False
            else:
                self.total_batch_count += 1

            self._ensure_worker_running()
            return job

    def enqueue_batch(
        self,
        items: List[Any],
        default_options: Optional[Dict[str, Any]] = None,
    ) -> List[QueueItem]:
        """Enqueue multiple episodes sequentially."""
        enqueued: List[QueueItem] = []
        for it in items:
            job = self.enqueue(it, default_options)
            if job:
                enqueued.append(job)
        return enqueued

    def remove(self, job_id: str) -> bool:
        """Remove a pending job from the queue by ID or ep_id."""
        with self.lock:
            target = None
            for j in self.queue:
                if j.id == job_id or j.ep_id == job_id:
                    target = j
                    break
            if target:
                self.queue.remove(target)
                self.total_batch_count = max(
                    self.completed_batch_count + (1 if self.active_job else 0),
                    self.total_batch_count - 1,
                )
                self.log(f"Removed from queue: {target.label}")
                return True
        return False

    def clear(self) -> int:
        """Clear all pending jobs in the queue."""
        with self.lock:
            count = len(self.queue)
            self.queue.clear()
            self.total_batch_count = self.completed_batch_count + (1 if self.active_job else 0)
            self.log(f"Queue cleared ({count} item(s) removed).")
            return count

    def pause(self):
        """Pause active download."""
        self.pause_event.clear()
        with self.lock:
            if self.active_job and self.active_job.status == "running":
                self.active_job.status = "paused"
            self.speed = "paused"
            self.last_status = "paused"
        self.log("Download paused by user")

    def resume(self):
        """Resume paused download."""
        self.pause_event.set()
        with self.lock:
            if self.active_job and self.active_job.status == "paused":
                self.active_job.status = "running"
            self.last_status = "running"
        self.log("Download resumed by user")

    def cancel_current(self):
        """Cancel/skip active episode and proceed to next in queue."""
        self.cancel_event.set()
        self.pause_event.set()  # Unblock if paused
        with self.lock:
            if self.active_job:
                self.active_job.status = "canceled"
                self.log(f"Skipped active episode: {self.active_job.label}")

    def cancel_all(self):
        """Cancel active download and clear all upcoming queue items."""
        with self.lock:
            self.cancel_all_flag = True
            cleared = len(self.queue)
            self.queue.clear()
            self.total_batch_count = self.completed_batch_count
            self.last_status = "canceled"
            self.speed = ""
            self.track = "canceled"
            if self.active_job:
                self.active_job.status = "canceled"
        self.cancel_event.set()
        self.pause_event.set()
        self.log(f"All downloads cancelled and queue cleared ({cleared} removed).")

    def get_queue_list(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [j.to_dict() for j in list(self.queue)]

    def get_active_job(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.active_job.to_dict() if self.active_job else None

    def get_state(self) -> Dict[str, Any]:
        """Return standardized state dict compatible with web GUI status panel and queue."""
        with self.lock:
            if self.cancel_all_flag or (self.active_job and self.active_job.status == "canceled"):
                status = "canceled"
            elif self.active_job:
                status = "paused" if not self.pause_event.is_set() else "running"
            elif self.last_status in ("canceled", "completed", "error"):
                status = self.last_status
            elif len(self.queue) > 0:
                status = "running"
            else:
                status = "idle"

            tot_batch = max(1, self.total_batch_count)
            ep_idx = min(self.completed_batch_count, tot_batch - 1)
            if status == "completed":
                ep_idx = tot_batch
                overall_pct = 100.0
                track_pct = 100.0
                episode_label = "all done"
            elif self.active_job:
                episode_label = self.active_job.label
                overall_pct = self.overall_pct
                track_pct = self.track_pct
            else:
                episode_label = ""
                overall_pct = 0.0
                track_pct = 0.0

            return {
                "status": status,
                "episode": episode_label,
                "track": self.track,
                "segs_done": self.current_seg,
                "segs_total": self.total_segs,
                "speed": self.speed,
                "overall_pct": overall_pct,
                "track_pct": track_pct,
                "ep_idx": ep_idx,
                "ep_total": tot_batch,
                "complete_file": self.complete_file,
                "log": list(self.log_messages[-100:]),
                "queued_count": len(self.queue),
                "queue": [j.to_dict() for j in list(self.queue)],
                "active_job": self.active_job.to_dict() if self.active_job else None,
                "history": [j.to_dict() for j in list(self.history[-10:])],
            }

    def _ensure_worker_running(self):
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker_thread.start()

    def _update_progress(self, job: QueueItem, cur: int, tot: int, speed: str, status: Any):
        track_type = str(status).lower() if status else "video"
        frac = (cur / tot) if tot > 0 else 0.0
        complete_file = track_type.endswith("-file")
        if complete_file:
            track_type = track_type[:-5]

        is_paused = track_type.endswith("-paused") or not self.pause_event.is_set()
        if track_type.endswith("-paused"):
            track_type = track_type[:-7]

        if "audio" in track_type:
            within_ep = frac * 0.15
            display_track = "audio"
        elif "mux" in track_type:
            within_ep = 0.98
            display_track = "muxing"
        elif "done" in track_type:
            within_ep = 1.0
            display_track = "done"
        else:
            within_ep = 0.15 + (frac * 0.80)
            display_track = "video"

        with self.lock:
            tot_batch = max(1, self.total_batch_count)
            comp_batch = self.completed_batch_count
            ep_base = (comp_batch / tot_batch) * 100
            ep_slice = (1 / tot_batch) * 100
            overall = round(ep_base + (within_ep * ep_slice), 1)
            cap = round(((comp_batch + 1) / tot_batch) * 100 - 0.1, 1)

            self.current_seg = cur
            self.total_segs = tot
            self.speed = "paused" if is_paused else (speed or "")
            self.track = display_track
            self.track_pct = round(frac * 100, 1) if "mux" not in track_type else 100.0
            self.overall_pct = min(overall, cap)
            self.complete_file = complete_file
            if is_paused:
                job.status = "paused"
                self.last_status = "paused"
            elif job.status == "paused":
                job.status = "running"
                self.last_status = "running"

    def _worker_loop(self):
        first_item = True
        while True:
            # Respect pause between jobs
            while not self.pause_event.is_set():
                if self.cancel_all_flag:
                    break
                time.sleep(0.2)

            with self.lock:
                if self.cancel_all_flag or not self.queue:
                    self.active_job = None
                    if self.completed_batch_count > 0 and not self.cancel_all_flag:
                        self.last_status = "completed"
                        self.track = "done"
                        self.speed = ""
                    elif self.cancel_all_flag:
                        self.last_status = "canceled"
                    else:
                        self.last_status = "idle"
                    self.cancel_all_flag = False
                    self.cancel_event.clear()
                    self.worker_thread = None
                    return

                job = self.queue.popleft()
                self.active_job = job
                job.status = "running"
                job.started_at = time.time()
                self.last_status = "running"
                self.current_seg = 0
                self.total_segs = 0
                self.speed = ""
                self.track = "starting"
                self.track_pct = 0.0
                self.cancel_event.clear()

            # Jittered cooldown between consecutive items in batch
            if not first_item:
                cooldown = random.uniform(*self.cooldown_range)
                end_time = time.time() + cooldown
                while time.time() < end_time:
                    if self.cancel_event.is_set() or self.cancel_all_flag:
                        break
                    time.sleep(0.1)
            first_item = False

            if self.cancel_event.is_set() or self.cancel_all_flag:
                with self.lock:
                    job.status = "canceled"
                    job.finished_at = time.time()
                    self.history.append(job)
                self.log(f"canceled: {job.label}")
                continue

            self.log(f"[{self.completed_batch_count + 1}/{self.total_batch_count}] {job.label} [{job.video_quality}/{job.audio_quality}]")

            try:
                client = self.client_factory()
                info = None
                try:
                    info = get_episode_info(client, job.ep_id)
                    if info and info.episode_metadata:
                        if not job.title and info.title:
                            job.title = info.title
                        if not job.season_number and info.episode_metadata.season_number:
                            job.season_number = info.episode_metadata.season_number
                        if not job.episode_number and info.episode_metadata.episode_number:
                            job.episode_number = info.episode_metadata.episode_number
                        if not job.series_title and info.episode_metadata.series_title:
                            job.series_title = info.episode_metadata.series_title
                except Exception as ex:
                    self.log(f"metadata fetch warning for {job.ep_id}: {ex}")

                def _cb(title, cur, tot, speed, status):
                    self._update_progress(job, cur, tot, speed, status)

                output_file = download_episode(
                    client=client,
                    base_content_id=job.ep_id,
                    info=info,
                    audio_langs=job.audio_langs,
                    subs_langs=job.subs_langs,
                    video_quality=job.video_quality,
                    audio_quality=job.audio_quality,
                    progress_cb=_cb,
                    force_download=job.force_download,
                    pause_event=self.pause_event,
                    cancel_event=self.cancel_event,
                    concurrency_config=ConcurrencyConfig(
                        min_workers=8,
                        max_workers=16,
                        initial_workers=16,
                        pool_size=32,
                    ),
                )

                if output_file and os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
                    file_size_mb = os.path.getsize(output_file) / (1024 * 1024)
                    job.output_file = output_file
                    job.file_size_mb = file_size_mb
                    job.status = "completed"
                    job.finished_at = time.time()
                    with self.lock:
                        self.completed_batch_count += 1
                        self.track = "done"
                        self.track_pct = 100.0
                        tot_batch = max(1, self.total_batch_count)
                        self.overall_pct = round((self.completed_batch_count / tot_batch) * 100, 1)
                        self.history.append(job)
                        if len(self.history) > self.max_history:
                            self.history.pop(0)
                    self.log(f"done: {job.label} ({file_size_mb:.1f} MB)")
                else:
                    raise RuntimeError(f"Download finished but output file missing: {output_file}")

            except Exception as e:
                if self.cancel_event.is_set() or isinstance(e, InterruptedError) or self.cancel_all_flag:
                    job.status = "canceled"
                    job.finished_at = time.time()
                    with self.lock:
                        self.history.append(job)
                        if len(self.history) > self.max_history:
                            self.history.pop(0)
                    self.log(f"canceled: {job.label}")
                else:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = time.time()
                    with self.lock:
                        self.history.append(job)
                        if len(self.history) > self.max_history:
                            self.history.pop(0)
                    self.log(f"failed: {job.label} — {e}")
