#!/usr/bin/env bash
# Test de fumée de la stack locale (à lancer après scripts/start.sh).
#   1) GET /health (backend direct + via le proxy Next.js du frontend)
#   2) Analyse en mode VCF direct sur un VCF synthétique (valide stockage local →
#      VCFAnalysis → BioGPT → rapport, sans Parabricks ni référence hg38)
#   3) Optionnel : analyse FASTQ sur un petit jeu fourni par l'utilisateur
#
# Usage : bash scripts/smoke_test.sh [--fastq R1.fastq.gz R2.fastq.gz] [--timeout SECONDES]
#   Les FASTQ doivent se trouver sous $LOCAL_DATA_ROOT (ils y sont copiés sinon).
# Code retour : 0 si tout est OK, 1 sinon.
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

FASTQ_R1=""; FASTQ_R2=""; TIMEOUT=1200
while [ $# -gt 0 ]; do
  case "$1" in
    --fastq) FASTQ_R1="${2:-}"; FASTQ_R2="${3:-}"; shift 3 ;;
    --timeout) TIMEOUT="${2:-1200}"; shift 2 ;;
    *) die "Option inconnue : $1" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl requis"
command -v python3 >/dev/null 2>&1 || die "python3 requis (lecture des réponses JSON)"

KO=0
ok() { printf '%s[ OK ]%s %s\n' "$C_OK" "$C_RST" "$1"; }
ko_msg() { printf '%s[ KO ]%s %s\n' "$C_KO" "$C_RST" "$1"; }
ko() { ko_msg "$1"; KO=$((KO + 1)); }
section() { printf '\n%s== %s ==%s\n' "$C_DIM" "$1" "$C_RST"; }

# json_get '<json>' 'clé' → valeur (chaîne vide si absente)
json_get() {
  python3 -c 'import json,sys
try: v = json.loads(sys.argv[1]).get(sys.argv[2], "")
except Exception: v = ""
print(v if not isinstance(v, (dict, list)) else json.dumps(v))' "$1" "$2"
}

# Soumet un job puis attend sa fin. $1 = libellé, $2 = endpoint, $3 = corps JSON.
# Affiche le job_id sur stdout si le job se termine en "completed" ; sinon code 1
# (sous-shell : l'appelant incrémente KO).
run_job() {
  local label="$1" endpoint="$2" body="$3" resp job_id status step last="" start
  resp=$(curl -s -X POST "$BACKEND_URL$endpoint" -H 'Content-Type: application/json' -d "$body")
  job_id=$(json_get "$resp" job_id)
  if [ -z "$job_id" ]; then
    ko_msg "$label : soumission refusée → $resp" >&2
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
      failed) ko_msg "$label : job en échec → $(json_get "$resp" error)" >&2; return 1 ;;
    esac
    if [ $(( $(date +%s) - start )) -ge "$TIMEOUT" ]; then
      ko_msg "$label : pas terminé après ${TIMEOUT}s (dernier état : $status)" >&2
      return 1
    fi
    sleep 5
  done
}

section "1. Santé"
HEALTH=$(curl -sf "$BACKEND_URL/health")
if [ -n "$HEALTH" ] && [ "$(json_get "$HEALTH" status)" = "ok" ]; then
  ok "Backend $BACKEND_URL/health"
  echo "       version=$(json_get "$HEALTH" version)  pipeline=$(json_get "$HEALTH" pipeline_backend)"
  echo "       raison : $(json_get "$HEALTH" pipeline_backend_reason)"
  clinvar=$(json_get "$HEALTH" clinvar)
  case "$clinvar" in
    *'"available": true'*) ok "Base ClinVar locale disponible" ;;
    *) echo "       ClinVar local absent : le VCF de test est pré-annoté ; indispensable pour les vrais VCF (download_reference.sh --clinvar-only)" ;;
  esac
else
  ko "Backend injoignable sur $BACKEND_URL/health — bash scripts/logs.sh backend"
  echo; die "Arrêt : le backend doit répondre pour la suite du test."
