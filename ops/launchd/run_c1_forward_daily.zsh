#!/bin/zsh

set -u
umask 077

readonly REPO_DIR="/Users/erenakpinar/AI_Hedge_Fund-c1-forward-minimum"
readonly PYTHON="/Users/erenakpinar/AI_Hedge_Fund/.venv-backtest/bin/python"
readonly FORWARD_SCRIPT="${REPO_DIR}/c1_forward.py"
readonly STATE_DIR="/Users/erenakpinar/Library/Application Support/AI_Hedge_Fund/c1-forward"
readonly LOG_DIR="/Users/erenakpinar/Library/Logs/AI_Hedge_Fund/c1-forward"
readonly LEDGER="${STATE_DIR}/c1-forward.jsonl"

/bin/mkdir -p "${STATE_DIR}" "${LOG_DIR}" || exit 1

readonly RUN_DAY="$(/bin/date +%Y-%m-%d)"
readonly LOG_PATH="${LOG_DIR}/${RUN_DAY}.log"

# Preserve launchd's stderr while the full run is redirected to its dated log.
exec 3>&2
{
    printf '[%s] START c1_forward end_exclusive=%s\n' \
        "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)" "${RUN_DAY}"

    cd "${REPO_DIR}"
    program_start="$("${PYTHON}" -c 'from c1_forward import PROGRAM_START; print(PROGRAM_START)')"
    exit_code=$?
    if (( exit_code != 0 )); then
        message="C1_FORWARD_ERROR status=${exit_code} unable to read PROGRAM_START log=${LOG_PATH}"
        printf '[%s] %s\n' "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)" "${message}"
        printf '%s\n' "${message}" >&3
        exit "${exit_code}"
    fi

    if [[ "${RUN_DAY}" > "${program_start}" ]]; then
        "${PYTHON}" "${FORWARD_SCRIPT}" \
            --ledger "${LEDGER}" \
            --end-exclusive "${RUN_DAY}"
        exit_code=$?
        if (( exit_code != 0 )); then
            message="C1_FORWARD_ERROR status=${exit_code} log=${LOG_PATH}"
            printf '[%s] %s\n' "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)" "${message}"
            printf '%s\n' "${message}" >&3
            exit "${exit_code}"
        fi
        printf '[%s] SUCCESS c1_forward\n' "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    else
        printf '[%s] SKIP no completed session after program_start=%s\n' \
            "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)" "${program_start}"
    fi
} >>"${LOG_PATH}" 2>&1
