# Project: Crunchyroller Optimization

## Architecture
Crunchyroller is a high-performance media acquisition and remuxing tool for Crunchyroll DASH streams.
The optimized architecture consists of 5 decoupled layers:
1. **Network & Session Pool Layer (`crunchyroll/http_client.py`, `crunchyroll/session_pool.py`)**:
   - High-performance `requests.Session` / `urllib3.PoolManager` with persistent HTTP/1.1 Keep-Alive and HTTP/2 connection pooling.
   - Dynamic AIMD concurrency scaler adjusting active workers (8 to 48) based on throughput feedback and error backoff.
   - Speculative tail-latency pre-fetching and chunk hedging.
2. **Streaming & Pipelined Assembly Layer (`crunchyroll/downloader.py`, `crunchyroll/stream_assembler.py`)**:
   - In-order bounded ring-buffer / reassembly queue (< 32 MB) receiving chunks from concurrent workers.
   - Pipelined single-pass stream writer writing sequentially directly to target disk stream, eliminating temporary segment file churn (reducing write amplification from 4x to 1x).
3. **Decryption & Stream Decoupling Layer (`crunchyroll/drm.py`, `crunchyroll/decryptor.py`)**:
   - Fast CENC AES-128-CTR decryption using FFmpeg native demuxer or memory-bounded streaming Python fallback (4 MB buffer, zero full-file loads).
4. **Muxing & Output Integrity Layer (`crunchyroll/merger.py`, `crunchyroll/integrity.py`)**:
   - FFmpeg Matroska remuxer with `-avoid_negative_ts make_zero -fflags +genpts -max_interleave_delta 0`.
   - Multi-track language tagging, default disposition configuration, and correct season/episode metadata.
   - Automated post-mux JSON `ffprobe` integrity validator asserting audio/video/subtitle stream counts, duration, and error-free bitstreams with atomic `.tmp.mkv` -> `.mkv` renaming.
5. **Application Entrypoints & Interface Layer (`main.py`, `web_gui.py`, `discord_bot.py`)**:
   - CLI flags for performance tuning (`--workers`, `--disable-hedging`), Web GUI REST progress and control, Discord bot queue management.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Persistent Connection Pooling | Reusable HTTP sessions with `HTTPAdapter(pool_connections=64, pool_maxsize=64)` | M1 | Survey R1 |
| 2 | Dynamic Worker Scaling | AIMD concurrency scaler adjusting worker count (8-48) dynamically | M1 | Survey R1 |
| 3 | Single-Pass Stream Assembler | In-memory bounded queue assembling downloaded chunks directly to disk | M1 | Survey R1 |
| 4 | Look-Ahead Chunk Pre-fetching | Speculative pre-fetching and tail-latency straggler hedging | M1 | Survey R1 |
| 5 | Concurrent Track Fetching | Overlapped video and multi-audio stream downloading | M1 | Survey R1 |
| 6 | Streaming CENC Decryption | Memory-bounded block-by-block decryption (zero multi-GB RAM allocations) | M2 | Survey R2 |
| 7 | Synchronized MKV Muxing | FFmpeg `-avoid_negative_ts make_zero -fflags +genpts -max_interleave_delta 0` | M2 | Survey R2 |
| 8 | Multi-Track Metadata & Tags | Audio/subtitle language tagging, default dispositions, metadata fix | M2 | Survey R2 |
| 9 | Automated FFprobe Validator | Post-mux JSON integrity validator asserting stream counts and duration | M2 | Survey R2 |
| 10 | Atomic File Finalization | Writing to `.tmp.mkv` and renaming to `.mkv` only after integrity verification | M2 | Survey R2 |
| 11 | Strict RAM Bounding (<100MB) | Bounded buffer queues (<32MB) asserting peak RSS < 100MB during downloads | M3 | Survey R3 |
| 12 | Memory Leak Prevention | Explicit buffer recycling and garbage collection across multi-episode batches | M3 | Survey R3 |
| 13 | Single URL Download CLI | Download single episode, season, or series URL via CLI | M4 | Survey Spec |
| 14 | Batch File Download CLI | Download list of URLs from text file with robust error recovery | M4 | Survey Spec |
| 15 | Manifest Debug Mode | CLI flag `--debug-manifest` dumping DASH MPD XML and playback JSON | M4 | Survey Spec |
| 16 | Web GUI REST State & Control | REST endpoints (`/api/state`, `/api/fetch`, `/api/download`, `/api/config`) | M4 | Survey Spec |
| 17 | Discord Bot Slash Commands | Slash commands (`/download`, `/status`, `/queue`, `/cancel`) with progress embed | M4 | Survey Spec |
| 18 | Automated Benchmark Harness | Throughput measurement (MB/s), worker scaling, and memory profiling suite | M4 | Survey R4 |
| 19 | Mock DASH Streaming Server | Self-contained mock HTTP server for reproducible offline testing | M_TEST | Survey R4 |
| 20 | E2E Regression & Tier 1-5 Suite | 4-tier requirement-driven E2E test suite + Tier 5 adversarial hardening | M_TEST / M_FINAL | Survey R4 |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M_TEST | E2E Testing Track | Test infra, mock server, Tiers 1-4 test suite, TEST_INFRA.md & TEST_READY.md | none | IN_PROGRESS |
| M1 | Download Pipeline (R1) | HTTP connection pooling, dynamic worker scaling, single-pass stream assembler, prefetching | none | IN_PROGRESS |
| M2 | Integrity & Decryption (R2) | Streaming decryption, sync muxing, metadata tags, atomic ffprobe validator | M1 interface | PLANNED |
| M3 | Memory Bounding (R3) | Strict RAM bounding < 100 MB, bounded queues, memory leak regression tests | M1, M2 | PLANNED |
| M4 | Benchmarking & Interfaces (R4) | Throughput benchmarking suite, CLI tuning flags, GUI & Discord bot validation | M1, M2, M3 | PLANNED |
| M_FINAL | Final E2E Pass & Hardening | 100% E2E test pass (Tiers 1-4) + Tier 5 Adversarial Coverage Hardening | M_TEST, M4 | PLANNED |