fi
if [ "$(json_get "$(curl -sf "$FRONTEND_URL/health")" status)" = "ok" ]; then
  ok "Frontend → proxy backend ($FRONTEND_URL/health)"
else
  ko "Proxy du frontend KO ($FRONTEND_URL/health) — bash scripts/logs.sh frontend"
fi

section "2. Analyse VCF direct (VCF synthétique BRCA1/BRCA2)"
PATIENT=SMOKE001
IN_DIR="$LOCAL_DATA_ROOT/patients/$PATIENT/input"
mkdir -p "$IN_DIR" && cp "$ROOT_DIR/scripts/testdata/smoke_brca.vcf" "$IN_DIR/smoke_brca.vcf" \
  || die "Impossible d'écrire dans $IN_DIR — sudo chown -R \$USER: $LOCAL_DATA_ROOT"
VCF_PATH="$IN_DIR/smoke_brca.vcf"
if JOB=$(run_job "VCF" /api/v1/analyze/vcf "{\"patient_id\":\"$PATIENT\",\"vcf_path\":\"$VCF_PATH\"}"); then
  ok "Job VCF terminé"
  REPORT=$(curl -sf "$BACKEND_URL/api/v1/jobs/$JOB/report")
  if [ -n "$REPORT" ]; then
    ok "Rapport clinique disponible ($(printf '%s' "$REPORT" | wc -c) octets)"
    RISK=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["clinical_prediction"]["risk_level"])' "$REPORT")
    GENES=$(python3 -c 'import json,sys; print(",".join(json.loads(sys.argv[1])["genomic_findings"]["identified_pathogenic_genes"]))' "$REPORT")
    [ "$RISK" = "HIGH" ] && ok "Niveau de risque HIGH (attendu)" || ko "Niveau de risque $RISK (HIGH attendu)"
    [ "$GENES" = "BRCA1,BRCA2" ] && ok "Gènes identifiés : $GENES" || ko "Gènes identifiés : '$GENES' (BRCA1,BRCA2 attendus)"
  else
    ko "Rapport introuvable pour le job $JOB"
  fi
  if find "$LOCAL_DATA_ROOT/patients/$PATIENT/output" -type f 2>/dev/null | grep -q .; then
    ok "Fichiers écrits dans patients/$PATIENT/output/"
  else
    ko "Aucun fichier dans $LOCAL_DATA_ROOT/patients/$PATIENT/output/"
  fi
else
  KO=$((KO + 1))
fi

section "3. Analyse FASTQ (optionnelle)"
if [ -z "$FASTQ_R1" ]; then
  echo "       ignorée — relancer avec --fastq R1.fastq.gz R2.fastq.gz (nécessite la référence hg38)"
else
  [ -f "$FASTQ_R1" ] && [ -f "$FASTQ_R2" ] || die "FASTQ introuvable(s) : $FASTQ_R1 $FASTQ_R2"
  PATIENT=SMOKEFQ
  IN_DIR="$LOCAL_DATA_ROOT/patients/$PATIENT/input"
  mkdir -p "$IN_DIR"
  R1="$(realpath "$FASTQ_R1")"; R2="$(realpath "$FASTQ_R2")"
  case "$R1" in "$LOCAL_DATA_ROOT"/*) ;; *) cp "$R1" "$IN_DIR/"; R1="$IN_DIR/$(basename "$R1")" ;; esac
  case "$R2" in "$LOCAL_DATA_ROOT"/*) ;; *) cp "$R2" "$IN_DIR/"; R2="$IN_DIR/$(basename "$R2")" ;; esac
  if JOB=$(run_job "FASTQ" /api/v1/analyze \
      "{\"patient_id\":\"$PATIENT\",\"fastq_r1\":\"$R1\",\"fastq_r2\":\"$R2\"}"); then
    ok "Job FASTQ terminé ($JOB)"
  else
    KO=$((KO + 1))
  fi
fi

echo
if [ "$KO" -eq 0 ]; then
  echo "${C_OK}Smoke test réussi.${C_RST}"
  exit 0
fi
echo "${C_KO}$KO vérification(s) en échec.${C_RST}"
exit 1
