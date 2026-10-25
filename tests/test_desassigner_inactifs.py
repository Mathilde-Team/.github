"""Tests de scripts/desassigner_inactifs.py (sans réseau)."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import desassigner_inactifs as d  # noqa: E402

MAINTENANT = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
DELAI = timedelta(hours=24)


def il_y_a(heures):
    return (MAINTENANT - timedelta(hours=heures)).isoformat().replace("+00:00", "Z")


def assigne(login, heures):
    return {"event": "assigned", "assignee": {"login": login}, "actor": {"login": "chef"}, "created_at": il_y_a(heures)}


def commentaire(login, heures):
    return {"event": "commented", "user": {"login": login}, "actor": {"login": login}, "created_at": il_y_a(heures)}


def pr(login, etat, heures=10, numero=7):
    return {
        "event": "cross-referenced",
        "actor": {"login": login},
        "created_at": il_y_a(heures),
        "source": {"issue": {"number": numero, "pull_request": {}, "state": etat, "user": {"login": login}}},
    }


class FauxClient:
    """Client simulé : chemin → réponse, et journal des écritures."""

    def __init__(self, pages):
        self.pages_ = pages
        self.ecritures = []

    def pages(self, chemin):
        reponse = self.pages_[chemin]
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    def envoyer(self, methode, chemin, corps):
        self.ecritures.append((methode, chemin, corps))


class TestDecider(unittest.TestCase):
    def test_inactif_depuis_l_assignation(self):
        r = d.decider("ana", [assigne("ana", 30)], MAINTENANT, DELAI)
        self.assertTrue(r.retirer)
        self.assertIn("30 h", r.raison)

    def test_assignation_recente(self):
        self.assertFalse(d.decider("ana", [assigne("ana", 3)], MAINTENANT, DELAI).retirer)

    def test_commentaire_de_l_assigne_remet_a_zero(self):
        r = d.decider("ana", [assigne("ana", 50), commentaire("ana", 5)], MAINTENANT, DELAI)
        self.assertFalse(r.retirer)
        self.assertIn("5 h", r.raison)

    def test_commentaire_d_un_autre_ne_compte_pas(self):
        self.assertTrue(d.decider("ana", [assigne("ana", 50), commentaire("bob", 1)], MAINTENANT, DELAI).retirer)

    def test_activite_avant_l_assignation_ignoree(self):
        r = d.decider("ana", [commentaire("ana", 60), assigne("ana", 30)], MAINTENANT, DELAI)
        self.assertTrue(r.retirer)

    def test_derniere_reassignation_prise_en_compte(self):
        chrono = [assigne("ana", 80), assigne("ana", 2)]
        self.assertFalse(d.decider("ana", chrono, MAINTENANT, DELAI).retirer)

    def test_commit_reference_par_l_assigne(self):
        ref = {"event": "referenced", "actor": {"login": "ana"}, "created_at": il_y_a(4)}
        self.assertFalse(d.decider("ana", [assigne("ana", 40), ref], MAINTENANT, DELAI).retirer)

    def test_pr_ouverte_suspend_le_retrait(self):
        r = d.decider("ana", [assigne("ana", 100), pr("ana", "open", heures=90)], MAINTENANT, DELAI)
        self.assertFalse(r.retirer)
        self.assertIn("#7", r.raison)

    def test_pr_fermee_ou_d_un_autre_ne_suspend_pas(self):
        chrono = [assigne("ana", 100), pr("ana", "closed", heures=90), pr("bob", "open", heures=90)]
        self.assertTrue(d.decider("ana", chrono, MAINTENANT, DELAI).retirer)

    def test_cross_reference_sans_date_ignoree(self):
        evt = pr("ana", "open")
        del evt["created_at"]
        self.assertTrue(d.decider("ana", [assigne("ana", 30), evt], MAINTENANT, DELAI).retirer)

    def test_evenement_sans_date_ignore(self):
        evt = {"event": "committed", "author": {"name": "ana"}}
        self.assertTrue(d.decider("ana", [assigne("ana", 30), evt], MAINTENANT, DELAI).retirer)

    def test_assignation_introuvable(self):
        r = d.decider("ana", [assigne("bob", 30)], MAINTENANT, DELAI)
        self.assertFalse(r.retirer)
        self.assertIsNone(r.reference)

    def test_auteur_absent(self):
        self.assertIsNone(d.auteur({"event": "x"}))


def issue(numero, assignes, labels=(), bot=()):
    personnes = [{"login": a, "type": "User"} for a in assignes] + [{"login": b, "type": "Bot"} for b in bot]
    return {"number": numero, "assignees": personnes, "labels": [{"name": n} for n in labels]}


LISTE = "/repos/o/x/issues?state=open&assignee=*"


class TestExecuter(unittest.TestCase):
    def client(self, issues, chronos):
        pages = {LISTE: issues}
        pages.update({f"/repos/o/x/issues/{n}/timeline": c for n, c in chronos.items()})
        return FauxClient(pages)

    def test_retrait_et_commentaire(self):
        c = self.client([issue(1, ["ana", "bob"])], {1: [assigne("ana", 30), assigne("bob", 2)]})
        texte, code = d.executer(c, "o/x", MAINTENANT, DELAI, "garder-assignation", False)
        self.assertEqual(code, 0)
        self.assertEqual(c.ecritures[0], ("DELETE", "/repos/o/x/issues/1/assignees", {"assignees": ["ana"]}))
        methode, chemin, corps = c.ecritures[1]
        self.assertEqual((methode, chemin), ("POST", "/repos/o/x/issues/1/comments"))
        self.assertIn("@ana", corps["body"])
        self.assertNotIn("@bob", corps["body"])
        self.assertIn("garder-assignation", corps["body"])
        self.assertIn("| #1 | @ana | retiré |", texte)
        self.assertIn("| #1 | @bob | conservé |", texte)

    def test_simulation_n_ecrit_rien(self):
        c = self.client([issue(1, ["ana"])], {1: [assigne("ana", 30)]})
        texte, code = d.executer(c, "o/x", MAINTENANT, DELAI, "garder-assignation", True)
        self.assertEqual(c.ecritures, [])
        self.assertIn("(simulation)", texte)
        self.assertIn("retiré", texte)

    def test_label_exemption_bots_et_pr(self):
        issues = [
            issue(1, ["ana"], labels=["garder-assignation"]),
            issue(2, [], bot=["Copilot"]),
            dict(issue(3, ["ana"]), pull_request={}),
        ]
        c = self.client(issues, {})
        texte, code = d.executer(c, "o/x", MAINTENANT, DELAI, "garder-assignation", False)
        self.assertEqual((c.ecritures, code), ([], 0))
        self.assertIn("Aucune issue", texte)

    def test_erreur_sur_une_issue_n_arrete_pas_les_autres(self):
        c = self.client([issue(1, ["ana"]), issue(2, ["bob"])], {1: urllib.error.URLError("panne"), 2: [assigne("bob", 30)]})
        texte, code = d.executer(c, "o/x", MAINTENANT, DELAI, "garder-assignation", False)
        self.assertEqual(code, 1)
        self.assertIn("Erreur : #1", texte)
        self.assertEqual(c.ecritures[0][1], "/repos/o/x/issues/2/assignees")


class FausseReponse(io.BytesIO):
    def __init__(self, corps, lien=""):
        super().__init__(corps)
        self.headers = {"Link": lien}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestClient(unittest.TestCase):
    def test_pagination_et_entetes(self):
        reponses = [
            FausseReponse(json.dumps([{"n": 1}]).encode(), '<https://api.github.com/p2>; rel="next"'),
            FausseReponse(json.dumps([{"n": 2}]).encode()),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=reponses) as urlopen:
            elements = d.Client("secret").pages("/repos/o/x/issues?state=open")
        self.assertEqual(elements, [{"n": 1}, {"n": 2}])
        premiere = urlopen.call_args_list[0].args[0]
        self.assertEqual(premiere.full_url, "https://api.github.com/repos/o/x/issues?state=open&per_page=100")
        self.assertEqual(premiere.get_header("Authorization"), "Bearer secret")
        self.assertEqual(urlopen.call_args_list[1].args[0].full_url, "https://api.github.com/p2")

    def test_envoyer_avec_corps_sans_jeton(self):
        with mock.patch("urllib.request.urlopen", return_value=FausseReponse(b"")) as urlopen:
            resultat = d.Client(None).envoyer("DELETE", "/repos/o/x/issues/1/assignees", {"assignees": ["ana"]})
        self.assertIsNone(resultat)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_method(), "DELETE")
        self.assertEqual(json.loads(req.data), {"assignees": ["ana"]})
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_pages_sans_parametre(self):
        with mock.patch("urllib.request.urlopen", return_value=FausseReponse(b"null")) as urlopen:
            self.assertEqual(d.Client(None).pages("/x"), [])
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/x?per_page=100"))

    def test_erreur_http_remontee(self):
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                d.Client(None).pages("/x")


class TestMain(unittest.TestCase):
    def test_main_ecrit_le_rapport(self):
        with tempfile.TemporaryDirectory() as dossier:
            sortie = Path(dossier) / "rapport.md"
            with mock.patch.object(d, "executer", return_value=("rapport\n", 0)) as executer:
                code = d.main(["--depot", "o/x", "--delai-heures", "12", "--simulation", "--sortie", str(sortie)])
            self.assertEqual(code, 0)
            self.assertEqual(sortie.read_text(encoding="utf-8"), "rapport\n")
            args = executer.call_args.args
            self.assertEqual((args[1], args[3], args[4], args[5]), ("o/x", timedelta(hours=12), "garder-assignation", True))

    def test_main_sur_la_sortie_standard(self):
        with mock.patch.object(d, "executer", return_value=("rapport\n", 1)), mock.patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(d.main(["--depot", "o/x"]), 1)
        self.assertEqual(out.getvalue(), "rapport\n")


if __name__ == "__main__":
    unittest.main()
