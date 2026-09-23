#!/usr/bin/env bash
# Télécharge la référence hg38 (GATK Resource Bundle, Broad Institute) et les
# known-sites BQSR dans $LOCAL_DATA_ROOT/reference/hg38, puis génère les index manquants.
#
# Usage : bash scripts/download_reference.sh [--yes] [--build-bwa-index] [--with-dbsnp]
#   --yes              pas de confirmation interactive
#   --build-bwa-index  construit l'index BWA localement (≈ 1-2 h, ~6 Go RAM) au lieu de le télécharger
#   --with-dbsnp       ajoute dbSNP 138 (~11 Go supplémentaires)
#
# Espace disque attendu : ~9,5 Go (FASTA 3,2 Go + index BWA 5,5 Go + known-sites ~0,1 Go)
#                         ~21 Go avec --with-dbsnp. Prévoir 2× pendant le téléchargement.
# Reprise : relancer le script ; les fichiers partiels (.part) sont complétés (curl -C -).
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
load_env

BUILD_BWA=0; WITH_DBSNP=0
for arg in "$@"; do
  case "$arg" in
    --yes) ZAYNB_YES=1 ;;
    --build-bwa-index) BUILD_BWA=1 ;;
    --with-dbsnp) WITH_DBSNP=1 ;;
    *) die "Option inconnue : $arg" ;;
  esac
done

BASE_URL="https://storage.googleapis.com/gcp-public-data--broad-references/hg38/v0"
REF_DIR="$LOCAL_DATA_ROOT/reference/hg38"
mkdir -p "$REF_DIR"

# fichier distant → nom local
FILES=(
  "Homo_sapiens_assembly38.fasta hg38.fa"
  "Homo_sapiens_assembly38.fasta.fai hg38.fa.fai"
  "Homo_sapiens_assembly38.dict hg38.dict"
  "Homo_sapiens_assembly38.known_indels.vcf.gz Homo_sapiens_assembly38.known_indels.vcf.gz"
  "Homo_sapiens_assembly38.known_indels.vcf.gz.tbi Homo_sapiens_assembly38.known_indels.vcf.gz.tbi"
  "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"
  "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi"
)
if [ "$BUILD_BWA" = 0 ]; then
  # Index du bundle (bwa index -6 → suffixe .64) : même format, renommé pour hg38.fa.*
  # (le fichier .alt n'est pas installé : alignement non ALT-aware, comme bwa index standard)
  for ext in amb ann bwt pac sa; do
    FILES+=("Homo_sapiens_assembly38.fasta.64.$ext hg38.fa.$ext")
  done
fi
if [ "$WITH_DBSNP" = 1 ]; then
  FILES+=("Homo_sapiens_assembly38.dbsnp138.vcf Homo_sapiens_assembly38.dbsnp138.vcf")
  FILES+=("Homo_sapiens_assembly38.dbsnp138.vcf.idx Homo_sapiens_assembly38.dbsnp138.vcf.idx")
fi

command -v curl >/dev/null 2>&1 || die "curl requis : sudo apt install curl"

remote_size() { curl -sIL "$BASE_URL/$1" | awk 'tolower($1)=="content-length:" {v=$2} END {gsub("\r","",v); print v}'; }
remote_md5()  { curl -sIL "$BASE_URL/$1" | tr -d '\r' | awk -F'md5=' 'tolower($0) ~ /^x-goog-hash:.*md5=/ {print $2}' | tail -1; }
local_md5()   { openssl dgst -md5 -binary "$1" | base64; }

info "Calcul de la taille à télécharger..."
TOTAL=0
TODO=()
for entry in "${FILES[@]}"; do
  read -r remote local <<<"$entry"
  size=$(remote_size "$remote")
  [ -n "$size" ] || die "Fichier distant inaccessible : $BASE_URL/$remote (accès Internet ?)"
  if [ -s "$REF_DIR/$local" ] && [ "$(stat -c %s "$REF_DIR/$local")" = "$size" ]; then
    continue
  fi
  TODO+=("$remote $local $size")
  TOTAL=$((TOTAL + size))
