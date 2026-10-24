# Socle qualité Mathilde-Team

Règles communes à toutes les productions de l'équipe, reprises de Gcm4, Netcross et SwitchCapture. Le dépôt [`modele-depot`](https://github.com/Mathilde-Team/modele-depot) les fournit prêtes à l'emploi. L'[audit quotidien](.github/workflows/audit-socle.yml) signale les dépôts non conformes dans l'issue « Audit du socle qualité ».

## 1. Licence

- **MIT** pour toute production de l'équipe : fichier `LICENSE` à la racine, **texte MIT standard non modifié**, « Copyright (c) 2026 Mathilde Deuscher ». Toute ligne ajoutée au texte empêche GitHub de reconnaître la licence (il affiche alors NOASSERTION).
- **Exception** : un fork ou un code dérivé garde la licence de son projet d'origine, car la GPL n'autorise pas de repasser le code en MIT. Les exceptions sont listées dans [`audit-exceptions.json`](audit-exceptions.json) (Gcm4, AsyncVnc4).

## 2. Branches

| Branche | Rôle |
|---|---|
| `main` | Livraison, **branche par défaut**. On n'y arrive que par une PR `dev` → `main` |
| `dev` | Intégration. Les PR de fonctionnalité ciblent `dev` |
| `feat/<n°>-…`, `fix/<n°>-…`, `docs/…`, `ci/…`, `test/…`, `chore/…` | Une branche par sujet, créée depuis `dev` |

## 3. Protections (versionnées)

Les règles sont rangées dans le dépôt, sous `.github/branch-protection/<branche>.json`. Ce sont les données exactes à envoyer à l'API « Update branch protection » :

```bash
gh api -X PUT repos/Mathilde-Team/<depot>/branches/<branche>/protection \
  --input .github/branch-protection/<branche>.json
```

| | `main` | `dev` |
|---|---|---|
| Checks requis | « Garde : PR vers main doit venir de dev » + « Qualité (…) » | « Qualité (…) » (et les autres jobs bloquants du dépôt) |
| Branche à jour (`strict`) | non | **oui** |
| PR obligatoire | oui, 0 approbation | non (les checks requis empêchent de toute façon de pousser directement) |
| Appliquée aux administrateurs (`enforce_admins`) | **oui** | **oui** |
| Force-push, suppression | interdits | interdits |
| Historique linéaire | non (les livraisons `dev` → `main` sont des merge commits) | non |

`app_id` 15368 = GitHub Actions : seul un check publié par Actions satisfait la règle.

## 4. Workflows obligatoires

| Fichier | Rôle |
|---|---|
| `.github/workflows/ci.yml` | Job **« Qualité (…) »** : lint, format, tests, propres à chaque langage |
| `.github/workflows/guard-main.yml` | Fait échouer toute PR vers `main` qui ne vient pas de `dev` |
| `.github/workflows/branch-protection.yml` | Compare chaque jour la protection réelle aux fichiers JSON versionnés (`scripts/check_branch_protection.py`) |
| `.github/workflows/maj-branches.yml` | Met à jour les PR vers `dev` à chaque avancée de `dev` (§11), via le workflow réutilisable de ce dépôt |

Conventions des workflows :
- runners auto-hébergés du groupe « maison » : `runs-on: ${{ fromJSON(vars.RUNS_ON_LINUX || '{"group":"maison","labels":["self-hosted","linux"]}') }}`. La variable `RUNS_ON_LINUX` permet de basculer sur d'autres runners, par exemple `"ubuntu-latest"` ;
- `permissions: contents: read` par défaut ;
- `actions/checkout@v7` et `astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0`, puis `uv python install` ;
- les versions des outils sont figées (yamllint, ESPHome…) et ne montent que volontairement.

### Couverture des tests : 95 % minimum (décision du 21/10/2026, #9)

