#!/usr/bin/env python3
"""Constitue le palmarès détaillé des archers de la Compagnie d'archers de
Pontoise à partir de l'espace licencié FFTA (consulter_palmares.html).

Principe :
    Pour chaque saison, on interroge uniquement les archers qui étaient
    effectivement licenciés compétition cette saison-là (liste fournie
    par le capitaine), et on POST le formulaire de consultation de
    palmarès pour chacun. On parse ensuite la page HTML renvoyée pour en
    extraire, ligne par ligne, chaque performance individuelle : date,
    nom du concours, place obtenue à ce concours précis, et points.

Authentification :
    Le endpoint est protégé par une session PHP classique. La valeur du
    cookie PHPSESSID doit être fournie via la variable d'environnement
    FFTA_SESSION_ID (jamais en argument de ligne de commande, jamais en
    dur dans un fichier versionné) :

        export FFTA_SESSION_ID="<valeur récupérée depuis le navigateur>"
        export FFTA_OWN_LICENCE="<ta licence>"
        python3 palmares_pontoise.py --config membres.yaml

    La session expire au bout d'un moment d'inactivité : si le script
    détecte qu'il reçoit la page de connexion au lieu du palmarès, il
    s'arrête avec un message clair plutôt que de continuer à boucler pour
    rien.

Sorties :
    - detail_pontoise.csv : une ligne par performance individuelle ;
    - site/ : site statique navigable généré à partir de ce CSV
      (voir generer_site.py, qui peut aussi être lancé seul, sans réseau).

Usage:
    python3 palmares_pontoise.py --config membres.yaml

    Les pages déjà téléchargées sont réutilisées depuis .cache_ffta/. Pour
    forcer un nouveau téléchargement ciblé (le reste vient du cache) :

        python3 palmares_pontoise.py --refresh-saison 2026
        python3 palmares_pontoise.py --refresh-licence 0693361D
        python3 palmares_pontoise.py --refresh-saison 2026 --refresh-licence 0693361D
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

import requests
import yaml
from bs4 import BeautifulSoup, Tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger("palmares_pontoise")

FFTA_PALMARES_URL = "https://extranet.ffta.fr/consulter_palmares.html"
CACHE_DIR = Path(".cache_ffta")
DELAY_SECONDS = 5.0  # politesse + évite le blocage anti-abus de la FFTA (captcha)
CAPTCHA_MARKERS = ("captcha-container", "altcha-widget")
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60  # secondes : les palmarès très fournis sont lents à générer

class SessionExpiree(Exception):
    """La FFTA a renvoyé la page de connexion : le cookie n'est plus valide."""


class CaptchaRequis(Exception):
    """La FFTA demande une vérification anti-robot (captcha) au lieu du palmarès."""


class EchecTelechargement(Exception):
    """Une page n'a pas pu être récupérée après toutes les tentatives."""


@dataclass
class ArcherRef:
    """Un archer du club à interroger pour une saison donnée."""

    nom: str
    licence: str


@dataclass
class CompetitionResult:
    """Une ligne de performance individuelle à un concours donné."""

    archer_nom: str
    archer_licence: str
    saison: str
    discipline: str
    sous_categorie: str
    categorie_officielle: bool
    date: str
    concours: str
    place: str
    points: str
    resultat_id: str


def load_membership(config_path: Path) -> dict[str, list[ArcherRef]]:
    """Charge la matrice archers-par-saison depuis un YAML.

    Format attendu (la clé optionnelle ``grands_concours_mots_cles`` est lue
    par generer_site.py) :

        archers_par_saison:
          "2025":
            - nom: "BARRE Guillaume"
              licence: "0693361D"
          "2026":
            - nom: "BARRE Guillaume"
              licence: "0693361D"
            - nom: "BARRE Abigael"
              licence: "0056968X"
    """
    data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    membership: dict[str, list[ArcherRef]] = {
        str(saison): [ArcherRef(**item) for item in archers]
        for saison, archers in data["archers_par_saison"].items()
    }
    total_combos = sum(len(archers) for archers in membership.values())
    logger.info(
        "%d saisons, %d combinaisons archer/saison à interroger",
        len(membership),
        total_combos,
    )
    return membership


