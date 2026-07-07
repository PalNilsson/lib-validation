#!/usr/bin/env bash
#
# run_validation.sh -- drive psutil / logstash / stomp smoke tests across
# ATLAS OS containers (setupATLAS -c <os>), for every (lib, tag) combination
# found in a matrix TSV produced by parse_versions.py.
#
# Must be run from an lxplus (or similar) node with access to setupATLAS.
# This script itself runs OUTSIDE any container; it only feeds commands
# into each container's non-interactive bash via stdin.
#
# Usage:
#   ./run_validation.sh <matrix.tsv> <arch> [os1 os2 ...]
#
# Example:
#   python3 parse_versions.py raw_versions_aarch64.txt > matrix_aarch64.tsv
#   ./run_validation.sh matrix_aarch64.tsv aarch64 centos8 el9 el10
#
# If no OS args are given, every OS present in the matrix is tested.
#
# Optional env vars, for cross-pairing a library tier against a DIFFERENT
# python tier than it's normally paired with (e.g. "does the testing-tier
# library also work under pilot-default python, not just pilot-testing?"):
#
#   LIB_TAG_FILTER=testing        only run rows whose own tag matches this
#   PYTHON_TAG_OVERRIDE=default   pair with this python tag instead of the
#                                 row's own tag
#   RESULT_TAG_LABEL=testing_py311   record results under this tag instead
#                                 of the row's own tag, so it lands in its
#                                 own report column rather than overwriting
#                                 the canonical result for that tag
#
# Example -- confirm testing-tier libs also work under pilot-default python:
#   LIB_TAG_FILTER=testing PYTHON_TAG_OVERRIDE=default \
#   RESULT_TAG_LABEL=testing_under_pilot_default \
#       ./run_validation.sh matrix_aarch64.tsv aarch64 el9 centos8 el10

set -uo pipefail  # deliberately no -e: one failed combo must not abort the run

HOST_WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRIX_FILE="${1:?usage: run_validation.sh <matrix.tsv> <arch> [os...]}"
ARCH="${2:?usage: run_validation.sh <matrix.tsv> <arch> [os...]}"
shift 2

LIB_TAG_FILTER="${LIB_TAG_FILTER:-}"
PYTHON_TAG_OVERRIDE="${PYTHON_TAG_OVERRIDE:-}"
RESULT_TAG_LABEL="${RESULT_TAG_LABEL:-}"

if [[ ! -f "$MATRIX_FILE" ]]; then
    echo "Matrix file not found: $MATRIX_FILE" >&2
    exit 1
fi
MATRIX_FILE="$(cd "$(dirname "$MATRIX_FILE")" && pwd)/$(basename "$MATRIX_FILE")"

# --- Path translation: host <-> inside-container ---------------------------
# `setupATLAS -c <os>` bind-mounts whatever directory you're *currently in*
# to /srv inside the container (confirmed from the `apptainer exec ... -B
# .../python/libraries_test:/srv ...` line it prints on startup -- this path
# tracked your cwd, it wasn't a fixed username/letter-bucket path). So
# rather than guessing your AFS layout, we just cd into this script's own
# directory before ever invoking setupATLAS, guaranteeing /srv inside the
# container always points at this directory.
cd "$HOST_WORKDIR" || { echo "Cannot cd to ${HOST_WORKDIR}" >&2; exit 1; }
CONTAINER_WORKDIR="/srv"

RESULTS_FILE_HOST="${HOST_WORKDIR}/results_${ARCH}.jsonl"
RESULTS_FILE_CONTAINER="${CONTAINER_WORKDIR}/results_${ARCH}.jsonl"
SMOKE_SCRIPT_CONTAINER="${CONTAINER_WORKDIR}/smoke_test.py"

