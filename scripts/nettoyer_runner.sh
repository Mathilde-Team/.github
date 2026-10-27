#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Nettoyage nocturne d'un runner auto-hébergé du groupe « maison » (#12).
#
# À installer par un administrateur sur la machine (ou dans le conteneur) du
# runner, par exemple en cron :
#   15 3 * * *  /chemin/nettoyer_runner.sh --runner /actions-runner --appliquer
#
# Sans --appliquer, le script n'efface rien : il liste ce qu'il supprimerait
# (simulation). Il ne touche qu'à ce qui est recréé automatiquement :
#   - _work/<dépôt> non modifiés depuis --jours-travail jours (recréés au
#     prochain checkout), jamais _work/_tool, _work/_actions, _work/_temp ;
#   - _diag/*.log de plus de --jours-diag jours ;
#   - caches uv, npm et electron de l'utilisateur au-delà de --cache-max-mo ;
#   - images et conteneurs Docker arrêtés de plus de 72 h (--docker).
set -euo pipefail

usage() {
  sed -n '2,15p' "$0"
  echo "Usage : $0 --runner <dossier> [--appliquer] [--jours-travail N] [--jours-diag N] [--cache-max-mo N] [--docker]"
}

runner=""
appliquer=0
jours_travail=7
jours_diag=7
cache_max_mo=4096
docker=0

while (( $# )); do
  case "$1" in
    --runner) runner="${2:-}"; shift 2 ;;
    --appliquer) appliquer=1; shift ;;
    --jours-travail) jours_travail="${2:-}"; shift 2 ;;
    --jours-diag) jours_diag="${2:-}"; shift 2 ;;
    --cache-max-mo) cache_max_mo="${2:-}"; shift 2 ;;
    --docker) docker=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "option inconnue : $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$runner" || ! -d "$runner" ]]; then
  echo "dossier du runner introuvable : '${runner}'" >&2
  exit 2
fi
for valeur in "$jours_travail" "$jours_diag" "$cache_max_mo"; do
  if [[ ! "$valeur" =~ ^[0-9]+$ ]]; then
    echo "valeur numérique invalide : '${valeur}'" >&2
    exit 2
  fi
done

mode="simulation"
(( appliquer )) && mode="nettoyage"
echo "[${mode}] runner ${runner}"

supprimer() {
  # Supprime (ou liste, en simulation) un chemin et affiche sa taille.
  local cible="$1" taille
  taille=$(du -sh "$cible" 2>/dev/null | cut -f1 || echo "?")
  echo "  suppression ${cible} (${taille})"
  if (( appliquer )); then
    rm -rf -- "$cible"
  fi
}

echo "Espaces de travail inactifs depuis ${jours_travail} jour(s) :"
if [[ -d "$runner/_work" ]]; then
  while IFS= read -r -d '' dossier; do
    case "$(basename "$dossier")" in
      _tool|_actions|_temp|_PipelineMapping) continue ;;
    esac
    supprimer "$dossier"
  done < <(find "$runner/_work" -mindepth 1 -maxdepth 1 -type d -mtime "+${jours_travail}" -print0)
fi

echo "Journaux _diag de plus de ${jours_diag} jour(s) :"
if [[ -d "$runner/_diag" ]]; then
  while IFS= read -r -d '' journal; do
    supprimer "$journal"
  done < <(find "$runner/_diag" -type f -name '*.log' -mtime "+${jours_diag}" -print0)
fi

echo "Caches de plus de ${cache_max_mo} Mo :"
for cache in "$HOME/.cache/uv" "$HOME/.npm" "$HOME/.cache/electron"; do
  [[ -d "$cache" ]] || continue
  taille_mo=$(du -sm "$cache" | cut -f1)
  if (( taille_mo > cache_max_mo )); then
    supprimer "$cache"
  fi
done

if (( docker )); then
  # NETTOYER_DOCKER : commande docker à utiliser (docker par défaut). Les tests
  # la remplacent pour ne jamais appeler le Docker réel de la machine.
  docker_cmd="${NETTOYER_DOCKER:-docker}"
  echo "Docker (éléments inutilisés de plus de 72 h) :"
  if ! command -v "$docker_cmd" >/dev/null 2>&1; then
    echo "  docker absent : étape ignorée"
  elif (( appliquer )); then
    "$docker_cmd" system prune -af --filter "until=72h"
  else
    "$docker_cmd" system df
  fi
fi

echo "Espace libre après ${mode} :"
df -h "$runner"
