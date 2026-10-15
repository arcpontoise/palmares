#!/usr/bin/env python3
"""Génère un site statique sur le palmarès de la Cie Archers de Pontoise.

Le site est construit à partir du CSV détaillé produit par
``palmares_pontoise.py`` (aucun accès réseau) :

    python3 generer_site.py                       # lit detail_pontoise.csv
    python3 generer_site.py --out site --config membres.yaml

Pages produites dans le dossier de sortie (``site/`` par défaut) :

    index.html            vue d'ensemble, graphiques, accès aux saisons
    saisons/<année>.html  une page par saison (podiums, archers)
    archers/index.html    annuaire filtrable des archers
    archers/<licence>.html  carrière d'un archer
    grands-concours.html  podiums des championnats et grands concours
    records.html          classement des médaillés et meilleurs scores
    distinctions.html     badges et écussons que les scores permettent de demander
    a-propos.html         méthode de calcul

Règles de comptage : plusieurs lignes d'un même archer pour un même
concours (classement scratch + classement de catégorie) comptent pour une
seule épreuve, avec la meilleure place obtenue.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import logging
import re
import shutil
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("generer_site")
ASSETS = Path(__file__).parent / "assets"  # logo du club et police manuscrite

CLUB = "Cie Archers de Pontoise"
MEDAILLES = {1: "🥇", 2: "🥈", 3: "🥉"}
DEFAULT_MOTS_CLES = [
    r"championnat de france",
    r"championnat régional",
    r"championnat regional",
    r"^cr\b",
    r"^crte\b",
    r"coupe de france",
]
# Retirés du nom avant d'appliquer les mots-clés : un « sélectif au championnat
# de France » n'est pas un championnat (mais « CR … sélectif … » reste un CR).
DEFAULT_EXCLUSIONS = [
    r"(s[ée]lectif|qualificatif)s?\s+((au|aux|pour\s+les?)\s+)?championnats?\s+de\s+france",
]
# Disciplines pour lesquelles un record de points est comparable.
DISCIPLINES_RECORDS = [
    "Salle (18 m)",
    "FITA / extérieur",
    "2x70 m",
    "2x50 m",
    "Fédéral",
    "TAE DI",
    "TAE DN",
    "TAE",
    "Campagne",
    "3D",
    "3D (40 cibles)",
]
# Le tir 3D est passé de 40 à 24 cibles (sans zone « 11 ») : les scores ne sont
# pas comparables de part et d'autre. Première saison au format 24 cibles.
PREMIERE_SAISON_3D_24_CIBLES = 2017


# --- Modèle ----------------------------------------------------------------


@dataclass
class Epreuve:
    """Une participation d'un archer à un concours (lignes scratch/catégorie fusionnées)."""

    licence: str
    nom: str
    saison: str
    date: str
    iso: str
    concours: str
    place: int | None
    points: int | None
    discipline: str
    arme: str
    sexe: str
    categorie: str
    grand: bool
    age: str = ""  # Poussin, Benjamin, Minime, Cadet, Junior, Adulte ("" si inconnu)
    distance: int | None = None  # en mètres, quand la catégorie la précise
    blason: int | None = None  # en cm, idem
    piquet: int | None = None  # 3D, campagne, nature : 1 rouge, 2 bleu, 3 blanc
    piquet_deduit: bool = False  # piquet non précisé par la FFTA, déduit de l'âge et de l'arc

    @property
    def podium(self) -> bool:
        return self.place in MEDAILLES


def normaliser_discipline(brut: str, saison: str = "") -> str:
    d = re.sub(r"\b(19|20)\d\d\b", "", brut).lower()
    if "3d" in d:
        if saison.isdigit() and int(saison) >= PREMIERE_SAISON_3D_24_CIBLES:
            return "3D"
        return "3D (40 cibles)"
    if "campagne" in d:
        return "Campagne"
    if "beursault" in d:
        return "Beursault"
    if "nature" in d:
        return "Parcours nature"
    if "tae" in d:
        return "TAE"
    if "fédéral" in d or "federal" in d:
        return "Fédéral"
    if "2x70" in d:
        return "2x70 m"
    if "2x50" in d:
        return "2x50 m"
    if "salle" in d or "18m" in d:
        return "Salle (18 m)"
    if "fita" in d or "extérieur" in d:
        return "FITA / extérieur"
    return "Autre"


def discipline_tae(arme: str, distance: int | None, blason: int | None) -> str:
    """TAE international (DI) ou national (DN), déduit de la distance et du blason.

    DI : classique à 60 m (U18, Senior 3) ou 70 m, poulies sur 80 cm.
    DN : classique à 50 m, poulies sur 122 cm. Sinon (jeunes, arc nu) : « TAE ».
    """
    if arme == "Poulies" and blason in (80, 122):
        return "TAE DI" if blason == 80 else "TAE DN"
    if arme == "Classique" and distance:
        if distance >= 60:
            return "TAE DI"
        if distance == 50:
            return "TAE DN"
    return "TAE"


def normaliser_arme(categorie: str) -> str:
    c = categorie.lower()
    if "poulies" in c and re.search(r"\bnu\b", c):
        return "Poulies nu"
    if "poulies" in c:
        return "Poulies"
    if "bare" in c or re.search(r"\bnu\b", c):
        return "Arc nu"
    if "chasse" in c:
        return "Arc chasse"
    if "droit" in c or "longbow" in c:
        return "Arc droit"
    if "libre" in c:
        return "Arc libre"
    if "classique" in c:
        return "Classique"
    return "Autre"


def deduire_piquet(discipline: str, arme: str, age: str) -> int | None:
    """Piquet habituel d'une catégorie, pour les résultats où la FFTA ne le précise pas.

    Règles relevées sur les résultats récents (292 sur 293 retrouvés). La 3D sur
    40 cibles (avant 2017) avait d'autres distances : pas de déduction.
    """
    jeune = age in ("Benjamin", "Minime")
    if discipline == "3D":
        if jeune:
            return 3
        if age == "Cadet":
            return 2 if arme == "Arc libre" else None
        if age in ("Junior", "Adulte"):
            return 1 if arme == "Arc libre" else 2
    if discipline == "Campagne":
        if jeune:
            return 3
        if arme in ("Classique", "Poulies"):
            return 2 if age == "Cadet" else 1
        if arme == "Arc nu":
            return 3 if age == "Cadet" else 2
        if arme == "Arc droit" and age in ("Junior", "Adulte"):
            return 3
    if discipline == "Parcours nature" and age in ("Junior", "Adulte"):
        return 1
    return None


def normaliser_sexe(categorie: str) -> str:
    c = categorie.lower()
    if "femme" in c or "dame" in c:
        return "Femme"
    if "homme" in c:
        return "Homme"
    return ""


def normaliser_age(categorie: str) -> str:
    """Classe d'âge (anciennes et nouvelles appellations) ; "" pour un classement scratch."""
    c = categorie.lower()
    if re.match(r"sc(ra|ar)tch", c):
        return ""
    for motif, age in (
        (r"poussin|\bu11\b", "Poussin"),
        (r"benjamin|\bu13\b", "Benjamin"),
        (r"minime|\bu15\b", "Minime"),
        (r"u18-u21|junior|\bu21\b", "Junior"),
        (r"cadet|\bu18\b", "Cadet"),
    ):
        if re.search(motif, c):
            return age
    return "Adulte"


def distance_blason(categorie: str) -> tuple[int | None, int | None]:
    """Première distance et premier blason indiqués après l'année (« … 2026 - 70m - 122cm »)."""
    m = re.search(r"\b(19|20)\d\d\b.*?(\d+)\s*m\s*-\s*(\d+)\s*cm", categorie)
    return (int(m.group(2)), int(m.group(3))) if m else (None, None)


def _iso(date_fr: str) -> str:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", date_fr)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else date_fr


def _entier(texte: str) -> int | None:
    m = re.search(r"\d+", texte or "")
    return int(m.group(0)) if m else None


def est_grand_concours(nom: str, mots_cles: list[str], exclusions: list[str] = DEFAULT_EXCLUSIONS) -> bool:
    for motif in exclusions:
        nom = re.sub(motif, " ", nom, flags=re.IGNORECASE)
    return any(re.search(m, nom.lower(), flags=re.IGNORECASE) for m in mots_cles)


def _vrai(valeur: Any) -> bool:
    return str(valeur).strip().lower() == "true"


