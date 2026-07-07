#!/usr/bin/env python3
"""Parse `showVersions <libs> | grep -e version -e default -e testing` output.

Turns ATLAS/CVMFS tag output like:

    psutil versions;
     --> 6.0.0-aarch64-el9           default            default-SL10  default-SL9
     --> 7.2.2-aarch64-el9           testing            testing-SL9

into a flat table of (os, lib, tag, version, native) rows, where `os` is the
label you'd pass to `setupATLAS -c <os>`, `tag` is "default" or "testing",
and `native` indicates whether the resolved version was actually built for
that OS or is a fallback reused from another OS generation's build (this
happens when a newer OS generation has no default-tier build yet).

Usage:
    python3 parse_versions.py raw_versions.txt            # TSV (default)
    python3 parse_versions.py raw_versions.txt --table    # human-readable
    showVersions psutil stomp logstash | grep -e version -e default -e testing \\
        | python3 parse_versions.py -                     # read from stdin
"""

import argparse
import re
import sys
from typing import Dict, Iterable, List, Optional, Tuple

# SL<N> is CVMFS/ATLAS's internal shorthand for an OS generation. Map each
# to the OS label actually accepted by `setupATLAS -c <os>` / embedded in
# version strings.
SL_TO_OS = {
    "SL7": "centos7",
    "SL8": "centos8",
    "SL9": "el9",
    "SL10": "el10",
}
TAGS_OF_INTEREST = ("default", "testing")

# python doesn't use bare "default"/"testing" tags at all -- its tags are
# "pilot-default"/"pilot-testing" (the pilot's own required interpreter,
# separate from the generic "recommended" system python). Everything else
# about the tagging scheme (SL<N> suffixes, bare-alias-only-means-SL9) is
# identical, so we just need the right prefix per package.
PACKAGE_TAG_PREFIX = {
    "python": "pilot-",
}

SECTION_RE = re.compile(r"^(\S+)\s+versions;\s*$")
ENTRY_RE = re.compile(r"^-->\s+(\S+)\s+(.*)$")


def parse(lines):
    # type: (Iterable[str]) -> Dict[str, Dict[str, str]]
    """Return {package: {tag: version}} from raw showVersions grep output."""
    tag_map = {}  # type: Dict[str, Dict[str, str]]
    current_pkg = None  # type: Optional[str]

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        section_match = SECTION_RE.match(line.strip())
        if section_match:
            current_pkg = section_match.group(1)
            tag_map.setdefault(current_pkg, {})
            continue

        entry_match = ENTRY_RE.match(line.strip())
        if entry_match and current_pkg is not None:
            version, rest = entry_match.groups()
            for tag in rest.split():
                tag_map[current_pkg][tag] = version

    return tag_map


def build_matrix(tag_map):
    # type: (Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str, str, bool]]
    """Flatten {package: {tag: version}} into (os, lib, tag, version, native) rows."""
    rows = []  # type: List[Tuple[str, str, str, str, bool]]

    for pkg, tags in tag_map.items():
        prefix = PACKAGE_TAG_PREFIX.get(pkg, "")
        for sl_label, os_label in SL_TO_OS.items():
            for tag in TAGS_OF_INTEREST:
                # Only trust explicit "<prefix><tag>-SL<N>" tags. The bare
                # alias (no suffix) is just a duplicate pointer at whichever
                # SL number is currently primary (SL9/el9 today) -- using it
                # for *other* OS generations invents coverage that doesn't
                # exist (confirmed the hard way: "python default" resolved
                # to the el9 build even inside a centos8 container and was
                # rejected as built for the wrong OS).
                version = tags.get(f"{prefix}{tag}-{sl_label}")
                if version is None:
                    continue
                native = os_label in version
                rows.append((os_label, pkg, tag, version, native))

    return rows


def print_tsv(rows):
    # type: (List[Tuple[str, str, str, str, bool]]) -> None
    for os_label, pkg, tag, version, native in sorted(rows):
        print(f"{os_label}\t{pkg}\t{tag}\t{version}\t{str(native).lower()}")


def print_table(rows):
    # type: (List[Tuple[str, str, str, str, bool]]) -> None
    headers = ("OS", "LIB", "TAG", "VERSION", "NATIVE")
    widths = [len(h) for h in headers]
    str_rows = [(o, l, t, v, "yes" if n else "no (fallback)") for o, l, t, v, n in sorted(rows)]
    for row in str_rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in str_rows:
        print(fmt.format(*row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        help="path to raw showVersions grep output, or '-' for stdin",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="print a human-readable table instead of TSV",
    )
    args = parser.parse_args()

    if args.input == "-":
        lines = sys.stdin.readlines()
    else:
        with open(args.input, encoding="utf-8") as fh:
            lines = fh.readlines()

    tag_map = parse(lines)
    rows = build_matrix(tag_map)

    if not rows:
        print("No (os, lib, tag, version) rows found — check input format.", file=sys.stderr)
        return 1

    if args.table:
        print_table(rows)
    else:
        print_tsv(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
