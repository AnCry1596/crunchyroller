"""
tests/test_runner.py

Unified test runner for Crunchyroller multi-tier requirement-driven test suite.
Supports Tier-based filtering, verbose output, structured JSON reporting, and timing metrics.
"""

import argparse
import json
import os
import sys
import time
import unittest
from typing import Any, Dict, List, Optional


class StructuredTestResult(unittest.TextTestResult):
    """Custom TestResult collecting detailed metrics per test case and tier."""

    def __init__(self, stream: Any, descriptions: bool, verbosity: int):
        super().__init__(stream, descriptions, verbosity)
        self.test_records: List[Dict[str, Any]] = []
        self._test_start_time: float = 0.0

    def startTest(self, test: unittest.TestCase) -> None:
        self._test_start_time = time.time()
        super().startTest(test)

    def addSuccess(self, test: unittest.TestCase) -> None:
        elapsed = time.time() - self._test_start_time
        self.test_records.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "module": test.__class__.__module__,
            "status": "PASSED",
            "duration": round(elapsed, 4),
            "message": "",
        })
        super().addSuccess(test)

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        elapsed = time.time() - self._test_start_time
        msg = self._exc_info_to_string(err, test)
        self.test_records.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "module": test.__class__.__module__,
            "status": "FAILED",
            "duration": round(elapsed, 4),
            "message": msg,
        })
        super().addFailure(test, err)

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        elapsed = time.time() - self._test_start_time
        msg = self._exc_info_to_string(err, test)
        self.test_records.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "module": test.__class__.__module__,
            "status": "ERROR",
            "duration": round(elapsed, 4),
            "message": msg,
        })
        super().addError(test, err)

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        elapsed = time.time() - self._test_start_time
        self.test_records.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "module": test.__class__.__module__,
            "status": "SKIPPED",
            "duration": round(elapsed, 4),
            "message": reason,
        })
        super().addSkip(test, reason)


class StructuredTestRunner(unittest.TextTestRunner):
    """Test runner using StructuredTestResult."""

    resultclass = StructuredTestResult

    def run(self, test: unittest.TestSuite) -> StructuredTestResult:  # type: ignore
        return super().run(test)  # type: ignore


def run_tier_suite(
    tiers: List[str],
    verbosity: int = 2,
    failfast: bool = False,
    json_report_path: Optional[str] = None,
) -> bool:
    """Executes specified test tiers and prints summary table."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    tier_file_map = {
        "1": "test_tier1_features.py",
        "2": "test_tier2_boundaries.py",
        "3": "test_tier3_combinations.py",
        "4": "test_tier4_scenarios.py",
        "5": "test_tier5_adversarial.py",
    }

    tests_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(tests_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    selected_files = []
    for t in tiers:
        t_key = str(t).lower().replace("tier", "").strip()
        if t_key in tier_file_map:
            selected_files.append(tier_file_map[t_key])
        elif t_key == "all":
            selected_files.extend(list(tier_file_map.values()))

    # Deduplicate while preserving order
    selected_files = list(dict.fromkeys(selected_files))
    if not selected_files:
        selected_files = list(tier_file_map.values())

    print("=" * 70)
    print(f"Crunchyroller E2E Test Suite Runner")
    print(f"Executing Tiers: {', '.join(tiers)} ({len(selected_files)} test files)")
    print("=" * 70)

    for filename in selected_files:
        file_path = os.path.join(tests_dir, filename)
        if os.path.exists(file_path):
            tier_suite = loader.discover(start_dir=tests_dir, pattern=filename, top_level_dir=project_root)
            suite.addTests(tier_suite)

    start_wall = time.time()
    runner = StructuredTestRunner(verbosity=verbosity, failfast=failfast)
    result = runner.run(suite)
    total_duration = time.time() - start_wall

    total_runs = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total_runs - failures - errors - skipped

    print("\n" + "=" * 70)
    print("TEST SUITE EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  Total Tests Executed : {total_runs}")
    print(f"  Passed               : {passed}")
    print(f"  Failed               : {failures}")
    print(f"  Errors               : {errors}")
    print(f"  Skipped              : {skipped}")
    print(f"  Total Duration       : {total_duration:.2f}s")
    success_rate = (passed / total_runs * 100) if total_runs > 0 else 0
    print(f"  Success Rate         : {success_rate:.1f}%")
    print("=" * 70)

    if json_report_path:
        report = {
            "summary": {
                "total": total_runs,
                "passed": passed,
                "failed": failures,
                "errors": errors,
                "skipped": skipped,
                "duration": total_duration,
                "success_rate": success_rate,
            },
            "tests": result.test_records,
        }
        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Structured JSON report written to: {json_report_path}")

    return result.wasSuccessful()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crunchyroller Unified Test Suite Runner")
    parser.add_argument(
        "--tier",
        type=str,
        default="all",
        help="Tier to execute (1, 2, 3, 4, 5, or all)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")
    parser.add_argument("-f", "--failfast", action="store_true", help="Stop on first failure")
    parser.add_argument("--json-report", type=str, default="", help="Path to write JSON test report")

    args = parser.parse_args()
    tier_list = [t.strip() for t in args.tier.split(",") if t.strip()]

    success = run_tier_suite(
        tiers=tier_list,
        verbosity=2 if args.verbose else 1,
        failfast=args.failfast,
        json_report_path=args.json_report or None,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