def construire_epreuves(rows: list[dict[str, Any]], mots_cles: list[str],
                        exclusions: list[str] = DEFAULT_EXCLUSIONS) -> list[Epreuve]:
    """Fusionne les lignes d'un même archer/concours et normalise les champs."""
    groupes: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groupes[(row["archer_licence"], row["date"], row["concours"])].append(row)

    epreuves: list[Epreuve] = []
    for lignes in groupes.values():

        def clef(ligne: dict[str, Any]) -> tuple[int, int]:
            place = _entier(str(ligne["place"]))
            return (0, place) if place else (1, 0)

        meilleure = min(lignes, key=clef)
        place = _entier(str(meilleure["place"]))
        points = [p for p in (_entier(str(x["points"])) for x in lignes) if p]
        categorie = str(meilleure["sous_categorie"])
        toutes = [str(x["sous_categorie"]) for x in lignes]
        age = next((a for a in map(normaliser_age, toutes) if a), "")
        distance, blason = next((db for db in map(distance_blason, toutes) if db[0]), (None, None))
        piquet = next((int(m.group(1)) for c in toutes if (m := re.search(r"piquet\s*(\d)", c, re.I))), None)
        epreuves.append(
            Epreuve(
                licence=meilleure["archer_licence"],
                nom=meilleure["archer_nom"],
                saison=str(meilleure["saison"]),
                date=re.sub(r"^\D+", "", meilleure["date"]),  # anciens CSV : « **10/08/2002 »
                iso=_iso(meilleure["date"]),
                concours=meilleure["concours"],
                place=place or None,
                points=max(points) if points else None,
                discipline=normaliser_discipline(str(meilleure["discipline"]), str(meilleure["saison"])),
                arme=normaliser_arme(categorie),
                sexe=normaliser_sexe(categorie),
                categorie=re.sub(r"\s*\b(19|20)\d\d\b.*", "", categorie).strip(),
                # Pictogramme FFTA (championnat ou coupe de France) ou nom du concours.
                grand=any(_vrai(x.get("championnat_france")) for x in lignes)
                or est_grand_concours(meilleure["concours"], mots_cles, exclusions),
                age=age,
                distance=distance,
                blason=blason,
                piquet=piquet,
            )
        )
    for e in epreuves:
        if e.discipline == "TAE":
            e.discipline = discipline_tae(e.arme, e.distance, e.blason)
    # Classement scratch seul : classe d'âge reprise d'un autre concours de la saison.
    ages = {(e.licence, e.saison): e.age for e in epreuves if e.age}
    for e in epreuves:
        e.age = e.age or ages.get((e.licence, e.saison), "Adulte")
        if e.piquet is None:
            e.piquet = deduire_piquet(e.discipline, e.arme, e.age)
            e.piquet_deduit = e.piquet is not None
    epreuves.sort(key=lambda e: (e.saison, e.iso), reverse=True)
    return epreuves


# --- Distinctions ----------------------------------------------------------
# Barèmes FFTA d'après https://fr.wikipedia.org/wiki/Distinctions_au_tir_%C3%A0_l%27arc
# Les distinctions sont théoriques : elles doivent être demandées par l'archer,
# et certaines conditions (blason, nombre de cibles) ne sont pas vérifiables.

SOURCE_DISTINCTIONS = "https://fr.wikipedia.org/wiki/Distinctions_au_tir_%C3%A0_l%27arc"
_COULEURS = ["Vert", "Blanc", "Noir", "Bleu", "Rouge", "Jaune", "★", "★★", "★★★"]
_ECUSSONS = ["Vert sur fond blanc", "Argent sur fond vert", "Or sur fond blanc",
             "Or sur fond noir", "Or sur fond bleu", "Or sur fond rouge"]
_ORANGE = ["Noir sur fond orange", "Argent sur fond orange", "Or sur fond orange"]
JEUNES = {"Poussin", "Benjamin", "Minime", "Cadet"}


@dataclass(frozen=True)
class Bareme:
    nom: str
    niveaux: tuple[str, ...]
    seuils: dict[str, tuple[int, ...]]  # colonne du barème -> points minimaux par niveau

    def image(self, niveau: int) -> str:
        """Insigne découpé par ``decouper_insignes.py`` (« Écureuil » -> ecureuil-0.png)."""
        sans_accents = unicodedata.normalize("NFKD", self.nom).encode("ascii", "ignore").decode()
        return f"{sans_accents.lower().replace(' ', '-')}-{niveau}.png"


BAREMES = {
    b.nom: b
    for b in (
        Bareme("Salle", tuple(_COULEURS), {
            "Classique": (455, 480, 500, 515, 530, 545, 555, 565, 575),
            "Poulies": (540, 550, 555, 560, 565, 570, 575, 580, 585),
        }),
        Bareme("FITA", tuple(_COULEURS), {
            "Classique": (480, 510, 535, 560, 585, 605, 625, 645, 660),
            "Poulies": (620, 635, 645, 655, 665, 675, 685, 695, 700),
        }),
        Bareme("Fédéral", ("1 archer", "2 archers", "3 archers", "4 archers", "1 archer noir"), {
            "Classique": (500, 550, 600, 640, 670),
            "Poulies": (550, 600, 640, 670, 690),
        }),
        Bareme("Marcassin campagne", tuple(_ECUSSONS[:4]), {"Classique": (160, 210, 270, 320)}),
        Bareme("Écureuil", tuple(_ECUSSONS), {
            "Classique": (200, 240, 260, 300, 340, 380),
            "Poulies": (220, 260, 280, 320, 360, 400),
            "Arc nu cadet": (160, 200, 220, 260, 300, 340),
            "Arc nu": (180, 220, 240, 280, 320, 360),
        }),
        Bareme("Marcassin nature", tuple(_ORANGE), {"Arc nu": (325, 550, 775)}),
        Bareme("Sanglier", tuple(_ECUSSONS), {
            "Arc droit": (240, 475, 700, 850, 1000, 1075),
            "Arc chasse": (350, 575, 800, 950, 1100, 1175),
            "Arc nu": (400, 625, 850, 1000, 1150, 1225),
            "Poulies": (450, 675, 900, 1050, 1200, 1275),
            "Arc libre": (600, 825, 1050, 1200, 1350, 1425),
        }),
        Bareme("Lynx", tuple(_ORANGE), {"Arc nu": (150, 175, 210)}),
        Bareme("Chevreuil", tuple(_ECUSSONS), {
            "Arc droit": (70, 125, 185, 235, 270, 335),
            "Arc chasse": (85, 140, 195, 260, 300, 360),
            "Arc nu": (110, 160, 220, 270, 315, 375),
            "Poulies": (140, 210, 280, 330, 385, 435),
            "Arc libre": (185, 260, 330, 380, 435, 460),
        }),
        Bareme("Beursault", ("1 marmot", "2 marmots", "3 marmots", "4 marmots"), {
            "Classique": (32, 35, 38, 40),
            "Poulies": (32, 35, 38, 40),
        }),
    )
}
DISCIPLINES_FITA = {"FITA / extérieur", "2x70 m", "2x50 m", "TAE DI", "TAE"}  # pas le TAE DN


def _distance_fita(age: str, arme: str) -> int | None:
    """Distance à laquelle les badges FITA se gagnent, selon l'âge et l'arc."""
    if age == "Benjamin":
        return 30
    if age == "Minime":
        return 40
    if age == "Poussin":
        return None
    if arme == "Poulies":
        return 50
    return 60 if age == "Cadet" else 70


