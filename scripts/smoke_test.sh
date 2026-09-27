#!/usr/bin/env bash
# Smoke test of the local stack (run after scripts/start.sh).
#   1) GET /health (backend directly + through the frontend Next.js proxy)
#   2) Direct VCF-mode analysis of a synthetic VCF (checks local storage →
#      VCFAnalysis → BioGPT → report, without Parabricks or the hg38 reference)
#   3) Optional: FASTQ analysis of a small user-provided dataset
#
# Usage: bash scripts/smoke_test.sh [--fastq R1.fastq.gz R2.fastq.gz] [--timeout SECONDS]
#   FASTQ files must live under $LOCAL_DATA_ROOT (otherwise they are copied there).
# Exit code: 0 if everything is OK, 1 otherwise.
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

FASTQ_R1=""; FASTQ_R2=""; TIMEOUT=1200
while [ $# -gt 0 ]; do
  case "$1" in
    --fastq) FASTQ_R1="${2:-}"; FASTQ_R2="${3:-}"; shift 3 ;;
    --timeout) TIMEOUT="${2:-1200}"; shift 2 ;;
    *) die "Unknown option: $1" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl required"
command -v python3 >/dev/null 2>&1 || die "python3 required (reading JSON responses)"

KO=0
ok() { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$1"; }
ko_msg() { printf '%s[ KO ]%s %s\n' "$C_KO" "$C_RST" "$1"; }
ko() { ko_msg "$1"; KO=$((KO + 1)); }
section() { printf '\n%s== %s ==%s\n' "$C_DIM" "$1" "$C_RST"; }

# json_get '<json>' 'key' → value (empty string if missing).
# The JSON goes through stdin: a FASTQ job response (full statistics) exceeds the 128 KB argv limit.
json_get() {
  printf '%s' "$1" | python3 -c 'import json,sys
try: v = json.load(sys.stdin).get(sys.argv[1], "")
except Exception: v = ""
print(v if not isinstance(v, (dict, list)) else json.dumps(v))' "$2"
}

# Submits a job then waits for it to finish. $1 = label, $2 = endpoint, $3 = JSON body.
# Prints the job_id on stdout if the job ends "completed"; otherwise exit code 1
# (subshell: the caller increments KO).
run_job() {
  local label="$1" endpoint="$2" body="$3" resp job_id status step last="" start
  resp=$(curl -s -X POST "$BACKEND_URL$endpoint" -H 'Content-Type: application/json' -d "$body")
  job_id=$(json_get "$resp" job_id)
  if [ -z "$job_id" ]; then
    ko_msg "$label: submission refused → $resp" >&2
    return 1
  fi
  echo "       job_id=$job_id (timeout ${TIMEOUT}s)" >&2
  start=$(date +%s)
  while :; do
    resp=$(curl -s "$BACKEND_URL/api/v1/jobs/$job_id")
    status=$(json_get "$resp" status)
    step=$(json_get "$resp" progress_message)
    if [ "$step" != "$last" ] && [ -n "$step" ]; then
      echo "       [$status] $step" >&2; last="$step"
    fi
    case "$status" in
      completed) echo "$job_id"; return 0 ;;
      failed) ko_msg "$label: job failed → $(json_get "$resp" error)" >&2; return 1 ;;
    esac
    if [ $(( $(date +%s) - start )) -ge "$TIMEOUT" ]; then
      ko_msg "$label: not finished after ${TIMEOUT}s (last state: $status)" >&2
      return 1
    fi
    sleep 5
  done
}

section "1. Health"
HEALTH=$(curl -sf "$BACKEND_URL/health")
if [ -n "$HEALTH" ] && [ "$(json_get "$HEALTH" status)" = "ok" ]; then
  ok "Backend $BACKEND_URL/health"
  echo "       version=$(json_get "$HEALTH" version)  pipeline=$(json_get "$HEALTH" pipeline_backend)"
  echo "       reason: $(json_get "$HEALTH" pipeline_backend_reason)"
  clinvar=$(json_get "$HEALTH" clinvar)
  case "$clinvar" in
    *'"available": true'*) ok "Local ClinVar release available" ;;
    *) echo "       Local ClinVar missing: the test VCF is pre-annotated; required for real VCFs (download_reference.sh --clinvar-only)" ;;
  esac
