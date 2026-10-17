"""Tests de scripts/audit_socle.py (sans réseau)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_socle as a  # noqa: E402


class FauxClient:
    """Client GitHub simulé : chemin d'API → réponse."""

    def __init__(self, reponses):
        self.reponses = reponses

    def get(self, chemin):
        return self.reponses.get(chemin)


def branche(*checks):
    return {"protected": bool(checks), "protection": {"required_status_checks": {"checks": [{"context": c} for c in checks]}}}


DEPOT = {"name": "x", "full_name": "o/x", "default_branch": "main"}
CONFORME = {
    "/repos/o/x/license": {"license": {"spdx_id": "MIT"}},
    "/repos/o/x/branches/main": branche(a.GARDE, "Qualité"),
    "/repos/o/x/branches/dev": branche("Qualité"),
    "/repos/o/x/contents/.github/workflows/guard-main.yml": {"name": "guard-main.yml"},
    "/repos/o/x/contents/.github/branch-protection": [{"name": "main.json"}, {"name": "dev.json"}],
}


class TestAudit(unittest.TestCase):
    def test_depot_conforme(self):
        r = a.auditer(FauxClient(CONFORME), DEPOT, {})
        self.assertTrue(r.conforme, r.controles)

    def test_sans_licence_ni_dev(self):
        rep = dict(CONFORME)
        del rep["/repos/o/x/license"], rep["/repos/o/x/branches/dev"]
        r = a.auditer(FauxClient(rep), DEPOT, {})
        self.assertFalse(r.controles["licence"])
        self.assertFalse(r.controles["dev"])
        self.assertFalse(r.controles["dev-prot"])
        self.assertFalse(r.conforme)

    def test_exception_licence(self):
        rep = dict(CONFORME, **{"/repos/o/x/license": {"license": {"spdx_id": "GPL-2.0"}}})
        r = a.auditer(FauxClient(rep), DEPOT, {"x": {"licence": "fork"}})
        self.assertIsNone(r.controles["licence"])
        self.assertTrue(r.conforme)

    def test_main_sans_garde(self):
        rep = dict(CONFORME, **{"/repos/o/x/branches/main": branche("Qualité")})
        r = a.auditer(FauxClient(rep), DEPOT, {})
        self.assertFalse(r.controles["main"])

    def test_rapport(self):
        r = a.auditer(FauxClient(CONFORME), DEPOT, {})
        self.assertIn("1/1 dépôts conformes", a.rapport([r]))



class FauxClientIssue(FauxClient):
    def __init__(self, reponses):
        super().__init__(reponses)
        self.envois = []

    def envoyer(self, methode, chemin, donnees):
        self.envois.append((methode, chemin, donnees))
        return {"html_url": "https://exemple/1"}


class TestIssue(unittest.TestCase):
    def test_cree_issue_si_non_conforme(self):
        c = FauxClientIssue({"/repos/o/g/issues?state=open&per_page=100": []})
        a.publier_issue(c, "o/g", "rapport", conforme=False)
        self.assertEqual(c.envois[0][0], "POST")

    def test_ferme_issue_si_conforme(self):
        c = FauxClientIssue({"/repos/o/g/issues?state=open&per_page=100": [{"title": a.TITRE_ISSUE, "number": 7}]})
        a.publier_issue(c, "o/g", "rapport", conforme=True)
        self.assertEqual(c.envois[0][:2], ("PATCH", "/repos/o/g/issues/7"))
        self.assertEqual(c.envois[0][2]["state"], "closed")


if __name__ == "__main__":
    unittest.main()
