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
  saison, podiums en grands concours, meilleurs scores avec la distinction
  correspondante, distinctions, parcours).
- **Grands concours** : podiums des championnats et coupes, année par année.
- **Records** : plus médaillés et meilleurs scores par discipline.
- **Distinctions** : badges et écussons fédéraux que les scores permettent de
  demander (barèmes de la page Wikipédia « Distinctions au tir à l'arc »),
  nouvelles distinctions saison par saison et niveau atteint par archer.
  Insignes : planches de Nhhi (Wikimedia Commons, CC BY-SA 4.0) découpées par
  `decouper_insignes.py` dans `assets/distinctions/`.
- **Palmarès national** : podiums et classements aux championnats de France
  depuis 1993, en individuel et par équipe. Ces résultats sont saisis à la
  main dans `palmares_national.yaml` (repris de l'ancien site WordPress,
  vérifiés et complétés avec l'extranet FFTA) et ne dépendent pas de la
  collecte : l'extranet ne donne ni les classements par équipe ni les places
  au Beursault.
- **À propos** : règles de comptage et critères « grand concours ».

Règles de comptage principales : un archer et un concours = une participation
(classements scratch et de catégorie fusionnés, meilleure place retenue) ;
podium = place 1 à 3 ; le tir 3D est scindé avant/après la saison 2017 (40
puis 24 cibles, scores non comparables) via `PREMIERE_SAISON_3D_24_CIBLES`
dans `generer_site.py`.

### Charte graphique

Couleurs, polices, blason et styles communs proviennent de la
[charte de la compagnie](https://github.com/arcpontoise/charte), chargée
directement depuis <https://charte.arcpontoise.fr/> par chaque page : une
modification de la charte s'applique au palmarès sans le régénérer.
`generer_site.py` ne produit que les styles propres au palmarès (`style.css`),
avec les variables de la charte (`--ink`, `--blue`, `--accent`, `--gold`…).

L'en-tête affiche le menu commun à tous les sites de la compagnie
(`<arc-menu actuel="competitions">`, script `js/menu.js` de la charte). Il se
modifie dans `menu.yaml` du dépôt `charte`, sans régénérer le palmarès. La
barre `nav.site` ne contient que les pages du palmarès.

Pour tester avec une copie locale de la charte, la servir en HTTP (le
navigateur refuse les scripts et les polices en `file://` depuis une page
servie en HTTP) :

```bash
(cd ~/git/arcpontoise_charte && python3 -m http.server 8001) &
CHARTE_URL=http://localhost:8001 python3 generer_site.py --out site
(cd site && python3 -m http.server 8003)
```

## Fichiers

| Fichier                       | Rôle                                               |
|-------------------------------|----------------------------------------------------|
| `palmares_pontoise.py`        | Collecte FFTA → `detail_pontoise.csv` (+ site)     |
| `generer_site.py`             | `detail_pontoise.csv` → `site/` (sans réseau)      |
| `membres.yaml`                | Archers par saison + mots-clés « grands concours » |
| `membres.example.yaml`        | Modèle de `membres.yaml`                           |
| `palmares_national.yaml`      | Palmarès national, saisi à la main                 |
| `detail_pontoise.csv`         | Données collectées (une ligne par performance)     |
| `requirements.txt`            | Dépendances Python de la collecte                  |
| `assets/`                     | Insignes des distinctions                          |
| `decouper_insignes.py`        | Découpe les planches d'insignes Wikimedia (Pillow) |
| `.github/workflows/pages.yml` | Lint, construction et publication sur GitHub Pages |
| `pyproject.toml`, `.yamllint` | Configuration de ruff et yamllint                  |
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
  France, régionaux, etc. Les championnats et coupes de France signalés par
  la FFTA (pictogramme dans le palmarès, colonne `championnat_france` du CSV)
  sont retenus d'office, même quand leur nom n'est qu'une ville.
- `grands_concours_exclusions` : expressions retirées du nom avant
  d'appliquer les mots-clés (ex. « sélectif au championnat de France »).
  Pour lister les intitulés réels :

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
- `--cache-seulement` : reconstruit le CSV et le site à partir du seul cache,
  sans session ni réseau (utile après une évolution de l'analyse des pages)

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

Le workflow `.github/workflows/pages.yml` :

- sur chaque pull request : job `lint` (yamllint, ruff), contrôle obligatoire
  de la branche `main`, puis construction du site pour vérifier qu'elle
  aboutit ;
- sur `main` : construction et publication du site quand les données, les
  insignes ou `generer_site.py` changent (ou à la demande : onglet *Actions* >
  *Publier le site* > *Run workflow*).

Mise en place, une seule fois :

1. Créer le dépôt GitHub et y pousser le projet.
2. Dans *Settings > Pages*, choisir **Source : GitHub Actions**.
3. Pousser sur `main` : le site est publié sur
   `https://<utilisateur>.github.io/<dépôt>/`.

Mise à jour des données :

La branche `main` est protégée : la mise à jour passe par une pull request.

```bash
git switch main && git pull
git switch -c donnees/$(date +%F)
python3 palmares_pontoise.py        # collecte, puis regénère detail_pontoise.csv
git add detail_pontoise.csv membres.yaml
git commit -m "Mise à jour du palmarès" && git push -u origin HEAD
```

Ouvrir ensuite la pull request et la fusionner une fois le contrôle `lint`
au vert. Le palmarès national se met à jour de la même façon, en modifiant
`palmares_national.yaml`.

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
