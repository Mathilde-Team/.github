"""Tests du client HTTP et de la ligne de commande de scripts/audit_socle.py.

Aucun accès réseau : ``urllib.request.urlopen`` est remplacé par une fausse
réponse (même approche que tests/test_check_branch_protection.py).
"""

from __future__ import annotations

import io
import json
import runpy
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_socle as a  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_socle.py"


class FausseReponse(io.BytesIO):
    """Réponse ``urlopen`` utilisable comme gestionnaire de contexte."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def erreur_http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "erreur", {}, None)


@pytest.fixture
def requetes(monkeypatch):
    """Remplace urlopen : chemin d'API → objet JSON, ou exception à lever."""
    vues: list = []
    reponses: dict = {}

    def faux_urlopen(requete, timeout):
        vues.append(requete)
        chemin = requete.full_url.removeprefix(a.API)
        valeur = reponses.get(chemin, erreur_http(404))
        if isinstance(valeur, Exception):
            raise valeur
        return FausseReponse(json.dumps(valeur).encode())

    monkeypatch.setattr(a.urllib.request, "urlopen", faux_urlopen)
    return reponses, vues


# --- Client -----------------------------------------------------------------


def test_get_avec_jeton_ajoute_l_en_tete(requetes):
    reponses, vues = requetes
    reponses["/repos/o/x"] = {"name": "x"}
    assert a.Client("jeton").get("/repos/o/x") == {"name": "x"}
    assert vues[0].get_header("Authorization") == "Bearer jeton"


def test_get_sans_jeton_n_envoie_pas_d_authentification(requetes):
    reponses, vues = requetes
    reponses["/repos/o/x"] = {"name": "x"}
    a.Client(None).get("/repos/o/x")
    assert vues[0].get_header("Authorization") is None


def test_get_404_renvoie_none(requetes):
    assert a.Client(None).get("/inconnu") is None


def test_get_autre_erreur_http_remonte(requetes):
    reponses, _ = requetes
    reponses["/interdit"] = erreur_http(403)
    with pytest.raises(urllib.error.HTTPError):
        a.Client(None).get("/interdit")


def test_envoyer_poste_du_json(requetes):
    reponses, vues = requetes
    reponses["/repos/o/g/issues"] = {"html_url": "https://exemple/1"}
    resultat = a.Client("jeton").envoyer("POST", "/repos/o/g/issues", {"title": "t"})
    assert resultat == {"html_url": "https://exemple/1"}
    assert vues[0].get_method() == "POST"
    assert json.loads(vues[0].data) == {"title": "t"}


def test_depots_parcourt_toutes_les_pages(requetes):
    reponses, _ = requetes
    base = "/orgs/o/repos?per_page=100&type=all&page="
    reponses[base + "1"] = [{"name": f"d{i}"} for i in range(100)]
    reponses[base + "2"] = [{"name": "dernier"}]
    depots = a.Client(None).depots("o")
    assert len(depots) == 101
    assert depots[-1] == {"name": "dernier"}


def test_depots_organisation_inconnue(requetes):
    assert a.Client(None).depots("inconnue") == []


# --- Audit : cas non couverts par test_audit_socle.py -------------------------


class FauxClient:
    def __init__(self, reponses):
        self.reponses = reponses

    def get(self, chemin):
        return self.reponses.get(chemin)


def test_branche_par_defaut_autre_que_main():
    depot = {"name": "x", "full_name": "o/x", "default_branch": "master"}
    r = a.auditer(FauxClient({}), depot, {})
    assert r.controles["main"] is False
    assert "branche par défaut : master" in r.notes
    assert "licence : aucune" in r.notes


def test_main_non_protegee_et_regles_illisibles():
    depot = {"name": "x", "full_name": "o/x", "default_branch": "main"}
    reponses = {
        "/repos/o/x/branches/main": {"protected": False},
        # contenu d'un fichier (dict) au lieu d'un dossier (liste)
        "/repos/o/x/contents/.github/branch-protection": {"name": "branch-protection"},
    }
    r = a.auditer(FauxClient(reponses), depot, {})
    assert "main non protégée" in r.notes
    assert r.controles["regles"] is False


def test_exception_ignoree_si_controle_conforme_ou_inconnu():
    depot = {"name": "x", "full_name": "o/x", "default_branch": "main"}
    reponses = {"/repos/o/x/license": {"license": {"spdx_id": "MIT"}}}
    r = a.auditer(FauxClient(reponses), depot, {"x": {"licence": "inutile", "autre": "?"}})
    assert r.controles["licence"] is True
    assert not any(n.startswith("exception") for n in r.notes)


