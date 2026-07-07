#!/usr/bin/env python3
"""Minimal, exception-safe smoke tests for psutil, logstash_async, and stomp.py.

Run once per (library, tag, version, os) combination, typically from inside
an ATLAS setupATLAS -c <os> container after the matching `lsetup` call has
already been made. Never raises: every outcome (success, exception, or
skip) is captured and appended as one JSON line to the results file, and
also echoed to stdout for live feedback while the driver script runs.

Usage:
    python3 smoke_test.py --lib psutil --tag default \\
        --version 6.0.0-aarch64-el9 --os el9 --arch aarch64 \\
        --results results.jsonl
"""

import argparse
import json
import platform
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _ok(detail):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {"status": "pass", "detail": detail}


def _fail(exc, detail=None):
    # type: (BaseException, Optional[Dict[str, Any]]) -> Dict[str, Any]
    return {
        "status": "fail",
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(limit=6),
        "detail": detail or {},
    }


def smoke_psutil():
    # type: () -> Dict[str, Any]
    """Import psutil and exercise a few commonly used, low-risk calls."""
    try:
        import psutil  # pylint: disable=import-outside-toplevel

        cpu_pct = psutil.cpu_percent(interval=0.1)
        vmem = psutil.virtual_memory()
        proc = psutil.Process()
        proc_info = proc.as_dict(attrs=["pid", "name", "status"])
        return _ok(
            {
                "psutil_version": getattr(psutil, "__version__", "unknown"),
                "cpu_percent": cpu_pct,
                "virtual_memory_percent": vmem.percent,
                "process_info": proc_info,
            }
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _fail(exc)


def smoke_logstash():
    # type: () -> Dict[str, Any]
    """Import logstash_async and instantiate the async handler.

    Does not attempt to actually deliver a log record over the network;
    only verifies that import and construction succeed, then tears the
    handler down cleanly.
    """
    handler = None
    try:
        import logstash_async  # pylint: disable=import-outside-toplevel
        from logstash_async.handler import (  # pylint: disable=import-outside-toplevel
            AsynchronousLogstashHandler,
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            db_path = tmp_db.name

        handler = AsynchronousLogstashHandler(
            host="localhost",
            port=5959,
            database_path=db_path,
        )
        record_ok = True
        try:
            import logging  # pylint: disable=import-outside-toplevel

            logger = logging.getLogger("smoke_test.logstash")
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            logger.info("smoke test log record", extra={"smoke_test": True})
        except Exception:  # pylint: disable=broad-except
            record_ok = False
        return _ok(
            {
                "logstash_async_version": getattr(
                    logstash_async, "__version__", "unknown"
                ),
                "handler_constructed": True,
                "log_call_completed": record_ok,
            }
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _fail(exc)
    finally:
        if handler is not None:
            try:
                handler.close()
            except Exception:  # pylint: disable=broad-except
                pass


def smoke_stomp():
    # type: () -> Dict[str, Any]
    """Import stomp.py and construct a Connection object without connecting.

    Deliberately does not call .connect() since no live broker is
    available in this validation pass; a constructor/API failure here
    still indicates a breaking change worth flagging.
    """
    try:
        import stomp  # pylint: disable=import-outside-toplevel

        raw_version = getattr(stomp, "__version__", None) or getattr(
            stomp, "VERSION", None
        )
        if isinstance(raw_version, tuple):
            version_str = ".".join(str(part) for part in raw_version)
        else:
            version_str = str(raw_version)

        conn = stomp.Connection([("localhost", 61613)])
        expected_methods = ["connect", "disconnect", "send", "subscribe"]
        missing = [m for m in expected_methods if not hasattr(conn, m)]
        if missing:
            raise AttributeError(f"Connection missing expected methods: {missing}")

        return _ok(
            {
                "stomp_version": version_str,
                "connection_constructed": True,
                "expected_methods_present": True,
            }
        )
    except Exception as exc:  # pylint: disable=broad-except
        return _fail(exc)


SMOKE_FUNCS = {
    "psutil": smoke_psutil,
    "logstash": smoke_logstash,
    "stomp": smoke_stomp,
}


def main() -> int:
    """Parse arguments, run the requested smoke test, and record the result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lib", required=True, choices=sorted(SMOKE_FUNCS))
    parser.add_argument("--tag", required=True, help="e.g. default, testing")
    parser.add_argument("--version", required=True, help="resolved lsetup version string")
    parser.add_argument("--os", required=True, help="e.g. el9, centos8, el10")
    parser.add_argument("--arch", required=True, help="e.g. aarch64, x86_64")
    parser.add_argument("--results", required=True, help="path to results JSONL file")
    parser.add_argument(
        "--native",
        default="unknown",
        help="whether this version is a native build for --os, or a fallback",
    )
    args = parser.parse_args()

    outcome = SMOKE_FUNCS[args.lib]()

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os": args.os,
        "arch": args.arch,
        "lib": args.lib,
        "tag": args.tag,
        "version": args.version,
        "native": args.native,
        "python_version": platform.python_version(),
        **outcome,
    }

    with open(args.results, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    status = record["status"].upper()
    print(f"[{status}] {args.os}/{args.lib}/{args.tag} ({args.version}) py{record['python_version']}")
    if record["status"] == "fail":
        print(f"    {record['error_type']}: {record['error_message']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