---

## Interface Contracts

### 1. `crunchyroll/session_pool.py` / HTTP Session Manager
```python
class SessionPool:
    def __init__(self, max_pool_size: int = 64, max_retries: int = 5, backoff_factor: float = 1.5): ...
    def get_session(self) -> requests.Session: ...
    def download_segment(self, url: str, timeout: int = 20) -> bytes: ...
    def close(self): ...
```

### 2. `crunchyroll/stream_assembler.py` / Stream Writer
```python
class StreamAssembler:
    def __init__(self, output_path: str, total_segments: int, max_in_flight_mb: int = 32): ...
    def add_segment(self, segment_index: int, data: bytes): ...
    def finish(self) -> str: ...  # returns output_path
```

### 3. `crunchyroll/integrity.py` / Stream Validator
```python
class StreamValidator:
    @staticmethod
    def verify_mkv(file_path: str, expected_video: bool = True, min_audio_tracks: int = 1, min_sub_tracks: int = 0) -> Tuple[bool, str, Dict[str, Any]]: ...
```

### 4. `crunchyroll/downloader.py` (Optimized API)
```python
def download_parts_optimized(
    base_url: str,
    rep_id: str,
    timeline: List[int],
    keys: Optional[Dict[bytes, bytes]],
    output_filename: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    concurrency_config: Optional[ConcurrencyConfig] = None
) -> str: ...
```

---

## Code Layout
- `crunchyroll/`
  - `__init__.py`: Package exports and stream wrappers
  - `session_pool.py`: HTTP session pooling, retry adapters, TCP tuning
  - `stream_assembler.py`: In-order bounded streaming reassembly buffer & disk writer
  - `downloader.py`: High-performance segment download pipeline and orchestrator
  - `decryptor.py`: Stream-based CENC AES-128 decryption and FFmpeg decryptor
  - `merger.py`: Multi-track FFmpeg Matroska muxer with timestamp normalization
  - `integrity.py`: Automated `ffprobe` stream validator and atomic file writer
  - `drm.py`: Widevine CDM key exchange
  - `mpd.py`: DASH MPD manifest parsing and timeline expansion
  - `http_client.py`: Crunchyroll API client with token refresh and rate limit handling
  - `auth.py`, `token.py`: Authentication, browser cookie discovery
  - `types.py`, `utils.py`: Dataclasses, language codes, filename sanitizer
- `benchmarks/`
  - `benchmark_throughput.py`: Concurrency and throughput benchmark measuring MB/s and speedup
  - `benchmark_memory.py`: Continuous memory profiling with psutil asserting RAM < 100 MB
- `tests/`
  - `test_runner.py`: Unified test runner
  - `mock_server.py`: Local mock HTTP DASH / segment streaming server
  - `test_tier1_features.py`: Unit tests for feature coverage (>=5 per feature)
  - `test_tier2_boundaries.py`: Boundary and edge-case testing (>=5 per feature)
  - `test_tier3_combinations.py`: Pairwise and multi-track combination tests
  - `test_tier4_scenarios.py`: Full realistic download and remuxing workload tests
  - `test_tier5_adversarial.py`: Adversarial edge-case and fault injection tests
- `main.py`, `web_gui.py`, `discord_bot.py`: CLI, GUI, and Bot entrypoints