class FauxClientIssue(FauxClient):
    def __init__(self, reponses):
        super().__init__(reponses)
        self.envois = []

    def envoyer(self, methode, chemin, donnees):
        self.envois.append((methode, chemin, donnees))
        return {"html_url": "https://exemple/1"}


def test_aucune_issue_creee_si_tout_est_conforme():
    c = FauxClientIssue({})
    assert a.publier_issue(c, "o/g", "rapport", conforme=True) == "aucune issue (tout est conforme)"
    assert c.envois == []


def test_issue_existante_mise_a_jour_sans_fermeture():
    ouvertes = [
        {"title": a.TITRE_ISSUE, "number": 3, "pull_request": {}},  # PR homonyme ignorée
        {"title": a.TITRE_ISSUE, "number": 7},
    ]
    c = FauxClientIssue({"/repos/o/g/issues?state=open&per_page=100": ouvertes})
    a.publier_issue(c, "o/g", "rapport", conforme=False)
    methode, chemin, donnees = c.envois[0]
    assert (methode, chemin) == ("PATCH", "/repos/o/g/issues/7")
    assert "state" not in donnees


# --- Ligne de commande --------------------------------------------------------


def org_conforme(reponses: dict) -> None:
    """Organisation « o » : un dépôt conforme, un archivé, un ignoré."""
    reponses["/orgs/o/repos?per_page=100&type=all&page=1"] = [
        {"name": "x", "full_name": "o/x", "default_branch": "main"},
        {"name": "vieux", "full_name": "o/vieux", "archived": True},
        {"name": "demo", "full_name": "o/demo"},
    ]
    regle = {"protected": True, "protection": {"required_status_checks": {"checks": [{"context": a.GARDE}]}}}
    reponses.update({
        "/repos/o/x/license": {"license": {"spdx_id": "MIT"}},
        "/repos/o/x/branches/main": regle,
        "/repos/o/x/branches/dev": regle,
        "/repos/o/x/contents/.github/workflows/guard-main.yml": {"name": "guard-main.yml"},
        "/repos/o/x/contents/.github/branch-protection": [{"name": "main.json"}, {"name": "dev.json"}],
    })


@pytest.fixture
def exceptions(tmp_path):
    chemin = tmp_path / "exceptions.json"
    chemin.write_text(json.dumps({"_commentaire": "ignoré", "demo": {"ignorer": "démo"}}), encoding="utf-8")
    return chemin


def test_main_conforme_ecrit_le_rapport(requetes, exceptions, tmp_path, monkeypatch, capsys):
    reponses, _ = requetes
    org_conforme(reponses)
    sortie = tmp_path / "rapport.md"
    monkeypatch.setattr(sys, "argv", ["audit_socle.py", "--org", "o", "--exceptions", str(exceptions),
                                      "--sortie", str(sortie)])
    assert a.main() == 0
    assert "1/1 dépôts conformes" in sortie.read_text(encoding="utf-8")
    assert "1/1 dépôts conformes" in capsys.readouterr().out


def test_main_non_conforme_code_1_puis_rapport_seul(requetes, exceptions, monkeypatch):
    reponses, _ = requetes
    org_conforme(reponses)
    del reponses["/repos/o/x/license"]
    argv = ["audit_socle.py", "--org", "o", "--exceptions", str(exceptions), "--depots", "x"]
    monkeypatch.setattr(sys, "argv", argv)
    assert a.main() == 1
    monkeypatch.setattr(sys, "argv", argv + ["--rapport-seul"])
    assert a.main() == 0


def test_main_filtre_depots_et_publie_l_issue(requetes, exceptions, monkeypatch, capsys):
    reponses, _ = requetes
    org_conforme(reponses)
    reponses["/repos/o/g/issues?state=open&per_page=100"] = [{"title": a.TITRE_ISSUE, "number": 1}]
    reponses["/repos/o/g/issues/1"] = {"html_url": "https://exemple/1"}
    monkeypatch.setattr(sys, "argv", ["audit_socle.py", "--org", "o", "--exceptions", str(exceptions),
                                      "--depots", "autre", "--issue", "o/g"])
    assert a.main() == 0
    assert "Issue : https://exemple/1" in capsys.readouterr().out


def test_execution_comme_script(requetes, exceptions, monkeypatch):
    reponses, _ = requetes
    org_conforme(reponses)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--org", "o", "--exceptions", str(exceptions)])
    with pytest.raises(SystemExit) as fin:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert fin.value.code == 0
