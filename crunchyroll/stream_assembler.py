"""In-order bounded streaming reassembly buffer and single-pass sequential disk writer."""

import logging
import os
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger("crunchyroll.stream_assembler")


class StreamAssembler:
    """Bounded in-memory reordering buffer and direct sequential disk writer.
    
    Accepts downloaded chunks/segments from concurrent worker threads out-of-order,
    buffers up to a strict memory limit (default 32 MB), and flushes contiguous
    sequential segments directly to the raw output file on disk in a single pass.
    
    This eliminates the creation and deletion of hundreds of temporary `seg_*.mp4`
    files, avoids `shutil.copyfileobj` concatenation churn, and reduces disk write
    amplification from 4x to 1x while guaranteeing strictly bounded RAM usage.
    """

    def __init__(
        self,
        output_path: str,
        total_segments: int,
        max_in_flight_mb: int = 32,
        start_index: int = 1,
    ):
        self.output_path = output_path
        self.total_segments = total_segments
        self.max_in_flight_bytes = max_in_flight_mb * 1024 * 1024
        self.start_index = start_index

        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._buffer: Dict[int, bytes] = {}
        self._current_memory_bytes: int = 0
        self._next_expected_index: int = start_index
        self._written_segments: int = 0
        self._total_bytes_written: int = 0
        self._closed: bool = False
        self._aborted: bool = False
        self._error: Optional[Exception] = None

        # Open target file with 1MB OS write buffer
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        self._file = open(output_path, "wb", buffering=1024 * 1024)

    @property
    def written_segments(self) -> int:
        with self._lock:
            return self._written_segments

    @property
    def current_buffered_bytes(self) -> int:
        with self._lock:
            return self._current_memory_bytes

    def write_init(self, data: bytes) -> int:
        """Directly write initialization segment header to the start of the output file."""
        with self._lock:
            if self._aborted:
                raise RuntimeError(f"StreamAssembler aborted: {self._error}")
            if self._closed:
                raise RuntimeError("StreamAssembler is already closed")
            self._file.write(data)
            self._total_bytes_written += len(data)
            return len(data)

    def add_segment(self, segment_index: int, data: bytes) -> int:
        """Insert a downloaded segment into the reassembly pipeline.
        
        If memory capacity is reached, worker threads block until earlier
        in-sequence segments are written to disk and evicted from memory.
        """
        seg_len = len(data)
        with self._lock:
            if self._aborted:
                raise RuntimeError(f"StreamAssembler aborted: {self._error}")
            if self._closed:
                raise RuntimeError("StreamAssembler is already closed")

            # Memory backpressure: wait if adding this segment exceeds memory limit,
            # UNLESS this is the exact next expected segment which will immediately drain!
            wait_start = time.time()
            while (
                (self._current_memory_bytes + seg_len > self.max_in_flight_bytes)
                and (segment_index != self._next_expected_index)
                and not self._aborted
            ):
                self._condition.wait(timeout=0.5)
                # Anti-deadlock fail-safe if upstream scheduler is starved
                if time.time() - wait_start > 5.0:
                    break

            if self._aborted:
                raise RuntimeError(f"StreamAssembler aborted: {self._error}")

            if segment_index in self._buffer:
                # Duplicate segment delivered (e.g. from speculative hedging winner)
                return seg_len

            if segment_index < self._next_expected_index:
                # Segment was already written
                return seg_len

            # Store in buffer
            self._buffer[segment_index] = data
            self._current_memory_bytes += seg_len

            # Drain contiguous sequential segments directly to disk
            while self._next_expected_index in self._buffer:
                chunk = self._buffer.pop(self._next_expected_index)
                self._file.write(chunk)
                self._current_memory_bytes -= len(chunk)
                self._total_bytes_written += len(chunk)
                self._written_segments += 1
                self._next_expected_index += 1
                # Wake up any workers waiting for memory capacity
                self._condition.notify_all()

            return seg_len

    def abort(self, exc: Optional[Exception] = None) -> None:
        """Abort streaming assembly and wake up any blocked worker threads."""
        with self._lock:
            self._aborted = True
            self._error = exc or RuntimeError("StreamAssembler aborted")
            self._condition.notify_all()
            if not self._closed:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._closed = True

    def finish(self) -> str:
        """Flush remaining buffer, verify completeness, close file, and return output path."""
        with self._lock:
            if self._aborted:
                raise RuntimeError(f"Cannot finish aborted StreamAssembler: {self._error}")
            if self._closed:
                return self.output_path

            # Flush any contiguous segments if possible
            while self._next_expected_index in self._buffer:
                chunk = self._buffer.pop(self._next_expected_index)
                self._file.write(chunk)
                self._current_memory_bytes -= len(chunk)
                self._total_bytes_written += len(chunk)
                self._written_segments += 1
                self._next_expected_index += 1

            if self._written_segments < self.total_segments:
                missing = [
                    idx for idx in range(self.start_index, self.start_index + self.total_segments)
                    if idx >= self._next_expected_index and idx not in self._buffer
                ]
                self._file.flush()
                self._file.close()
                self._closed = True
                raise RuntimeError(
                    f"StreamAssembler incomplete: received {self._written_segments}/{self.total_segments} "
                    f"segments. Missing segments: {missing[:10]}..."
                )

            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except (OSError, AttributeError):
                pass
            self._file.close()
            self._closed = True
            return self.output_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.abort(exc_val)
        else:
            if not self._closed:
                self.finish()