def _bareme_epreuve(e: Epreuve) -> tuple[str, str, int, int] | None:
    """(barème, colonne, score, niveau maximal) applicables à une épreuve, sinon None."""
    score = e.points
    if not score:
        return None
    arme = "Poulies" if e.arme == "Poulies nu" else e.arme
    cible = "Classique" if arme == "Arc nu" else arme  # salle, FITA, fédéral
    jeune = e.age in JEUNES
    if e.discipline == "Salle (18 m)" and cible in ("Classique", "Poulies"):
        # Les 3 étoiles se gagnent obligatoirement sur blason de 40 cm.
        petit = e.blason == 40 if e.blason else e.age not in ("Poussin", "Benjamin", "Minime")
        return "Salle", cible, score, 9 if petit else 6
    if e.discipline in DISCIPLINES_FITA and cible in ("Classique", "Poulies"):
        distance = e.distance or {"2x70 m": 70, "2x50 m": 50}.get(e.discipline)
        attendue = _distance_fita(e.age, e.arme)
        if attendue is None or (distance and distance != attendue):
            return None
        if e.arme == "Poulies" and e.blason and e.blason != 80:
            return None  # barème poulies : blason de 80 cm réduit
        return "FITA", cible, score, 9
    if e.discipline == "Fédéral" and cible in ("Classique", "Poulies"):
        return "Fédéral", cible, score, 5
    if e.discipline == "Campagne" and score <= 432 and e.age != "Poussin":  # 24 cibles
        if arme == "Classique":
            return ("Marcassin campagne", "Classique", score, 4) if jeune else ("Écureuil", "Classique", score, 6)
        if arme == "Poulies" and e.age not in JEUNES - {"Cadet"}:
            return "Écureuil", "Poulies", score, 6
        if arme == "Arc nu" and e.age not in JEUNES - {"Cadet"}:
            return "Écureuil", "Arc nu cadet" if e.age == "Cadet" else "Arc nu", score, 6
        return None
    for discipline, maxi, jeunes, adultes in (
        ("Parcours nature", 1470, "Marcassin nature", "Sanglier"),
        ("3D", 528, "Lynx", "Chevreuil"),
    ):
        if e.discipline == discipline and score <= maxi:
            if jeune and not (e.age == "Cadet" and arme == "Arc libre"):
                return (jeunes, arme, score, 3) if arme == "Arc nu" else None
            return (adultes, arme, score, 6) if arme in BAREMES[adultes].seuils else None
    if e.discipline == "Beursault" and arme in ("Classique", "Poulies") and score >= 1000:
        return "Beursault", arme, score // 1000, 4  # points notés « honneurs × 1000 + points »
    return None


@dataclass
class Distinction:
    bareme: Bareme
    niveau: int  # indice dans bareme.niveaux

    @property
    def libelle(self) -> str:
        return f"{self.bareme.nom} – {self.bareme.niveaux[self.niveau]}"


def distinction(e: Epreuve) -> Distinction | None:
    """Distinction la plus haute que permet le score d'une épreuve."""
    regle = _bareme_epreuve(e)
    if regle is None:
        return None
    nom, colonne, score, plafond = regle
    bareme = BAREMES[nom]
    atteints = [i for i, seuil in enumerate(bareme.seuils[colonne][:plafond]) if score >= seuil]
    return Distinction(bareme, atteints[-1]) if atteints else None


@dataclass
class Obtention:
    """Progression d'un archer pour un barème et un arc donnés."""

    bareme: Bareme
    arme: str
    etapes: list[tuple[int, Epreuve]]  # (niveau, épreuve) à chaque nouveau niveau, dans l'ordre

    @property
    def niveau(self) -> int:
        return self.etapes[-1][0]

    @property
    def epreuve(self) -> Epreuve:
        return self.etapes[-1][1]

    def niveau_avant(self, saison: str) -> int | None:
        avant = [n for n, e in self.etapes if e.saison < saison]
        return avant[-1] if avant else None


def obtentions(liste: list[Epreuve]) -> list[Obtention]:
    """Distinctions d'un archer, la plus récente en premier."""
    par_cle: dict[tuple[str, str], Obtention] = {}
    for e in sorted(liste, key=lambda x: x.iso):
        d = distinction(e)
        if d is None:
            continue
        cle = (d.bareme.nom, e.arme)
        if cle not in par_cle:
            par_cle[cle] = Obtention(d.bareme, e.arme, [(d.niveau, e)])
        elif d.niveau > par_cle[cle].niveau:
            par_cle[cle].etapes.append((d.niveau, e))
    return sorted(par_cle.values(), key=lambda o: o.epreuve.iso, reverse=True)


# --- Briques HTML ----------------------------------------------------------

_CSS = """
/* Charte graphique de la compagnie */
@font-face {
  font-family: "Caveat";
  src: url("static/caveat.woff2") format("woff2");
  font-weight: 400 700;
  font-display: swap;
}
:root {
  --accent: #3f60aa; --bleu: #1b2b5a; --bleu-moyen: #475b8b; --encre: #09132d;
  --fond: #f3ebe7; --carte: #fffdfb; --gris: #3b3f50; --gris-doux: #767e98;
  --filet: #acafc1; --pale: #eef1f8; --hover: #e8edf7; --grand: #fff4d1;
  --m0: #3f60aa; --m1: #c99a06; --m2: #8d99a3; --m3: #a8602d;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--fond); color: var(--encre);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); }
a:focus-visible, :focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
h1, h2 { font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; }
h1 { font-size: 1.55rem; margin: 0.3rem 0 0.2rem; }
h2 { font-size: 1.2rem; margin: 2rem 0 0.6rem; color: var(--bleu); }
h3 { margin: 1rem 0 0.4rem; color: var(--bleu); }
.manuscrit { font-family: "Caveat", "Segoe Script", "Bradley Hand", cursive;
  text-transform: none; letter-spacing: 0; }
.gabarit { max-width: 1240px; margin: 0 auto; }
header.site { background: var(--bleu); color: var(--fond); padding: 1.4rem 1.25rem 1.1rem;
  border-bottom: 4px solid var(--accent); }
header.site .entete-flex { display: flex; align-items: center; gap: 1rem; }
header.site a.marque { display: flex; align-items: center; gap: 1rem; color: inherit;
  text-decoration: none; }
.logo { height: 64px; width: auto; flex: none; }
header.site .manuscrit { margin: 0; font-size: 1.55rem; line-height: 1.1; color: #ccd4ea; }
header.site .titre { margin: 0.15rem 0 0; font-size: 1.55rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.03em; }
nav.site { display: flex; flex-wrap: wrap; gap: 0.4rem 1.4rem; padding: 0.8rem 1.25rem 0; }
nav.site a { color: var(--accent); font-weight: 600; }
nav.site a[aria-current] { color: var(--encre); text-decoration: none; }
main { padding: 1.25rem 1.25rem 1.5rem; }
footer { padding: 0 1.25rem 1.5rem; font-size: 0.88rem; color: var(--gris); }
.fil { color: var(--gris); font-size: 0.9rem; }
.fil a { color: var(--gris); }
.chiffres { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem; margin: 1rem 0; }
.chiffre { background: var(--carte); border: 1px solid var(--filet);
  border-left: 4px solid var(--accent); border-radius: 6px; padding: 0.7rem 0.9rem; }
.chiffre b { display: block; font-size: 1.7rem; line-height: 1.2; color: var(--bleu); }
.chiffre span { color: var(--gris); font-size: 0.9rem; }
.carte { background: var(--carte); border: 1px solid var(--filet); border-radius: 6px;
  padding: 0.8rem 1rem; margin: 1rem 0; }
.saisons { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.8rem; }
.saisons a { display: block; background: var(--carte); border: 1px solid var(--filet);
  border-left: 4px solid var(--accent); border-radius: 6px; padding: 0.5rem 0.8rem;
  text-decoration: none; color: var(--encre); }
.saisons a:hover { background: var(--pale); }
.saisons a b { font-size: 1.1rem; color: var(--bleu); }
.saisons a span { display: block; font-size: 0.85rem; color: var(--gris); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; background: var(--carte); font-size: 0.92rem; }
th, td { border: 1px solid var(--filet); padding: 0.4rem 0.65rem; text-align: left; }
th { background: var(--bleu); color: #fff; font-weight: 600; white-space: nowrap; }
tbody tr:hover { background: var(--hover); }
tr.grand { background: var(--grand); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
th.triable { cursor: pointer; user-select: none; }
th.triable::after { content: " \\2195"; color: #aab4d4; font-weight: normal; }
th.triable.asc::after { content: " \\25B2"; color: #fff; }
th.triable.desc::after { content: " \\25BC"; color: #fff; }
input.filtre { width: 100%; max-width: 360px; padding: 0.5rem 0.7rem; font-size: 1rem;
  border: 1px solid var(--filet); border-radius: 6px; background: var(--carte); color: var(--encre); }
.pager { display: flex; justify-content: space-between; margin: 0.8rem 0; font-weight: 600; }
.legende { display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.9rem; color: var(--gris);
  margin: 0.3rem 0; }
.legende i { display: inline-block; width: 0.8rem; height: 0.8rem; border-radius: 2px;
  margin-right: 0.35rem; vertical-align: -1px; }
svg.chart { width: 100%; height: auto; display: block; }
svg.chart text { fill: var(--gris); font-size: 11px; }
svg.chart .grille { stroke: var(--filet); stroke-width: 1; }
svg.chart rect.m0 { fill: var(--m0); } svg.chart rect.m1 { fill: var(--m1); }
svg.chart rect.m2 { fill: var(--m2); } svg.chart rect.m3 { fill: var(--m3); }
svg.chart rect.b { stroke: var(--carte); stroke-width: 2; }
.i0 { background: var(--m0); } .i1 { background: var(--m1); }
.i2 { background: var(--m2); } .i3 { background: var(--m3); }
.participant { white-space: nowrap; }
.piquet { white-space: nowrap; }
.piquet.deduit { font-style: italic; color: var(--gris); }
.piquet i { display: inline-block; width: 0.75rem; height: 0.75rem; border-radius: 50%;
  border: 1px solid var(--gris-doux); margin-right: 0.35rem; vertical-align: -1px; }
.distinction { white-space: nowrap; }
img.insigne { height: 32px; width: auto; vertical-align: middle; margin-left: 0.3rem; }
.etoiles { color: #9a6b00; font-size: 0.85rem; margin-left: 0.1rem; }
.etapes { color: var(--gris); font-size: 0.85rem; white-space: nowrap; }
.etapes img.insigne { height: 22px; margin: 0 0.15rem 0 0; }
details { margin: 0.6rem 0; }
summary { cursor: pointer; font-weight: 600; padding: 0.3rem 0; color: var(--bleu); }
@media (max-width: 600px) {
  header.site { padding: 0.8rem 1rem; }
  .logo { height: 44px; }
  header.site .manuscrit, header.site .titre { font-size: 1.2rem; }
}
"""

