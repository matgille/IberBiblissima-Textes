#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Valide chaque document du corpus contre le schéma RelaxNG du projet
(schemata/out/ODD_gold.rng par défaut).

Usage:
    python scripts/check_schema.py [dossier] [schéma] [--skip-metadata]

- dossier : par défaut «data» ;
- schéma   : par défaut «schemata/out/ODD_gold.rng» ;
- --skip-metadata : ignore les fichiers metadata.xml (validés sinon,
  comme les autres).

Par défaut, TOUS les .xml du dossier sont validés, metadata.xml
compris: ce sont aussi des documents TEI soumis au même ODD.

Le schéma est chargé une seule fois, puis chaque document est
validé. En cas d'échec, le message d'erreur de libxml2 (position
comprise) est affiché. Code de retour: 0 si tout est valide,
1 sinon, 2 si le schéma ou le dossier est introuvable/invalide.

Dépendance: lxml (pip install lxml)
"""

import sys
from pathlib import Path

from lxml import etree

DOSSIER_DEFAUT = Path("data")
SCHEMA_DEFAUT = Path("schemata/out/ODD_gold.rng")


def iterer_fichiers(dossier: Path, skip_metadata: bool):
    """Fichiers XML du corpus (un niveau de sous-dossier ou a plat)."""
    motifs = ["*/*.xml", "*.xml"]
    vus = set()
    for motif in motifs:
        for f in sorted(dossier.glob(motif)):
            if not f.is_file():
                continue
            if skip_metadata and f.name == "metadata.xml":
                continue
            cle = (f.parent.name, f.name)
            if cle in vus:
                continue
            vus.add(cle)
            yield f


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_metadata = "--skip-metadata" in sys.argv
    dossier = Path(args[0]) if args else DOSSIER_DEFAUT
    schema_path = Path(args[1]) if len(args) > 1 else SCHEMA_DEFAUT

    if not dossier.is_dir():
        sys.exit(f"Erreur: le dossier «{dossier}» n'existe pas.")
    if not schema_path.is_file():
        sys.exit(f"Erreur: le schéma «{schema_path}» est introuvable.")

    # Chargement du schéma RelaxNG (une seule fois)
    try:
        relaxng = etree.RelaxNG(etree.parse(str(schema_path)))
    except etree.RelaxNGError as err:
        sys.exit(f"Erreur: schéma RelaxNG invalide ({schema_path}): "
                 f"{err}")
    except etree.XMLSyntaxError as err:
        sys.exit(f"Erreur: le schéma n'est pas un XML valide "
                 f"({schema_path}): {err}")

    total = 0
    valides = 0
    invalides = 0
    erreurs = 0

    for fichier in iterer_fichiers(dossier, skip_metadata):
        total += 1
        try:
            doc = etree.parse(str(fichier))
        except etree.XMLSyntaxError as err:
            invalides += 1
            print(f"✗ {fichier}: XML invalide ({err})")
            continue

        if relaxng.validate(doc):
            valides += 1
            continue

        invalides += 1
        print(f"✗ {fichier}:")
        for entree in relaxng.error_log:
            print(f"    ligne {entree.line}: {entree.message}")

    print()
    print(f"Documents validés : {total}")
    print(f"Valides           : {valides}")
    print(f"Invalides         : {invalides}")
    if invalides:
        sys.exit(1)


if __name__ == "__main__":
    main()