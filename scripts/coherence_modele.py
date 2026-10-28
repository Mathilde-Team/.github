#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# /// script
# requires-python = ">=3.10"
# dependencies = ["loguru==0.7.3"]
# ///
"""Signale les divergences entre Mathilde-Team/.github et modele-depot.

Les deux dépôts partagent des fichiers (scripts du socle, workflows de garde et
de protection, configuration…). Tout fichier présent aux deux endroits doit
être identique, sauf exception déclarée dans coherence-exceptions.json.

Pour chaque divergence, l'issue « Cohérence .github ↔ modele-depot » de ce
dépôt indique la version la plus récente (dernier commit qui touche le
fichier) : c'est la réponse par défaut, mais la question est posée. L'issue
est créée, mise à jour (avec un commentaire à chaque nouvel écart, pour
notifier), puis fermée quand tout est de nouveau cohérent. Une issue fermée à
la main sans corriger vaut « écart accepté » tant que les fichiers ne
changent plus.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

API = "https://api.github.com"
DEPOTS = ("Mathilde-Team/.github", "Mathilde-Team/modele-depot")
TITRE = "Cohérence .github ↔ modele-depot : divergences à arbitrer"
MARQUEUR = "<!-- coherence-modele"
LIGNES_DIFF = 60


@dataclass
class Commit:
    """Dernier commit qui touche un fichier dans un dépôt."""

    sha: str
    date: str
    auteur: str
    message: str


@dataclass
class Divergence:
    """Un fichier présent dans les deux dépôts, avec un contenu différent."""

    chemin: str
    blobs: dict[str, str]
    commits: dict[str, Commit | None] = field(default_factory=dict)
    diff: str = ""

    @property
    def recent(self) -> str | None:
        """Dépôt dont la version est la plus récente (None si indécidable)."""
        dates = {d: c.date for d, c in self.commits.items() if c}
        if len(dates) < 2 or len(set(dates.values())) < 2:
            logger.debug("{} : dates indécidables {}", self.chemin, dates)
            return None
        depot = max(dates, key=dates.__getitem__)
        logger.debug("{} : version la plus récente dans {}", self.chemin, depot)
        return depot


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
        logger.debug("{} {} : {} octets", methode, url, len(brut))
        return (json.loads(brut) if brut else None), lien

    def get(self, chemin: str) -> object:
        """GET simple."""
        resultat, _ = self.requete("GET", API + chemin)
        logger.debug("GET {} terminé", chemin)
        return resultat

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
        """POST/PATCH avec un corps JSON."""
        resultat, _ = self.requete(methode, API + chemin, corps)
        logger.debug("{} {} terminé", methode, chemin)
        return resultat


def arbre(client: Client, depot: str, ref: str) -> dict[str, str]:
    """Fichiers du dépôt à `ref` : chemin → SHA du blob."""
    logger.debug("Arbre de {}@{}", depot, ref)
    reponse = client.get(f"/repos/{depot}/git/trees/{urllib.parse.quote(ref)}?recursive=1")
    fichiers = {e["path"]: e["sha"] for e in reponse["tree"] if e["type"] == "blob"}
    logger.debug("{}@{} : {} fichiers", depot, ref, len(fichiers))
    return fichiers


def dernier_commit(client: Client, depot: str, ref: str, chemin: str) -> Commit | None:
    """Dernier commit de `ref` qui modifie `chemin`."""
    logger.debug("Dernier commit de {} dans {}@{}", chemin, depot, ref)
    requete = urllib.parse.urlencode({"sha": ref, "path": chemin, "per_page": 1})
    commits = client.get(f"/repos/{depot}/commits?{requete}") or []
    if not commits:
        logger.debug("{} : aucun commit trouvé dans {}", chemin, depot)
        return None
    c = commits[0]
    auteur = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
    commit = Commit(c["sha"], c["commit"]["committer"]["date"], auteur, c["commit"]["message"].splitlines()[0])
    logger.debug("{} dans {} : {} du {}", chemin, depot, commit.sha[:7], commit.date)
    return commit


def contenu(client: Client, depot: str, sha: str) -> str:
    """Texte d'un blob (remplace les octets non UTF-8)."""
    logger.debug("Blob {} de {}", sha[:7], depot)
    blob = client.get(f"/repos/{depot}/git/blobs/{sha}")
    texte = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
    logger.debug("Blob {} : {} caractères", sha[:7], len(texte))
    return texte


def differences(a: str, b: str, nom_a: str, nom_b: str) -> str:
    """Diff unifié, tronqué à LIGNES_DIFF lignes."""
    logger.debug("Diff {} ↔ {}", nom_a, nom_b)
    lignes = list(difflib.unified_diff(a.splitlines(), b.splitlines(), nom_a, nom_b, n=2, lineterm=""))
    if len(lignes) > LIGNES_DIFF:
        lignes = lignes[:LIGNES_DIFF] + [f"… ({len(lignes) - LIGNES_DIFF} lignes de plus)"]
    logger.debug("Diff : {} lignes", len(lignes))
    return "\n".join(lignes)


