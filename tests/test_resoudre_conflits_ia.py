"""Tests de scripts/resoudre_conflits_ia.py (sans réseau : Ollama simulé, git local)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resoudre_conflits_ia as r  # noqa: E402

DIFF3 = (
    "import os\n"
    "<<<<<<< HEAD\nimport json\n||||||| base\n=======\nimport re\n>>>>>>> dev\n"
    "fin\n"
)
ENV = {"OLLAMA_URL": "http://ollama.test:1435/", "OLLAMA_MODELE": "m"}


def reponse_ollama(contenu):
    """Faux retour de urlopen : corps /api/chat contenant `contenu` (dict sérialisé)."""
    corps = json.dumps({"message": {"content": json.dumps(contenu)}}).encode()
    faux = mock.MagicMock()
    faux.__enter__.return_value = io.BytesIO(corps)
    return faux


def lancer(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Lecture(unittest.TestCase):
    def test_bloc_diff3(self):
        segments = r.lire_conflits(DIFF3)
        self.assertEqual(segments[0], "import os\n")
        bloc = segments[1]
        self.assertEqual((bloc.ours, bloc.base, bloc.theirs), (["import json\n"], [], ["import re\n"]))
        self.assertEqual(segments[2], "fin\n")

    def test_bloc_en_tete_de_fichier(self):
        segments = r.lire_conflits("<<<<<<< HEAD\na\n||||||| base\nb\n=======\nc\n>>>>>>> dev\n")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].base, ["b\n"])

    def test_sans_base_refuse(self):
        with self.assertRaises(r.NonSimple):
            r.lire_conflits("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> dev\n")

    def test_fin_manquante_refusee(self):
        with self.assertRaises(r.NonSimple):
            r.lire_conflits("<<<<<<< HEAD\na\n||||||| base\n=======\nb\n")

    def test_imbrication_refusee(self):
        with self.assertRaises(r.NonSimple):
            r.lire_conflits("<<<<<<< HEAD\n<<<<<<< x\n")


class Validation(unittest.TestCase):
    bloc = r.Bloc(["import json\n"], [], ["import re\n"])

    def test_union_acceptee(self):
        self.assertEqual(r.valider(self.bloc, "import re\nimport json"), ["import re\n", "import json\n"])

    def test_ligne_inventee_refusee(self):
        with self.assertRaises(r.NonSimple):
            r.valider(self.bloc, "import re\nimport yaml\n")

    def test_marqueur_refuse(self):
        with self.assertRaises(r.NonSimple):
            r.valider(self.bloc, "=======\n")

    def test_vide_refusee(self):
        with self.assertRaises(r.NonSimple):
            r.valider(self.bloc, "")

    def test_vide_acceptee_si_deux_cotes_vides(self):
        self.assertEqual(r.valider(r.Bloc(["\n"], ["x\n"], []), ""), [])


class Reglages(unittest.TestCase):
    def test_obligatoires(self):
        with self.assertRaises(ValueError):
            r.lire_reglages({"OLLAMA_URL": "http://x"})

    def test_valeurs(self):
        reglages = r.lire_reglages({**ENV, "IA_MAX_BLOCS": "2", "IA_DELAI": "30"})
        self.assertEqual((reglages.max_blocs, reglages.delai, reglages.max_fichiers), (2, 30, 3))


class Exclusions(unittest.TestCase):
    def test_fichiers_exclus(self):
        for chemin in ("uv.lock", "web/package-lock.json", ".github/workflows/ci.yml", ".github/branch-protection/dev.json"):
            self.assertTrue(r.EXCLUS.search(chemin), chemin)
        self.assertFalse(r.EXCLUS.search("src/uv_lock.py"))


class Contexte(unittest.TestCase):
    def test_voisins(self):
        segments = ["a\nb\n", r.Bloc([], [], []), "c\nd\n"]
        self.assertEqual(r.contexte(segments, 1, -1, n=1), "b")
        self.assertEqual(r.contexte(segments, 1, 1, n=1), "c")
        self.assertEqual(r.contexte(segments, 0, -1), "(rien)")


class Interrogation(unittest.TestCase):
    def test_requete(self):
        reglages = r.lire_reglages(ENV)
        with mock.patch("urllib.request.urlopen", return_value=reponse_ollama({"simple": True})) as appel:
            self.assertEqual(r.interroger(reglages, "x"), {"simple": True})
        requete = appel.call_args.args[0]
        self.assertEqual(requete.full_url, "http://ollama.test:1435/api/chat")
        corps = json.loads(requete.data)
        self.assertEqual((corps["options"]["temperature"], corps["stream"]), (0, False))
        self.assertEqual(corps["format"], r.SCHEMA)
        self.assertEqual(appel.call_args.kwargs["timeout"], 900)


class Collecte(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.ancien = os.getcwd()
        os.chdir(self.dossier.name)
        self.reglages = r.lire_reglages(ENV)

    def tearDown(self):
        os.chdir(self.ancien)
        self.dossier.cleanup()

    def ecrire(self, chemin, texte):
        Path(chemin).write_text(texte, encoding="utf-8")

    def test_aucun_fichier(self):
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, [])

    def test_trop_de_fichiers(self):
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, ["a", "b", "c", "d"])

    def test_fichier_exclu(self):
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, ["uv.lock"])

    def test_illisible(self):
        Path("bin").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, ["bin"])

    def test_bloc_trop_long(self):
        self.ecrire("a.py", DIFF3)
        self.reglages.max_lignes = 0
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, ["a.py"])

    def test_trop_de_blocs(self):
        self.ecrire("a.py", DIFF3 * 2)
        self.reglages.max_blocs = 1
        with self.assertRaises(r.NonSimple):
            r.collecter(self.reglages, ["a.py"])

    def test_ok(self):
        self.ecrire("a.py", DIFF3)
        self.assertEqual(len(r.collecter(self.reglages, ["a.py"])["a.py"]), 3)


class Principal(unittest.TestCase):
    """Bout en bout sur un vrai conflit git, Ollama simulé."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.ancien = os.getcwd()
        d = self.dossier.name
        os.chdir(d)
        lancer("init", "-q", "-b", "dev", cwd=d)
        lancer("config", "user.email", "t@t", cwd=d)
        lancer("config", "user.name", "t", cwd=d)
        Path("a.py").write_text("import os\nimport sys\n\n\ndef f():\n    return 1\n", encoding="utf-8")
        lancer("add", ".", cwd=d)
        lancer("commit", "-qm", "base", cwd=d)
        lancer("checkout", "-qb", "feat", cwd=d)
        Path("a.py").write_text("import os\nimport sys\nimport json\n\n\ndef f():\n    return 1\n", encoding="utf-8")
        lancer("commit", "-qam", "feat", cwd=d)
        lancer("checkout", "-q", "dev", cwd=d)
        Path("a.py").write_text("import os\nimport sys\nimport re\n\n\ndef f():\n    return 1\n", encoding="utf-8")
        lancer("commit", "-qam", "dev", cwd=d)
        lancer("checkout", "-q", "feat", cwd=d)
        subprocess.run(["git", "-c", "merge.conflictStyle=diff3", "merge", "--no-ff", "--no-edit", "dev"],
                       cwd=d, capture_output=True, check=False)

    def tearDown(self):
        os.chdir(self.ancien)
        self.dossier.cleanup()

    def principal(self, contenu=None, erreur=None):
        effet = {"side_effect": erreur} if erreur else {"return_value": reponse_ollama(contenu)}
        with mock.patch("urllib.request.urlopen", **effet):
            return r.main(ENV)

    def test_resolu(self):
        code = self.principal({"simple": True, "resolution": "import re\nimport json\n", "explication": "deux imports"})
        self.assertEqual(code, 0)
        self.assertIn("import re\nimport json\n", Path("a.py").read_text(encoding="utf-8"))
        self.assertNotIn("<<<<<<<", Path("a.py").read_text(encoding="utf-8"))
        self.assertEqual(r.git("diff", "--name-only", "--diff-filter=U"), "")
        self.assertIn("deux imports", Path("resume-ia.md").read_text(encoding="utf-8"))

    def test_non_simple_rien_ne_change(self):
        code = self.principal({"simple": False, "resolution": "", "explication": "même logique"})
        self.assertEqual(code, 2)
        self.assertIn("<<<<<<<", Path("a.py").read_text(encoding="utf-8"))
        self.assertIn("non simple", Path("resume-ia.md").read_text(encoding="utf-8"))

    def test_ligne_inventee_rien_ne_change(self):
        code = self.principal({"simple": True, "resolution": "import yaml\n", "explication": ""})
        self.assertEqual(code, 2)
        self.assertIn("<<<<<<<", Path("a.py").read_text(encoding="utf-8"))

    def test_ollama_injoignable(self):
        self.assertEqual(self.principal(erreur=urllib.error.URLError("refus")), 1)
        self.assertIn("<<<<<<<", Path("a.py").read_text(encoding="utf-8"))

    def test_reglages_absents(self):
        self.assertEqual(r.main({}), 1)

    def test_mode_debug(self):
        self.assertEqual(r.main({"SOCLE_DEBUG": "1"}), 1)

    def test_environnement_du_processus(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(r.main(), 1)


if __name__ == "__main__":
    unittest.main()
