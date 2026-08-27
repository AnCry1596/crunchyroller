"""
Crunchyroller Performance & Resource Benchmarking Suite.

Provides automated tools for measuring download throughput across worker pools
and profiling continuous memory consumption (RSS) to guarantee strict memory bounding (< 100 MB).
"""

from .benchmark_throughput import run_throughput_benchmark
from .benchmark_memory import run_memory_benchmark

__all__ = [
    "run_throughput_benchmark",
    "run_memory_benchmark",
]
