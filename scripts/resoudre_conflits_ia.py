#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# /// script
# requires-python = ">=3.10"
# dependencies = ["loguru==0.7.3"]
# ///
"""Résout les conflits SIMPLES d'une fusion git en cours avec un LLM local (Ollama).

À lancer dans un dépôt où `git -c merge.conflictStyle=diff3 merge ...` vient
d'échouer sur des conflits. Le script :
  1. refuse d'emblée les cas non simples (trop de fichiers ou de blocs, blocs
     trop longs, fichiers exclus : verrous de dépendances, workflows, binaires) ;
  2. envoie chaque bloc (base / ours / theirs) au modèle, réponse JSON imposée ;
  3. valide la réponse : aucune ligne inventée (chaque ligne vient de « ours »
     ou de « theirs »), aucun marqueur restant, pas de résolution vide ;
  4. réécrit les fichiers et les ajoute à l'index (`git add`), tout ou rien.

Il ne committe et ne pousse rien : le workflow maj-branches-reutilisable.yml
s'en charge (SOCLE.md §11).

Codes de sortie : 0 tout résolu, 2 conflit jugé non simple (rien n'est
modifié), 1 erreur technique (Ollama injoignable, réponse illisible…).

Variables d'environnement :
  OLLAMA_URL        ex. http://192.168.105.46:1435 (obligatoire)
  OLLAMA_MODELE     ex. qwen2.5-coder:14b (obligatoire)
  IA_MAX_FICHIERS   défaut 3
  IA_MAX_BLOCS      défaut 6 (tous fichiers confondus)
  IA_MAX_LIGNES     défaut 60 (par côté et par bloc)
  IA_DELAI          délai max d'une réponse Ollama en secondes (défaut 900, CPU seul)
  IA_RESUME         fichier Markdown du résumé (défaut resume-ia.md)
  SOCLE_DEBUG       1 pour afficher les traces de debug (SOCLE §8)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from loguru import logger

EXCLUS = re.compile(
    r"(^|/)(uv\.lock|poetry\.lock|Cargo\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock"
    r"|go\.sum|Gemfile\.lock)$|^\.github/workflows/|^\.github/branch-protection/"
)
OUVRE, BASE, SEP, FERME = "<<<<<<< ", "||||||| ", "=======", ">>>>>>> "

SCHEMA = {
    "type": "object",
    "properties": {
        "simple": {"type": "boolean"},
        "resolution": {"type": "string"},
        "explication": {"type": "string"},
    },
    "required": ["simple", "resolution", "explication"],
}

CONSIGNE = """Tu résous un conflit de fusion git dans le fichier {chemin}.
- BASE : version commune d'origine.
- OURS : version de la branche de la pull request.
- THEIRS : version de la branche cible (dev), déjà validée.

Règles strictes :
1. Conflit simple = les deux côtés ajoutent ou modifient des choses indépendantes
   (imports, entrées de liste, fonctions distinctes, lignes voisines). Garde alors
   les deux apports, dans un ordre cohérent, sans doublon.
2. Si les deux côtés modifient la MÊME logique de façon incompatible, ou si tu
   hésites : réponds "simple": false et une résolution vide.
3. N'invente aucune ligne : chaque ligne de ta résolution doit venir de OURS ou THEIRS.
4. Respecte exactement l'indentation. Pas de marqueurs <<<<<<<, =======, >>>>>>>.
5. "resolution" contient uniquement le texte qui remplace le bloc en conflit.

Contexte avant le bloc :
{avant}
----- BASE -----
{base}
----- OURS -----
{ours}
----- THEIRS -----
{theirs}
----- FIN -----
Contexte après le bloc :
{apres}
"""


class NonSimple(Exception):
    """Le conflit dépasse ce que l'IA a le droit de résoudre."""


@dataclass
class Bloc:
    """Un bloc en conflit au format diff3, et sa résolution validée."""

    ours: list[str]
    base: list[str]
    theirs: list[str]
    resolution: list[str] = field(default_factory=list)
    explication: str = ""