_JS = """
function cleTri(texte) {
  const t = texte.trim();
  const d = t.match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})$/);
  if (d) return [0, Number(d[3] + d[2] + d[1])];
  const n = t.replace(/[^\\w]/g, '');
  if (/^\\d+$/.test(n)) return [0, Number(n)];
  return [1, t.toLowerCase()];
}
function comparer(a, b) {
  if (a[0] !== b[0]) return a[0] - b[0];
  if (typeof a[1] === 'number') return a[1] - b[1];
  return a[1].localeCompare(b[1], 'fr');
}
document.querySelectorAll('table.tri').forEach(function (table) {
  const tbody = table.tBodies[0];
  table.querySelectorAll('thead th').forEach(function (th, index) {
    th.classList.add('triable');
    th.addEventListener('click', function () {
      const sens = th.classList.contains('asc') ? -1 : 1;
      table.querySelectorAll('thead th').forEach(function (o) { o.classList.remove('asc', 'desc'); });
      th.classList.add(sens === 1 ? 'asc' : 'desc');
      const lignes = Array.from(tbody.rows).map(function (row, pos) {
        return { row: row, pos: pos, cle: cleTri(row.cells[index].dataset.tri || row.cells[index].textContent) };
      });
      lignes.sort(function (x, y) { return sens * comparer(x.cle, y.cle) || x.pos - y.pos; });
      lignes.forEach(function (l) { tbody.appendChild(l.row); });
    });
  });
});
// Un lien vers une section repliée (#s2024) l'ouvre.
function ouvrirAncre() {
  const cible = location.hash && document.getElementById(location.hash.slice(1));
  if (cible && cible.tagName === 'DETAILS') { cible.open = true; cible.scrollIntoView(); }
}
window.addEventListener('hashchange', ouvrirAncre);
ouvrirAncre();
document.querySelectorAll('input[data-filtre]').forEach(function (input) {
  const table = document.getElementById(input.dataset.filtre);
  input.addEventListener('input', function () {
    const q = input.value.toLowerCase();
    Array.from(table.tBodies[0].rows).forEach(function (row) {
      row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
});
"""


def esc(texte: Any) -> str:
    return html_lib.escape(str(texte))


def place_html(place: int | None) -> str:
    if place is None:
        return "–"
    return f"{MEDAILLES[place]} {place}" if place in MEDAILLES else str(place)


PIQUETS = {1: ("Rouge", "#c62828"), 2: ("Bleu", "#2445b8"), 3: ("Blanc", "#ffffff")}


def piquet_html(e: Epreuve) -> str:
    if e.piquet not in PIQUETS:
        return ""
    nom, couleur = PIQUETS[e.piquet]
    if e.piquet_deduit:
        return (f"<span class='piquet deduit' title=\"Piquet {e.piquet}, déduit de la catégorie (non précisé par "
                f"la FFTA)\"><i style='background:{couleur}'></i>{nom}</span>")
    return f"<span class='piquet' title='Piquet {e.piquet}'><i style='background:{couleur}'></i>{nom}</span>"


def insigne(bareme: Bareme, niveau: int, profondeur: int) -> str:
    """Image de l'insigne ; le nom du niveau apparaît au survol."""
    nom = bareme.niveaux[niveau]
    libelle = esc(f"{bareme.nom} – {nom}")
    # Les insignes à étoiles ne se distinguent pas en petit : on écrit les étoiles.
    etoiles = f"<span class='etoiles'>{nom}</span>" if "★" in nom else ""
    return (f"<img class='insigne' src='{'../' * profondeur}static/distinctions/{bareme.image(niveau)}' "
            f"alt='{libelle}' title='{libelle}' />{etoiles}")


def distinction_html(d: Distinction | None, profondeur: int) -> str:
    """Nom du barème suivi de l'insigne ; le niveau, masqué, reste cherchable par le filtre."""
    if d is None:
        return ""
    return (f"<span class='distinction'>{esc(d.bareme.nom)}{insigne(d.bareme, d.niveau, profondeur)}"
            f"<span hidden>{esc(d.bareme.niveaux[d.niveau])}</span></span>")


def tableau_obtentions(obts: list[tuple[str, str, Obtention]], profondeur: int, ident: str,
                       avec_archer: bool = True, saison: str = "") -> str:
    """``obts`` : (licence, nom, obtention). Avec ``saison`` : niveau atteint avant cette saison."""
    entetes = [("Archer", False)] if avec_archer else []
    entetes += [("Discipline", False), ("Arc", False)]
    if saison:
        entetes.append(("Avant la saison", False))
    entetes += [("Distinction", False), ("Score", True), ("Date", False), ("Concours", False), ("Progression", False)]
    lignes = []
    for licence, nom, o in obts:
        e = o.epreuve
        cel = [lien_archer(licence, nom, profondeur)] if avec_archer else []
        cel += [esc(e.discipline), esc(e.arme)]
        if saison:
            avant = o.niveau_avant(saison)
            cel.append(insigne(o.bareme, avant, profondeur) if avant is not None else "–")
        cel.append(distinction_html(Distinction(o.bareme, o.niveau), profondeur))
        etapes = " → ".join(f"{insigne(o.bareme, n, profondeur)}{esc(x.saison)}" for n, x in o.etapes)
        score = f"{(e.points or 0) // 1000} honneurs" if o.bareme.nom == "Beursault" else str(e.points)
        cel += [score, esc(e.date), esc(e.concours), f"<span class='etapes'>{etapes}</span>"]
        lignes.append(tr(cel, {len(cel) - 4}))
    return tableau(entetes, lignes, ident)


