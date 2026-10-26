"""Tests de scripts/nettoyer_tous_runners.sh (#12) : découverte des racines
des runners (courante via $RUNNER_TEMP + sœurs contenant _work/) et appel de
nettoyer_runner.sh sur chacune, avec transmission des options.

Tout se joue dans des dossiers temporaires : aucun fichier réel du runner ni
cache réel de l'utilisateur n'est touché (HOME remplacé, NETTOYER_DOCKER
absent → étape Docker ignorée), comme dans test_espace_disque.py.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
TOUS = RACINE / "scripts" / "nettoyer_tous_runners.sh"
VIEUX = time.time() - 30 * 86400  # 30 jours


def vieillir(chemin):
    os.utime(chemin, (VIEUX, VIEUX))


def monter_arbre(base: Path) -> tuple[Path, Path]:
    """Deux runners sœurs (a = courant, b) + un voisin sans _work (ignoré).

    Renvoie (RUNNER_TEMP du runner a, racine du runner b).
    """
    for runner, depots in (("a", ("vieux-depot", "frais-depot")), ("b", ("vieux-depot",))):
        travail = base / f"runner-{runner}" / "_work"
        for depot in depots:
            dossier = travail / depot
            dossier.mkdir(parents=True)
            (dossier / "fichier.txt").write_text("x", encoding="utf-8")
        (travail / "_tool").mkdir()
        vieillir(travail / "vieux-depot")
    diag = base / "runner-a" / "_diag"
    diag.mkdir()
    journal = diag / "vieux.log"
    journal.write_text("journal", encoding="utf-8")
    vieillir(journal)
    (base / "pas-un-runner").mkdir()  # voisin sans _work : ignoré
    return base / "runner-a" / "_work" / "_temp", base / "runner-b"


def lancer(temp_runner: Path, *args: str) -> subprocess.CompletedProcess:
    """Exécute nettoyer_tous_runners.sh comme dans un job (env RUNNER_TEMP,
    HOME isolé, Docker réel jamais appelé)."""
    with tempfile.TemporaryDirectory() as brut:
        maison = Path(brut)
        env = {
            **os.environ,
            "RUNNER_TEMP": str(temp_runner),
            "HOME": str(maison),
            "NETTOYER_DOCKER": "docker-toujours-absent-de-test",
        }
        return subprocess.run(
            ["bash", str(TOUS), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class TestNettoyerTousRunners(unittest.TestCase):
    def test_appliquer_nettoie_le_runner_courant_et_ses_soeurs(self):
        with tempfile.TemporaryDirectory() as brut:
            base = Path(brut)
            temp_a, racine_b = monter_arbre(base)
            resultat = lancer(temp_a, "--appliquer")
            self.assertEqual(resultat.returncode, 0, resultat.stderr)
            # espaces de travail vieux supprimés, frais et _tool conservés
            self.assertFalse((base / "runner-a" / "_work" / "vieux-depot").exists())
            self.assertTrue((base / "runner-a" / "_work" / "frais-depot").exists())
            self.assertTrue((base / "runner-a" / "_work" / "_tool").exists())
            self.assertFalse((base / "runner-a" / "_diag" / "vieux.log").exists())
            # la sœur découverte par le scan est nettoyée aussi
            self.assertFalse((racine_b / "_work" / "vieux-depot").exists())
            # le voisin sans _work n'est pas traité comme un runner
            self.assertTrue((base / "pas-un-runner").exists())
            # les deux racines apparaissent dans la sortie
            self.assertIn("runner-a", resultat.stdout)
            self.assertIn("runner-b", resultat.stdout)

    def test_simulation_ne_supprime_rien(self):
        with tempfile.TemporaryDirectory() as brut:
            base = Path(brut)
            temp_a, _ = monter_arbre(base)
            resultat = lancer(temp_a)  # sans --appliquer
            self.assertEqual(resultat.returncode, 0, resultat.stderr)
            self.assertTrue((base / "runner-a" / "_work" / "vieux-depot").exists())
            self.assertTrue((base / "runner-b" / "_work" / "vieux-depot").exists())
            self.assertIn("[simulation]", resultat.stdout)

    def test_executable_directement_comme_le_workflow(self):
        """Le workflow lance `scripts/nettoyer_tous_runners.sh` sans `bash` :
        le bit exécutable doit être présent (sinon Permission denied, que les
        autres tests — via `bash script` — ne voient pas)."""
        self.assertTrue(os.access(TOUS, os.X_OK), f"{TOUS} doit être exécutable (git update-index --chmod=+x)")
        with tempfile.TemporaryDirectory() as brut:
            base = Path(brut)
            temp_a, _ = monter_arbre(base)
            maison = Path(brut)
            env = {
                **os.environ,
                "RUNNER_TEMP": str(temp_a),
                "HOME": str(maison),
                "NETTOYER_DOCKER": "docker-toujours-absent-de-test",
            }
            # exécution DIRECTE (sans `bash`) : comme le workflow
            resultat = subprocess.run(
                [str(TOUS), "--docker"], capture_output=True, text=True, env=env, check=False
            )
            self.assertEqual(resultat.returncode, 0, resultat.stderr)

    def test_sans_runner_temp_erreur_claire(self):
        env = {k: v for k, v in os.environ.items() if k != "RUNNER_TEMP"}
        resultat = subprocess.run(
            ["bash", str(TOUS)], capture_output=True, text=True, env=env, check=False
        )
        self.assertEqual(resultat.returncode, 2)
        self.assertIn("RUNNER_TEMP", resultat.stderr)


if __name__ == "__main__":
    unittest.main()