def trouver(client: Client, ref: str, exceptions: dict[str, str]) -> list[Divergence]:
    """Liste les fichiers communs aux deux dépôts dont le contenu diffère."""
    logger.debug("Recherche des divergences sur {} ({} exceptions)", ref, len(exceptions))
    a, b = DEPOTS
    arbres = {d: arbre(client, d, ref) for d in DEPOTS}
    communs = sorted(set(arbres[a]) & set(arbres[b]))
    resultat = []
    for chemin in communs:
        if chemin in exceptions:
            logger.debug("{} : exception ({})", chemin, exceptions[chemin])
            continue
        blobs = {d: arbres[d][chemin] for d in DEPOTS}
        if blobs[a] == blobs[b]:
            continue
        div = Divergence(chemin, blobs)
        div.commits = {d: dernier_commit(client, d, ref, chemin) for d in DEPOTS}
        div.diff = differences(
            contenu(client, a, blobs[a]), contenu(client, b, blobs[b]), f"{a}/{chemin}", f"{b}/{chemin}"
        )
        logger.info("Divergence : {}", chemin)
        resultat.append(div)
    logger.debug("{} fichier(s) commun(s), {} divergence(s)", len(communs), len(resultat))
    return resultat


def etat(divs: list[Divergence]) -> dict[str, list[str]]:
    """Empreinte des divergences : chemin → SHA des deux blobs."""
    empreinte = {d.chemin: [d.blobs[x] for x in DEPOTS] for d in divs}
    logger.debug("État : {} entrée(s)", len(empreinte))
    return empreinte


def signature(empreinte: dict[str, list[str]]) -> str:
    """Résumé stable de l'état (sert à reconnaître un écart déjà arbitré)."""
    valeur = hashlib.sha256(json.dumps(empreinte, sort_keys=True).encode()).hexdigest()[:16]
    logger.debug("Signature {}", valeur)
    return valeur


def lire_etat(corps: str | None) -> dict[str, list[str]]:
    """Relit l'état mémorisé dans le corps d'une issue."""
    trouve = re.search(re.escape(MARQUEUR) + r" (\{.*?\}) -->", corps or "")
    if not trouve:
        logger.debug("Aucun état mémorisé dans l'issue")
        return {}
    try:
        empreinte = json.loads(trouve.group(1))
    except json.JSONDecodeError:
        logger.warning("État illisible dans l'issue, ignoré")
        return {}
    logger.debug("État relu : {} entrée(s)", len(empreinte))
    return empreinte


def nom_court(depot: str) -> str:
    """« Mathilde-Team/.github » → « .github »."""
    return depot.split("/", 1)[1]


def decrire_commit(depot: str, commit: Commit | None) -> str:
    """Ligne Markdown décrivant le dernier commit d'un fichier."""
    if commit is None:
        return f"`{nom_court(depot)}` : historique introuvable"
    lien = f"https://github.com/{depot}/commit/{commit.sha}"
    return f"`{nom_court(depot)}` : [{commit.sha[:7]}]({lien}) du {commit.date[:10]} par @{commit.auteur} — « {commit.message} »"


def corps_issue(divs: list[Divergence], ref: str) -> str:
    """Corps de l'issue de suivi."""
    logger.debug("Corps de l'issue : {} divergence(s)", len(divs))
    empreinte = etat(divs)
    texte = [
        f"{MARQUEUR} {json.dumps(empreinte, sort_keys=True)} -->",
        f"Contrôle quotidien : ces fichiers existent dans `.github` **et** dans `modele-depot` (branche `{ref}`) "
        "mais n'ont pas le même contenu.",
        "",
        "**Réponse par défaut : la version la plus récente fait foi** et sera reportée dans l'autre dépôt. "
        "Répondez en commentaire si ce n'est pas le bon choix pour un fichier. Une différence voulue se déclare "
        "dans [`coherence-exceptions.json`](https://github.com/Mathilde-Team/.github/blob/main/coherence-exceptions.json). Fermer l'issue sans corriger "
        "vaut « écart accepté » tant que ces fichiers ne changent plus.",
        "",
    ]
    for d in divs:
        recent = d.recent
        if recent:
            autre = next(x for x in DEPOTS if x != recent)
            defaut = f"**Par défaut** : garder la version de `{nom_court(recent)}` et la reporter dans `{nom_court(autre)}`."
        else:
            defaut = "**Par défaut** : indécidable (même date ou historique absent), à arbitrer."
        texte += [f"### `{d.chemin}`", ""]
        texte += [f"- {decrire_commit(x, d.commits.get(x))}" for x in DEPOTS]
        texte += ["", defaut, "", "<details><summary>Différences</summary>", "", "````diff", d.diff, "````", "", "</details>", ""]
    logger.debug("Corps de l'issue prêt")
    return "\n".join(texte)