def build_session(session_id: str) -> requests.Session:
    """Construit une session HTTP réutilisant le cookie FFTA fourni."""
    session = requests.Session()
    session.cookies.set("PHPSESSID", session_id, domain="extranet.ffta.fr")
    session.headers.update(
        {
            "User-Agent": "palmares-pontoise-script/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://extranet.ffta.fr",
            "Referer": FFTA_PALMARES_URL,
        }
    )
    return session


def _est_captcha(page_html: str) -> bool:
    return any(marker in page_html for marker in CAPTCHA_MARKERS)


def fetch_palmares_html(
    session: requests.Session,
    archer: ArcherRef,
    saison: str,
    own_licence: str,
    refresh: bool = False,
) -> tuple[str, bool]:
    """Récupère (avec cache disque) le HTML du palmarès d'un archer pour une saison.

    Retourne ``(html, depuis_cache)``. Avec ``refresh=True``, le cache est ignoré
    et réécrit.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{archer.licence}_{saison}.html"
    if cache_file.exists() and not refresh:
        cached = cache_file.read_text(encoding="utf-8")
        if not _est_captcha(cached):  # une page captcha mise en cache est un échec
            return cached, True

    payload = {
        "operation": "enregistrer",
        "Nom": "",
        "Prenom": "",
        "CodeLicencie": archer.licence,
        "SaisonAnnee": saison,
        "LicenceCodeAdherent": own_licence,
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.post(FFTA_PALMARES_URL, data=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            page_html = response.text
            if "Utilisez vos identifiants" in page_html:
                raise SessionExpiree(
                    "Session expirée (page de connexion reçue) : "
                    "rafraîchis FFTA_SESSION_ID depuis le navigateur."
                )
            if _est_captcha(page_html):
                raise CaptchaRequis(
                    "La FFTA demande une vérification de sécurité (captcha) : "
                    "le script s'arrête pour ne pas insister. Ouvre l'extranet "
                    "dans ton navigateur, valide la vérification, attends un "
                    "moment, puis relance (les pages valides sont en cache)."
                )
            cache_file.write_text(page_html, encoding="utf-8")
            return page_html, False
        except requests.RequestException as exc:  # noqa: PERF203
            last_error = exc
            logger.warning(
                "Échec (%d/%d) pour %s/%s : %s",
                attempt,
                MAX_RETRIES,
                archer.licence,
                saison,
                exc,
            )
            time.sleep(DELAY_SECONDS * attempt)

    raise EchecTelechargement(
        f"{archer.licence}/{saison} : {type(last_error).__name__}"
    ) from last_error


def _clean_text(tag: Tag | None) -> str:
    return tag.get_text(strip=True) if tag is not None else ""


def parse_palmares(
    page_html: str, archer: ArcherRef, saison: str
) -> Iterator[CompetitionResult]:
    """Parcourt le HTML d'un palmarès et produit une ligne par performance.

    La page peut contenir deux tableaux ``table.crh`` : un pour les
    catégories officielles, un pour les non-officielles. On les traite de
    la même façon, en ne retenant que le drapeau categorie_officielle.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    for table in soup.select("table.crh"):
        header_text = _clean_text(table.select_one("thead"))
        categorie_officielle = "non officielle" not in header_text.lower()

        discipline = ""
        sous_categorie = ""
        for row in table.select("tbody tr"):
            classes = row.get("class") or []

            header_cell = row.find("h3")
            if header_cell is not None:
                discipline = _clean_text(header_cell)
                continue

            if "clmt" in classes:
                link = row.find("a")
                sous_categorie = _clean_text(link)
                continue

            if "epRow" in classes:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue
                date_texte = _clean_text(cells[0])
                concours = _clean_text(cells[1])
                place = _clean_text(cells[2])
                points_texte = _clean_text(cells[3])
                points_match = re.search(r"\d+", points_texte)
                points = points_match.group(0) if points_match else points_texte
                resultat_id = row.get("data-rid", "")

                yield CompetitionResult(
                    archer_nom=archer.nom,
                    archer_licence=archer.licence,
                    saison=saison,
                    discipline=discipline,
                    sous_categorie=sous_categorie,
                    categorie_officielle=categorie_officielle,
                    date=date_texte,
                    concours=concours,
                    place=place,
                    points=points,
                    resultat_id=str(resultat_id),
                )


