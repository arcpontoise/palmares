# Palmarès FFTA – Cie Archers de Pontoise

Site statique sur le palmarès de la Compagnie d'archers de Pontoise (club
FFTA `0895289`), construit à partir de l'espace licencié FFTA
(`https://extranet.ffta.fr/consulter_palmares.html`) et publié sur GitHub
Pages.

Le travail se fait en deux temps :

1. **Collecte** (en local, avec un compte licencié FFTA) :
   `palmares_pontoise.py` interroge le palmarès de chaque archer pour chaque
   saison et écrit `detail_pontoise.csv`.
2. **Site** (en local ou par GitHub Actions) : `generer_site.py` transforme ce
   CSV en pages HTML dans `site/`, sans accès réseau.

## Le site

- **Accueil** : chiffres clés, podiums et nombre d'archers par saison, accès
  aux saisons.
- **Saisons** : une page par saison (podiums, grands concours, archers).
- **Archers** : annuaire filtrable et une fiche par archer (médailles par
  saison, podiums en grands concours, meilleurs scores, parcours).
- **Grands concours** : podiums des championnats et coupes, année par année.
- **Records** : plus médaillés et meilleurs scores par discipline.
- **À propos** : règles de comptage et critères « grand concours ».

Règles de comptage principales : un archer et un concours = une participation
(classements scratch et de catégorie fusionnés, meilleure place retenue) ;
podium = place 1 à 3 ; le tir 3D est scindé avant/après la saison 2016 (40
puis 24 cibles, scores non comparables) via `PREMIERE_SAISON_3D_24_CIBLES`
dans `generer_site.py`.

## Fichiers

| Fichier                       | Rôle                                               |
|-------------------------------|----------------------------------------------------|
| `palmares_pontoise.py`        | Collecte FFTA → `detail_pontoise.csv` (+ site)     |
| `generer_site.py`             | `detail_pontoise.csv` → `site/` (sans réseau)      |
| `membres.yaml`                | Archers par saison + mots-clés « grands concours » |
| `membres.example.yaml`        | Modèle de `membres.yaml`                           |
| `detail_pontoise.csv`         | Données collectées (une ligne par performance)     |
| `requirements.txt`            | Dépendances Python de la collecte                  |
| `.github/workflows/pages.yml` | Publication sur GitHub Pages                       |
| `.cache_ffta/`                | Cache des pages FFTA (généré, non versionné)       |
| `site/`                       | Site généré (non versionné)                        |

## Installation

```bash
python3 -mvenv .venv --upgrade-deps && . .venv/bin/activate
pip install -r requirements.txt --no-cache-dir
```

