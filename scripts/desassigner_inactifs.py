#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# /// script
# requires-python = ">=3.10"
# dependencies = ["loguru==0.7.3"]
# ///
"""Retire les assignés des issues ouvertes sans activité de leur part.

Règle, par issue ouverte et par assigné humain :
  - le compteur part de la dernière assignation de cette personne ;
  - toute action de l'assigné sur l'issue le remet à zéro (commentaire,
    commit ou PR qui référence l'issue, label, modification…) ;
  - une PR ouverte par l'assigné qui référence l'issue suspend le retrait ;
  - passé le délai (24 h par défaut), l'assigné est retiré et un commentaire
    explique pourquoi ;
  - une issue portant le label d'exemption n'est pas traitée ;
  - les comptes bots (Copilot…) ne sont jamais retirés.

Appelé par le workflow réutilisable .github/workflows/desassignation-inactifs.yml
avec le GITHUB_TOKEN du dépôt appelant (permission issues: write).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

API = "https://api.github.com"
LABEL_EXEMPTION = "garder-assignation"
MARQUEUR = "<!-- desassignation-inactifs -->"


@dataclass
class Decision:
    """Verdict pour un assigné d'une issue."""

    login: str
    retirer: bool
    raison: str
    reference: datetime | None = None


class Client:
    """Accès minimal à l'API REST de GitHub (bibliothèque standard)."""

    def __init__(self, jeton: str | None) -> None:
        logger.debug("Client GitHub : jeton fourni={}", bool(jeton))
        self.jeton = jeton

    def requete(self, methode: str, url: str, corps: object | None = None) -> tuple[object, str]:
        """Envoie une requête ; renvoie (JSON décodé, en-tête Link)."""
        logger.debug("{} {}", methode, url)
        donnees = json.dumps(corps).encode() if corps is not None else None
        entetes = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.jeton:
            entetes["Authorization"] = f"Bearer {self.jeton}"
        if donnees is not None:
            entetes["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=donnees, headers=entetes, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=30) as rep:
                brut = rep.read()
                lien = rep.headers.get("Link", "") or ""
        except urllib.error.HTTPError as err:
            logger.error("{} {} : HTTP {} {}", methode, url, err.code, err.reason)
            raise
        resultat = json.loads(brut) if brut else None
        logger.debug("{} {} : {} octets", methode, url, len(brut))
        return resultat, lien

    def pages(self, chemin: str) -> list[dict]:
        """GET paginé (per_page=100) ; concatène toutes les pages."""
        logger.debug("Lecture paginée de {}", chemin)
        separateur = "&" if "?" in chemin else "?"
        url: str | None = f"{API}{chemin}{separateur}per_page=100"
        elements: list[dict] = []
        while url:
            page, lien = self.requete("GET", url)
            elements.extend(page or [])
            suivant = re.search(r'<([^>]+)>;\s*rel="next"', lien)
            url = suivant.group(1) if suivant else None
        logger.debug("{} : {} éléments", chemin, len(elements))
        return elements

    def envoyer(self, methode: str, chemin: str, corps: object) -> object:
        """POST/DELETE avec un corps JSON."""
        resultat, _ = self.requete(methode, API + chemin, corps)
        logger.debug("{} {} terminé", methode, chemin)
        return resultat


def date(texte: str) -> datetime:
    """Convertit un horodatage ISO 8601 de GitHub en datetime UTC."""
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


def auteur(evenement: dict) -> str | None:
    """Login de la personne à l'origine d'un événement de la chronologie."""
    personne = evenement.get("actor") or evenement.get("user") or {}
    return personne.get("login")


def decider(login: str, chronologie: list[dict], maintenant: datetime, delai: timedelta) -> Decision:
    """Décide s'il faut retirer `login` de l'issue décrite par `chronologie`."""
    logger.debug("Décision pour {} : {} événements, délai {}", login, len(chronologie), delai)
    assignations = [
        date(e["created_at"])
        for e in chronologie
        if e.get("event") == "assigned" and (e.get("assignee") or {}).get("login") == login
    ]
    if not assignations:
        logger.debug("{} : aucune assignation trouvée, on ne touche à rien", login)
        return Decision(login, False, "assignation introuvable dans la chronologie")
    reference = max(assignations)

    for e in chronologie:
        if e.get("event") != "cross-referenced" or not e.get("created_at"):
            continue
        source = (e.get("source") or {}).get("issue") or {}
        if (
            "pull_request" in source
            and source.get("state") == "open"
            and (source.get("user") or {}).get("login") == login
        ):
            logger.debug("{} : PR ouverte #{} liée", login, source.get("number"))
            return Decision(login, False, f"PR ouverte #{source.get('number')} liée", reference)

    for e in chronologie:
        horodatage = e.get("created_at")
        if horodatage and auteur(e) == login and date(horodatage) > reference:
            reference = date(horodatage)

    inactif = maintenant - reference
    if inactif >= delai:
        logger.debug("{} : inactif depuis {}, retrait", login, inactif)
        return Decision(login, True, f"aucune activité depuis {heures(inactif)}", reference)
    logger.debug("{} : actif il y a {}, conservé", login, inactif)
    return Decision(login, False, f"dernière activité il y a {heures(inactif)}", reference)


def heures(duree: timedelta) -> str:
    """Durée lisible arrondie à l'heure (« 26 h »)."""
    return f"{int(duree.total_seconds() // 3600)} h"


def message(logins: list[str], delai: timedelta, label: str) -> str:
    """Commentaire publié sur l'issue après le retrait."""
    mentions = ", ".join(f"@{login}" for login in logins)
    texte = (
        f"{MARQUEUR}\n"
        f"{mentions} : assignation retirée automatiquement, faute d'activité de votre part "
        f"sur cette issue depuis {heures(delai)} (commentaire, commit, PR…).\n\n"
        "Si vous y travaillez toujours, réassignez-vous et laissez un mot sur l'avancement. "
        f"Pour une issue qui demande une assignation longue, ajoutez le label `{label}`."
    )
    logger.debug("Message préparé pour {} assigné(s)", len(logins))
    return texte


def traiter_issue(
    client: Client,
    depot: str,
    issue: dict,
    maintenant: datetime,
    delai: timedelta,
    label: str,
    simulation: bool,
) -> list[Decision]:
    """Analyse une issue et retire les assignés inactifs ; renvoie les décisions."""
    numero = issue["number"]
    logger.debug("Issue {}#{} : {} assigné(s)", depot, numero, len(issue.get("assignees") or []))
    if any(lbl.get("name") == label for lbl in issue.get("labels") or []):
        logger.info("{}#{} : label {} présent, ignorée", depot, numero, label)
        return []
    humains = [a["login"] for a in issue.get("assignees") or [] if a.get("type") != "Bot"]
    if not humains:
        logger.debug("{}#{} : aucun assigné humain", depot, numero)
        return []
    chronologie = client.pages(f"/repos/{depot}/issues/{numero}/timeline")
    decisions = [decider(login, chronologie, maintenant, delai) for login in humains]
    a_retirer = [d.login for d in decisions if d.retirer]
    for d in decisions:
        logger.info("{}#{} {} : {} ({})", depot, numero, d.login, "retrait" if d.retirer else "conservé", d.raison)
    if a_retirer and not simulation:
        client.envoyer("DELETE", f"/repos/{depot}/issues/{numero}/assignees", {"assignees": a_retirer})
        client.envoyer("POST", f"/repos/{depot}/issues/{numero}/comments", {"body": message(a_retirer, delai, label)})
    logger.debug("Issue {}#{} traitée : {} retrait(s)", depot, numero, len(a_retirer))
    return decisions


def rapport(lignes: list[tuple[int, Decision]], simulation: bool, erreurs: list[str]) -> str:
    """Résumé Markdown pour $GITHUB_STEP_SUMMARY."""
    logger.debug("Rapport : {} décision(s), {} erreur(s)", len(lignes), len(erreurs))
    titre = "## Désassignation des issues inactives" + (" (simulation)" if simulation else "")
    if not lignes and not erreurs:
        return f"{titre}\n\nAucune issue ouverte assignée.\n"
    texte = [titre, "", "| Issue | Assigné | Décision | Raison |", "|---|---|---|---|"]
    for numero, d in lignes:
        verdict = "retiré" if d.retirer else "conservé"
        texte.append(f"| #{numero} | @{d.login} | {verdict} | {d.raison} |")
    texte.extend(f"\nErreur : {e}" for e in erreurs)
    return "\n".join(texte) + "\n"


def executer(
    client: Client, depot: str, maintenant: datetime, delai: timedelta, label: str, simulation: bool
) -> tuple[str, int]:
    """Traite toutes les issues ouvertes assignées ; renvoie (rapport, code de sortie)."""
    logger.debug("Exécution sur {} : simulation={}", depot, simulation)
    issues = [
        i for i in client.pages(f"/repos/{depot}/issues?state=open&assignee=*") if "pull_request" not in i
    ]
    logger.info("{} : {} issue(s) ouverte(s) assignée(s)", depot, len(issues))
    lignes: list[tuple[int, Decision]] = []
    erreurs: list[str] = []
    for issue in issues:
        try:
            decisions = traiter_issue(client, depot, issue, maintenant, delai, label, simulation)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as err:
            logger.exception("{}#{} : échec du traitement", depot, issue.get("number"))
            erreurs.append(f"#{issue.get('number')} : {err}")
            continue
        lignes.extend((issue["number"], d) for d in decisions)
    code = 1 if erreurs else 0
    logger.debug("Exécution terminée : code {}", code)
    return rapport(lignes, simulation, erreurs), code


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée en ligne de commande."""
    logger.debug("Arguments : {}", argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--depot", required=True, help="propriétaire/nom du dépôt")
    parser.add_argument("--delai-heures", type=float, default=24.0)
    parser.add_argument("--label-exemption", default=LABEL_EXEMPTION)
    parser.add_argument("--simulation", action="store_true", help="n'écrit rien, affiche les décisions")
    parser.add_argument("--sortie", help="fichier où écrire le rapport Markdown")
    args = parser.parse_args(argv)

    client = Client(os.environ.get("GITHUB_TOKEN"))
    texte, code = executer(
        client,
        args.depot,
        datetime.now(timezone.utc),
        timedelta(hours=args.delai_heures),
        args.label_exemption,
        args.simulation,
    )
    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8") as fichier:
            fichier.write(texte)
    else:
        sys.stdout.write(texte)
    logger.debug("Fin : code {}", code)
    return code


if __name__ == "__main__":  # pragma: no cover - point d'entrée CLI
    sys.exit(main())