@dataclass
class Reglages:
    """Paramètres lus dans l'environnement."""

    url: str
    modele: str
    max_fichiers: int = 3
    max_blocs: int = 6
    max_lignes: int = 60
    delai: int = 900
    resume: str = "resume-ia.md"


def lire_reglages(env: dict[str, str]) -> Reglages:
    """Construit les réglages ; ValueError si OLLAMA_URL ou OLLAMA_MODELE manque."""
    logger.debug("Lecture des réglages : {} variable(s)", len(env))
    url, modele = env.get("OLLAMA_URL", ""), env.get("OLLAMA_MODELE", "")
    if not url or not modele:
        logger.debug("Sortie lire_reglages : variable obligatoire absente")
        raise ValueError("OLLAMA_URL et OLLAMA_MODELE sont obligatoires")
    reglages = Reglages(
        url=url,
        modele=modele,
        max_fichiers=int(env.get("IA_MAX_FICHIERS", "3")),
        max_blocs=int(env.get("IA_MAX_BLOCS", "6")),
        max_lignes=int(env.get("IA_MAX_LIGNES", "60")),
        delai=int(env.get("IA_DELAI", "900")),
        resume=env.get("IA_RESUME", "resume-ia.md"),
    )
    logger.debug("Sortie lire_reglages : modèle {}, délai {} s", reglages.modele, reglages.delai)
    return reglages


def lire_conflits(texte: str) -> list[str | Bloc]:
    """Découpe un fichier à marqueurs diff3 en segments : texte ou Bloc."""
    logger.debug("Lecture des conflits : {} caractère(s)", len(texte))
    segments: list[str | Bloc] = []
    lignes = texte.splitlines(keepends=True)
    i, tampon = 0, []
    while i < len(lignes):
        if not lignes[i].startswith(OUVRE):
            tampon.append(lignes[i])
            i += 1
            continue
        if tampon:
            segments.append("".join(tampon))
            tampon = []
        parties: dict[str, list[str]] = {"ours": [], "base": [], "theirs": []}
        courant, base_vue, ferme = "ours", False, False
        i += 1
        while i < len(lignes):
            ligne = lignes[i]
            if ligne.startswith(FERME):
                ferme = True
                break
            if ligne.startswith(OUVRE):
                logger.debug("Sortie lire_conflits : conflits imbriqués")
                raise NonSimple("conflits imbriqués")
            if ligne.startswith(BASE):
                courant, base_vue = "base", True
            elif ligne.rstrip("\r\n") == SEP:
                courant = "theirs"
            else:
                parties[courant].append(ligne)
            i += 1
        if not ferme:
            logger.debug("Sortie lire_conflits : marqueur de fin manquant")
            raise NonSimple("marqueur de fin manquant")
        if not base_vue:
            logger.debug("Sortie lire_conflits : pas de section base")
            raise NonSimple("marqueurs sans base : relancer la fusion avec merge.conflictStyle=diff3")
        segments.append(Bloc(parties["ours"], parties["base"], parties["theirs"]))
        i += 1
    if tampon:
        segments.append("".join(tampon))
    logger.debug("Sortie lire_conflits : {} segment(s)", len(segments))
    return segments


def valider(bloc: Bloc, resolution: str) -> list[str]:
    """Contrôles anti-hallucination ; renvoie les lignes retenues."""
    logger.debug("Validation : {} caractère(s) proposés", len(resolution))
    lignes = resolution.splitlines(keepends=True)
    if lignes and not lignes[-1].endswith("\n"):
        lignes[-1] += "\n"
    for ligne in lignes:
        if ligne.startswith((OUVRE, BASE, FERME)) or ligne.rstrip("\r\n") == SEP:
            logger.debug("Sortie valider : marqueur dans la réponse")
            raise NonSimple("marqueur de conflit dans la réponse")
    autorisees = {ligne.rstrip() for ligne in bloc.ours + bloc.theirs}
    inventees = [ligne for ligne in lignes if ligne.strip() and ligne.rstrip() not in autorisees]
    if inventees:
        logger.debug("Sortie valider : {} ligne(s) inventée(s)", len(inventees))
        raise NonSimple(f"ligne absente des deux côtés : {inventees[0].strip()[:80]!r}")
    contenu = any(ligne.strip() for ligne in bloc.ours + bloc.theirs)
    if contenu and not any(ligne.strip() for ligne in lignes):
        logger.debug("Sortie valider : résolution vide")
        raise NonSimple("résolution vide alors que les deux côtés ont du contenu")
    logger.debug("Sortie valider : {} ligne(s) retenue(s)", len(lignes))
    return lignes


