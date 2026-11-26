#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Vérifie les citeStructure de chaque document XML-TEI du corpus.

Pour chaque texte dans data/ (data/ID/ID.xml, metadata.xml ignoré),
le script:
  1. relit les <citeStructure> déclarés dans le teiHeader
     (n'importe où dans le <teiHeader>);
  2. évalue le XPath de l'attribut @match de chaque unité
     (page, colonne, ligne...) dans le tei:body, en respectant
     l'imbrication (le match d'une unité fille est évalué dans le
     contexte des nœuds de l'unité mère);
  3. signale les unités dont le match ne trouve aucun nœud.

Usage:
    python scripts/check_citestructure.py [dossier]

Sans argument, le dossier est «data». Codes de retour: 0 si tout est
bon (ou aucune déclaration), 1 si au moins une unité ne matche rien.

Dépendance: lxml (pip install lxml)
"""

import sys
import re
from pathlib import Path
from collections import Counter

from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"

# Ajoute le prefix TEI aux tests de nom non prefixes d'un XPath:
# "//body//pb" -> "//t:body//t:pb". Les fonctions (text(), ...), les
# attributs (@n) et les axes (::axis) ne sont pas touches.
_RE_NOM = re.compile(r"(?<![\w:.@()'\"])(?=[A-Za-z_])[A-Za-z_]"
                     r"[\w.\-]*(?![\w(:])")
_SENTINELLE = "\x00"
_AXES = ("child", "descendant", "parent", "self", "ancestor",
         "following", "preceding", "attribute", "namespace",
         "following-sibling", "preceding-sibling",
         "descendant-or-self", "ancestor-or-self")


def prefixer_tei(match: str) -> str:
    """Version du XPath avec les noms TEI prefixes par t:."""
    expr = match.replace("::", _SENTINELLE)

    def repl(m):
        nom = m.group(0)
        if nom.lower() in _AXES:
            return nom
        return "t:" + nom

    return _RE_NOM.sub(repl, expr).replace(_SENTINELLE, "::")


def evaluer_match(match, contexte, ns):
    """Évalue le XPath @match depuis un contexte.

    Essaie d'abord le XPath tel quel, puis, si aucun nœud n'est
    trouvé, une variante où les noms TEI sont préfixés (utile quand
    le match est écrit sans préfixe et que le document est
    namespacé).

    Renvoie la liste des éléments trouvés, ou None si le XPath est
    invalide dans les deux formes.
    """
    if not match:
        return []
    essais = [match, prefixer_tei(match)]
    invalide = True
    for expr in essais:
        try:
            resultats = contexte.xpath(expr, namespaces=ns)
        except etree.XPathError:
            continue
        invalide = False
        elements = [r for r in resultats if isinstance(r, etree._Element)]
        if elements:
            return elements
    if invalide:
        return None  # XPath invalide: remonté séparément
    return []


def documents(dossier: Path):
    """Fichiers XML du corpus, metadata.xml ignoré."""
    motifs = ["*/*.xml", "*.xml"]
    vus = set()
    for motif in motifs:
        for f in sorted(dossier.glob(motif)):
            if f.name == "metadata.xml" or not f.is_file():
                continue
            cle = (f.parent.name, f.name)
            if cle in vus:
                continue
            vus.add(cle)
            yield f


def lire_cite_structures(header):
    """Extrait les citeStructure du header sous forme d'arbre.

    Renvoie une liste de dicts: {unit, match, delim, enfants: [...]}.
    """
    structures = []

    def parse(el):
        node = {
            "unit": el.get("unit"),
            "match": el.get("match"),
            "delim": el.get("delim"),
            "enfants": [parse(c) for c in el
                        if isinstance(c.tag, str)
                        and etree.QName(c).localname == "citeStructure"],
        }
        return node

    for el in header.iter():
        if not isinstance(el.tag, str):
            continue
        if etree.QName(el).localname == "citeStructure":
            # ne garder que les racines (pas les unités imbriquées)
            parent = el.getparent()
            if (parent is None or
                    etree.QName(parent).localname != "citeStructure"):
                structures.append(parse(el))
    return structures


def verifier(arbre, structures, ns):
    """Vérifie chaque unité: au moins un nœud dans le tei:body.

    Renvoie (ok, problemes, stats) où problemes est une liste de
    descriptifs et stats un Counter {unité: nb de nœuds}.
    """
    problemes = []
    stats = Counter()

    body = arbre.xpath("//t:body", namespaces=ns)
    if not body:
        return False, ["aucun tei:body trouvé"], stats
    body = body[0]

    def verifier_noeud(node, contexte, chemin):
        unit = node["unit"] or "?"
        match = node["match"] or ""
        # contexte initial: le body (les matchs «//body//pb» partent
        # de la racine du document)
        resultats = evaluer_match(match, contexte, ns)
        if resultats is None:
            problemes.append(
                f"{chemin}: XPath invalide ({match!r})")
            return
        stats[unit] += len(resultats)
        if not resultats:
            problemes.append(
                f"{chemin}: aucun nœud trouvé pour "
                f"unit={unit!r} (match {match!r})")
        for enfant in node["enfants"]:
            for r in resultats:
                verifier_noeud(enfant, r, chemin + "/" + unit)

    for struct in structures:
        verifier_noeud(struct, body, "body")
    return (not problemes), problemes, stats


def main():
    dossier = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    if not dossier.is_dir():
        sys.exit(f"Erreur: le dossier «{dossier}» n'existe pas.")

    ns = {"t": TEI_NS}
    total = 0
    echecs = 0
    sans_declaration = 0

    for fichier in documents(dossier):
        total += 1
        try:
            arbre = etree.parse(str(fichier))
        except etree.XMLSyntaxError as err:
            print(f"✗ {fichier}: XML invalide ({err})")
            echecs += 1
            continue

        header = arbre.xpath("//t:teiHeader", namespaces=ns)
        if not header:
            print(f"✗ {fichier}: aucun teiHeader")
            echecs += 1
            continue

        structures = lire_cite_structures(header[0])
        if not structures:
            print(f"– {fichier}: aucune citeStructure déclarée "
                  f"(ignoré)")
            sans_declaration += 1
            continue

        ok, problemes, stats = verifier(arbre, structures, ns)
        if ok:
            resume = ", ".join(f"{u}={n}" for u, n in sorted(stats.items()))
            print(f"✓ {fichier}: {resume or 'aucun nœud'}")
        else:
            echecs += 1
            print(f"✗ {fichier}:")
            for p in problemes:
                print(f"    - {p}")

    print()
    print(f"Documents analysés : {total}")
    print(f"Sans déclaration   : {sans_declaration}")
    print(f"En échec           : {echecs}")
    if echecs:
        sys.exit(1)


if __name__ == "__main__":
    main()