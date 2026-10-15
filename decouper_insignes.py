#!/usr/bin/env python3
"""Découpe les planches d'insignes de Wikimedia Commons en une image par niveau.

Images de Nhhi, licence CC BY-SA 4.0, utilisées par la page Wikipédia
« Distinctions au tir à l'arc ». À lancer une fois (nécessite Pillow) :

    python3 decouper_insignes.py <dossier des planches téléchargées>

Écrit ``assets/distinctions/<barème>-<niveau>.png`` (niveau = indice dans le
barème de ``generer_site.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SORTIE = Path(__file__).parent / "assets" / "distinctions"
HAUTEUR = 48  # pixels ; affiché en 24 px (écrans haute densité)

# planche -> (préfixe, position de chaque niveau dans la planche)
PLANCHES = {
    "Badges_Salle_FFTA.png": ("salle", range(9)),
    "Badges_extérieur_FFTA.png": ("fita", range(9)),
    "Badges_tir_fédéral.png": ("federal", range(5)),
    "Badges_marcassin.png": ("marcassin-campagne", range(4)),
    # Planche dans l'ordre vert, argent, or/blanc, bleu, rouge, noir.
    "Badges_ecureuil.png": ("ecureuil", [0, 1, 2, 5, 3, 4]),
    "Badges_marcassin_nature.png": ("marcassin-nature", range(3)),
    "Badges_sanglier.png": ("sanglier", range(6)),
    "Badges_lynx.png": ("lynx", range(3)),
    "Badges_chevreuil.png": ("chevreuil", range(6)),
    "Badges_beursault.png": ("beursault", range(4)),
}


def _fond(pixel: tuple[int, ...]) -> bool:
    return min(pixel[:3]) > 225


def segments(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Boîtes des insignes, séparés par des colonnes de fond clair."""
    largeur, hauteur = image.size
    px = image.load()
    pleines = [sum(not _fond(px[x, y]) for y in range(hauteur)) > hauteur * 0.08 for x in range(largeur)]
    boites, debut = [], None
    for x, plein in enumerate(pleines + [False]):
        if plein and debut is None:
            debut = x
        elif not plein and debut is not None:
            if x - debut > 15:
                lignes = [y for y in range(hauteur) if any(not _fond(px[c, y]) for c in range(debut, x))]
                boites.append((debut, lignes[0], x, lignes[-1] + 1))
            debut = None
    return boites


def main() -> None:
    source = Path(sys.argv[1])
    SORTIE.mkdir(parents=True, exist_ok=True)
    for fichier, (prefixe, positions) in PLANCHES.items():
        image = Image.open(source / fichier).convert("RGB")
        boites = segments(image)
        positions = list(positions)
        if len(boites) != len(positions):
            sys.exit(f"{fichier} : {len(boites)} insignes trouvés, {len(positions)} attendus")
        for niveau, position in enumerate(positions):
            insigne = image.crop(boites[position])
            largeur = round(insigne.width * HAUTEUR / insigne.height)
            insigne.resize((largeur, HAUTEUR), Image.LANCZOS).save(SORTIE / f"{prefixe}-{niveau}.png", optimize=True)
        print(f"{fichier} : {len(boites)} insignes")


if __name__ == "__main__":
    main()