def interroger(reglages: Reglages, invite: str) -> dict:
    """Appelle /api/chat d'Ollama avec un schéma JSON imposé ; renvoie la réponse décodée."""
    logger.debug("Appel Ollama {} : invite de {} caractère(s)", reglages.modele, len(invite))
    corps = json.dumps({
        "model": reglages.modele,
        "messages": [{"role": "user", "content": invite}],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8192},
    }).encode()
    requete = urllib.request.Request(
        f"{reglages.url.rstrip('/')}/api/chat", corps, {"Content-Type": "application/json"})
    with urllib.request.urlopen(requete, timeout=reglages.delai) as reponse:
        donnees = json.loads(json.loads(reponse.read())["message"]["content"])
    logger.debug("Sortie interroger : simple={}", donnees.get("simple"))
    return donnees


def contexte(segments: list, index: int, sens: int, n: int = 15) -> str:
    """Lignes de texte voisines du bloc (avant si sens < 0, après sinon)."""
    logger.debug("Contexte du segment {} (sens {})", index, sens)
    voisin = index + sens
    if 0 <= voisin < len(segments) and isinstance(segments[voisin], str):
        lignes = segments[voisin].splitlines()
        logger.debug("Sortie contexte : {} ligne(s) disponibles", len(lignes))
        return "\n".join(lignes[-n:] if sens < 0 else lignes[:n])
    logger.debug("Sortie contexte : aucun texte voisin")
    return "(rien)"


def git(*args: str) -> str:
    """Lance git et renvoie sa sortie standard."""
    logger.debug("git {}", " ".join(args))
    sortie = subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout
    logger.debug("Sortie git : {} caractère(s)", len(sortie))
    return sortie


def collecter(reglages: Reglages, fichiers: list[str]) -> dict[str, list]:
    """Lit les fichiers en conflit et applique les garde-fous de taille et d'exclusion."""
    logger.debug("Collecte : {} fichier(s) en conflit", len(fichiers))
    if not fichiers:
        logger.debug("Sortie collecter : aucun conflit de contenu")
        raise NonSimple("aucun fichier en conflit de contenu (conflit de renommage ou de suppression ?)")
    if len(fichiers) > reglages.max_fichiers:
        logger.debug("Sortie collecter : trop de fichiers")
        raise NonSimple(f"{len(fichiers)} fichiers en conflit (max {reglages.max_fichiers})")
    for chemin in fichiers:
        if EXCLUS.search(chemin):
            logger.debug("Sortie collecter : {} exclu", chemin)
            raise NonSimple(f"{chemin} : fichier exclu de la résolution automatique")
    contenus: dict[str, list] = {}
    total = 0
    for chemin in fichiers:
        try:
            with open(chemin, encoding="utf-8") as f:
                texte = f.read()
        except (UnicodeDecodeError, FileNotFoundError) as exc:
            logger.info("{} : illisible en texte ({})", chemin, exc.__class__.__name__)
            raise NonSimple(f"{chemin} : illisible en texte ({exc.__class__.__name__})") from exc
        segments = lire_conflits(texte)
        for bloc in (s for s in segments if isinstance(s, Bloc)):
            total += 1
            if max(len(bloc.ours), len(bloc.theirs), len(bloc.base)) > reglages.max_lignes:
                logger.debug("Sortie collecter : bloc trop long dans {}", chemin)
                raise NonSimple(f"{chemin} : bloc de plus de {reglages.max_lignes} lignes")
        contenus[chemin] = segments
    if total > reglages.max_blocs:
        logger.debug("Sortie collecter : trop de blocs")
        raise NonSimple(f"{total} blocs en conflit (max {reglages.max_blocs})")
    logger.debug("Sortie collecter : {} bloc(s) à résoudre", total)
    return contenus


