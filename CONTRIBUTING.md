# Contribuer aux dépôts Mathilde-Team

Ce guide s'applique à tous les dépôts de l'organisation qui n'ont pas leur propre `CONTRIBUTING.md`. Les règles complètes sont dans le [socle qualité](https://github.com/Mathilde-Team/.github/blob/main/SOCLE.md).

## Flux de travail

```bash
git switch dev && git pull
git switch -c feat/<n°-issue>-description      # ou fix/…, docs/…, ci/…, chore/…
# … commits …
git push -u origin feat/<n°-issue>-description
gh pr create --base dev                         # PR vers dev, jamais vers main
```

- **`dev`** : toutes les PR de fonctionnalité la ciblent. La CI (« Qualité (…) ») doit être verte, sur une branche à jour avec `dev`.
- **`main`** : livraison, uniquement par une **PR `dev` → `main`**. Le check « Garde : PR vers main doit venir de dev » refuse toute autre branche source.
- Ni push direct, ni force-push sur `main` et `dev`, **y compris pour les administrateurs**.

## Avant d'ouvrir une PR

- La CI passe en local : lint, format et tests du dépôt (voir son README).
- Une PR traite un seul sujet. Elle référence son issue (`Closes #n`).
- Ne jamais versionner de secret, de mot de passe ni de donnée personnelle. Fournir un fichier `*.example` à la place.

## Licence

Sauf mention contraire dans le dépôt, les contributions sont publiées sous **licence GPL-3.0-or-later**. Exceptions ([SOCLE.md §1](https://github.com/Mathilde-Team/.github/blob/main/SOCLE.md#1-licence-et-version)) : les intégrations Home Assistant et IAO restent en MIT, et un fork garde la licence de son projet d'origine (code amont de Gcm4 en GPL-3.0 seule).