def synchroniser(client: Client, depot: str, divs: list[Divergence], ref: str, simulation: bool) -> str:
    """Crée, met à jour ou ferme l'issue de suivi ; renvoie l'action menée."""
    logger.debug("Synchronisation de l'issue dans {} : {} divergence(s)", depot, len(divs))
    issues = [
        i
        for i in client.pages(f"/repos/{depot}/issues?state=all")
        if "pull_request" not in i and MARQUEUR in (i.get("body") or "")
    ]
    ouverte = next((i for i in issues if i["state"] == "open"), None)
    fermee = next((i for i in issues if i["state"] == "closed"), None)
    actuel = etat(divs)

    if not divs:
        if ouverte is None:
            logger.debug("Tout est cohérent, aucune issue ouverte")
            return "cohérent"
        if not simulation:
            chemin = f"/repos/{depot}/issues/{ouverte['number']}"
            client.envoyer("POST", chemin + "/comments", {"body": "Plus aucune divergence : `.github` et `modele-depot` sont cohérents."})
            client.envoyer("PATCH", chemin, {"state": "closed", "state_reason": "completed"})
        logger.info("Issue #{} fermée : tout est cohérent", ouverte["number"])
        return f"fermeture #{ouverte['number']}"

    corps = corps_issue(divs, ref)
    if ouverte is not None:
        precedent = lire_etat(ouverte.get("body"))
        if precedent == actuel:
            logger.debug("Issue #{} déjà à jour", ouverte["number"])
            return f"inchangé #{ouverte['number']}"
        nouveaux = sorted(c for c, v in actuel.items() if precedent.get(c) != v)
        if not simulation:
            chemin = f"/repos/{depot}/issues/{ouverte['number']}"
            client.envoyer("PATCH", chemin, {"body": corps})
            if nouveaux:
                liste = "\n".join(f"- `{c}`" for c in nouveaux)
                client.envoyer("POST", chemin + "/comments", {"body": f"Nouvelle divergence à arbitrer :\n\n{liste}"})
        logger.info("Issue #{} mise à jour ({} nouvel(s) écart(s))", ouverte["number"], len(nouveaux))
        return f"mise à jour #{ouverte['number']}"

    if fermee is not None and signature(lire_etat(fermee.get("body"))) == signature(actuel):
        logger.info("Écarts déjà arbitrés dans l'issue fermée #{}", fermee["number"])
        return f"déjà arbitré #{fermee['number']}"
    if not simulation:
        client.envoyer("POST", f"/repos/{depot}/issues", {"title": TITRE, "body": corps, "labels": ["question"]})
    logger.info("Issue créée pour {} divergence(s)", len(divs))
    return "création"


def rapport(divs: list[Divergence], action: str, simulation: bool) -> str:
    """Résumé Markdown pour $GITHUB_STEP_SUMMARY."""
    logger.debug("Rapport : {} divergence(s), action {}", len(divs), action)
    titre = "## Cohérence .github ↔ modele-depot" + (" (simulation)" if simulation else "")
    lignes = [titre, "", f"Issue de suivi : {action}.", ""]
    if not divs:
        lignes.append("Aucune divergence.")
    for d in divs:
        recent = d.recent
        lignes.append(f"- `{d.chemin}` : plus récent dans {nom_court(recent) if recent else '— (indécidable)'}")
    return "\n".join(lignes) + "\n"


def charger_exceptions(chemin: Path) -> dict[str, str]:
    """Exceptions assumées : chemin → raison (clés « _… » ignorées)."""
    logger.debug("Lecture des exceptions {}", chemin)
    if not chemin.exists():
        logger.debug("Pas de fichier d'exceptions")
        return {}
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    exceptions = {k: v for k, v in donnees.items() if not k.startswith("_")}
    logger.debug("{} exception(s)", len(exceptions))
    return exceptions


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée en ligne de commande."""
    logger.debug("Arguments : {}", argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--issue", default=DEPOTS[0], help="dépôt où tenir l'issue de suivi")
    parser.add_argument("--ref", default="main", help="branche comparée dans les deux dépôts")
    parser.add_argument("--exceptions", default="coherence-exceptions.json")
    parser.add_argument("--simulation", action="store_true", help="n'écrit rien")
    parser.add_argument("--sortie", help="fichier où écrire le rapport Markdown")
    args = parser.parse_args(argv)

    client = Client(os.environ.get("GITHUB_TOKEN"))
    divs = trouver(client, args.ref, charger_exceptions(Path(args.exceptions)))
    action = synchroniser(client, args.issue, divs, args.ref, args.simulation)
    texte = rapport(divs, action, args.simulation)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding="utf-8")
    else:
        sys.stdout.write(texte)
    logger.debug("Fin : {} divergence(s)", len(divs))
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée CLI
    sys.exit(main())