def export_csv(rows: list[CompetitionResult], path: Path) -> None:
    if not rows:
        logger.warning("Aucune ligne à exporter dans %s", path)
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    logger.info("Export : %s (%d lignes)", path, len(rows))


# --- Point d'entrée ---------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("membres.yaml"))
    parser.add_argument(
        "--own-licence",
        type=str,
        default=None,
        help="Ta licence (compte connecté). Sinon lue depuis FFTA_OWN_LICENCE.",
    )
    parser.add_argument("--out-detail", type=Path, default=Path("detail_pontoise.csv"))
    parser.add_argument(
        "--out-site",
        type=Path,
        default=Path("site"),
        help="Dossier du site statique (voir generer_site.py).",
    )
    parser.add_argument(
        "--refresh-saison",
        action="append",
        default=[],
        metavar="ANNEE",
        help="Retélécharge (en ignorant le cache) cette saison. Répétable.",
    )
    parser.add_argument(
        "--refresh-licence",
        action="append",
        default=[],
        metavar="LICENCE",
        help="Retélécharge (en ignorant le cache) cet archer. Répétable.",
    )
    args = parser.parse_args()
    refresh_saisons = set(args.refresh_saison)
    refresh_licences = {lic.upper() for lic in args.refresh_licence}

    session_id = os.environ.get("FFTA_SESSION_ID")
    if not session_id:
        raise SystemExit(
            "Variable d'environnement FFTA_SESSION_ID manquante. "
            "Récupère la valeur du cookie PHPSESSID depuis ton navigateur "
            "(DevTools > Application > Cookies) et exporte-la avant de "
            "relancer le script."
        )
    own_licence = args.own_licence or os.environ.get("FFTA_OWN_LICENCE")
    if not own_licence:
        raise SystemExit(
            "Ta propre licence est requise (--own-licence ou FFTA_OWN_LICENCE)."
        )

    membership = load_membership(args.config)
    session = build_session(session_id)

    all_rows: list[CompetitionResult] = []
    echecs: list[str] = []
    combos = [
        (archer, saison)
        for saison, archers in membership.items()
        for archer in archers
    ]
    for index, (archer, saison) in enumerate(combos, start=1):
        logger.info(
            "[%d/%d] %s (%s) - saison %s",
            index,
            len(combos),
            archer.nom,
            archer.licence,
            saison,
        )
        # Si des filtres de rafraîchissement sont donnés, un combo est forcé
        # s'il satisfait chacun des filtres fournis ; les autres passent par le cache.
        refresh = bool(refresh_saisons or refresh_licences) and (
            (not refresh_saisons or saison in refresh_saisons)
            and (not refresh_licences or archer.licence.upper() in refresh_licences)
        )
        try:
            page_html, depuis_cache = fetch_palmares_html(
                session, archer, saison, own_licence, refresh=refresh
            )
        except EchecTelechargement as exc:
            logger.error("Abandon de %s", exc)
            echecs.append(f"{archer.nom} ({archer.licence}) saison {saison}")
            continue
        all_rows.extend(parse_palmares(page_html, archer, saison))
        if not depuis_cache:
            time.sleep(DELAY_SECONDS)

    export_csv(all_rows, args.out_detail)

    from generer_site import generer_site, lire_mots_cles

    generer_site([asdict(row) for row in all_rows], lire_mots_cles(args.config), args.out_site)

    if echecs:
        logger.warning(
            "%d combinaison(s) non récupérée(s), absentes du CSV et du site "
            "(relancer, ou cibler avec --refresh-licence / --refresh-saison) :\n  %s",
            len(echecs),
            "\n  ".join(echecs),
        )
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (SessionExpiree, CaptchaRequis) as exc:
        raise SystemExit(f"Erreur : {exc}") from None
    except KeyboardInterrupt:
        raise SystemExit("Interrompu (les pages déjà reçues restent en cache).") from None
