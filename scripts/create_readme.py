#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Statistiques sur un corpus de documents XML-TEI.

Usage:
    python scripts/tei_stats.py <dossier>

Produit:
  - le nombre total de textes (fichiers TEI parsés)
  - le nombre total de folios (éléments //tei:pb)
  - le nombre total de mots (dans <tei:text>, header exclu)
  - la répartition par genre: 3e classe du @target de <catRef>
    (ex. "#prose #didactica #juridica" -> genre = "juridica")
  - le nombre de textes qui sont des traductions
    (presence d'un tei:bibl[@type='translation'])
  - un fichier tei_stats.tex avec les tableaux LaTeX
    (totaux, catégorisation formelle, genre,
    traductions / oeuvres originales, liste complète des oeuvres)

Dépendances: aucune (stdlib uniquement). lxml est utilisé si disponible.
"""

import sys
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter, defaultdict

try:
    from lxml import etree as _lxml  # optionnel
    HAVE_LXML = True
except ImportError:
    HAVE_LXML = False

TEI = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI}

# Intro du README: a remplir manuellement.
# Son contenu est place au debut du stats/README.md genere.
INTRO_README = ""


def local(tag: str) -> str:
    """Renvoie le nom local d'un tag éventuellement namespacé.
    Ignore les non-éléments (commentaires, processing instructions)
    dont le tag n'est pas une chaîne (cas lxml)."""
    return tag.rsplit("}", 1)[-1]


def is_element(el) -> bool:
    """True si l'objet est un véritable élément (et non un
    commentaire ou une processing instruction)."""
    return isinstance(el.tag, str)


def find_all(root, localname):
    """Trouve tous les éléments //tei:<localname>, quel que soit le parseur."""
    if HAVE_LXML:
        return root.findall(f".//{{{TEI}}}{localname}")
    return [el for el in root.iter()
            if is_element(el) and local(el.tag) == localname]


def find_descendant(root, path):
    """Trouve le premier élément selon un chemin 'tei:a/tei:b' relatif à root."""
    if HAVE_LXML:
        return root.find(path, NS)
    parts = path.split("/")
    cur = [root]
    for part in parts:
        _, name = part.split(":")
        nxt = [e for el in cur for e in el
               if is_element(e) and local(e.tag) == name]
        if not nxt:
            return None
        cur = nxt
    return cur[0]


def get_children(el, localname):
    return [c for c in el if is_element(c) and local(c.tag) == localname]


def parse_file(path: Path):
    """Parse un fichier TEI et retourne un dict de statistiques."""
    stats = {
        "fichier": path.name,
        "titre": "",
        "pb": 0,
        "mots": 0,
        "classe_formelle": "",
        "genre": "",
        "traduction": "",
        "erreur": "",
    }

    try:
        if HAVE_LXML:
            root = _lxml.parse(str(path)).getroot()
        else:
            root = ET.parse(str(path)).getroot()
    except Exception as e:
        stats["erreur"] = f"XML invalide: {e}"
        return stats

    # Titre principal
    title = find_descendant(root, "tei:teiHeader/tei:fileDesc/"
                                  "tei:titleStmt/tei:title")
    if title is not None:
        stats["titre"] = " ".join(title.itertext()).strip()

    # Folios: //tei:pb
    stats["pb"] = len(find_all(root, "pb"))

    # Mots: uniquement dans <tei:text>, header exclu
    if local(root.tag) == "TEI":
        text_el = find_descendant(root, "tei:text")
    else:  # fichier sans racine TEI (rare)
        text_el = None
    text_candidates = [text_el] if text_el is not None else \
        [e for e in get_children(root, "text")]
    if text_candidates:
        texte = " ".join(text_candidates[0].itertext())
        # Decompte de tokens (tous les blancs sont separateurs,
        # la ponctuation compte comme token)
        stats["mots"] = len(texte.split())

    # Categorisation: <catRef target="#a #b #c ..."/>
    catref = find_descendant(root, "tei:teiHeader/tei:profileDesc/"
                                   "tei:textClass/tei:catRef")
    if catref is not None:
        refs = [r.lstrip("#") for r in (catref.get("target") or "").split()]
        if len(refs) >= 1:
            stats["classe_formelle"] = refs[0]  # ex. prose / vers
        if len(refs) >= 3:
            stats["genre"] = refs[2]  # 3e classe = genre
        elif len(refs) == 2:
            stats["genre"] = refs[1]

    # Traduction: presence d'un tei:bibl[@type='translation']
    stats["traduction"] = "oui" if find_all(root, "bibl") and any(
        (b.get("type") or "") == "translation"
        for b in find_all(root, "bibl")) else "non"

    # Manuscrit: msDesc avec ana contenant
    # "frbr.manifestation_singleton", sinon non manuscrit
    stats["manuscrit"] = "non"
    for ms in find_all(root, "msDesc"):
        ana = ms.get("ana") or ""
        if "frbr.manifestation_singleton" in ana:
            stats["manuscrit"] = "oui"
            break

    # Epoque: avant / apres 1500, d'apres les dates du teiHeader
    # (on preferent la date de l'oeuvre (bibl[@type='work']),
    #  sinon l'origDate du msDesc, sinon toute date ISO)
    stats["epoque"] = "(sans date)"
    candidats = []
    for bibl in find_all(root, "bibl"):
        if (bibl.get("type") or "") == "work":
            for d in [e for e in bibl
                      if is_element(e) and local(e.tag) == "date"]:
                candidats.append(("work", d))
    for d in find_all(root, "origDate"):
        candidats.append(("origDate", d))
    if not candidats:
        for d in find_all(root, "date"):
            candidats.append(("date", d))

    def borne(d, nom):
        v = d.get(nom) or d.get(nom.replace("-iso", ""))
        try:
            return int(v) if v else None
        except ValueError:
            return None

    for _, d in candidats:
        avant = borne(d, "notBefore-iso")
        apres = borne(d, "notAfter-iso")
        if avant is None and apres is None:
            continue
        # Regle: apres 1500 si la periode depasse 1500
        # (terminus apres 1500), sinon avant
        if apres is not None:
            stats["epoque"] = "après 1500" if apres > 1500 else "avant 1500"
        else:
            stats["epoque"] = ("après 1500" if avant > 1500
                               else "avant 1500")
        break

    return stats


def par_volume(items):
    """Trie les categories par volume croissant
    (tokens, puis folios, puis textes, puis nom)."""
    return sorted(items, key=lambda kv: (kv[1][2], kv[1][1],
                                         kv[1][0], kv[0]))


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python tei_stats.py <dossier>")

    dossier = Path(sys.argv[1])
    if not dossier.is_dir():
        sys.exit(f"Erreur: «{dossier}» n'est pas un dossier.")

    # Fichiers: soit a plat (dossier/*.xml), soit dans des sous-dossiers
    # par identifiant (dossier/ID/ID.xml), par ex. data/ID/ID.xml
    motifs = ["*.xml", "*.tei", "*/*.xml", "*/*.tei"]
    fichiers = sorted(
        f for m in motifs for f in dossier.glob(m)
        if f.name != "metadata.xml"
    )
    if not fichiers:
        sys.exit(f"Aucun fichier .xml / .tei trouvé dans {dossier}.")

    resultats = [parse_file(f) for f in fichiers]

    # Totaux
    nb_textes = len(resultats)
    total_pb = sum(r["pb"] for r in resultats)
    total_mots = sum(r["mots"] for r in resultats)

    # Volumes par catégorisation formelle et par genre
    # Une classe regroupee comptant plus de 4 textes garde son nom,
    # sinon elle est fusionnee dans "autre".
    SEUIL = 4

    def reagroupe(table):
        nouveau = defaultdict(lambda: [0, 0, 0])
        for k, (t, p, m) in table.items():
            cle = k if t > SEUIL else "autre"
            nouveau[cle][0] += t
            nouveau[cle][1] += p
            nouveau[cle][2] += m
        return nouveau

    vol_formelle_brut = defaultdict(lambda: [0, 0, 0])  # [textes, pb, mots]
    vol_genre_brut = defaultdict(lambda: [0, 0, 0])
    for r in resultats:
        if r["erreur"]:
            continue
        for key, table in ((r["classe_formelle"], vol_formelle_brut),
                           (r["genre"], vol_genre_brut)):
            if key:
                table[key][0] += 1
                table[key][1] += r["pb"]
                table[key][2] += r["mots"]

    vol_formelle = reagroupe(vol_formelle_brut)
    vol_genre = reagroupe(vol_genre_brut)

    # Volumes traductions / oeuvres originales
    vol_trad = defaultdict(lambda: [0, 0, 0])
    for r in resultats:
        if r["erreur"]:
            continue
        cle = ("Traductions" if r["traduction"] == "oui"
               else "Oeuvres originales")
        vol_trad[cle][0] += 1
        vol_trad[cle][1] += r["pb"]
        vol_trad[cle][2] += r["mots"]

    # Volumes manuscrits / non manuscrits
    vol_msc = defaultdict(lambda: [0, 0, 0])
    for r in resultats:
        if r["erreur"]:
            continue
        cle = ("Manuscrits" if r["manuscrit"] == "oui"
               else "Non manuscrits")
        vol_msc[cle][0] += 1
        vol_msc[cle][1] += r["pb"]
        vol_msc[cle][2] += r["mots"]

    # Rapport console
    print("=" * 60)
    print("STATISTIQUES DU CORPUS")
    print("=" * 60)
    print(f"Dossier                    : {dossier}")
    print(f"Nombre total de textes     : {nb_textes}")
    print(f"Nombre total de folios (pb): {total_pb}")
    print(f"Nombre total de tokens     : {total_mots}")
    nb_trad = sum(1 for r in resultats if r["traduction"] == "oui")
    print(f"Textes traductions        : {nb_trad}")
    print()

    print("Par catégorisation formelle (1re classe du catRef):")
    for k, (t, p, m) in par_volume(vol_formelle.items()):
        print(f"  {k:<20} textes: {t:>4}   folios: {p:>6}   mots: {m:>9}")
    print()

    print("Par genre (3e classe du catRef):")
    for k, (t, p, m) in par_volume(vol_genre.items()):
        print(f"  {k:<20} textes: {t:>4}   folios: {p:>6}   mots: {m:>9}")
    print()

    print("Traductions / oeuvres originales:")
    for k, (t, p, m) in par_volume(vol_trad.items()):
        print(f"  {k:<20} textes: {t:>4}   folios: {p:>6}   mots: {m:>9}")
    print()

    print("Manuscrits / non manuscrits:")
    for k, (t, p, m) in par_volume(vol_msc.items()):
        print(f"  {k:<20} textes: {t:>4}   folios: {p:>6}   mots: {m:>9}")
    print()

    erreurs = [r for r in resultats if r["erreur"]]
    if erreurs:
        print(f"Attention, {len(erreurs)} fichier(s) en erreur:")
        for r in erreurs:
            print(f"  {r['fichier']}: {r['erreur']}")
        print()

    # Liste complète des oeuvres (stdout)
    print("Liste des oeuvres:")
    for i, r in enumerate(sorted(resultats,
                                 key=lambda x: (x["titre"] or
                                                 x["fichier"]).lower()), 1):
        titre = r["titre"] or r["fichier"]
        mention = " (traduction)" if r["traduction"] == "oui" else ""
        print(f"  {i:>3}. {titre}{mention}  [{r['fichier']}]")
    print()

    # Export LaTeX
    # Dossier de sortie "stats" (créé s'il n'existe pas)
    stats_dir = Path("stats")
    stats_dir.mkdir(exist_ok=True)

    tex_out = stats_dir / "tei_stats.tex"
    write_latex(tex_out, dossier, nb_textes, total_pb, total_mots,
                vol_formelle, vol_genre, nb_trad, vol_trad, vol_msc,
                resultats)
    print(f"Tableaux LaTeX écrits dans: {tex_out}")

    # Export Markdown
    md_out = stats_dir / "tei_stats.md"
    readme_out = Path("README.md")
    write_markdown(md_out, dossier, nb_textes, total_pb, total_mots,
                   nb_trad, vol_trad, vol_msc, vol_formelle, vol_genre,
                   vol_formelle_brut, vol_genre_brut, resultats)
    # README a la racine: intro (via INTRO_README) + les stats
    contenu_md = md_out.read_text(encoding="utf-8")
    intro = INTRO_README.strip()
    readme_out.write_text(
        (intro + "\n\n" if intro else "") + contenu_md,
        encoding="utf-8")
    print(f"README écrit dans: {readme_out}")


LATEX_SPECIAL = {"\\": r"\textbackslash{}",
                 "&": r"\&", "%": r"\%", "$": r"\$",
                 "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                 "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def latex_escape(s):
    """Échappe les caractères spéciaux LaTeX."""
    return "".join(LATEX_SPECIAL.get(c, c) for c in str(s))


def write_latex(path, dossier, nb_textes, total_pb, total_mots,
                vol_formelle, vol_genre, nb_traductions, vol_trad,
                vol_msc, resultats):
    """Écrit les tableaux LaTeX (totaux et volumes par catégorie)."""
    lignes = []
    lignes.append("% Généré automatiquement par tei_stats.py")
    lignes.append("\\begin{table}[h]")
    lignes.append("  \\centering")
    lignes.append("  \\caption{Totaux du corpus}")
    lignes.append("  \\begin{tabular}{lr}")
    lignes.append("    \\hline")
    lignes.append("    Textes & %d \\\\" % nb_textes)
    lignes.append("    Traductions & %d \\\\" % nb_traductions)
    lignes.append("    Folios (\\texttt{pb}) & %d \\\\" % total_pb)
    lignes.append("    Mots & %s \\\\" % f"{total_mots:,}".replace(",", "\\,"))
    lignes.append("    \\hline")
    lignes.append("  \\end{tabular}")
    lignes.append("\\end{table}")
    lignes.append("")

    for titre_tab, table in (("Manuscrits / non manuscrits",
                              vol_msc),
                             ("Traductions / oeuvres originales",
                              vol_trad),
                             ("Catégorisation formelle "
                              "(1\\up{re} classe du \\texttt{catRef})",
                              vol_formelle),
                             ("Genre (3\\up{e} classe du \\texttt{catRef})",
                              vol_genre)):
        if not table:
            continue
        lignes.append("\\begin{table}[h]")
        lignes.append("  \\centering")
        lignes.append("  \\caption{%s}" % titre_tab)
        lignes.append("  \\begin{tabular}{lrrr}")
        lignes.append("    \\hline")
        lignes.append("    Catégorie & Textes & Folios & Mots \\\\")
        lignes.append("    \\hline")
        for k, (t, p, m) in par_volume(table.items()):
            lignes.append("    %s & %d & %d & %s \\\\"
                          % (latex_escape(k), t, p,
                             f"{m:,}".replace(",", "\\,")))
        lignes.append("    \\hline")
        lignes.append("  \\end{tabular}")
        lignes.append("\\end{table}")
        lignes.append("")

    path.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def table_md(lignes, table):
    """Écrit un tableau Markdown d'une table de volumes."""
    if not table:
        return
    lignes.append("| Catégorie | Textes | Folios | Tokens |")
    lignes.append("|---|---:|---:|---:|")
    t_tot = p_tot = m_tot = 0
    for k, (t, p, m) in par_volume(table.items()):
        lignes.append(f"| {k} | {t} | {p} | {m:,} |".replace(",", "\u202f"))
        t_tot += t
        p_tot += p
        m_tot += m
    lignes.append(f"| **Total** | **{t_tot}** | **{p_tot}** "
                  f"| **{m_tot:,}** |".replace(",", "\u202f"))
    lignes.append("")


def write_markdown(path, dossier, nb_textes, total_pb, total_mots,
                   nb_trad, vol_trad, vol_msc, vol_formelle, vol_genre,
                   vol_formelle_brut, vol_genre_brut, resultats):
    """Écrit le rapport Markdown détaillé par catégorie."""
    lignes = []
    lignes.append("# Statistiques du corpus")
    lignes.append("")
    lignes.append(f"Dossier analysé : `{dossier}`")
    lignes.append("")

    # Totaux
    lignes.append("## Totaux")
    lignes.append("")
    lignes.append("| Indicateur | Valeur |")
    lignes.append("|---|---:|")
    lignes.append(f"| Textes | {nb_textes} |")
    lignes.append(f"| Traductions | {nb_trad} |")
    lignes.append(f"| Oeuvres originales | {nb_textes - nb_trad} |")
    lignes.append(f"| Folios (`pb`) | {total_pb} |")
    lignes.append(f"| Tokens | {total_mots:,} |".replace(",", "\u202f"))
    lignes.append("")

    # Traductions / originales (detail)
    lignes.append("## Traductions / oeuvres originales")
    lignes.append("")
    table_md(lignes, vol_trad)

    # Manuscrits / non manuscrits
    lignes.append("## Manuscrits / non manuscrits")
    lignes.append("")
    table_md(lignes, vol_msc)

    # Categorisation formelle
    lignes.append("## Catégorisation formelle "
                  "(1re classe du `catRef`)")
    lignes.append("")
    lignes.append("### Vue regroupée (classes de plus de 4 textes, "
                  "sinon « autre »)")
    lignes.append("")
    table_md(lignes, vol_formelle)
    lignes.append("### Détail par classe (toutes classes, sans fusion)")
    lignes.append("")
    table_md(lignes, vol_formelle_brut)

    # Genre
    lignes.append("## Genre (3e classe du `catRef`)")
    lignes.append("")
    lignes.append("### Vue regroupée (classes de plus de 4 textes, "
                  "sinon « autre »)")
    lignes.append("")
    table_md(lignes, vol_genre)
    lignes.append("### Détail par classe (toutes classes, sans fusion)")
    lignes.append("")
    table_md(lignes, vol_genre_brut)

    # Classification des oeuvres: listes de titres par analyse
    def titres_par(analyse):
        """Renvoie un dict categorie -> titres tries pour une analyse."""
        groupes = defaultdict(list)
        for r in resultats:
            if r["erreur"]:
                continue
            if analyse == "formelle":
                cle = r["classe_formelle"] or "(sans classe formelle)"
            elif analyse == "genre":
                cle = r["genre"] or "(sans genre)"
            elif analyse == "epoque":
                cle = r["epoque"] or "(sans date)"
            else:  # traduction
                cle = ("Traductions" if r["traduction"] == "oui"
                       else "Oeuvres originales")
            titre = r["titre"] or r["fichier"]
            mention = " *(traduction)*" if r["traduction"] == "oui" else ""
            groupes[cle].append(titre + mention)
        for cle in groupes:
            groupes[cle] = sorted(groupes[cle], key=str.lower)
        return groupes

    for titre_section, analyse in (
            ("Classification des oeuvres par catégorisation formelle",
             "formelle"),
            ("Classification des oeuvres par genre", "genre"),
            ("Classification des oeuvres: traductions et originales",
             "traduction"),
            ("Classification des oeuvres: avant / après 1500",
             "epoque")):
        lignes.append(f"## {titre_section}")
        lignes.append("")
        groupes = titres_par(analyse)
        if not any(groupes.values()):
            lignes.append("Aucun texte catégorisé pour cette analyse.")
            lignes.append("")
            continue
        for cle in sorted(groupes, key=lambda c: c.lower()):
            lignes.append(f"### {cle} ({len(groupes[cle])})")
            lignes.append("")
            for titre in groupes[cle]:
                lignes.append(f"- { titre}")
            lignes.append("")

    path.write_text("\n".join(lignes) + "\n", encoding="utf-8")




if __name__ == "__main__":
    main()
