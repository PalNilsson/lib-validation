#!/usr/bin/env python3
"""Summarize a results.jsonl file produced by smoke_test.py.

Usage:
    python3 summarize_results.py results_aarch64.jsonl
"""

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Dict, List


def dedup_latest(records):
    # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Keep only the most recent record per (os, lib, tag).

    results_<arch>.jsonl is append-only, and re-running run_validation.sh
    against the same file while iterating is completely normal -- every
    past attempt (including ones from before a bug was fixed) stays in the
    file. JSONL lines are written strictly in chronological order (each
    container runs its combos sequentially, one container at a time), so
    the last occurrence of a given (os, lib, tag) in the file is always its
    most recent result.
    """
    latest = {}  # type: Dict[Any, Dict[str, Any]]
    for rec in records:
        key = (rec.get("os"), rec.get("lib"), rec.get("tag"))
        latest[key] = rec  # later occurrences overwrite earlier ones
    return list(latest.values())


def load_results(path):
    # type: (str) -> List[Dict[str, Any]]
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def print_matrix(records):
    # type: (List[Dict[str, Any]]) -> None
    """Print an OS x (lib/tag) pass/fail grid."""
    by_os = defaultdict(dict)  # type: Dict[str, Dict[str, str]]
    columns = []  # type: List[str]
    for rec in records:
        col = f"{rec['lib']}/{rec['tag']}"
        if col not in columns:
            columns.append(col)
        mark = "PASS" if rec["status"] == "pass" else "FAIL"
        if not rec.get("native", True) in (True, "true"):
            mark += "*"  # flag fallback/non-native builds
        by_os[rec["os"]][col] = mark

    col_width = max([len(c) for c in columns] + [4]) + 1
    os_width = max([len(o) for o in by_os] + [len("OS")]) + 1

    header = "OS".ljust(os_width) + "".join(c.ljust(col_width) for c in columns)
    print(header)
    print("-" * len(header))
    for os_name in sorted(by_os):
        row = os_name.ljust(os_width)
        row += "".join(by_os[os_name].get(c, "-").ljust(col_width) for c in columns)
        print(row)
    print("\n* = fallback build reused from another OS generation (not native)")


def print_failures(records):
    # type: (List[Dict[str, Any]]) -> None
    failures = [r for r in records if r["status"] == "fail"]
    if not failures:
        print("\nNo failures. 🎉")
        return

    print(f"\n{len(failures)} failure(s):\n")
    for rec in failures:
        pyver = rec.get("python_version", "?")
        print(
            f"  [{rec['os']}] {rec['lib']} ({rec['tag']}, {rec['version']}, py{pyver}): "
            f"{rec.get('error_type', '?')}: {rec.get('error_message', '?')}"
        )


def print_python_versions(records):
    # type: (List[Dict[str, Any]]) -> None
    """Print which interpreter each (os, tag) combo actually ran under.

    Every row in a given (os, tag) group should show exactly one python
    version -- if you see more than one, `lsetup "python <tag>"` resolved
    inconsistently across libraries in that group, which is worth digging
    into.
    """
    seen = defaultdict(set)  # type: Dict[Any, set]
    for rec in records:
        seen[(rec["os"], rec["tag"])].add(rec.get("python_version", "?"))

    print("\nPython interpreter actually used per (os, tag):")
    for key in sorted(seen):
        os_name, tag = key
        versions = seen[key]
        flag = "  <-- inconsistent!" if len(versions) > 1 else ""
        print(f"  {os_name}/{tag}: {', '.join(sorted(versions))}{flag}")


def print_counts(records):
    # type: (List[Dict[str, Any]]) -> None
    total = len(records)
    passed = sum(1 for r in records if r["status"] == "pass")
    failed = total - passed
    print(f"\n{passed}/{total} passed, {failed}/{total} failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_file")
    parser.add_argument(
        "--json", action="store_true", help="dump raw failing records as JSON"
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="show every historical attempt instead of only the latest "
        "result per (os, lib, tag) -- useful for seeing how a combo's "
        "result changed across reruns, but noisy for a normal status check",
    )
    args = parser.parse_args()

    try:
        all_records = load_results(args.results_file)
    except FileNotFoundError:
        print(f"No such file: {args.results_file}", file=sys.stderr)
        return 1

    if not all_records:
        print("No results recorded yet.")
        return 0

    if args.history:
        records = all_records
    else:
        records = dedup_latest(all_records)
        n_dropped = len(all_records) - len(records)
        if n_dropped:
            print(
                f"({n_dropped} older record(s) from previous reruns "
                f"superseded -- showing latest per (os, lib, tag); "
                f"pass --history to see everything)\n"
            )

    print_matrix(records)
    print_python_versions(records)
    print_failures(records)
    print_counts(records)

    if args.json:
        failures = [r for r in records if r["status"] == "fail"]
        print("\n" + json.dumps(failures, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
