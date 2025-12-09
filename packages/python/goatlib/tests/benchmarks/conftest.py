"""Common utilities and fixtures for routing benchmarks."""

import json
import time
import tracemalloc
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import psutil


class BenchmarkMetrics:
    """Base class to track performance metrics during benchmark execution."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset all metrics."""
        self.timings: Dict[str, float] = {}
        self.memory_usage: Dict[str, Dict[str, float]] = {}
        self.response_stats: Dict[str, Any] = {}

    def start_timing(self, phase: str) -> None:
        """Start timing a specific phase."""
        self.timings[f"{phase}_start"] = time.perf_counter()

    def end_timing(self, phase: str) -> None:
        """End timing a specific phase and calculate duration."""
        end_time = time.perf_counter()
        start_time = self.timings.get(f"{phase}_start", end_time)
        self.timings[f"{phase}_duration"] = end_time - start_time

    def record_memory(self, phase: str) -> None:
        """Record memory usage at a specific phase."""
        current, peak = tracemalloc.get_traced_memory()
        process = psutil.Process()
        self.memory_usage[phase] = {
            "current_mb": current / 1024 / 1024,
            "peak_mb": peak / 1024 / 1024,
            "process_rss_mb": process.memory_info().rss / 1024 / 1024,
        }

    def get_duration(self, phase: str) -> Optional[float]:
        """Get duration of a specific phase."""
        return self.timings.get(f"{phase}_duration")

    def get_total_duration(self) -> float:
        """Calculate total duration from all recorded phases."""
        return sum(v for k, v in self.timings.items() if k.endswith("_duration"))

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "timings": self.timings,
            "memory_usage": self.memory_usage,
            "response_stats": self.response_stats,
            "timestamp": datetime.now().isoformat(),
        }

    def print_summary(self) -> None:
        """Print a formatted summary of metrics."""
        print("\n" + "=" * 60)
        print("BENCHMARK SUMMARY")
        print("=" * 60)

        if self.timings:
            print("\n⏱️  TIMINGS:")
            for key, value in self.timings.items():
                if key.endswith("_duration"):
                    phase = key.replace("_duration", "")
                    print(f"  {phase}: {value:.4f}s")

        if self.memory_usage:
            print("\n💾 MEMORY USAGE:")
            for phase, stats in self.memory_usage.items():
                print(f"  {phase}:")
                print(f"    Current: {stats['current_mb']:.2f} MB")
                print(f"    Peak: {stats['peak_mb']:.2f} MB")
                print(f"    Process RSS: {stats['process_rss_mb']:.2f} MB")

        if self.response_stats:
            print("\n📊 RESPONSE STATS:")
            for key, value in self.response_stats.items():
                print(f"  {key}: {value}")

        print("=" * 60 + "\n")


def save_benchmark_results(
    metrics: BenchmarkMetrics, test_name: str, output_dir: Optional[Path] = None
) -> Path:
    """
    Save benchmark results to JSON file.

    Args:
        metrics: The metrics object to save
        test_name: Name of the test for the filename
        output_dir: Optional custom output directory. Defaults to benchmarks/results

    Returns:
        Path to the saved file
    """
    if output_dir is None:
        output_dir = Path(__file__).parent / "results"

    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{test_name}_{timestamp}.json"
    filepath = output_dir / filename

    with open(filepath, "w") as f:
        json.dump(metrics.to_dict(), f, indent=2)

    print(f"\n📁 Benchmark results saved to: {filepath}")
    return filepath


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.2f}µs"
    elif seconds < 1:
        return f"{seconds * 1000:.2f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.2f}s"


def format_memory(bytes_value: float) -> str:
    """Format memory in bytes to human-readable string."""
    if bytes_value < 1024:
        return f"{bytes_value:.2f}B"
    elif bytes_value < 1024**2:
        return f"{bytes_value / 1024:.2f}KB"
    elif bytes_value < 1024**3:
        return f"{bytes_value / (1024**2):.2f}MB"
    else:
        return f"{bytes_value / (1024**3):.2f}GB"
