# Mathilde-Team/.github

Dépôt de configuration de l'organisation **Mathilde-Team**.

| Fichier | Rôle |
|---|---|
| [`SOCLE.md`](SOCLE.md) | **Socle qualité** commun : licence MIT, branches `main`/`dev`, protections, CI, garde |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Guide de contribution **par défaut** de tous les dépôts de l'organisation qui n'en ont pas |
| [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md) | Modèle de PR **par défaut** de tous les dépôts qui n'en ont pas |
| `profile/README.md` | Page d'accueil publique de l'organisation |
| [`scripts/audit_socle.py`](scripts/audit_socle.py) | Audit du socle sur tous les dépôts (bibliothèque standard) |
| [`audit-exceptions.json`](audit-exceptions.json) | Exceptions assumées (forks sous GPL, dépôt de démonstration) |
| `.github/workflows/audit-socle.yml` | Audit quotidien, avec rapport dans l'issue « Audit du socle qualité » |
| [`scripts/resoudre_conflits_ia.py`](scripts/resoudre_conflits_ia.py) | Résolution des conflits simples par IA locale (Ollama), appelée par `maj-branches-reutilisable.yml` (SOCLE.md §11) |

Nouveau dépôt : partir du modèle [`modele-depot`](https://github.com/Mathilde-Team/modele-depot), puis lancer `scripts/appliquer_socle.sh`. La marche à suivre est décrite dans [SOCLE.md](SOCLE.md#5-nouveau-dépôt--marche-à-suivre).

Audit local :

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/audit_socle.py --org Mathilde-Team
```

Ce dépôt suit lui-même le socle : PR vers `dev`, puis `dev` → `main`. Licence [MIT](LICENSE).