(`generer_site.py` seul n'a besoin que de `PyYAML`.)

## Configuration : `membres.yaml`

Copier le modèle puis le compléter avec la liste des licenciés par saison :

```bash
cp membres.example.yaml membres.yaml
```

```yaml
archers_par_saison:
  "2026":
    - nom: "NOM Prénom"
      licence: "0000000X"

grands_concours_mots_cles:
  - "championnat de france"
  - "^cr\\b"
```

- `archers_par_saison` : seule la licence est envoyée à la FFTA, le nom ne
  sert qu'à l'affichage (le site affiche le nom de la saison la plus
  récente).
- `grands_concours_mots_cles` : expressions régulières (insensibles à la
  casse) appliquées au nom du concours pour repérer les championnats de
  France, régionaux, etc. Pour lister les intitulés réels :

  ```bash
  python3 -c "import csv;print(*sorted({r['concours'] for r in csv.DictReader(open('detail_pontoise.csv'))}),sep='\n')"
  ```

## Collecte des données

### Authentification

Le formulaire nécessite une session licencié active. Récupérer la valeur du
cookie `PHPSESSID` dans le navigateur (DevTools > Application > Cookies >
`extranet.ffta.fr`) après s'être connecté, puis :

```bash
export FFTA_SESSION_ID="<valeur du cookie>"
export FFTA_OWN_LICENCE="<ta licence>"
```

Ne jamais passer ces valeurs en argument ni les versionner. La session expire
après inactivité : le script s'arrête avec un message explicite.

### Lancement

```bash
python3 palmares_pontoise.py
```

Options (valeurs par défaut entre parenthèses) :

- `--config` (`membres.yaml`)
- `--own-licence` (sinon `FFTA_OWN_LICENCE`)
- `--out-detail` (`detail_pontoise.csv`)
- `--out-site` (`site`)
- `--refresh-saison ANNEE` : retélécharge cette saison en ignorant le cache
  (répétable)
- `--refresh-licence LICENCE` : retélécharge cet archer en ignorant le cache
  (répétable)

À la fin, le script écrit le CSV puis génère le site. Le site peut aussi être
régénéré à tout moment sans réseau : `python3 generer_site.py` (options
`--detail`, `--config`, `--out`).

### Cache, captcha et respect du serveur FFTA

Chaque page récupérée est enregistrée dans
`.cache_ffta/<licence>_<saison>.html`. Une page déjà en cache n'est pas
réinterrogée ; un délai de 5 s sépare les requêtes réelles. Une requête qui
échoue (délai dépassé…) est retentée 3 fois, puis la combinaison est
signalée en fin d'exécution et le script continue : il suffit de relancer.

Pour rafraîchir des données (typiquement la saison en cours) sans tout
relancer :

```bash
python3 palmares_pontoise.py --refresh-saison 2026                  # une saison
python3 palmares_pontoise.py --refresh-licence 0000000X             # un archer
python3 palmares_pontoise.py --refresh-saison 2026 --refresh-licence 0000000X
```

Avec les deux options, seules les combinaisons respectant les deux filtres
sont retéléchargées. Les saisons passées ne changent plus : inutile de vider
le cache.

Si la FFTA affiche une vérification de sécurité (captcha) au lieu du
palmarès, le script s'arrête immédiatement sans mettre cette page en cache
(et ignore les pages captcha d'anciens runs). Valider la vérification dans le
navigateur, patienter, puis relancer par petits lots ; ne pas chercher à la
contourner.

## Publication sur GitHub Pages

Le workflow `.github/workflows/pages.yml` régénère le site à chaque push sur
`main` qui modifie `detail_pontoise.csv`, `membres.yaml` ou `generer_site.py`
(ou à la demande : onglet *Actions* > *Publier le site* > *Run workflow*).

Mise en place, une seule fois :

1. Créer le dépôt GitHub et y pousser le projet.
2. Dans *Settings > Pages*, choisir **Source : GitHub Actions**.
3. Pousser sur `main` : le site est publié sur
   `https://<utilisateur>.github.io/<dépôt>/`.

Mise à jour des données :

```bash
python3 palmares_pontoise.py        # collecte, puis regénère detail_pontoise.csv
git add detail_pontoise.csv membres.yaml
git commit -m "Mise à jour du palmarès" && git push
```

Le site est reconstruit à partir du CSV : GitHub Actions n'accède jamais à la
FFTA et ne détient aucun cookie.

### Données personnelles

Le site publie des noms d'archers, leurs numéros de licence (dans les fiches)
et leurs résultats, et `detail_pontoise.csv` / `membres.yaml` sont versionnés
pour la construction. Sur un compte GitHub gratuit, GitHub Pages est
**public** même si le dépôt est privé. Avant de publier, s'assurer de
l'accord du club et des archers concernés (RGPD).

## Notes techniques

- Endpoint : `POST /consulter_palmares.html` avec `operation=enregistrer`,
  `CodeLicencie` (archer recherché), `SaisonAnnee` et `LicenceCodeAdherent`
  (licence du compte connecté).
- Toutes les performances sont présentes dans le HTML renvoyé (lignes
  `tr.epRow`), masquées par CSS ; aucun appel AJAX supplémentaire n'est
  nécessaire.
- Les appels `AjaxPalmares.php?operation=placeClassement` ne servent qu'au
  classement national de la catégorie (colonne « Pl. ») et sont ignorés.
