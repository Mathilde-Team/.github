"""Tests de l'action espace-disque et de scripts/nettoyer_runner.sh (#12).

Les scripts sont lancés dans des dossiers temporaires : aucun fichier réel du
runner n'est touché, et Docker n'est jamais appelé (PATH sans docker).
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
ESPACE = RACINE / ".github" / "actions" / "espace-disque" / "espace_disque.sh"
NETTOYER = RACINE / "scripts" / "nettoyer_runner.sh"
VIEUX = time.time() - 30 * 86400  # 30 jours


def lancer(script, *args, env=None):
    return subprocess.run(["bash", str(script), *map(str, args)], capture_output=True, text=True,
                          env=env, check=False)


def vieillir(chemin):
    os.utime(chemin, (VIEUX, VIEUX))


class TestEspaceDisque(unittest.TestCase):
    def test_assez_de_place(self):
        with tempfile.TemporaryDirectory() as d:
            r = lancer(ESPACE, d, 0, env={**os.environ, "RUNNER_NAME": "RunnerTest"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Mo sur RunnerTest", r.stdout)

    def test_pas_assez_de_place(self):
        with tempfile.TemporaryDirectory() as d:
            r = lancer(ESPACE, d, 10**12)
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error::", r.stdout)
        self.assertIn("runner inconnu", r.stdout)

    def test_arguments_invalides(self):
        self.assertEqual(lancer(ESPACE, "/dossier/absent", 1).returncode, 2)
        with tempfile.TemporaryDirectory() as d:
            r = lancer(ESPACE, d, "2Go")
        self.assertEqual(r.returncode, 2)
        self.assertIn("minimum invalide", r.stdout)

    def test_action_appelle_le_script(self):
        action = (ESPACE.parent / "action.yml").read_text(encoding="utf-8")
        self.assertIn("espace_disque.sh", action)
        self.assertIn('default: "2048"', action)


class TestNettoyerRunner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self._tmp.name)
        self.runner = self.racine / "runner"
        self.home = self.racine / "home"
        travail = self.runner / "_work"
        for nom in ("ancien", "recent", "_tool", "_actions", "_temp"):
            (travail / nom).mkdir(parents=True)
        for nom in ("ancien", "_tool", "_actions", "_temp"):
            vieillir(travail / nom)
        diag = self.runner / "_diag"
        diag.mkdir()
        (diag / "Runner_ancien.log").write_text("x")
        vieillir(diag / "Runner_ancien.log")
        (diag / "Runner_recent.log").write_text("x")
        cache = self.home / ".cache" / "uv"
        cache.mkdir(parents=True)
        (cache / "gros").write_bytes(b"0" * 2 * 1024 * 1024)
        (self.home / ".npm").mkdir()
        # PATH sans docker, pour ne jamais toucher au Docker de la machine.
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}

    def tearDown(self):
        self._tmp.cleanup()

    def nettoyer(self, *args):
        return lancer(NETTOYER, "--runner", self.runner, "--cache-max-mo", 1, *args, env=self.env)

    def test_simulation_par_defaut_ne_supprime_rien(self):
        r = self.nettoyer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[simulation]", r.stdout)
        self.assertIn("_work/ancien", r.stdout)
        self.assertTrue((self.runner / "_work" / "ancien").exists())
        self.assertTrue((self.home / ".cache" / "uv").exists())

    def test_appliquer_supprime_seulement_l_inactif(self):
        r = self.nettoyer("--appliquer", "--docker")
        self.assertEqual(r.returncode, 0, r.stderr)
        travail = self.runner / "_work"
        self.assertFalse((travail / "ancien").exists())
        for garde in ("recent", "_tool", "_actions", "_temp"):
            self.assertTrue((travail / garde).exists(), garde)
        self.assertFalse((self.runner / "_diag" / "Runner_ancien.log").exists())
        self.assertTrue((self.runner / "_diag" / "Runner_recent.log").exists())
        self.assertFalse((self.home / ".cache" / "uv").exists())
        self.assertTrue((self.home / ".npm").exists())  # sous le seuil
        self.assertIn("docker absent", r.stdout)

    def test_docker_en_simulation(self):
        faux = self.racine / "bin"
        faux.mkdir()
        (faux / "docker").write_text("#!/bin/sh\necho \"docker $*\"\n")
        (faux / "docker").chmod(0o755)
        self.env["PATH"] = f"{faux}:/usr/bin:/bin"
        r = self.nettoyer("--docker")
        self.assertIn("docker system df", r.stdout)
        r = self.nettoyer("--docker", "--appliquer")
        self.assertIn("docker system prune -af --filter until=72h", r.stdout)

    def test_runner_sans_work_ni_diag(self):
        vide = self.racine / "vide"
        vide.mkdir()
        r = lancer(NETTOYER, "--runner", vide, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_erreurs_d_arguments(self):
        self.assertEqual(lancer(NETTOYER, env=self.env).returncode, 2)
        self.assertEqual(lancer(NETTOYER, "--runner", self.racine / "absent", env=self.env).returncode, 2)
        r = self.nettoyer("--jours-diag", "sept")
        self.assertEqual(r.returncode, 2)
        self.assertIn("valeur numérique invalide", r.stderr)
        r = lancer(NETTOYER, "--inconnue", env=self.env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("option inconnue", r.stderr)

    def test_aide(self):
        r = lancer(NETTOYER, "--help", env=self.env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)


if __name__ == "__main__":
    unittest.main()
