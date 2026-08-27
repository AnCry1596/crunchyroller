# Crunchyroller Test Infrastructure & E2E Validation Architecture

## Overview
The Crunchyroller test suite implements an automated, 5-tier requirement-driven verification architecture designed to validate high-throughput streaming, connection pooling, adaptive concurrency, stream decoupling, streaming decryption, Matroska (MKV) multiplexing, and strictly bounded memory consumption (< 100 MB RSS).

---

## 1. Test Architecture & Tier Hierarchy

```
tests/
├── __init__.py                  # Test package marker
├── mock_server.py               # Local multi-threaded Mock HTTP DASH & DRM Streaming Server
├── test_runner.py               # Structured test runner with tier filtering & JSON reporting
├── test_tier1_features.py       # Tier 1: Core Feature Unit Tests (13 features, 66 tests)
├── test_tier2_boundaries.py     # Tier 2: Boundary & Corner Case Tests (8 categories, 40 tests)
├── test_tier3_combinations.py   # Tier 3: Cross-Feature Combination Tests (5 matrices, 20 tests)
├── test_tier4_scenarios.py      # Tier 4: Real-World E2E Scenarios (FFmpeg muxing, ffprobe, psutil RSS < 100MB) (12 tests)
└── test_tier5_adversarial.py    # Tier 5: Adversarial Edge-Cases & Fault Injection (18 tests)
```

---

## 2. Test Tiers Specification

### Tier 1: Core Feature Unit Tests (`test_tier1_features.py`)
- **Coverage**: 13 core features across all system layers (66 total unit tests):
  1. Persistent Connection Pooling & HTTP Session Reuse (`SessionPool`)
  2. Dynamic Concurrency & AIMD Worker Scaling (`AIMDConcurrencyScaler`)
  3. Single-Pass Stream Assembler (`StreamAssembler`)
  4. MPD Manifest Parsing & Representation Selection (`parse_manifest`, `get_base_url`)
  5. CENC PSSH Extraction & Base64 Decoding (`get_pssh`)
  6. Token Refresh & Authentication Lifecycle (`CrunchyrollHttpClient`)
  7. Filename Sanitization & ISO-639-2 Language Mappings (`sanitize_filename`, `LANGUAGE_CODES`)
  8. CLI Argument Parser, Aliases & Batch File Parsing (`main.py`)
  9. URL Type Resolution & CMS Object Deserialization (`parse_url_type`, `get_episode`)
  10. Subtitle Processing & ASS Format Validation (`download_subs`)
  11. FFmpeg Matroska Remuxer & Track Disposition Mapping (`merge_everything`)
  12. Web GUI REST API Endpoints & SafeStream Encoding (`web_gui.py`)
  13. Discord Bot Range Parser & Single Instance Lock (`discord_bot.py`)

### Tier 2: Boundary & Corner Case Tests (`test_tier2_boundaries.py`)
- **Coverage**: 8 boundary categories (40 total unit tests):
  1. Empty & Malformed DASH Manifests (missing Period, 0 AdaptationSets, invalid XML)
  2. Single-Segment & Extreme Repeat Count Timelines (`r=0`, `r=1000`, `r=50000`, `r=-1`, boundary `startNumber`)
  3. HTTP 420 Rate Limiting Backoff & Max Retry Exhaustion
  4. Network Timeouts, Read Stalls & Socket Errors
  5. Reverse, Inverted, Non-Contiguous, and Invalid Episode Ranges (`8-5`, `all`, out-of-range IDs)
  6. Malformed, Parameterized, and Non-Standard Crunchyroll URLs
  7. Zero-Length and Truncated Files
  8. Extreme Unicode, RTL, Emojis, Colons, and Reserved OS Filenames

### Tier 3: Cross-Feature Combination Tests (`test_tier3_combinations.py`)
- **Coverage**: 5 cross-feature matrices (20 total tests):
  1. Concurrent Video + Multi-Audio Dub Downloads (`ja-JP`, `en-US`, `es-419`)
  2. Session Pool Reuse under Dynamic Concurrency & Straggler Hedging
  3. Multi-Track Muxing with Dispositions, Language Metadata Tags & Subtitles
  4. Batch Processing with Failure Isolation & Recovery across Multi-URL Lists
  5. Dual-Stream Concurrent Assembly & Memory Bounding

