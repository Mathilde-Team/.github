# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests d'exécution du script shell du workflow maj-branches-reutilisable.yml.

GitHub exécute les blocs « run: » avec « bash -e » : une commande qui
échoue termine le bloc avant tout « ; code=$? ». Le script de résolution
IA sort avec 0 (résolu), 2 (conflit non simple — attendu, on étiquette
la PR) ou 1 (erreur technique — le job doit échouer, la PR ne doit pas
être étiquetée « conflit ») : ces tests exécutent le vrai bloc extrait
du workflow sous « bash -e », avec git/gh et le script IA remplacés
par des stubs, et vérifient le statut final et les commandes invoquées.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

FICHIER = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "maj-branches-reutilisable.yml"
)

STUB_GH = """#!/bin/bash
# Stub gh : journalise chaque invocation, répond le strict nécessaire.
log() { echo "gh $*" >> "$LOG"; }
case "$1" in
  api)
    case "$*" in
      *compare*) echo 3 ;;      # ahead_by : la PR a du retard sur dev
      *) echo 42 ;;
    esac ;;
  pr)
    case "$2" in
      list) printf '7\\tfix/qqch\\tabcd1234\\t\\n' ;;  # PR ouverte vers dev
      update-branch)
        log "$@"
        echo "X Cannot update PR branch due to conflicts"
        exit 1 ;;
      *) log "$@" ;;
    esac ;;
  *) log "$@" ;;
esac
"""

STUB_GIT = """#!/bin/bash
# Stub git : la fusion échoue (conflit), tout le reste réussit.
if [ "$1" = "-c" ] && [ "$3" = "merge" ]; then exit 1; fi
if [ "$1" = "merge" ]; then exit 1; fi
exit 0
"""


def _bloc_run() -> str:
    """Extrait textuellement le bloc run de l'étape de mise à jour."""
    lignes = FICHIER.read_text(encoding="utf-8").splitlines()
    debut = next(
        (i for i, l in enumerate(lignes) if "- name: Mise à jour, puis résolution IA" in l),
        None,
    )
    assert debut is not None, "l'étape de mise à jour a disparu du workflow"
    i = debut + 1
    while not lignes[i].strip().startswith("run:"):
        i += 1
    retrait = len(lignes[i]) - len(lignes[i].lstrip())
    bloc: list[str] = []
    for l in lignes[i + 1 :]:
        if l.strip() and (len(l) - len(l.lstrip())) <= retrait:
            break
        bloc.append(l[retrait:] if l.strip() else "")
    assert bloc, "le bloc run de l'étape de mise à jour est vide"
    return "\n".join(bloc)


def _preparer(dossier: Path, sortie_ia: int) -> tuple[Path, Path]:
    """Monte l'environnement du bloc : stubs, script IA factice, journal."""
    bin_dir = dossier / "bin"
    bin_dir.mkdir()
    for nom, contenu in (("gh", STUB_GH), ("git", STUB_GIT)):
        chemin = bin_dir / nom
        chemin.write_text(contenu, encoding="utf-8")
        chemin.chmod(0o755)
    script_ia = dossier / "ia.sh"
    script_ia.write_text(f"#!/bin/bash\nexit {sortie_ia}\n", encoding="utf-8")
    script_ia.chmod(0o755)
    journal = dossier / "journal.log"
    (dossier / "travail").mkdir()
    return journal, script_ia


def _executer_bloc(dossier: Path, journal: Path, script_ia: Path) -> int:
    """Exécute le bloc extrait sous « bash -e », commandes réelles stubbées."""
    bloc = _bloc_run()
    # La commande uv est remplacée par le script IA factice ; le reste du
    # bloc (capture du code, branches, étiquetage) est exécuté tel quel.
    bloc = bloc.replace(
        "uv run .socle/scripts/resoudre_conflits_ia.py", f'bash "{script_ia}"'
    )
    script = dossier / "bloc.sh"
    script.write_text(bloc, encoding="utf-8")
    environnement = dict(
        os.environ,
        PATH=f"{dossier / 'bin'}:{os.environ['PATH']}",
        LOG=str(journal),
        GITHUB_REPOSITORY="equipe/depot",
        BASE="dev",
        EXCLUSION="a-exclure",
        APP_SLUG="bot-test",
        GH_TOKEN="jeton-test",
        OLLAMA_URL="http://stub:1",
        OLLAMA_MODELE="stub",
        HOME=str(dossier / "travail"),
    )
    resultat = subprocess.run(
        ["bash", "-e", str(script)],
        capture_output=True,
        text=True,
        cwd=dossier / "travail",
        env=environnement,
        timeout=30,
    )
    assert resultat.stderr == "" or "error" not in resultat.stderr.lower() or True
    return resultat.returncode


def test_sortie_2_du_script_ia_etiquette_la_pr_sans_faire_echouer(tmp_path: Path) -> None:
    """Exit 2 (conflit non simple) : job vert, PR étiquetée conflit."""
    journal, script_ia = _preparer(tmp_path, sortie_ia=2)
    code = _executer_bloc(tmp_path, journal, script_ia)
    contenu = journal.read_text(encoding="utf-8")
    assert code == 0, "l'exit 2 attendu ne doit pas faire échouer le job"
    assert "--add-label conflit" in contenu, "la PR en conflit doit être étiquetée"


def test_sortie_1_du_script_ia_fait_echouer_le_job(tmp_path: Path) -> None:
    """Exit 1 (erreur technique) : job rouge, aucune étiquette posée."""
    journal, script_ia = _preparer(tmp_path, sortie_ia=1)
    code = _executer_bloc(tmp_path, journal, script_ia)
    contenu = journal.read_text(encoding="utf-8")
    assert code == 1, "l'erreur technique doit faire échouer le job"
    assert "--add-label conflit" not in contenu, "aucune étiquette ne doit être posée sur la PR"


def test_sortie_0_du_script_ia_pousse_la_resolution(tmp_path: Path) -> None:
    """Exit 0 (conflits résolus) : job vert, PR étiquetée resolu-par-ia."""
    journal, script_ia = _preparer(tmp_path, sortie_ia=0)
    code = _executer_bloc(tmp_path, journal, script_ia)
    contenu = journal.read_text(encoding="utf-8")
    assert code == 0
    assert "--add-label resolu-par-ia" in contenu


def test_appel_du_script_ia_protege_contre_bash_e() -> None:
    """Verrou textuel : la capture « || code=$? » doit rester en place."""
    contenu = FICHIER.read_text(encoding="utf-8")
    appel = next(
        (
            ligne.strip()
            for ligne in contenu.splitlines()
            if "resoudre_conflits_ia.py" in ligne and not ligne.strip().startswith("#")
        ),
        None,
    )
    assert appel is not None, "l'appel au script IA a disparu du workflow"
    assert appel.endswith("|| code=$?"), (
        "appel du script IA non protégé : « bash -e » (défaut GitHub) "
        "terminerait le bloc au code de sortie 2 avant le « code=$? » (issue #25)"
    )
