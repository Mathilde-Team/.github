"""Tests du script shell du workflow maj-branches-reutilisable.yml.

GitHub exécute les blocs « run: » avec « bash -e » : une commande qui
échoue (exit non nul) termine le bloc avant tout « ; code=$? » suivant.
Le script de résolution IA sort avec 2 par contrat (« conflit non
simple ») : l'appel doit donc être protégé par « || code=$? » sous
peine de faire échouer le job au lieu d'étiqueter la PR (issue #25).
"""

from pathlib import Path

FICHIER = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "maj-branches-reutilisable.yml"
)


def test_appel_du_script_ia_protege_contre_bash_e() -> None:
    """L'appel au script IA ne doit pas tuer le bloc sous « bash -e »."""
    contenu = FICHIER.read_text(encoding="utf-8")
    appel = next(
        (
            ligne.strip()
            for ligne in contenu.splitlines()
            if "uv run .socle/scripts/resoudre_conflits_ia.py" in ligne
        ),
        None,
    )
    assert appel is not None, "l'appel au script IA a disparu du workflow"
    # Le code 2 du script (conflit non simple) doit être capturé, pas subi.
    assert appel.endswith("|| code=$?"), (
        "appel du script IA non protégé : « bash -e » (défaut GitHub) "
        "terminerait le bloc au code de sortie 2 avant le « code=$? » (issue #25)"
    )


def test_pas_d_instruction_non_protegee_suivie_de_code_dollar() -> None:
    """Aucune commande suivie de « ; code=$? » ne doit rester dans le workflow."""
    contenu = FICHIER.read_text(encoding="utf-8")
    for ligne in contenu.splitlines():
        if ligne.strip().startswith("#"):
            continue
        if "code=$?" in ligne:
            assert "|| code=$?" in ligne, (
                f"« ; code=$? » sans protection « || » : {ligne.strip()} "
                "(bash -e, issue #25)"
            )