def resoudre(reglages: Reglages, contenus: dict[str, list]) -> list[str]:
    """Fait résoudre chaque bloc par le modèle et valide ; renvoie le rapport Markdown."""
    logger.debug("Résolution : {} fichier(s)", len(contenus))
    entete = (f"Conflits résolus par **{reglages.modele}** (IA locale). "
              "**Relire avant de repasser la PR en « Ready ».**")
    rapport = [entete, ""]
    for chemin, segments in contenus.items():
        rapport.append(f"### `{chemin}`")
        for i, seg in enumerate(segments):
            if not isinstance(seg, Bloc):
                continue
            reponse = interroger(reglages, CONSIGNE.format(
                chemin=chemin, avant=contexte(segments, i, -1), apres=contexte(segments, i, 1),
                base="".join(seg.base), ours="".join(seg.ours), theirs="".join(seg.theirs)))
            if not reponse.get("simple"):
                logger.debug("Sortie resoudre : modèle « non simple » sur {}", chemin)
                raise NonSimple(f"{chemin} : le modèle juge le conflit non simple "
                                f"({str(reponse.get('explication', ''))[:200]})")
            seg.resolution = valider(seg, str(reponse.get("resolution", "")))
            seg.explication = str(reponse.get("explication", ""))
            logger.info("{} : bloc {} résolu", chemin, i)
            rapport += [f"- {seg.explication}", "", "```", "".join(seg.resolution).rstrip("\n"), "```", ""]
    logger.debug("Sortie resoudre : rapport de {} ligne(s)", len(rapport))
    return rapport


def ecrire(contenus: dict[str, list]) -> None:
    """Réécrit les fichiers résolus et les ajoute à l'index."""
    logger.debug("Écriture : {} fichier(s)", len(contenus))
    for chemin, segments in contenus.items():
        with open(chemin, "w", encoding="utf-8") as f:
            f.write("".join(s if isinstance(s, str) else "".join(s.resolution) for s in segments))
        git("add", "--", chemin)
    logger.debug("Sortie ecrire")


def main(env: dict[str, str] | None = None) -> int:
    """Point d'entrée ; renvoie le code de sortie (0, 1 ou 2)."""
    env = dict(os.environ) if env is None else env
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if env.get("SOCLE_DEBUG") else "WARNING")
    logger.debug("Démarrage de la résolution IA")
    try:
        reglages = lire_reglages(env)
    except ValueError as exc:
        logger.error("{}", exc)
        logger.debug("Sortie main : code 1")
        return 1
    try:
        fichiers = [f for f in git("diff", "--name-only", "--diff-filter=U").splitlines() if f]
        contenus = collecter(reglages, fichiers)
        rapport = resoudre(reglages, contenus)
    except NonSimple as exc:
        logger.info("Conflit non simple : {}", exc)
        with open(reglages.resume, "w", encoding="utf-8") as f:
            f.write(f"Conflit non résolu automatiquement : {exc}\n")
        logger.debug("Sortie main : code 2")
        return 2
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        logger.error("Ollama ({}) : {} ({})", reglages.url, exc, exc.__class__.__name__)
        logger.debug("Sortie main : code 1")
        return 1
    ecrire(contenus)  # tout est validé : on écrit seulement maintenant (tout ou rien)
    with open(reglages.resume, "w", encoding="utf-8") as f:
        f.write("\n".join(rapport) + "\n")
    logger.info("{} fichier(s) résolu(s) par {}", len(contenus), reglages.modele)
    logger.debug("Sortie main : code 0")
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée CLI
    sys.exit(main())
