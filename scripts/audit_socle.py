#!/usr/bin/env python3
"""Audit du socle qualité Mathide-Team sur tous les dépôts de l'organisation.

Contrôles par dépôt (voir SOCLE.md) :
  licence    licence MIT détectée par GitHub (fichier LICENSE)
  dev        branche `dev` présente
  main       branche par défaut `main` protégée, avec le check de garde
  garde      workflow .github/workflows/guard-main.yml présent
  dev-prot   branche `dev` protégée, avec au moins un check requis
  regles     règles versionnées .github/branch-protection/{main,dev}.json présentes

Les exceptions assumées (licence d'origine d'un fork, dépôt de démonstration…)
sont décrites dans audit-exceptions.json.

Écrit un rapport Markdown (sortie standard, ou --sortie). Code de sortie 1 si
au moins un dépôt est non conforme. Bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.github.com"
GARDE = "Garde : PR vers main doit venir de dev"
CONTROLES = ["licence", "dev", "main", "garde", "dev-prot", "regles"]


@dataclass
class Resultat:
    """Résultat de l'audit d'un dépôt."""

    depot: str
    controles: dict[str, bool | None] = field(default_factory=dict)  # None = exception
    notes: list[str] = field(default_factory=list)

    @property
    def conforme(self) -> bool:
        return all(v is not False for v in self.controles.values())


class Client:
    """Accès minimal à l'API REST de GitHub."""

    def __init__(self, jeton: str | None) -> None:
        self.jeton = jeton

    def get(self, chemin: str) -> object | None:
        """GET ; renvoie None si la ressource n'existe pas (404)."""
        requete = urllib.request.Request(
            API + chemin,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        if self.jeton:
            requete.add_header("Authorization", f"Bearer {self.jeton}")
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                return json.load(reponse)
        except urllib.error.HTTPError as erreur:
            if erreur.code == 404:
                return None
            raise

    def envoyer(self, methode: str, chemin: str, donnees: dict) -> object:
        """POST/PATCH JSON."""
        requete = urllib.request.Request(
            API + chemin, data=json.dumps(donnees).encode(), method=methode,
            headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {self.jeton}"},
        )
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            return json.load(reponse)

    def depots(self, org: str) -> list[dict]:
        """Dépôts visibles de l'organisation (publics, plus privés si le jeton le permet)."""
        resultat, page = [], 1
        while True:
            lot = self.get(f"/orgs/{org}/repos?per_page=100&type=all&page={page}") or []
            resultat += lot
            if len(lot) < 100:
                return resultat
            page += 1


def checks_requis(branche: dict | None) -> set[str]:
    """Noms des checks requis d'une branche (représentation publique)."""
    if not branche or not branche.get("protected"):
        return set()
    rsc = (branche.get("protection") or {}).get("required_status_checks") or {}
    return {c["context"] for c in rsc.get("checks", [])} | set(rsc.get("contexts", []))


def auditer(client: Client, depot: dict, exceptions: dict) -> Resultat:
    """Audite un dépôt."""
    nom = depot["full_name"]
    res = Resultat(nom)
    exc = exceptions.get(depot["name"], {})
    q = urllib.parse.quote

    licence = (client.get(f"/repos/{nom}/license") or {}).get("license") or {}
    res.controles["licence"] = licence.get("spdx_id") == "MIT"
    if not res.controles["licence"]:
        res.notes.append(f"licence : {licence.get('spdx_id') or 'aucune'}")

    defaut = depot.get("default_branch")
    main = client.get(f"/repos/{nom}/branches/{q(defaut)}")
    res.controles["main"] = defaut == "main" and GARDE in checks_requis(main)
    if defaut != "main":
        res.notes.append(f"branche par défaut : {defaut}")
    elif not (main or {}).get("protected"):
        res.notes.append("main non protégée")

    dev = client.get(f"/repos/{nom}/branches/dev")
    res.controles["dev"] = dev is not None
    res.controles["dev-prot"] = bool(checks_requis(dev))

    res.controles["garde"] = client.get(f"/repos/{nom}/contents/.github/workflows/guard-main.yml") is not None
    regles = client.get(f"/repos/{nom}/contents/.github/branch-protection") or []
    fichiers = {f["name"] for f in regles} if isinstance(regles, list) else set()
    res.controles["regles"] = {"main.json", "dev.json"} <= fichiers

    for controle, raison in exc.items():
        if controle in res.controles and res.controles[controle] is False:
            res.controles[controle] = None
            res.notes.append(f"exception {controle} : {raison}")
    return res


def rapport(resultats: list[Resultat]) -> str:
    """Tableau Markdown des résultats."""
    signe = {True: "✅", False: "❌", None: "➖"}
    lignes = [
        "| Dépôt | " + " | ".join(CONTROLES) + " | Notes |",
        "|---|" + "---|" * len(CONTROLES) + "---|",
    ]
    for r in sorted(resultats, key=lambda r: (r.conforme, r.depot.lower())):
        cases = " | ".join(signe[r.controles.get(c)] for c in CONTROLES)
        lignes.append(f"| `{r.depot.split('/')[1]}` | {cases} | {'; '.join(r.notes)} |")
    ko = sum(not r.conforme for r in resultats)
    entete = f"**{len(resultats) - ko}/{len(resultats)} dépôts conformes au socle.**\n\n"
    legende = "\n\n✅ conforme · ❌ non conforme · ➖ exception assumée (audit-exceptions.json). Règles : [SOCLE.md](../SOCLE.md)."
    return entete + "\n".join(lignes) + legende


TITRE_ISSUE = "Audit du socle qualité"


def publier_issue(client: Client, depot: str, texte: str, conforme: bool) -> str:
    """Crée ou met à jour l'issue de suivi ; la ferme quand tout est conforme."""
    ouvertes = client.get(f"/repos/{depot}/issues?state=open&per_page=100") or []
    issue = next((i for i in ouvertes if i["title"] == TITRE_ISSUE and "pull_request" not in i), None)
    corps = texte + "\n\n_Issue mise à jour automatiquement par le workflow « Audit du socle qualité »._"
    if issue:
        donnees = {"body": corps, **({"state": "closed"} if conforme else {})}
        return client.envoyer("PATCH", f"/repos/{depot}/issues/{issue['number']}", donnees)["html_url"]
    if conforme:
        return "aucune issue (tout est conforme)"
    return client.envoyer("POST", f"/repos/{depot}/issues", {"title": TITRE_ISSUE, "body": corps})["html_url"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", default="Mathide-Team")
    parser.add_argument("--exceptions", default="audit-exceptions.json")
    parser.add_argument("--sortie", help="fichier Markdown du rapport")
    parser.add_argument("--depots", nargs="*", help="limiter l'audit à ces dépôts")
    parser.add_argument("--issue", metavar="OWNER/DEPOT", help="publier le rapport dans l'issue de suivi de ce dépôt")
    parser.add_argument("--rapport-seul", action="store_true", help="code de sortie 0 même si non conforme")
    args = parser.parse_args()

    exceptions = json.loads(Path(args.exceptions).read_text(encoding="utf-8"))
    exceptions = {k: v for k, v in exceptions.items() if not k.startswith("_")}
    client = Client(os.environ.get("GITHUB_TOKEN"))
    depots = [
        d for d in client.depots(args.org)
        if not d.get("archived") and not exceptions.get(d["name"], {}).get("ignorer")
        and (not args.depots or d["name"] in args.depots)
    ]
    resultats = [auditer(client, d, exceptions) for d in depots]
    texte = rapport(resultats)
    if args.sortie:
        Path(args.sortie).write_text(texte + "\n", encoding="utf-8")
    print(texte)
    conforme = all(r.conforme for r in resultats)
    if args.issue:
        print(f"\nIssue : {publier_issue(client, args.issue, texte, conforme)}")
    return 0 if conforme or args.rapport_seul else 1


if __name__ == "__main__":
    sys.exit(main())