def page(titre: str, corps: str, profondeur: int, actif: str, saisons: list[str]) -> str:
    racine = "../" * profondeur
    liens = [
        ("accueil", "index.html", "Accueil"),
        ("archers", "archers/index.html", "Archers"),
        ("grands", "grands-concours.html", "Grands concours"),
        ("records", "records.html", "Records"),
        ("distinctions", "distinctions.html", "Distinctions"),
        ("apropos", "a-propos.html", "À propos"),
    ]
    nav = "".join(
        f"<a href='{racine}{href}'{' aria-current=page' if cle == actif else ''}>{label}</a>"
        for cle, href, label in liens
    )
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(titre)} – {CLUB}</title>
<link rel="icon" href="{racine}static/logo.png" />
<link rel="stylesheet" href="{racine}style.css" />
</head>
<body>
<header class="site"><div class="gabarit">
<a class="marque" href="{racine}index.html">
<img class="logo" src="{racine}static/logo.png" alt="Blason de Pontoise" />
<div><p class="manuscrit">Compagnie d'archers de Pontoise</p>
<p class="titre">Palmarès</p></div>
</a>
</div></header>
<nav class="site gabarit">{nav}</nav>
<main class="gabarit">
{corps}
</main>
<footer class="gabarit">Palmarès issu de l'extranet FFTA – généré le {date.today():%d/%m/%Y}.
Voir <a href="{racine}a-propos.html">la méthode de calcul</a>.</footer>
<script src="{racine}site.js"></script>
</body>
</html>
"""


def tableau(entetes: list[tuple[str, bool]], lignes: list[str], ident: str = "", tri: bool = True) -> str:
    """``entetes`` : (libellé, numérique) ; ``lignes`` : <tr> déjà construits."""
    ths = "".join(f"<th{' class=num' if num else ''}>{esc(label)}</th>" for label, num in entetes)
    id_attr = f" id='{ident}'" if ident else ""
    cls = " class='tri'" if tri else ""
    return (
        f"<div class='table-wrap'><table{id_attr}{cls}><thead><tr>{ths}</tr></thead>"
        f"<tbody>{''.join(lignes)}</tbody></table></div>"
    )


def tr(cellules: list[str], numeriques: set[int] = frozenset(), grand: bool = False) -> str:  # type: ignore[assignment]
    tds = "".join(
        f"<td{' class=num' if i in numeriques else ''}>{c}</td>" for i, c in enumerate(cellules)
    )
    return f"<tr{' class=grand' if grand else ''}>{tds}</tr>"


def graphique(
    items: list[tuple[str, list[int]]],
    classes: list[str],
    noms: list[str],
    unite: str,
    titre: str,
) -> str:
    """Barres (empilées si plusieurs séries) en SVG, avec infobulles <title>."""
    if not items:
        return ""
    largeur, hauteur = 760, 230
    gauche, bas, haut = 34, 26, 10
    totaux = [sum(vals) for _, vals in items]
    maxi = max(totaux) or 1
    pas = next(p for p in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500) if maxi / p <= 5)
    sommet = pas * (-(-maxi // pas))
    zone_h = hauteur - bas - haut
    slot = (largeur - gauche - 6) / len(items)
    bw = min(30.0, slot * 0.72)
    saut = 1 if len(items) <= 12 else 2

    def y(valeur: float) -> float:
        return haut + zone_h - valeur / sommet * zone_h

    svg = [f"<svg class='chart' viewBox='0 0 {largeur} {hauteur}' role='img' aria-label='{esc(titre)}'>"]
    for graduation in range(0, sommet + 1, pas):
        yy = y(graduation)
        svg.append(f"<line class='grille' x1='{gauche}' x2='{largeur}' y1='{yy:.1f}' y2='{yy:.1f}' />")
        svg.append(f"<text x='{gauche - 6}' y='{yy + 4:.1f}' text-anchor='end'>{graduation}</text>")
    for i, (label, valeurs) in enumerate(items):
        x = gauche + i * slot + (slot - bw) / 2
        cumul = 0
        details = ", ".join(f"{n} : {v}" for n, v in zip(noms, valeurs) if len(noms) > 1)
        infobulle = f"{label} – {sum(valeurs)} {unite}" + (f" ({details})" if details else "")
        # Empilement depuis la dernière série : l'or (1re série) se retrouve en haut.
        for valeur, classe in reversed(list(zip(valeurs, classes))):
            if valeur <= 0:
                continue
            haut_barre = y(cumul + valeur)
            h = y(cumul) - haut_barre
            svg.append(
                f"<rect class='{classe} b' x='{x:.1f}' y='{haut_barre:.1f}' width='{bw:.1f}' "
                f"height='{h:.1f}'><title>{esc(infobulle)}</title></rect>"
            )
            cumul += valeur
        if i % saut == 0:
            svg.append(
                f"<text x='{x + bw / 2:.1f}' y='{hauteur - 8}' text-anchor='middle'>{esc(label)}</text>"
            )
    svg.append("</svg>")
    legende = ""
    if len(noms) > 1:
        legende = "<div class='legende'>" + "".join(
            f"<span><i class='i{c[1]}'></i>{esc(n)}</span>" for c, n in zip(classes, noms)
        ) + "</div>"
    return f"<div class='carte'><b>{esc(titre)}</b>{legende}{''.join(svg)}</div>"


def chiffre(valeur: Any, libelle: str) -> str:
    return f"<div class='chiffre'><b>{valeur}</b><span>{esc(libelle)}</span></div>"


# --- Calculs ---------------------------------------------------------------


def medailles(epreuves: list[Epreuve]) -> list[int]:
    return [sum(1 for e in epreuves if e.place == p) for p in (1, 2, 3)]


def lien_archer(licence: str, nom: str, profondeur: int) -> str:
    prefixe = "../" * profondeur
    return f"<a href='{prefixe}archers/{esc(licence)}.html'>{esc(nom)}</a>"


def lien_saison(saison: str, profondeur: int) -> str:
    return f"<a href='{'../' * profondeur}saisons/{esc(saison)}.html'>{esc(saison)}</a>"


def tableau_epreuves(epreuves: list[Epreuve], profondeur: int, ident: str, avec_archer: bool = True,
                     avec_saison: bool = True) -> str:
    entetes = [("Date", False)]
    if avec_saison:
        entetes.append(("Saison", False))
    if avec_archer:
        entetes.append(("Archer", False))
    entetes += [("Place", True), ("Concours", False), ("Discipline", False), ("Catégorie", False), ("Points", True)]
    lignes = []
    for e in epreuves:
        cel = [esc(e.date)]
        if avec_saison:
            cel.append(lien_saison(e.saison, profondeur))
        if avec_archer:
            cel.append(lien_archer(e.licence, e.nom, profondeur))
        cel += [place_html(e.place), esc(e.concours), esc(e.discipline), esc(e.categorie), str(e.points or "")]
        nums = {len(cel) - 5, len(cel) - 1}
        lignes.append(tr(cel, nums, e.grand))
    return tableau(entetes, lignes, ident)


# --- Pages -----------------------------------------------------------------


def page_accueil(epreuves: list[Epreuve], par_saison: dict[str, list[Epreuve]], saisons: list[str]) -> str:
    archers = {e.licence for e in epreuves}
    podiums = [e for e in epreuves if e.podium]
    gc = [e for e in podiums if e.grand]
    or_total = sum(1 for e in podiums if e.place == 1)
    items_medailles = [(s, medailles(par_saison[s])) for s in reversed(saisons)]
    items_archers = [(s, [len({e.licence for e in par_saison[s]})]) for s in reversed(saisons)]

    cartes = "".join(
        f"<a href='saisons/{s}.html'><b>{s}</b>"
        f"<span>{len({e.licence for e in par_saison[s]})} archers</span>"
        f"<span>{sum(1 for e in par_saison[s] if e.podium)} podiums</span></a>"
        for s in saisons
    )
    derniere = saisons[0]
    # Podiums en grands concours d'abord, complétés par les plus récents (8 au plus).
    podiums_saison = [e for e in par_saison[derniere] if e.podium]
    retenus = ([e for e in podiums_saison if e.grand] + [e for e in podiums_saison if not e.grand])[:8]
    recents = sorted(retenus, key=lambda e: e.iso, reverse=True)
    recents_html = ""
    if recents:
        recents_html = (
            f"<h2>Derniers podiums ({derniere})</h2><p class='fil'>Surlignés : grands concours. "
            f"<a href='saisons/{derniere}.html'>Les {len(podiums_saison)} podiums de la saison</a></p>"
            + tableau_epreuves(recents, 0, "recents", avec_saison=False)
        )
    return f"""
<h1>Palmarès de la {CLUB}</h1>
<p class="fil">{saisons[-1]} – {saisons[0]} · données de l'extranet FFTA</p>
<div class="chiffres">
{chiffre(len(archers), "archers au palmarès")}
{chiffre(len(saisons), "saisons")}
{chiffre(len(epreuves), "participations")}
{chiffre(len(podiums), "podiums")}
{chiffre(or_total, "victoires 🥇")}
{chiffre(len(gc), "podiums en grands concours")}
</div>
{graphique(items_medailles, ["m1", "m2", "m3"], ["Or", "Argent", "Bronze"], "podiums", "Podiums par saison")}
{graphique(items_archers, ["m0"], ["Archers"], "archers", "Archers ayant tiré au moins un concours, par saison")}
<h2>Les saisons</h2>
<div class="saisons">{cartes}</div>
{recents_html}
<h2>Explorer</h2>
<ul>
<li><a href="archers/index.html">Tous les archers</a> – carrière, médailles, meilleurs scores</li>
<li><a href="grands-concours.html">Grands concours</a> – championnats et coupes, année par année</li>
<li><a href="records.html">Records</a> – classement des médaillés et meilleurs scores</li>
</ul>
"""


def page_saison(saison: str, eps: list[Epreuve], saisons: list[str]) -> str:
    i = saisons.index(saison)
    precedent = f"<a href='{saisons[i + 1]}.html'>← {saisons[i + 1]}</a>" if i + 1 < len(saisons) else "<span></span>"
    suivant = f"<a href='{saisons[i - 1]}.html'>{saisons[i - 1]} →</a>" if i > 0 else "<span></span>"
    podiums = [e for e in eps if e.podium]
    gc = [e for e in podiums if e.grand]
    g, s, b = medailles(eps)

    par_archer: dict[str, list[Epreuve]] = defaultdict(list)
    for e in eps:
        par_archer[e.licence].append(e)
    lignes = []
    for licence, liste in sorted(par_archer.items(), key=lambda kv: (-sum(medailles(kv[1])), kv[1][0].nom)):
        m = medailles(liste)
        lignes.append(
            tr(
                [lien_archer(licence, liste[0].nom, 1), str(len(liste)), str(m[0]), str(m[1]), str(m[2]),
                 str(sum(1 for e in liste if e.podium and e.grand))],
                {1, 2, 3, 4, 5},
            )
        )
    tab_archers = tableau(
        [("Archer", False), ("Concours", True), ("🥇", True), ("🥈", True), ("🥉", True), ("Podiums GC", True)],
        lignes,
    )
    bloc_gc = (
        "<h2>Podiums en grands concours</h2>" + tableau_epreuves(gc, 1, "gc", avec_saison=False)
        if gc
        else "<h2>Podiums en grands concours</h2><p>Aucun podium en grand concours cette saison.</p>"
    )
    return f"""