- **Seuil bloquant ≥ 95 %**, lignes **et** branches (`branch = true`). Il est appliqué par le job « Qualité », ou par le job de couverture combinée s'il est requis sur `dev`, ainsi que par la même commande dans le `Makefile`. Une PR qui fait passer la couverture sous le seuil en vigueur échoue au job requis sur `dev`.
- **Seuil déclaré une seule fois** : `fail_under = 95` dans `[tool.coverage.report]` de `pyproject.toml` pour Python, et les options `--test-coverage-*` de `npm run test:coverage` pour Node. Le CI et le `Makefile` le lisent, sans valeur recopiée. Modèle : `modele-depot` (`pyproject.toml`, `Makefile`, job « Qualité » de `ci.yml`).
- **Le code d'interface compte.** GTK et Electron sont mesurés par le job graphique (Xvfb) et combinés (`coverage combine`). On ne les exclut pas pour atteindre le seuil.
- **Exclusions limitées au code réellement inatteignable en test** (`omit`, `# pragma: no cover`) : garde `if __name__ == "__main__"`, branche propre à une plateforme… Chacune est justifiée par un commentaire.
- **Paliers pour un dépôt sous 95 %** : le seuil est d'abord fixé à la mesure du moment (entier inférieur), pour qu'aucune régression ne soit possible, puis relevé à chaque PR de tests jusqu'à 95 %. Le palier en vigueur est noté dans l'issue de suivi du dépôt.
- Tests lancés comme des scripts (sans pytest) : chaque script tourne sous `coverage run`, puis `coverage combine` et `coverage report` lisent le même `fail_under`. Exemple : `gcm-redfish-core` (gcm-redfish-core#7).

## 5. Nouveau dépôt : marche à suivre

1. GitHub → **New repository** → *Repository template* : **`Mathilde-Team/modele-depot`** (ou `gh repo create Mathilde-Team/<nom> --public --template Mathilde-Team/modele-depot`).
2. Adapter le job « Qualité (lint, tests) » de `ci.yml` au langage du dépôt. Si le nom du job change, mettre à jour les deux fichiers JSON.
3. Appliquer le socle (administrateur) : `scripts/appliquer_socle.sh Mathilde-Team/<nom>`. Le script crée `dev` et applique les protections **versionnées dans le dépôt cible** (`.github/branch-protection/*.json`, lues sur `dev` puis `main` ; règle générique du modèle seulement à défaut). Il refuse d'appliquer une règle dont un contrôle requis n'est produit par aucun job des workflows du dépôt (#5).
4. À partir de là, tout passe par une PR vers `dev`.

## 6. Pourquoi pas de règles imposées par l'organisation

Les [rulesets d'organisation](https://docs.github.com/fr/organizations/managing-organization-settings/creating-rulesets-for-repositories-in-your-organization) s'appliqueraient automatiquement à chaque nouveau dépôt. Ils ne sont pas inclus dans l'offre gratuite de l'organisation, et demandent un jeton avec le droit `admin:org`. Le socle repose donc sur trois éléments :
- le **modèle** de dépôt ;
- le **script** d'initialisation ;
- l'**audit** quotidien.

Avec l'offre GitHub Team, les protections du paragraphe 3 pourraient devenir un ruleset ciblant tous les dépôts (`~ALL`), branches `main` et `dev`.

## 7. Journalisation des authentifications (convention par défaut)

Toute intégration ou script de l'équipe qui s'authentifie auprès d'un service suit la même règle :

- tant qu'aucune connexion n'a réussi — et après chaque nouvel échec —, les étapes d'authentification sont tracées au niveau **INFO** : visibles dans le journal sans activer les logs de debug ;
- après une première connexion réussie, elles passent au niveau **DEBUG** ;
- tout échec réactive le mode bavard.

Un échec de connexion ne doit jamais rester silencieux : l'exception est journalisée avec sa cause exacte (DNS, TLS, délai dépassé…), sans jamais y inclure mots de passe, cookies ou données de compte. Référence d'implémentation : Besancon-Emoi #21 (helper `_auth_log` dans `api.py`).

## 8. Journalisation Python : loguru

**Tout code Python de l'équipe journalise avec [loguru](https://loguru.readthedocs.io/)** : applications, bibliothèques, CLI et scripts de `scripts/`.

**Seule exception : les intégrations Home Assistant** (`custom_components/`). Elles gardent le module standard `logging` (`_LOGGER = logging.getLogger(__name__)`), car c'est lui que la configuration `logger:` de Home Assistant pilote. Les autres règles s'y appliquent à l'identique, avec `_LOGGER`.

Règles, vérifiées par analyse AST dans les tests de chaque dépôt (modèles : Netcross `tests/test_loguru_regles.py` et `tests/test_loguru_formatting.py`, Gcm4 `tests/test_loguru_regles.py` et `scripts/audit_debug_sorties.py`) :

1. **Entrée et sorties** : toute fonction ou méthode non triviale émet un `debug()` en entrée, avec ses paramètres significatifs résumés (tailles, types, jamais un secret), et un `debug()` juste avant chaque sortie (chaque `return`, la fin implicite, et chaque `raise` volontaire). Exemptées : fonctions vides, accesseurs d'une ligne. Sur un chemin chaud (appelé à chaque paquet, ligne, événement), la trace d'entrée passe en `trace()` ou est omise, et seuls les refus sont tracés (Netcross #441).
2. **`except`** : chaque bloc `except` contient un appel de journal ; jamais d'exception avalée en silence.
3. **Logger de module** : tout module qui définit des fonctions a un logger au niveau du module (`from loguru import logger`, ou `_LOGGER` pour Home Assistant).
4. **Pas de `import logging`** hors Home Assistant.
5. **Format** : loguru utilise les placeholders `{}` avec autant d'arguments que de `{}` ; jamais de `%s`/`%d`, que loguru n'interprète pas. À l'inverse, `logging` (Home Assistant) utilise `%s` avec des arguments, jamais de f-string dans l'appel.
6. **Secrets** : aucun mot de passe, jeton, clé ou cookie dans un message ; `bool(x)`, `len(x)` ou `***` à la place.
7. Les messages de journal sont en français et ne sont pas traduits.

Un dépôt qui ne respecte pas encore ces règles fige ses écarts dans une référence versionnée (par exemple `scripts/audit_debug_sorties.baseline`) : le test échoue si le compte augmente, et la référence est baissée à chaque PR qui instrumente du code. Le compte doit atteindre zéro.

## 11. Mise à jour automatique des branches de PR

`dev` est protégée en `strict: true` (§3) : chaque fusion dans `dev` met les autres PR « en retard ». À chaque push sur `dev`, chaque PR ouverte vers `dev` est mise à jour (« Update branch »). Sont ignorées les PR en brouillon, venant d'un fork ou portant le label `pas-de-maj-auto`.

- **Conflit** : le runner « maison » fusionne en style `diff3` et fait résoudre les conflits **simples** par l'IA locale (Ollama, variables d'organisation `OLLAMA_URL` et `OLLAMA_MODELE`). Garde-fous, en tout ou rien :
  - au plus 3 fichiers, 6 blocs et 60 lignes par bloc ;
  - verrous de dépendances, workflows et règles `branch-protection` exclus ;
  - aucune ligne inventée : chaque ligne vient d'un des deux côtés ;
  - le modèle peut répondre « non simple ».
- **Résolu par l'IA** : commit poussé sur la branche, PR **repassée en brouillon** (non fusionnable sans relecture), label `resolu-par-ia` et commentaire détaillant chaque résolution. Relire, attendre la CI, puis *Ready for review*.
- **Non résolu** : label `conflit` et un commentaire qui donne la raison. Le label est retiré dès qu'une mise à jour réussit.
- **Jeton** : GitHub App de l'organisation (secrets `MAJ_APP_ID` et `MAJ_APP_CLE_PRIVEE` ; Contents, Pull requests, Issues en écriture). Un push fait avec `GITHUB_TOKEN` ne relancerait pas la CI, et la PR resterait bloquée sur le check requis.

Mise en œuvre : workflow réutilisable [`maj-branches-reutilisable.yml`](.github/workflows/maj-branches-reutilisable.yml) et script [`scripts/resoudre_conflits_ia.py`](scripts/resoudre_conflits_ia.py). Chaque dépôt n'a qu'un petit appelant, `.github/workflows/maj-branches.yml`, fourni par `modele-depot`. Sans `OLLAMA_URL`, seules les mises à jour sont faites, et les conflits sont signalés.
