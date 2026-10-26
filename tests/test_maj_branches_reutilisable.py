"""Tests du correctif #25 : l'exit 2 de resoudre_conflits_ia.py (« conflit
jugé non simple », par contrat) ne doit pas tuer le job du workflow
maj-branches-reutilisable ; seul l'exit 1 (erreur technique Ollama) reste
bloquant.

Le workflow s'exécute sous ``bash -e {0}`` (défaut GitHub Actions) augmenté
du ``set -uo pipefail`` de l'étape : un ``; code=$?`` derrière une commande
qui échoue ne protège rien — le shell meurt avant l'affectation. Seuls
``|| …`` ou un contexte ``if`` neutralisent ``-e``.

La ligne d'invocation est extraite TELLE QUELLE du workflow : si le motif
est modifié ou retiré, ces tests échouent.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
WORKFLOW = RACINE / ".github" / "workflows" / "maj-branches-reutilisable.yml"

LIGNE_INVOCATION = re.compile(
    r"IA_RESUME=resume-ia\.md uv run \.socle/scripts/resoudre_conflits_ia\.py[^\n]*"
)


def invocation_du_workflow() -> str:
    """La ligne d'invocation du script IA, extraite telle quelle du workflow."""
    trouve = LIGNE_INVOCATION.search(WORKFLOW.read_text(encoding="utf-8"))
    assert trouve, "ligne d'invocation de resoudre_conflits_ia.py introuvable dans le workflow"
    return trouve.group(0).strip()


def lancer_etape(sortie_ia: int) -> subprocess.CompletedProcess:
    """Exécute sous ``bash -euo pipefail`` une étape miniature qui reprend la
    ligne d'invocation du workflow, avec un script IA factice sortant
    ``sortie_ia`` et un ``uv`` factice (``uv run X`` → ``python3 X``).

    Renvoie le résultat de l'étape : code de sortie, sortie standard.
    """
    with tempfile.TemporaryDirectory() as brut:
        tmp = Path(brut)
        scripts = tmp / ".socle" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "resoudre_conflits_ia.py").write_text(
            "import os\nimport sys\n\nsys.exit(int(os.environ[\"CODE_SORTIE_IA\"]))\n",
            encoding="utf-8",
        )
        faux_bin = tmp / "bin"
        faux_bin.mkdir()
        (faux_bin / "uv").write_text(
            '#!/usr/bin/env bash\n[ "$1" = run ] && shift\nexec python3 "$@"\n',
            encoding="utf-8",
        )
        (faux_bin / "uv").chmod(0o755)
        etape = tmp / "etape.sh"
        etape.write_text(
            "set -euo pipefail\n"
            "code=0\n"
            f"{invocation_du_workflow()}\n"
            'if [ "$code" -eq 1 ]; then echo "::error::erreur technique"; exit 1; fi\n'
            'if [ "$code" -eq 0 ]; then echo "RESOLU_PAR_IA"; exit 0; fi\n'
            'echo "PR_ETIQUETEE_CONFLIT"\n',
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "PATH": f"{faux_bin}{os.pathsep}{os.environ['PATH']}",
            "CODE_SORTIE_IA": str(sortie_ia),
        }
        return subprocess.run(
            ["bash", str(etape)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp,
            check=False,
        )


class TestExit2NeTuePasLeJob(unittest.TestCase):
    def test_exit2_le_job_continue_jusqu_a_l_etiquetage(self):
        """Exit 2 = conflit non simple : l'étape doit atteindre l'étiquetage."""
        resultat = lancer_etape(2)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertIn("PR_ETIQUETEE_CONFLIT", resultat.stdout)

    def test_exit1_erreur_technique_bloquante(self):
        """Exit 1 = erreur technique Ollama : l'étape doit échouer."""
        resultat = lancer_etape(1)
        self.assertEqual(resultat.returncode, 1, resultat.stderr)
        self.assertNotIn("PR_ETIQUETEE_CONFLIT", resultat.stdout)


class TestMotifDuWorkflow(unittest.TestCase):
    def test_l_invocation_est_protegee_de_bash_e(self):
        """L'ancien motif fragile (``; code=$?``) doit avoir disparu."""
        contenu = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("resoudre_conflits_ia.py || code=$?", contenu)
        self.assertNotIn("resoudre_conflits_ia.py; code=$?", contenu)

    def test_le_code_1_reste_bloquant_dans_le_workflow(self):
        """Le workflow doit garder le garde-fou « exit 1 → job bloqué »."""
        contenu = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [ "$code" -eq 1 ]', contenu)
        self.assertRegex(contenu, r"exit 1\s*\n\s*fi\s*\n\s*if \[ \"\$code\" -eq 0 \]")


if __name__ == "__main__":
    unittest.main()
