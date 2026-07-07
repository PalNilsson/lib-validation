# psutil / logstash_async / stomp.py version validation

Smoke-tests every `default` and `testing` tagged version of psutil,
logstash_async, and stomp.py, across every OS container available via
`setupATLAS -c <os>`, without needing a live ActiveMQ broker. Built for
validating PanDA Pilot 3's grid dependencies across CERN's supported OS
generations and python tiers.

## Requirements

- An lxplus (or similar) node with access to `setupATLAS` and `showVersions`
- `ATLAS_LOCAL_ROOT_BASE` set in your shell (standard on lxplus)
- bash, python3 — nothing beyond the standard library is used by any script
  in this repo

## Quick start

```bash
git clone <this-repo-url> lib-validation
cd lib-validation

# Once per architecture (aarch64, x86_64), from a matching host:
showVersions logstash stomp psutil python | grep -e version -e default -e testing \
    > raw_versions_aarch64.txt
python3 parse_versions.py raw_versions_aarch64.txt > matrix_aarch64.tsv

chmod +x run_validation.sh
./run_validation.sh matrix_aarch64.tsv aarch64
python3 summarize_results.py results_aarch64.jsonl
```

See below for the x86_64 pass, the `pilot-default`/`pilot-testing`
cross-check, and what the output actually means.

For reference: lxplus998 is a host with `aarch64` CPU, and lxplus952 is a host 
with `x86_64` CPU.  

## Files

- `parse_versions.py` — turns raw `showVersions ... | grep -e version -e
  default -e testing` output into a flat `(os, lib, tag, version, native)`
  matrix (TSV).
- `smoke_test.py` — runs one library's minimal smoke test (import +
  a couple of representative calls) and appends a JSON result line. Never
  raises; every exception is caught and recorded.
- `run_validation.sh` — loops over OS containers, feeding each one a
  non-interactive script that runs `lsetup` + `smoke_test.py` for every
  (lib, tag) combo, each in its own subshell so library versions don't leak
  into each other within the same container session.
- `summarize_results.py` — turns `results_<arch>.jsonl` into a pass/fail
  grid plus full failure detail.

## One-time setup

Copy this whole directory under your AFS home, e.g.:

```
~/python/libraries_test/
```

`run_validation.sh` always `cd`s into its own directory before calling
`setupATLAS -c <os>`, because that container binds whatever directory
you're currently in to `/srv` inside it (confirmed from the `apptainer
exec ... -B .../libraries_test:/srv ...` line it prints on startup — this
tracks your cwd, not a fixed path). That's what makes `smoke_test.py`
visible inside every container without you needing to hand-edit any paths.
Just make sure all five files stay together in one directory.

## Running a pass (per architecture)

`showVersions` only lists builds for the architecture of the host you run
it from, so do this once per architecture (aarch64, x86_64), from a
matching host, *outside* any container. Include `python` in the same call —
its versions go into the same matrix and get looked up automatically:

```bash
showVersions logstash stomp psutil python | grep -e version -e default -e testing \
    > raw_versions_aarch64.txt

python3 parse_versions.py raw_versions_aarch64.txt --table   # sanity check
python3 parse_versions.py raw_versions_aarch64.txt > matrix_aarch64.tsv

./run_validation.sh matrix_aarch64.tsv aarch64          # all OSes in the matrix
# or restrict explicitly:
./run_validation.sh matrix_aarch64.tsv aarch64 el9 centos8 el10
```

Repeat on an x86_64 node for the x86_64 pass — same commands, different
`raw_versions_x86_64.txt` / `matrix_x86_64.tsv` / `results_x86_64.jsonl`.
Don't mix architectures: a `showVersions python` run from an x86_64 host
only lists `x86_64` version strings, which won't set up inside an aarch64
container (same rejection you'd get from a wrong-OS version string).

## Cross-checking a library tier against a different python

To confirm a claim like "the testing-tier libraries also work under
`pilot-default` python, not just `pilot-testing`", three environment
variables let you re-pair without touching the matrix or clobbering the
canonical results:

```bash
LIB_TAG_FILTER=testing PYTHON_TAG_OVERRIDE=default \
RESULT_TAG_LABEL=testing_under_pilot_default \
    ./run_validation.sh matrix_aarch64.tsv aarch64 el9 centos8 el10
```

- `LIB_TAG_FILTER` restricts to rows whose own tag matches (here, only the
  `testing`-tier library versions run).
- `PYTHON_TAG_OVERRIDE` pairs them with a different python tag (here,
  `pilot-default` instead of `pilot-testing`).
- `RESULT_TAG_LABEL` records the outcome under a distinct tag, so it shows
  up as its own `psutil/testing_under_pilot_default`-style column in
  `summarize_results.py` instead of overwriting the real `testing` result.

Leave all three unset for normal runs — nothing changes unless you set them.

## Reading results

```bash
python3 summarize_results.py results_aarch64.jsonl
```

Prints a grid like:

```
OS      logstash/default logstash/testing psutil/default ...
el10    PASS*            PASS             PASS*          ...
```

`*` marks a `default`-tier version on el10 that's actually the el9 build
reused as a fallback (no native el10 default build exists yet for that
package) — not a bug, just worth knowing it's not el10-native.

Failures print with their exception type/message; `results_<arch>.jsonl`
keeps the full traceback for anything that needs deeper digging.

Since `results_<arch>.jsonl` is append-only, re-running `run_validation.sh`
against the same file while you iterate is normal — `summarize_results.py`
automatically shows only the latest result per `(os, lib, tag)`, so older
attempts (including pre-fix failures from earlier in a debugging session)
don't linger in the report. Pass `--history` if you actually want to see
every past attempt for comparison.

## Known limitations

- Each (lib, tag) test pins its own matching interpreter first, resolved to
  an explicit version string from the *same* matrix file (e.g.
  `lsetup "python 3.12.13-aarch64-el9"`), not a bare tag. This matters
  because `psutil`/`logstash`/`stomp`'s `default`/`testing` builds are
  meant to pair with the pilot's own `pilot-default`/`pilot-testing`
  python — which is a **different version** from whatever `recommended` or
  the container's ambient system python happens to be. Bare
  `lsetup "python default"` / `lsetup "python testing"` turned out not to
  be a safe shortcut here: `default` resolved to the el9 build even inside
  other OS containers (and was correctly rejected as built for the wrong
  OS), and `testing` isn't even a registered tag for python at all — only
  `pilot-testing` is. Explicit per-OS version strings sidestep all of that.
  If a (os, tag) pair has no python row in the matrix, that combo is
  skipped with a clear message in the log rather than silently running
  against whatever python was already active. `summarize_results.py` also
  prints which interpreter each (os, tag) group actually ran under, and
  flags it if that's inconsistent across libraries in the same group.
- The stomp smoke test never calls `.connect()` — no broker is assumed to
  be reachable from inside these containers. It only verifies import and
  `Connection(...)` construction, which is enough to catch constructor/API
  breakage but not connection-lifecycle regressions. That's still covered
  by the separate live-broker integration tests.
- `centos7`/SL7 rows only appear for packages that actually publish an
  SL7-tagged build (currently just logstash's legacy 2.3.0) — psutil and
  stomp have no SL7 coverage at all in the parsed matrix, which is correct,
  not a parsing gap.
 