#!/usr/bin/env bash
# Trouve les racines des runners maison sur la machine et lance
# scripts/nettoyer_runner.sh sur chacune (#12) : tâche planifiée du workflow
# nettoyage-nocturne.yml, sans cron à installer à la main sur les machines.
#
# Racines découvertes :
#   - celle du runner du job courant, via $RUNNER_TEMP (= <racine>/_work/_temp) ;
#   - ses sœurs éventuelles : tout dossier voisin contenant un sous-dossier
#     _work (plusieurs services runner sur la même machine).
#
# Les options sont transmises telles quelles à nettoyer_runner.sh :
#   nettoyer_tous_runners.sh --appliquer --docker
# Sans --appliquer, rien n'est effacé (simulation).
set -euo pipefail

if [ -z "${RUNNER_TEMP:-}" ]; then
  echo "RUNNER_TEMP requis (ce script s'exécute dans un job Actions)" >&2
  exit 2
fi

ici="$(cd "$(dirname "$0")" && pwd)"
nettoyer="$ici/nettoyer_runner.sh"

courante="$(dirname "$(dirname "$RUNNER_TEMP")")"
racines=()
if [ -d "$courante/_work" ]; then
  racines+=("$courante")
fi
parent="$(dirname "$courante")"
for candidat in "$parent"/*/; do
  if [ -d "${candidat}_work" ]; then
    racines+=("${candidat%/}")
  fi
done

if [ "${#racines[@]}" -eq 0 ]; then
  echo "aucune racine de runner trouvée sous '$parent'" >&2
  exit 2
fi

# dédoublonnage (la racine courante peut réapparaître dans le scan)
mapfile -t racines < <(printf '%s\n' "${racines[@]}" | sort -u)

for racine in "${racines[@]}"; do
  "$nettoyer" --runner "$racine" "$@"
done
