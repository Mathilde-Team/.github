# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de scripts/coherence_modele.py (sans réseau)."""

import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import coherence_modele as c  # noqa: E402

A, B = c.DEPOTS


def commit_api(sha, date, login="ana", message="fix: x\n\ndétail"):
    return [{"sha": sha, "author": {"login": login} if login else None,
             "commit": {"committer": {"date": date}, "author": {"name": "Ana Nom"}, "message": message}}]


def blob(texte):
    return {"content": base64.b64encode(texte.encode()).decode()}


class FauxClient:
    """Client simulé : chemin → réponse ; journal des écritures."""

    def __init__(self, reponses, issues=()):
        self.reponses = reponses
        self.issues = list(issues)
        self.ecritures = []

    def get(self, chemin):
        return self.reponses[chemin]

    def pages(self, chemin):
        return self.issues

    def envoyer(self, methode, chemin, corps):
        self.ecritures.append((methode, chemin, corps))


def commits_url(depot, chemin):
    return f"/repos/{depot}/commits?sha=main&path={chemin.replace('/', '%2F')}&per_page=1"


def deux_depots(fichiers_a, fichiers_b, commits=None):
    """Réponses pour deux arbres ; fichiers : chemin → (sha, texte)."""
    rep = {}
    for depot, fichiers in ((A, fichiers_a), (B, fichiers_b)):
        rep[f"/repos/{depot}/git/trees/main?recursive=1"] = {
            "tree": [{"path": p, "sha": s, "type": "blob"} for p, (s, _) in fichiers.items()]
            + [{"path": "scripts", "sha": "t", "type": "tree"}]
        }
        for _p, (s, texte) in fichiers.items():
            rep[f"/repos/{depot}/git/blobs/{s}"] = blob(texte)
    for (depot, chemin), valeur in (commits or {}).items():
        rep[commits_url(depot, chemin)] = valeur
    return rep


class TestTrouver(unittest.TestCase):
    def test_divergence_exception_et_fichiers_propres(self):
        rep = deux_depots(
            {"x.py": ("a1", "un\n"), "same": ("s", "="), "README.md": ("r1", "a"), "seul_a": ("o", "")},
            {"x.py": ("b1", "deux\n"), "same": ("s", "="), "README.md": ("r2", "b"), "seul_b": ("p", "")},
            {(A, "x.py"): commit_api("aaaaaaa1", "2026-10-19T10:00:00Z"),
             (B, "x.py"): commit_api("bbbbbbb1", "2026-10-22T10:00:00Z", login=None)},
        )
        divs = c.trouver(FauxClient(rep), "main", {"README.md": "voulu"})
        self.assertEqual([d.chemin for d in divs], ["x.py"])
        d = divs[0]
        self.assertEqual(d.recent, B)
        self.assertEqual(d.commits[B].auteur, "Ana Nom")
        self.assertEqual(d.commits[A].message, "fix: x")
        self.assertIn("-un", d.diff)
        self.assertIn("+deux", d.diff)

    def test_historique_absent(self):
        rep = deux_depots({"x": ("a", "1")}, {"x": ("b", "2")}, {(A, "x"): [], (B, "x"): None})
        d = c.trouver(FauxClient(rep), "main", {})[0]
        self.assertIsNone(d.recent)
        self.assertIn("historique introuvable", c.decrire_commit(A, d.commits[A]))

    def test_meme_date_indecidable(self):
        d = c.Divergence("x", {A: "a", B: "b"}, {A: c.Commit("1", "2026", "u", "m"), B: c.Commit("2", "2026", "u", "m")})
        self.assertIsNone(d.recent)

    def test_diff_tronque(self):
        texte = c.differences("\n".join(map(str, range(200))), "", "a", "b")
        self.assertIn("lignes de plus", texte)
        self.assertEqual(len(texte.splitlines()), c.LIGNES_DIFF + 1)