### Tier 4: Realistic E2E Download Scenarios (`test_tier4_scenarios.py`)
- **Coverage**: End-to-end integration workflows with live Mock Server, real FFmpeg remuxing, and `ffprobe` stream validation (12 tests):
  1. Full Episode Acquisition: Downloads H.264 video, AAC audio, and ASS subtitles from Mock Server, remuxes to `.mkv` with FFmpeg, and verifies with `ffprobe` JSON stream inspection.
  2. Multi-Audio Dubs & Multi-Subtitles Muxing: Downloads 1 video, 3 audio dubs (`ja-JP`, `en-US`, `es-419`), and 2 subtitle tracks, asserting 6 valid streams with correct language tags and default dispositions.
  3. Strict Memory Bounding (< 100 MB RSS): Continuously samples process RSS via `psutil` during high-throughput downloads of 30 concurrent segments, asserting peak RAM < 100 MB (measured ~51-54 MB).
  4. Multi-Episode Batch Workflows: Sequentially processes a 3-episode batch, asserting valid output files and verification that all intermediate `.raw.mp4`, `.mp4`, and `.ass` files are removed.
  5. CLI Full Entrypoint Workflow: Validates argument parsing, aliases, and batch file ingestion.
  6. Web GUI REST API Workflow: Tests state transitions (`idle -> downloading -> completed`) and config persistence.
  7. Discord Bot Workflow: Validates range selection syntax and single-instance lock socket binding.
  8. Stream Integrity & Atomic File Renaming: Validates `.tmp.mkv` -> `.mkv` atomic commit pattern.

### Tier 5: Adversarial Edge-Case & Fault Injection Tests (`test_tier5_adversarial.py`)
- **Coverage**: Adversarial fault injection and extreme error scenarios (18 tests):
  1. Simulated Socket Disconnections & Resets (abrupt connection resets, connection refused on unreachable port, persistent 500 error exhaustion)
  2. Corrupted Segment Data & Bitstream Truncation (zero-byte chunks, StreamAssembler abort on unrecoverable error and cleanup)
  3. Rate-Limit Bursts (HTTP 420/429 bursts, AIMD multiplicative drop, floor bounding >= 8 workers, ceiling bounding <= 48 workers)
  4. Malformed DASH Manifests (negative repeat counts `r="-5"`, missing initialization templates, foreign namespaces, corrupted base64 PSSH)
  5. Network Latency Stalls & Hedging Races (speculative secondary request beating stalled primary, timeout exhaustion)
  6. DRM License Server Faults (HTTP 403 Forbidden handling, invalid PSSH handling)
  7. FFmpeg Muxing Failures (corrupted input bitstream causing non-zero exit code, asserting automatic removal of partial corrupted output MKV)
  8. Concurrent Contention & Races (32 threads pushing reverse-order chunks, thread-safe concurrent `SessionPool.close()`)

---

## 3. Mock DASH Streaming Server (`tests/mock_server.py`)

`MockCrunchyrollServer` is a lightweight, thread-safe `http.server.ThreadingHTTPServer` designed for 100% offline, reproducible integration testing:
- **Synthetic Bitstream Synthesis**: Uses `MockMediaGenerator` to generate valid DASH-compliant MP4 initialization (`init.mp4`) and media segment chunks (`seg_00001.mp4`) for H.264 video and AAC audio.
- **Dynamic MPD Manifest Generation**: Generates compliant DASH XML manifests with multi-representation video qualities (1080p, 720p, 480p, 360p) and multi-audio representations (192k, 96k) with configurable `SegmentTimeline` repeat counts.
- **Subtitles**: Serves standard ASS subtitle files with styles and dialogue events.
- **Auth & CMS API Simulation**: Simulates `/auth/v1/token`, `/playback/v3/.../play`, `/content/v2/cms/objects/...`, and `/content/v2/cms/series/.../seasons`.
- **Fault Injection Framework**:
  - `simulated_latency`: Injects artificial network delay.
  - `rate_limit_remaining`: Returns HTTP 420 Rate Limited responses for N requests.
  - `auth_fail_remaining`: Returns HTTP 401 Unauthorized responses to test token renewal.
  - `flaky_segment_error_rate`: Injects random HTTP 500 errors.
  - `custom_routes`: Allows test cases to inject custom callback handlers for specific routes.

---

## 4. Execution Commands

### Run Full Test Suite via Unified Test Runner
```bash
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier all --json-report /tmp/test_report.json
```

### Run Specific Test Tiers
```bash
# Tier 1: Core Feature Tests
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier 1

# Tier 2: Boundary Tests
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier 2

# Tier 3: Combination Tests
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier 3

# Tier 4: Real-World Scenarios
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier 4

# Tier 5: Adversarial Fault Injection
/home/vure/crunchyroller/.venv/bin/python tests/test_runner.py --tier 5
```

### Run via Standard Unittest Discovery
```bash
/home/vure/crunchyroller/.venv/bin/python -m unittest discover -s tests -v
```