<p class="fil"><a href="../index.html">Accueil</a> › Saisons › {saison}</p>
<h1>Saison {saison}</h1>
<div class="pager">{precedent}{suivant}</div>
<div class="chiffres">
{chiffre(len(par_archer), "archers")}
{chiffre(len(eps), "participations")}
{chiffre(g, "🥇 or")}{chiffre(s, "🥈 argent")}{chiffre(b, "🥉 bronze")}
{chiffre(len(gc), "podiums en grands concours")}
</div>
{bloc_gc}
<h2>Tous les podiums ({len(podiums)})</h2>
{tableau_epreuves(podiums, 1, "podiums", avec_saison=False)}
<h2>Les archers de la saison</h2>
<input class="filtre" data-filtre="archers-saison" placeholder="Filtrer par nom…" />
{tab_archers.replace("<table", "<table id='archers-saison'", 1)}
"""


def page_annuaire(par_archer: dict[str, list[Epreuve]]) -> str:
    lignes = []
    for licence, liste in sorted(par_archer.items(), key=lambda kv: kv[1][0].nom):
        saisons = sorted({e.saison for e in liste})
        m = medailles(liste)
        lignes.append(
            tr(
                [lien_archer(licence, liste[0].nom, 1),
                 f"{saisons[0]}–{saisons[-1]}" if len(saisons) > 1 else saisons[0],
                 str(len(saisons)), str(len(liste)), str(m[0]), str(m[1]), str(m[2]),
                 str(sum(1 for e in liste if e.podium and e.grand))],
                {2, 3, 4, 5, 6, 7},
            )
        )
    tab = tableau(
        [("Archer", False), ("Période", False), ("Saisons", True), ("Concours", True),
         ("🥇", True), ("🥈", True), ("🥉", True), ("Podiums GC", True)],
        lignes,
    ).replace("<table", "<table id='annuaire'", 1)
    return f"""
<p class="fil"><a href="../index.html">Accueil</a> › Archers</p>
<h1>Les archers ({len(par_archer)})</h1>
<p><input class="filtre" data-filtre="annuaire" placeholder="Rechercher un archer…" autofocus /></p>
{tab}
"""


def page_archer(liste: list[Epreuve]) -> str:
    nom = liste[0].nom
    saisons = sorted({e.saison for e in liste})
    m = medailles(liste)
    gc = [e for e in liste if e.podium and e.grand]
    podiums = [e for e in liste if e.podium]

    items = [(s, medailles([e for e in liste if e.saison == s])) for s in saisons]
    graph = graphique(items, ["m1", "m2", "m3"], ["Or", "Argent", "Bronze"], "podiums", "Podiums par saison") \
        if podiums else ""

    lignes = []
    for s in reversed(saisons):
        d = [e for e in liste if e.saison == s]
        mm = medailles(d)
        disciplines = ", ".join(sorted({e.discipline for e in d}))
        armes = ", ".join(sorted({e.arme for e in d if e.arme != "Autre"}))
        lignes.append(
            tr([lien_saison(s, 1), str(len(d)), str(mm[0]), str(mm[1]), str(mm[2]),
                esc(armes), esc(disciplines)], {1, 2, 3, 4})
        )
    parcours = tableau(
        [("Saison", False), ("Concours", True), ("🥇", True), ("🥈", True), ("🥉", True),
         ("Arc", False), ("Disciplines", False)],
        lignes,
    )

    meilleurs: dict[tuple[str, str], Epreuve] = {}
    for e in liste:
        if e.points and e.discipline in DISCIPLINES_RECORDS:
            cle = (e.discipline, e.arme)
            if cle not in meilleurs or e.points > (meilleurs[cle].points or 0):
                meilleurs[cle] = e
    lignes_scores = [
        tr([esc(d), esc(a), str(e.points), distinction_html(distinction(e), 1), esc(e.date), esc(e.concours)], {2})
        for (d, a), e in sorted(meilleurs.items(), key=lambda kv: (DISCIPLINES_RECORDS.index(kv[0][0]), kv[0][1]))
    ]
    bloc_scores = (
        "<h2>Meilleurs scores</h2>"
        + tableau([("Discipline", False), ("Arc", False), ("Points", True), ("Distinction", False),
                   ("Date", False), ("Concours", False)], lignes_scores)
        if lignes_scores
        else ""
    )
    obts = obtentions(liste)
    bloc_distinctions = (
        "<h2>Distinctions</h2><p class='fil'>Plus haut niveau atteint par barème et par arc, d'après les scores "
        "en concours officiel (<a href='../distinctions.html'>détails</a>).</p>"
        + tableau_obtentions([(o.epreuve.licence, nom, o) for o in obts], 1, "distinctions", avec_archer=False)
        if obts
        else ""
    )
    bloc_gc = (
        "<h2>Podiums en grands concours</h2>" + tableau_epreuves(gc, 1, "gc", avec_archer=False) if gc else ""
    )
    bloc_podiums = (
        f"<h2>Tous les podiums ({len(podiums)})</h2>" + tableau_epreuves(podiums, 1, "podiums", avec_archer=False)
        if podiums
        else ""
    )
    return f"""
<p class="fil"><a href="../index.html">Accueil</a> › <a href="index.html">Archers</a> › {esc(nom)}</p>
<h1>{esc(nom)}</h1>
<p class="fil">Licence {esc(liste[0].licence)} · {saisons[0]}–{saisons[-1]}</p>
<div class="chiffres">
{chiffre(len(saisons), "saisons")}
{chiffre(len(liste), "concours")}
{chiffre(m[0], "🥇 or")}{chiffre(m[1], "🥈 argent")}{chiffre(m[2], "🥉 bronze")}
{chiffre(len(gc), "podiums en grands concours")}
</div>
{graph}
{bloc_gc}
{bloc_scores}
{bloc_distinctions}
<h2>Parcours saison par saison</h2>
{parcours}
{bloc_podiums}
<details><summary>Tous les concours ({len(liste)})</summary>
{tableau_epreuves(liste, 1, "tous", avec_archer=False)}
</details>
"""


def page_grands_concours(epreuves: list[Epreuve], saisons: list[str]) -> str:
    gc = [e for e in epreuves if e.grand]
    sections = []
    for s in saisons:
        d = [e for e in gc if e.saison == s]
        if not d:
            continue
        podiums = [e for e in d if e.podium]
        # Une ligne par concours, avec tous les participants du club (médaillés en tête).
        par_concours: dict[tuple[str, str], list[Epreuve]] = defaultdict(list)
        for e in d:
            par_concours[(e.iso, e.concours)].append(e)
        lignes = []
        for (_, concours), liste in sorted(par_concours.items(), reverse=True):
            liste.sort(key=lambda e: (e.place is None, e.place or 0, e.nom))
            participants = " · ".join(
                f"<span class='participant'>{MEDAILLES[e.place] + ' ' if e.podium else ''}"
                f"{lien_archer(e.licence, e.nom, 0)}"
                f"{f' <span class=etapes>{e.place}e</span>' if e.place and not e.podium else ''}</span>"
                for e in liste
            )
            disciplines = ", ".join(sorted({e.discipline for e in liste}))
            lignes.append(tr([esc(liste[0].date), esc(concours), esc(disciplines), participants],
                             grand=any(e.podium for e in liste)))
        tab = tableau([("Date", False), ("Concours", False), ("Discipline", False), ("Participants", False)],
                      lignes)
        sections.append(
            f"<details id='s{s}'{' open' if s == saisons[0] else ''}><summary>Saison {esc(s)} – "
            f"{len(par_concours)} concours, {len(d)} participations, {len(podiums)} podium(s)</summary>"
            f"<p class='fil'>{lien_saison(s, 0)}</p>{tab}</details>"
        )
    lignes_ancre = " · ".join(f"<a href='#s{s}'>{s}</a>" for s in saisons if any(e.saison == s for e in gc))
    items = [(s, medailles([e for e in gc if e.saison == s])) for s in reversed(saisons)]
    return f"""
