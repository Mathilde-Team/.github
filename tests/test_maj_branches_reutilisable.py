"""Tests du correctif #25 : l'exit 2 de resoudre_conflits_ia.py (« conflit
jugé non simple », par contrat) ne doit pas tuer le job du workflow
maj-branches-reutilisable ; seul l'exit 1 (erreur technique Ollama) reste
bloquant.

Le workflow s'exécute sous ``bash -e {0}`` (défaut GitHub Actions) augmenté
du ``set -uo pipefail`` de l'étape : un ``; code=$?`` derrière une commande
qui échoue ne protège rien — le shell meurt avant l'affectation. Seuls
``|| …`` ou un contexte ``if`` neutralisent ``-e``.

Le bloc d'invocation (initialisation ``code=0`` comprise) est extrait TEL
QUEL du workflow et exécuté sous ``bash -euo pipefail`` avec un script IA
factice : si l'initialisation disparaît, un succès du script laisse ``code``
indéfini (``set -u``) ou périmé (PR précédente de la boucle) — les tests
échouent alors.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
WORKFLOW = RACINE / ".github" / "workflows" / "maj-branches-reutilisable.yml"

# L'initialisation de code=0 doit précéder IMMÉDIATEMENT l'invocation :
# sans elle, un succès du script ne passe pas par le `|| code=$?` et code
# reste indéfini ou garde la valeur de la PR précédente de la boucle.
BLOC_INVOCATION = re.compile(
    r"(^[ \t]*code=0\n"
    r"[ \t]*IA_RESUME=resume-ia\.md uv run \.socle/scripts/resoudre_conflits_ia\.py[^\n]*$)",
    re.MULTILINE,
)

GARDE_EXIT_1 = re.compile(
    r'if \[ "\$code" -eq 1 \]; then[^}]*?exit 1[^}]*?fi\n[ \t]*if \[ "\$code" -eq 0 \]'
)


def bloc_du_workflow() -> str:
    """Le bloc d'invocation du script IA, extrait tel quel du workflow."""
    trouve = BLOC_INVOCATION.search(WORKFLOW.read_text(encoding="utf-8"))
    assert trouve, "bloc d'invocation (code=0 + resoudre_conflits_ia.py) introuvable dans le workflow"
    return trouve.group(1)


def lancer_etape(*codes: int) -> subprocess.CompletedProcess:
    """Exécute sous ``bash -euo pipefail`` une étape miniature qui rejoue le
    bloc d'invocation extrait du workflow pour chaque code de sortie voulu
    (le script factice lit le sien dans ``code-ia.txt``), suivi du contrôle
    du workflow (exit 1 bloquant, exit 0 résolu, sinon étiquette conflit).

    ``uv`` est remplacé par un stub (``uv run X`` → ``python3 X``) : le
    script factice décide du code de sortie, pas le vrai script.
    """
    with tempfile.TemporaryDirectory() as brut:
        tmp = Path(brut)
        scripts = tmp / ".socle" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "resoudre_conflits_ia.py").write_text(
            "import sys\n\nsys.exit(int(open(\"code-ia.txt\").read().strip()))\n",
            encoding="utf-8",
        )
        faux_bin = tmp / "bin"
        faux_bin.mkdir()
        (faux_bin / "uv").write_text(
            '#!/usr/bin/env bash\n[ "$1" = run ] && shift\nexec python3 "$@"\n',
            encoding="utf-8",
        )
        (faux_bin / "uv").chmod(0o755)
        bloc = bloc_du_workflow()
        etape = tmp / "etape.sh"
        etape.write_text(
            "set -euo pipefail\ncd \"$(dirname \"$0\")\"\n"
            + "".join(
                f'echo {code} > code-ia.txt\n{bloc}\n'
                + 'if [ "$code" -eq 1 ]; then echo "::error::erreur technique"; exit 1; fi\n'
                + 'if [ "$code" -eq 0 ]; then echo "RESOLU_PAR_IA"; else echo "PR_ETIQUETEE_CONFLIT"; fi\n'
                for code in codes
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "PATH": f"{faux_bin}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(
            ["bash", str(etape)], capture_output=True, text=True, env=env, check=False
        )


class TestCodesDeSortie(unittest.TestCase):
    def test_exit2_puis_succes_dans_la_meme_boucle(self):
        """Un conflit non simple (2) puis une résolution réussie (0) : la
        seconde PR doit être classée résolue, sans code périmé ni variable
        indéfinie."""
        resultat = lancer_etape(2, 0)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual(
            resultat.stdout.strip().splitlines(),
            ["PR_ETIQUETEE_CONFLIT", "RESOLU_PAR_IA"],
            resultat.stderr,
        )

    def test_exit1_erreur_technique_bloquante(self):
        """Exit 1 = erreur technique Ollama : l'étape doit échouer."""
        resultat = lancer_etape(1)
        self.assertEqual(resultat.returncode, 1, resultat.stderr)
        self.assertNotIn("PR_ETIQUETEE_CONFLIT", resultat.stdout)

    def test_succes_puis_conflit(self):
        resultat = lancer_etape(0, 2)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        self.assertEqual(
            resultat.stdout.strip().splitlines(),
            ["RESOLU_PAR_IA", "PR_ETIQUETEE_CONFLIT"],
            resultat.stderr,
        )


class TestMotifsDuWorkflow(unittest.TestCase):
    def test_l_invocation_est_protegee_de_bash_e(self):
        """L'ancien motif fragile (``; code=$?``) doit avoir disparu."""
        contenu = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("resoudre_conflits_ia.py || code=$?", contenu)
        self.assertNotIn("resoudre_conflits_ia.py; code=$?", contenu)

    def test_le_code_1_reste_bloquant_dans_le_workflow(self):
        """Le workflow doit garder le garde-fou « exit 1 → job bloqué » juste
        avant le test « code = 0 → résolu »."""
        contenu = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(contenu, GARDE_EXIT_1)


if __name__ == "__main__":
    unittest.main()
