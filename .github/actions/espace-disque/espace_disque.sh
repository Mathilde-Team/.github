#!/usr/bin/env bash
# Vérifie l'espace libre d'un dossier (#12).
# Usage : espace_disque.sh <dossier> <minimum en Mo>
# Code de sortie : 0 si assez de place, 1 sinon, 2 si les arguments sont invalides.
set -euo pipefail

dossier="${1:-}"
minimum_mo="${2:-}"

if [[ -z "$dossier" || ! -d "$dossier" ]]; then
  echo "::error::dossier à mesurer introuvable : '${dossier}'"
  exit 2
fi
if [[ ! "$minimum_mo" =~ ^[0-9]+$ ]]; then
  echo "::error::minimum invalide : '${minimum_mo}' (nombre entier de Mo attendu)"
  exit 2
fi

libre_ko=$(df --output=avail -k "$dossier" | tail -1 | tr -d ' ')
libre_mo=$(( libre_ko / 1024 ))
runner="${RUNNER_NAME:-runner inconnu}"

if (( libre_mo < minimum_mo )); then
  echo "::error::${libre_mo} Mo libres sur ${runner} (${minimum_mo} Mo requis), voir Mathilde-Team/.github#12"
  df -h "$dossier" || true
  exit 1
fi
echo "Espace libre : ${libre_mo} Mo sur ${runner} (minimum ${minimum_mo} Mo)"