<p class="fil"><a href="index.html">Accueil</a> › Grands concours</p>
<h1>Grands concours</h1>
<p>Championnats de France et régionaux, coupes de France et assimilés
(<a href="a-propos.html">critères</a>). Pour chaque concours, tous les archers du club qui y ont tiré :
médaillés en tête, puis place obtenue. Les lignes surlignées sont celles avec au moins un podium.</p>
{graphique(items, ["m1", "m2", "m3"], ["Or", "Argent", "Bronze"], "podiums", "Podiums en grands concours par saison")}
<p>Aller à : {lignes_ancre}</p>
{''.join(sections)}
"""


def page_records(epreuves: list[Epreuve], par_archer: dict[str, list[Epreuve]]) -> str:
    classement = sorted(
        par_archer.items(),
        key=lambda kv: (tuple(-x for x in medailles(kv[1])), kv[1][0].nom),
    )
    lignes = []
    for rang, (licence, liste) in enumerate([c for c in classement if sum(medailles(c[1]))][:30], start=1):
        m = medailles(liste)
        lignes.append(
            tr([str(rang), lien_archer(licence, liste[0].nom, 0), str(m[0]), str(m[1]), str(m[2]), str(sum(m)),
                str(sum(1 for e in liste if e.podium and e.grand))], {0, 2, 3, 4, 5, 6})
        )
    tab_medailles = tableau(
        [("#", True), ("Archer", False), ("🥇", True), ("🥈", True), ("🥉", True), ("Total", True), ("Podiums GC", True)],
        lignes,
    )

    blocs = []
    for discipline in DISCIPLINES_RECORDS:
        groupes: dict[tuple[str, str], dict[str, Epreuve]] = defaultdict(dict)
        for e in epreuves:
            if e.discipline != discipline or not e.points or e.arme == "Autre":
                continue
            meilleur = groupes[(e.arme, e.sexe)].get(e.licence)
            if meilleur is None or e.points > (meilleur.points or 0):
                groupes[(e.arme, e.sexe)][e.licence] = e
        if not groupes:
            continue
        sous = []
        for (arme, sexe), par_licence in sorted(groupes.items()):
            top = sorted(par_licence.values(), key=lambda e: -(e.points or 0))[:5]
            avec_piquet = any(e.piquet for e in top)
            lignes_r = [
                tr([str(r), lien_archer(e.licence, e.nom, 0), str(e.points)]
                   + ([piquet_html(e) or "–"] if avec_piquet else [])
                   + [lien_saison(e.saison, 0), esc(e.concours)], {0, 2})
                for r, e in enumerate(top, start=1)
            ]
            sous.append(
                f"<h3>{esc(arme)}{' – ' + esc(sexe) if sexe else ''}</h3>"
                + tableau([("#", True), ("Archer", False), ("Points", True)]
                          + ([("Piquet", False)] if avec_piquet else [])
                          + [("Saison", False), ("Concours", False)],
                          lignes_r, tri=False)
            )
        blocs.append(f"<details><summary>{esc(discipline)}</summary>{''.join(sous)}</details>")
    return f"""
<p class="fil"><a href="index.html">Accueil</a> › Records</p>
<h1>Records</h1>
<h2>Les plus médaillés (30 premiers)</h2>
{tab_medailles}
<h2>Meilleurs scores par discipline</h2>
<p>Meilleure performance de chaque archer, 5 premiers par arc et par sexe. En 3D, campagne et nature, le piquet
de tir est indiqué : rouge (1), bleu (2) ou blanc (3). La FFTA ne le précise que depuis 2020 environ ; avant, il est
<i>déduit</i> de la catégorie d'âge et de l'arc (en italique), sauf pour la 3D sur 40 cibles.</p>
{''.join(blocs)}
"""


def page_distinctions(par_archer: dict[str, list[Epreuve]], saisons: list[str]) -> str:
    derniere = saisons[0]
    toutes = sorted(
        ((licence, liste[0].nom, o) for licence, liste in par_archer.items() for o in obtentions(liste)),
        key=lambda t: (t[1], t[2].bareme.nom, t[2].arme),
    )
    actifs = {licence for licence, liste in par_archer.items() if liste[0].saison == derniere}

    sections = []
    for s in saisons:
        # Progression arrêtée à la saison s, pour chaque distinction qui y a progressé.
        lignes = [
            (licence, nom, Obtention(o.bareme, o.arme, [et for et in o.etapes if et[1].saison <= s]))
            for licence, nom, o in toutes
            if any(e.saison == s for _, e in o.etapes)
        ]
        if not lignes:
            continue
        nb_archers = len({licence for licence, _, _ in lignes})
        sections.append(
            f"<details{' open' if s == derniere else ''}><summary>Saison {esc(s)} – {len(lignes)} distinction(s), "
            f"{nb_archers} archer(s)</summary>{tableau_obtentions(lignes, 0, f'saison{s}', saison=s)}</details>"
        )
    nouvelles = [t for t in toutes if any(e.saison == derniere for _, e in t[2].etapes)]
    en_cours = [t for t in toutes if t[0] in actifs]
    anciens = [t for t in toutes if t[0] not in actifs]
    return f"""
<p class="fil"><a href="index.html">Accueil</a> › Distinctions</p>
<h1>Distinctions</h1>
<p>Badges et écussons fédéraux que les scores obtenus en concours officiel permettent de demander, d'après les
barèmes de la page <a href="{SOURCE_DISTINCTIONS}">Distinctions au tir à l'arc</a> (<a href="a-propos.html#distinctions">règles
et limites</a>). Les distinctions ne sont pas attribuées automatiquement : à vérifier avant de les remettre.</p>
<div class="chiffres">
{chiffre(len({t[0] for t in toutes}), "archers concernés")}
{chiffre(len({t[0] for t in en_cours}), f"archers de la saison {derniere}")}
{chiffre(len(nouvelles), f"distinctions gagnées ou améliorées en {derniere}")}
</div>
<h2>Nouvelles distinctions, saison par saison</h2>
<p>Chaque ligne indique le niveau atteint à la fin de la saison et celui qui était déjà acquis avant.</p>
{''.join(sections)}
<h2>Archers de la saison {esc(derniere)} : plus haut niveau atteint</h2>
<p><input class="filtre" data-filtre="actifs" placeholder="Filtrer (archer, badge, arc…)" /></p>
{tableau_obtentions(en_cours, 0, "actifs")}
<details><summary>Anciens archers ({len({t[0] for t in anciens})})</summary>
{tableau_obtentions(anciens, 0, "anciens")}
</details>
"""


def page_apropos(mots_cles: list[str], exclusions: list[str], nb_lignes: int) -> str:
    motifs = "".join(f"<li><code>{esc(m)}</code></li>" for m in mots_cles)
    exclus = "".join(f"<li><code>{esc(m)}</code></li>" for m in exclusions)
    return f"""
