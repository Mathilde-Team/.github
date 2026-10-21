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

Conventions des workflows :
- runners auto-hébergés du groupe « maison » : `runs-on: ${{ fromJSON(vars.RUNS_ON_LINUX || '{"group":"maison","labels":["self-hosted","linux"]}') }}`. La variable `RUNS_ON_LINUX` permet de basculer sur d'autres runners, par exemple `"ubuntu-latest"` ;
- `permissions: contents: read` par défaut ;
- `actions/checkout@v7` et `astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0`, puis `uv python install` ;
- les versions des outils sont figées (yamllint, ESPHome…) et ne montent que volontairement.

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