# --- Which OSes to run -------------------------------------------------
if [[ $# -gt 0 ]]; then
    OS_LIST=("$@")
else
    mapfile -t OS_LIST < <(cut -f1 "$MATRIX_FILE" | sort -u)
fi

echo "Arch:            ${ARCH}"
echo "Matrix file:     ${MATRIX_FILE}"
echo "OSes to test:    ${OS_LIST[*]}"
echo "Results file:    ${RESULTS_FILE_HOST}"
if [[ -n "$LIB_TAG_FILTER" || -n "$PYTHON_TAG_OVERRIDE" || -n "$RESULT_TAG_LABEL" ]]; then
    echo "Cross-pairing:   lib tag filter=${LIB_TAG_FILTER:-<all>}, python tag override=${PYTHON_TAG_OVERRIDE:-<none>}, result label=${RESULT_TAG_LABEL:-<row default>}"
fi
echo

for os in "${OS_LIST[@]}"; do
    # python rows describe the companion interpreter only -- they're looked
    # up per (os, tag) below, not smoke-tested themselves via smoke_test.py.
    if [[ -n "$LIB_TAG_FILTER" ]]; then
        rows="$(awk -F'\t' -v os="$os" -v tag="$LIB_TAG_FILTER" \
            '$1==os && $2!="python" && $3==tag' "$MATRIX_FILE")"
    else
        rows="$(awk -F'\t' -v os="$os" '$1==os && $2!="python"' "$MATRIX_FILE")"
    fi
    if [[ -z "$rows" ]]; then
        echo "=== ${os}: no matrix rows, skipping ==="
        continue
    fi

    echo "=== Container: ${os} ==="
    inner_script="$(mktemp)"
    {
        echo "shopt -s expand_aliases"
        echo "set +euo pipefail"
        # lsetup is normally auto-defined only for interactive shells at the
        # Apptainer> prompt (almost certainly gated on something like
        # '[ -n "$PS1" ]' in the container's rc). Our piped-in script is
        # non-interactive, so that auto-source never runs and lsetup is
        # simply undefined -- re-source it explicitly. apptainer exec uses
        # --cleanenv, so $ATLAS_LOCAL_ROOT_BASE won't survive into the
        # container; embed the already-resolved host-side value instead.
        echo "export ATLAS_LOCAL_ROOT_BASE=\"${ATLAS_LOCAL_ROOT_BASE}\""
        echo "source \"${ATLAS_LOCAL_ROOT_BASE}/user/atlasLocalSetup.sh\" --quiet"
        while IFS=$'\t' read -r row_os lib tag version native; do
            # Resolve the exact companion python version from the SAME
            # matrix file rather than passing a bare tag -- "python default"
            # / "python testing" turned out not to be OS-aware at all (it
            # resolved to the el9 build even inside other containers, and
            # "testing" isn't even a registered tag for python -- only
            # "pilot-testing" is). Explicit resolved versions avoid all of
            # that ambiguity, exactly like we already do for the libraries.
            python_lookup_tag="${PYTHON_TAG_OVERRIDE:-$tag}"
            result_tag="${RESULT_TAG_LABEL:-$tag}"
            pyver="$(awk -F'\t' -v os="$row_os" -v tag="$python_lookup_tag" \
                '$1==os && $2=="python" && $3==tag {print $4}' "$MATRIX_FILE")"
            if [[ -z "$pyver" ]]; then
                cat <<EOF
echo "SKIP: no python ${python_lookup_tag} version found for ${row_os} in matrix -- skipping ${lib} ${result_tag}"
EOF
                continue
            fi
            cat <<EOF
(
  lsetup "python ${pyver}"
  lsetup "${lib} ${version}"
  python3 "${SMOKE_SCRIPT_CONTAINER}" --lib ${lib} --tag ${result_tag} \\
      --version "${version}" --os ${row_os} --arch ${ARCH} \\
      --native ${native} --results "${RESULTS_FILE_CONTAINER}"
)
EOF
        done <<< "$rows"
    } > "$inner_script"

    container_log="${HOST_WORKDIR}/container_${os}_${ARCH}.log"

    # setupATLAS is a *shell alias* (`alias setupATLAS='source
    # ${ATLAS_LOCAL_ROOT_BASE}/user/atlasLocalSetup.sh'`), not a function or
    # executable -- aliases are never visible to a script run as its own
    # process, so we can't call `setupATLAS` here directly. Instead we
    # reproduce exactly what the alias expands to.
    if [[ -z "${ATLAS_LOCAL_ROOT_BASE:-}" ]]; then
        echo "ATLAS_LOCAL_ROOT_BASE is not set in this shell -- cannot set up" >&2
        echo "the ATLAS environment non-interactively. Run 'echo \$ATLAS_LOCAL_ROOT_BASE'" >&2
        echo "in your normal login shell and make sure it's exported, then retry." >&2
        rm -f "$inner_script"
        continue
    fi
    ( set +euo pipefail; source "${ATLAS_LOCAL_ROOT_BASE}/user/atlasLocalSetup.sh" -c "$os" ) \
        < "$inner_script" > "$container_log" 2>&1
    rm -f "$inner_script"
    echo "    log: ${container_log}"
done

echo
echo "Done. Run: python3 summarize_results.py ${RESULTS_FILE_HOST}"