<p class="fil"><a href="index.html">Accueil</a> › À propos</p>
<h1>À propos et méthode</h1>
<p>Ces pages sont générées automatiquement à partir des palmarès individuels de l'extranet
FFTA ({nb_lignes} lignes de résultats) pour les archers du club listés dans
<code>membres.yaml</code>.</p>
<h2>Règles de comptage</h2>
<ul>
<li><b>Participation</b> : un archer et un concours (date + nom). Les classements scratch et de
catégorie d'un même concours sont fusionnés, la meilleure place est retenue.</li>
<li><b>Podium</b> : place 1, 2 ou 3. Une place « 0 » ou absente signifie classement non renseigné.</li>
<li><b>Saison</b> : celle indiquée par la FFTA (la saison 2026 démarre à l'automne 2025).</li>
<li><b>Points</b> : le score le plus élevé parmi les lignes fusionnées.</li>
<li><b>Disciplines</b> : regroupées (Salle et 18 m, FITA et tir extérieur…) pour comparer les saisons
entre elles ; les records n'existent que pour les disciplines à score comparable.
Le tir 3D est scindé en deux : jusqu'à la saison {PREMIERE_SAISON_3D_24_CIBLES - 1} (40 cibles, sans zone 11)
et depuis {PREMIERE_SAISON_3D_24_CIBLES} (24 cibles, avec zone 11), dont les scores ne sont pas comparables.
Le TAE est séparé en <b>TAE DI</b> (international : arc classique à 60 ou 70 m, poulies sur blason de 80 cm) et
<b>TAE DN</b> (national : arc classique à 50 m, poulies sur blason de 122 cm), d'après la distance et le blason de
la catégorie ; « TAE » seul quand ils ne permettent pas de trancher (jeunes à courte distance, arc nu).</li>
<li><b>Piquets</b> (3D, campagne, nature) : rouge (1), bleu (2), blanc (3), lus dans la catégorie FFTA (depuis
2020 environ). Avant, ils sont déduits de l'âge et de l'arc, d'après les règles observées sur les résultats récents :
en 3D, rouge pour l'arc libre, bleu pour les autres arcs (et les cadets en arc libre), blanc pour les benjamins et
minimes ; en campagne, rouge pour le classique et les poulies, bleu pour l'arc nu et les cadets, blanc pour les
jeunes, l'arc nu cadet et l'arc droit ; en nature, rouge pour les adultes. Pas de déduction pour la 3D sur 40 cibles
(avant {PREMIERE_SAISON_3D_24_CIBLES}), dont les distances étaient différentes.</li>
<li><b>Nom affiché</b> : celui de la saison la plus récente.</li>
</ul>
<h2>Grands concours</h2>
<p>Un concours est « grand » dans deux cas :</p>
<ul>
<li>la FFTA le signale comme championnat ou coupe de France (pictogramme dans le palmarès), même si son nom
n'est qu'une ville ;</li>
<li>son nom correspond à l'un de ces motifs (insensibles à la casse) :<ul>{motifs}</ul></li>
</ul>
<p>Avant d'appliquer les motifs, ces expressions sont retirées du nom : un concours « sélectif au championnat
de France » n'est donc grand que si le reste de son nom l'indique (par exemple un championnat régional).</p>
<ul>{exclus}</ul>
<h2 id="distinctions">Distinctions</h2>
<p>Les barèmes viennent de la page Wikipédia
<a href="{SOURCE_DISTINCTIONS}">Distinctions au tir à l'arc</a>. Seules les distinctions fédérales gagnées en concours
sont calculées (pas les plumes ni les flèches de progression, ni les distinctions WA). Chaque score est comparé au
barème de sa discipline, de son arc et de la classe d'âge de l'archer :</p>
<ul>
<li><b>Salle</b> : arc classique et arc nu, ou arc à poulies. Les étoiles exigent un blason de 40 cm : elles ne sont
pas comptées sur blason de 60/80 cm, ni pour les poussins, benjamins et minimes quand le blason n'est pas indiqué.</li>
<li><b>FITA</b> (FITA, 2x70 m, 2x50 m, tir extérieur, TAE DI) : uniquement à la distance du barème (benjamin 30 m,
minime 40 m, cadet classique 60 m, classique junior et plus 70 m, poulies 50 m sur blason de 80 cm). Le TAE DN
ne compte donc pas, ni le TAE DI à 60 m d'un Senior 3 (le barème prévoit 70 m). Quand la distance n'est pas indiquée
(anciennes saisons FITA), le score est supposé tiré à la bonne distance.</li>
<li><b>Fédéral</b> : arc classique et arc nu, ou arc à poulies.</li>
<li><b>Campagne</b> : Marcassin (classique, jeunes jusqu'à cadet) ou Écureuil. Seuls les scores de 432 points
au plus sont retenus (parcours de 24 cibles).</li>
<li><b>Nature</b> : Marcassin (arc nu, jeunes) ou Sanglier (scores de 1 470 au plus, parcours de 42 cibles
supposé). <b>3D</b> (depuis {PREMIERE_SAISON_3D_24_CIBLES}) : Lynx (arc nu, jeunes) ou Chevreuil ; un
cadet en arc libre relève du barème adulte. En 3D et en nature, l'arc à poulies nu suit la colonne « arc à poulies ».
Les scores 3D sur 40 cibles ne sont pas pris en compte.</li>
<li><b>Beursault</b> : marmots selon le nombre d'honneurs de la partie (32, 35, 38, 40).</li>
<li>Un classement scratch seul reprend la classe d'âge d'un autre concours de la même saison (adulte à défaut).</li>
</ul>
<p>Ces barèmes sont ceux d'aujourd'hui : ils sont appliqués tels quels aux anciennes saisons. Une distinction doit
être demandée par l'archer : la liste indique ce qui est possible, pas ce qui a été remis.</p>
<p>Images des insignes : <a href="https://commons.wikimedia.org/wiki/User:Nhhi">Nhhi</a>, Wikimedia Commons,
licence <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.fr">CC BY-SA 4.0</a> (planches découpées
par niveau).</p>
"""


# --- Orchestration ---------------------------------------------------------


def generer_site(rows: list[dict[str, Any]], mots_cles: list[str], sortie: Path,
                 exclusions: list[str] = DEFAULT_EXCLUSIONS) -> None:
    """Écrit le site complet dans ``sortie`` à partir des lignes de résultats."""
    epreuves = construire_epreuves(rows, mots_cles, exclusions)
    if not epreuves:
        logger.warning("Aucune donnée : site non généré")
        return

    par_saison: dict[str, list[Epreuve]] = defaultdict(list)
    par_archer: dict[str, list[Epreuve]] = defaultdict(list)
    for e in epreuves:
        par_saison[e.saison].append(e)
        par_archer[e.licence].append(e)
    saisons = sorted(par_saison, reverse=True)
    # Le nom affiché est celui de la saison la plus récente (epreuves déjà triées).
    for liste in par_archer.values():
        nom_recent = liste[0].nom
        for e in liste:
            e.nom = nom_recent

    (sortie / "saisons").mkdir(parents=True, exist_ok=True)
    (sortie / "archers").mkdir(exist_ok=True)

    def ecrire(chemin: str, titre: str, corps: str, profondeur: int, actif: str) -> None:
        (sortie / chemin).write_text(page(titre, corps, profondeur, actif, saisons), encoding="utf-8")

    (sortie / "static").mkdir(exist_ok=True)
    for fichier in ("logo.png", "caveat.woff2"):
        shutil.copyfile(ASSETS / fichier, sortie / "static" / fichier)
    shutil.copytree(ASSETS / "distinctions", sortie / "static" / "distinctions", dirs_exist_ok=True)
    (sortie / "style.css").write_text(_CSS, encoding="utf-8")
    (sortie / "site.js").write_text(_JS, encoding="utf-8")
    ecrire("index.html", "Palmarès", page_accueil(epreuves, par_saison, saisons), 0, "accueil")
    for s in saisons:
        ecrire(f"saisons/{s}.html", f"Saison {s}", page_saison(s, par_saison[s], saisons), 1, "accueil")
    ecrire("archers/index.html", "Les archers", page_annuaire(par_archer), 1, "archers")
    for licence, liste in par_archer.items():
        ecrire(f"archers/{licence}.html", liste[0].nom, page_archer(liste), 1, "archers")
    ecrire("grands-concours.html", "Grands concours", page_grands_concours(epreuves, saisons), 0, "grands")
    ecrire("records.html", "Records", page_records(epreuves, par_archer), 0, "records")
    ecrire("distinctions.html", "Distinctions", page_distinctions(par_archer, saisons), 0, "distinctions")
    ecrire("a-propos.html", "À propos", page_apropos(mots_cles, exclusions, len(rows)), 0, "apropos")
    logger.info(
        "Site généré dans %s : %d saisons, %d archers, %d participations",
        sortie, len(saisons), len(par_archer), len(epreuves),
    )


def lire_mots_cles(config: Path) -> list[str]:
    if config.exists():
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        return list(data.get("grands_concours_mots_cles", DEFAULT_MOTS_CLES))
    return DEFAULT_MOTS_CLES


def lire_exclusions(config: Path) -> list[str]:
    if config.exists():
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        return list(data.get("grands_concours_exclusions", DEFAULT_EXCLUSIONS))
    return DEFAULT_EXCLUSIONS


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--detail", type=Path, default=Path("detail_pontoise.csv"))
    parser.add_argument("--config", type=Path, default=Path("membres.yaml"))
    parser.add_argument("--out", type=Path, default=Path("site"))
    args = parser.parse_args()
    with args.detail.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    generer_site(rows, lire_mots_cles(args.config), args.out, lire_exclusions(args.config))


if __name__ == "__main__":
    main()