def divergence(chemin="x.py", sha_a="a1", sha_b="b1", recent=B):
    dates = {A: "2026-10-19T00:00:00Z", B: "2026-10-22T00:00:00Z"}
    if recent == A:
        dates = {A: dates[B], B: dates[A]}
    commits = {d: c.Commit(f"{d[-1]}234567890", dates[d], "ana", "msg") for d in c.DEPOTS}
    return c.Divergence(chemin, {A: sha_a, B: sha_b}, commits, "-x\n+y")


def issue(numero, etat, divs, autre=False):
    corps = "sans marqueur" if autre else c.corps_issue(divs, "main")
    return {"number": numero, "state": etat, "body": corps}


class TestCorps(unittest.TestCase):
    def test_corps_et_relecture(self):
        divs = [divergence(), divergence("y", recent=A), c.Divergence("z", {A: "1", B: "2"}, {A: None, B: None})]
        corps = c.corps_issue(divs, "main")
        self.assertIn("garder la version de `modele-depot` et la reporter dans `.github`", corps)
        self.assertIn("garder la version de `.github` et la reporter dans `modele-depot`", corps)
        self.assertIn("indécidable", corps)
        self.assertEqual(c.lire_etat(corps), c.etat(divs))

    def test_lire_etat_absent_ou_illisible(self):
        self.assertEqual(c.lire_etat(None), {})
        self.assertEqual(c.lire_etat(f"{c.MARQUEUR} {{pas du json}} -->"), {})


class TestSynchroniser(unittest.TestCase):
    def test_creation(self):
        client = FauxClient({}, [{"number": 9, "state": "open", "body": "x", "pull_request": {}}])
        self.assertEqual(c.synchroniser(client, A, [divergence()], "main", False), "création")
        methode, chemin, corps = client.ecritures[0]
        self.assertEqual((methode, chemin, corps["title"], corps["labels"]), ("POST", f"/repos/{A}/issues", c.TITRE, ["question"]))

    def test_simulation_n_ecrit_rien(self):
        client = FauxClient({})
        self.assertEqual(c.synchroniser(client, A, [divergence()], "main", True), "création")
        self.assertEqual(client.ecritures, [])

    def test_inchange(self):
        divs = [divergence()]
        client = FauxClient({}, [issue(3, "open", divs)])
        self.assertEqual(c.synchroniser(client, A, divs, "main", False), "inchangé #3")
        self.assertEqual(client.ecritures, [])

    def test_mise_a_jour_avec_commentaire_pour_un_nouvel_ecart(self):
        client = FauxClient({}, [issue(3, "open", [divergence()])])
        action = c.synchroniser(client, A, [divergence(), divergence("y")], "main", False)
        self.assertEqual(action, "mise à jour #3")
        self.assertEqual(client.ecritures[0][0:2], ("PATCH", f"/repos/{A}/issues/3"))
        self.assertIn("`y`", client.ecritures[1][2]["body"])

    def test_mise_a_jour_sans_commentaire_si_un_ecart_disparait(self):
        client = FauxClient({}, [issue(3, "open", [divergence(), divergence("y")])])
        c.synchroniser(client, A, [divergence()], "main", False)
        self.assertEqual([e[0] for e in client.ecritures], ["PATCH"])

    def test_mise_a_jour_en_simulation(self):
        client = FauxClient({}, [issue(3, "open", [divergence()])])
        self.assertEqual(c.synchroniser(client, A, [divergence(sha_b="b2")], "main", True), "mise à jour #3")
        self.assertEqual(client.ecritures, [])

    def test_fermeture_quand_tout_est_coherent(self):
        client = FauxClient({}, [issue(3, "open", [divergence()]), issue(1, "open", [], autre=True)])
        self.assertEqual(c.synchroniser(client, A, [], "main", False), "fermeture #3")
        self.assertEqual([e[0] for e in client.ecritures], ["POST", "PATCH"])
        self.assertEqual(client.ecritures[1][2]["state"], "closed")

    def test_fermeture_en_simulation_et_rien_a_faire(self):
        client = FauxClient({}, [issue(3, "open", [divergence()])])
        self.assertEqual(c.synchroniser(client, A, [], "main", True), "fermeture #3")
        self.assertEqual(c.synchroniser(FauxClient({}), A, [], "main", False), "cohérent")
        self.assertEqual(client.ecritures, [])

    def test_issue_fermee_a_la_main_vaut_ecart_accepte(self):
        divs = [divergence()]
        client = FauxClient({}, [issue(4, "closed", divs)])
        self.assertEqual(c.synchroniser(client, A, divs, "main", False), "déjà arbitré #4")
        self.assertEqual(client.ecritures, [])

    def test_nouvel_ecart_apres_fermeture_rouvre_la_question(self):
        client = FauxClient({}, [issue(4, "closed", [divergence()])])
        self.assertEqual(c.synchroniser(client, A, [divergence(sha_a="a2")], "main", False), "création")