else
  ko "Backend unreachable at $BACKEND_URL/health — bash scripts/logs.sh backend"
  echo; die "Stopping: the backend must respond for the rest of the test."
fi
if [ "$(json_get "$(curl -sf "$FRONTEND_URL/health")" status)" = "ok" ]; then
  ok "Frontend → backend proxy ($FRONTEND_URL/health)"
else
  ko "Frontend proxy KO ($FRONTEND_URL/health) — bash scripts/logs.sh frontend"
fi

section "2. Direct VCF analysis (synthetic BRCA1/BRCA2 VCF)"
PATIENT=SMOKE001
IN_DIR="$LOCAL_DATA_ROOT/patients/$PATIENT/input"
mkdir -p "$IN_DIR" && cp "$ROOT_DIR/scripts/testdata/smoke_brca.vcf" "$IN_DIR/smoke_brca.vcf" \
  || die "Cannot write to $IN_DIR — sudo chown -R \$USER: $LOCAL_DATA_ROOT"
VCF_PATH="$IN_DIR/smoke_brca.vcf"
if JOB=$(run_job "VCF" /api/v1/analyze/vcf "{\"patient_id\":\"$PATIENT\",\"vcf_path\":\"$VCF_PATH\"}"); then
  ok "VCF job completed"
  REPORT=$(curl -sf "$BACKEND_URL/api/v1/jobs/$JOB/report")
  if [ -n "$REPORT" ]; then
    ok "Clinical report available ($(printf '%s' "$REPORT" | wc -c) bytes)"
    RISK=$(printf '%s' "$REPORT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["clinical_prediction"]["risk_level"])')
    GENES=$(printf '%s' "$REPORT" | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["genomic_findings"]["identified_pathogenic_genes"]))')
    [ "$RISK" = "HIGH" ] && ok "Risk level HIGH (expected)" || ko "Risk level $RISK (HIGH expected)"
    [ "$GENES" = "BRCA1,BRCA2" ] && ok "Identified genes: $GENES" || ko "Identified genes: '$GENES' (BRCA1,BRCA2 expected)"
  else
    ko "Report not found for job $JOB"
  fi
  if find "$LOCAL_DATA_ROOT/patients/$PATIENT/output" -type f 2>/dev/null | grep -q .; then
    ok "Files written to patients/$PATIENT/output/"
  else
    ko "No file in $LOCAL_DATA_ROOT/patients/$PATIENT/output/"
  fi
else
  KO=$((KO + 1))
fi

section "3. FASTQ analysis (optional)"
if [ -z "$FASTQ_R1" ]; then
  echo "       skipped — rerun with --fastq R1.fastq.gz R2.fastq.gz (requires the hg38 reference)"
else
  [ -f "$FASTQ_R1" ] && [ -f "$FASTQ_R2" ] || die "FASTQ not found: $FASTQ_R1 $FASTQ_R2"
  PATIENT=SMOKEFQ
  IN_DIR="$LOCAL_DATA_ROOT/patients/$PATIENT/input"
  mkdir -p "$IN_DIR"
  R1="$(realpath "$FASTQ_R1")"; R2="$(realpath "$FASTQ_R2")"
  case "$R1" in "$LOCAL_DATA_ROOT"/*) ;; *) cp "$R1" "$IN_DIR/"; R1="$IN_DIR/$(basename "$R1")" ;; esac
  case "$R2" in "$LOCAL_DATA_ROOT"/*) ;; *) cp "$R2" "$IN_DIR/"; R2="$IN_DIR/$(basename "$R2")" ;; esac
  if JOB=$(run_job "FASTQ" /api/v1/analyze \
      "{\"patient_id\":\"$PATIENT\",\"fastq_r1\":\"$R1\",\"fastq_r2\":\"$R2\"}"); then
    ok "FASTQ job completed ($JOB)"
  else
    KO=$((KO + 1))
  fi
fi

echo
if [ "$KO" -eq 0 ]; then
  echo "${C_OK}Smoke test passed.${C_RST}"
  exit 0
fi
echo "${C_KO}$KO check(s) failed.${C_RST}"
exit 1