done

if [ "${#TODO[@]}" -eq 0 ]; then
  info "Tous les fichiers sont déjà présents."
else
  TOTAL_GB=$(awk -v b="$TOTAL" 'BEGIN {printf "%.1f", b/1024/1024/1024}')
  FREE_GB=$(df -BG --output=avail "$REF_DIR" | tail -1 | tr -dc '0-9')
  info "${#TODO[@]} fichier(s) à télécharger : ${TOTAL_GB} Go (libre : ${FREE_GB} Go dans $REF_DIR)"
  confirm "Lancer le téléchargement de ${TOTAL_GB} Go ?" || die "Annulé."

  for entry in "${TODO[@]}"; do
    read -r remote local size <<<"$entry"
    dest="$REF_DIR/$local"
    info "↓ $remote → $local"
    curl -fL --retry 5 --retry-delay 10 -C - -o "$dest.part" "$BASE_URL/$remote"
    got=$(stat -c %s "$dest.part")
    [ "$got" = "$size" ] || die "Taille incorrecte pour $local ($got ≠ $size) — relancez le script pour reprendre"
    md5=$(remote_md5 "$remote")
    if [ -n "$md5" ] && command -v openssl >/dev/null 2>&1; then
      [ "$(local_md5 "$dest.part")" = "$md5" ] || { rm -f "$dest.part"; die "MD5 invalide pour $local — fichier supprimé, relancez"; }
      info "  MD5 vérifié"
    fi
    mv "$dest.part" "$dest"
  done
fi

# --- Index manquants ---------------------------------------------------------
FA="$REF_DIR/hg38.fa"
if [ ! -s "$FA.fai" ]; then
  command -v samtools >/dev/null 2>&1 || die "samtools requis pour l'index .fai : sudo apt install samtools"
  info "samtools faidx"; samtools faidx "$FA"
fi
if [ ! -s "$REF_DIR/hg38.dict" ]; then
  info "gatk CreateSequenceDictionary (via Docker)"
  docker run --rm -v "$REF_DIR:$REF_DIR" "${GATK_DOCKER_IMAGE:-broadinstitute/gatk:4.2.6.1}" \
    gatk CreateSequenceDictionary -R "$FA" -O "$REF_DIR/hg38.dict"
fi
if [ ! -s "$FA.bwt" ]; then
  command -v bwa >/dev/null 2>&1 || die "bwa requis pour construire l'index : sudo apt install bwa"
  info "bwa index (≈ 1-2 h)..."; bwa index "$FA"
fi
for vcf in Homo_sapiens_assembly38.known_indels.vcf.gz Mills_and_1000G_gold_standard.indels.hg38.vcf.gz; do
  if [ -s "$REF_DIR/$vcf" ] && [ ! -s "$REF_DIR/$vcf.tbi" ]; then
    command -v tabix >/dev/null 2>&1 || die "tabix requis : sudo apt install tabix"
    tabix -p vcf "$REF_DIR/$vcf"
  fi
done

# --- Vérification finale -----------------------------------------------------
info "Vérification..."
head -c 1 "$FA" | grep -q '>' || die "hg38.fa ne ressemble pas à un FASTA"
[ "$(wc -l < "$FA.fai")" -ge 25 ] || die "hg38.fa.fai incomplet"
grep -q '^@SQ' "$REF_DIR/hg38.dict" || die "hg38.dict invalide"
for ext in amb ann bwt pac sa; do [ -s "$FA.$ext" ] || die "Index BWA manquant : hg38.fa.$ext"; done
gzip -t "$REF_DIR/Homo_sapiens_assembly38.known_indels.vcf.gz" || die "known_indels corrompu"

du -sh "$REF_DIR"
echo "${C_OK}Référence hg38 prête dans $REF_DIR${C_RST}"