class FausseReponse(io.BytesIO):
    def __init__(self, corps, lien=""):
        super().__init__(corps)
        self.headers = {"Link": lien}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestClient(unittest.TestCase):
    def test_get_pages_envoyer(self):
        reponses = [
            FausseReponse(b'{"ok": 1}'),
            FausseReponse(b"[1]", '<https://api.github.com/p2>; rel="next"'),
            FausseReponse(b"null"),
            FausseReponse(b""),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=reponses) as urlopen:
            client = c.Client("secret")
            self.assertEqual(client.get("/x"), {"ok": 1})
            self.assertEqual(client.pages("/y?state=all"), [1])
            self.assertIsNone(client.envoyer("PATCH", "/z", {"a": 1}))
        requetes = [appel.args[0] for appel in urlopen.call_args_list]
        self.assertEqual(requetes[0].get_header("Authorization"), "Bearer secret")
        self.assertTrue(requetes[1].full_url.endswith("/y?state=all&per_page=100"))
        self.assertEqual(requetes[2].full_url, "https://api.github.com/p2")
        self.assertEqual((requetes[3].get_method(), json.loads(requetes[3].data)), ("PATCH", {"a": 1}))

    def test_sans_jeton_et_erreur_http(self):
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=[FausseReponse(b"[]"), err]) as urlopen:
            client = c.Client(None)
            self.assertEqual(client.pages("/y"), [])
            self.assertIsNone(urlopen.call_args.args[0].get_header("Authorization"))
            with self.assertRaises(urllib.error.HTTPError):
                client.get("/x")


class TestMain(unittest.TestCase):
    def test_exceptions(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "e.json"
            self.assertEqual(c.charger_exceptions(chemin), {})
            chemin.write_text('{"_doc": "x", "README.md": "voulu"}', encoding="utf-8")
            self.assertEqual(c.charger_exceptions(chemin), {"README.md": "voulu"})

    def test_rapport(self):
        texte = c.rapport([divergence(), c.Divergence("z", {A: "1", B: "2"})], "création", True)
        self.assertIn("(simulation)", texte)
        self.assertIn("plus récent dans modele-depot", texte)
        self.assertIn("indécidable", texte)
        self.assertIn("Aucune divergence", c.rapport([], "cohérent", False))

    def test_main_fichier_et_sortie_standard(self):
        with tempfile.TemporaryDirectory() as dossier:
            sortie = Path(dossier) / "r.md"
            with mock.patch.object(c, "trouver", return_value=[]) as trouver, \
                    mock.patch.object(c, "synchroniser", return_value="cohérent") as sync:
                self.assertEqual(c.main(["--ref", "dev", "--simulation", "--sortie", str(sortie), "--exceptions", str(sortie) + ".x"]), 0)
                self.assertIn("Aucune divergence", sortie.read_text(encoding="utf-8"))
                self.assertEqual(trouver.call_args.args[1:], ("dev", {}))
                self.assertEqual(sync.call_args.args[1:], (A, [], "dev", True))
                with mock.patch("sys.stdout", new=io.StringIO()) as out:
                    self.assertEqual(c.main([]), 0)
                self.assertIn("Cohérence", out.getvalue())


if __name__ == "__main__":
    unittest.main()
