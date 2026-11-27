#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Valide chaque document du corpus contre le schéma RelaxNG du projet
(schemata/out/ODD_gold.rng par défaut).

Usage:
    python scripts/check_schema.py [dossier] [schéma] [options]

- dossier : par défaut «data» ;
- schéma   : par défaut «schemata/out/ODD_gold.rng» ;
- --jing   : valide avec Jing (même moteur RelaxNG qu'oXygen;
             recommandé si l'ODD contient des patterns XSD exotiques
             comme \\p{...} que libxml2 ne supporte pas) ;
- --schematron [CHEMIN] : exécute en plus les règles Schematron
             embarquées dans le .rng (par défaut le même schéma),
             ou un .sch autonome si un chemin est fourni. Jing
             n'exécute pas le Schematron embarqué: cette option le
             complète. Les asserts role="warn" sont des
             avertissements non bloquants, les autres échouent ;
- --xml-model : chaque document est validé contre le schéma déclaré
             dans son propre PI <?xml-model?> (comportement oXygen);
- --skip-metadata : ignore les fichiers metadata.xml (validés sinon,
             comme les autres).

Par défaut, TOUS les .xml du dossier sont validés, metadata.xml
compris, avec le validateur RelaxNG de libxml2 (lxml).

Codes de retour: 0 si tout est valide, 1 sinon, 2 si le schéma ou
le dossier est introuvable/invalide.
"""

import sys
import re
from pathlib import Path

try:
    from lxml import etree
except ImportError:  # mode --jing uniquement
    etree = None

DOSSIER_DEFAUT = Path("data")
SCHEMA_DEFAUT = Path("schemata/out/ODD_gold.rng")


def schema_de_xml_model(arbre, fichier: Path):
    """Renvoie le chemin du schéma RelaxNG déclaré par le PI
    <?xml-model ... schematypens="http://relaxng.org/ns/structure/1.0"?>,
    résolu relativement au document, ou None."""
    racine = arbre.getroot()
    for pi in arbre.xpath("/processing-instruction('xml-model')"):
        texte = pi.text or ""
        if "relaxng.org/ns/structure/1.0" not in texte:
            continue
        for morceau in texte.split():
            if morceau.startswith("href="):
                href = morceau[len("href="):].strip("'\"")
                return (fichier.parent / href).resolve()
    return None


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


TEI_NS = "http://www.tei-c.org/ns/1.0"
SCH_NS = "http://purl.oclc.org/dsdl/schematron"
SVRL_NS = "http://purl.oclc.org/dsdl/svrl"


def extraire_schematron(schéma_arbre):
    """Extrait les patterns Schematron embarqués dans un .rng généré
    par l'ODD et renvoie un <sch:schema> autonome, ou None. Accepte
    aussi un .sch déjà autonome."""
    import copy
    racine = schéma_arbre.getroot()
    if isinstance(racine.tag, str) and racine.tag == "{%s}schema" % SCH_NS:
        return schéma_arbre
    patterns = [el for el in racine.iter()
                if isinstance(el.tag, str)
                and el.tag == "{%s}pattern" % SCH_NS]
    if not patterns:
        return None
    schema = etree.Element("{%s}schema" % SCH_NS,
                           nsmap={"sch": SCH_NS})
    prefixes_vus = set()
    for p in patterns:
        for el in p.iter():
            for attr in ("context", "test"):
                v = el.get(attr)
                if not v:
                    continue
                for m in re.finditer(r"\b([A-Za-z_][\w\-]*):", v):
                    prefixes_vus.add(m.group(1))
    uris = {"tei": TEI_NS}
    for pre in sorted(prefixes_vus):
        if pre in uris:
            el = etree.SubElement(schema, "{%s}ns" % SCH_NS)
            el.set("prefix", pre)
            el.set("uri", uris[pre])
    for p in patterns:
        schema.append(copy.deepcopy(p))
    return etree.ElementTree(schema)


def compiler_schematron(schéma_arbre):
    """Compile le schéma Schematron. Renvoie (validateur, erreur)."""
    from lxml import isoschematron
    try:
        return isoschematron.Schematron(schéma_arbre), None
    except Exception as err:
        return None, str(err)


def vérifier_schematron(doc, validateur):
    """Renvoie la liste des asserts échoués: (message, role)."""
    echecs = []
    try:
        validateur.validate(doc)
    except Exception as err:
        return [(f"erreur d'exécution Schematron: {err}", None)]
    rapport = validateur.validation_report
    if rapport is None:
        return echecs
    for fa in rapport.iter("{%s}failed-assert" % SVRL_NS):
        message = " ".join(" ".join(fa.itertext()).split())
        echecs.append((message, fa.get("role")))
    return echecs


def valider_avec_jing(fichiers, schéma, cwd):
    """Valide les fichiers avec Jing (même moteur RelaxNG qu'oXygen).

    Renvoie (par_fichier, code_retour) où par_fichier associe à chaque
    fichier la liste de ses messages d'erreur Jing. Jing gère les
    patterns XSD que libxml2 ne supporte pas (ex. \\p{...}).
    """
    import subprocess
    cmd = ["jing", str(schéma)] + [str(f) for f in fichiers]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(cwd))
    sortie = (proc.stderr or "") + (proc.stdout or "")
    par_fichier = {f.name: [] for f in fichiers}
    for ligne in sortie.splitlines():
        m = re.match(r"(.+?):(\d+):\d+:\s*(fatal\s+)?error:\s*(.*)",
                     ligne)
        if not m:
            continue
        chemin, num, message = m.group(1), m.group(2), m.group(4)
        nom = Path(chemin).name
        if nom in par_fichier:
            par_fichier[nom].append(
                f"ligne {num}: {message}")
    return par_fichier, proc.returncode


def main_jing(dossier: Path, schema_path: Path, skip_metadata: bool,
             validateur_sch=None):
    """Validation via Jing (parité exacte avec oXygen) + Schematron
    en complément (Jing n'exécute pas le Schematron embarqué)."""
    fichiers = list(iterer_fichiers(dossier, skip_metadata))
    if not fichiers:
        sys.exit(f"Aucun XML trouvé dans «{dossier}».")
    total = len(fichiers)
    par_fichier, code = valider_avec_jing(fichiers, schema_path, None)
    if code == 2 or (code not in (0, 1)):
        # Jing: 0 = valide, 1 = documents invalides, 2 = erreur
        sys.exit("Erreur: Jing n'a pas pu s'exécuter ou le schéma "
                 "est invalide.")
    invalides = 0
    for f in fichiers:
        erreurs = list(par_fichier.get(f.name, []))
        avert = []
        if validateur_sch is not None:
            try:
                doc = etree.parse(str(f))
            except Exception as err:
                erreurs.append(f"XML illisible pour Schematron ({err})")
            else:
                for message, role in vérifier_schematron(doc,
                                                        validateur_sch):
                    if role == "warn":
                        avert.append(message)
                    else:
                        erreurs.append(f"[Schematron] {message}")
        if erreurs:
            invalides += 1
            print(f"✗ {f}:")
            for e in erreurs:
                print(f"    {e}")
        else:
            for a in avert:
                print(f"⚠ {f}: [Schemotron/warn] {a}")
            print(f"✓ {f}" + (" (avertissements)" if avert else ""))
    print()
    print(f"Documents validés : {total}")
    print(f"Valides           : {total - invalides}")
    print(f"Invalides         : {invalides}")
    if invalides:
        sys.exit(1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_metadata = "--skip-metadata" in sys.argv
    use_xml_model = "--xml-model" in sys.argv
    use_jing = "--jing" in sys.argv
    dossier = Path(args[0]) if args else DOSSIER_DEFAUT
    schema_path = Path(args[1]) if len(args) > 1 else SCHEMA_DEFAUT

    # --schematron [CHEMIN]: règles embarquées dans le .rng (par
    # défaut le même schéma), ou un .sch autonome
    validateur_sch = None
    if "--schematron" in sys.argv:
        i = sys.argv.index("--schematron")
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            sch_path = Path(sys.argv[i + 1])
            args = [a for a in args if a != str(sch_path)]
        else:
            sch_path = schema_path
        if etree is None:
            print("Attention: --schematron requiert lxml; "
                  "validation Schematron ignorée.")
        else:
            if not sch_path.is_file():
                sys.exit(f"Erreur: le fichier de règles "
                         f"«{sch_path}» est introuvable.")
            try:
                arbre_règles = etree.parse(str(sch_path))
            except etree.XMLSyntaxError as err:
                sys.exit(f"Erreur: {sch_path} n'est pas un XML "
                         f"valide ({err}).")
            règles = extraire_schematron(arbre_règles)
            if règles is None:
                sys.exit(f"Erreur: aucune règle Schematron trouvée "
                         f"dans «{sch_path}».")
            validateur_sch, err = compiler_schematron(règles)
            if validateur_sch is None:
                sys.exit(f"Erreur: Schematron invalide "
                         f"({sch_path}): {err}")

    if not dossier.is_dir():
        sys.exit(f"Erreur: le dossier «{dossier}» n'existe pas.")

    if use_jing:
        import shutil
        if shutil.which("jing") is None:
            sys.exit("Erreur: «jing» n'est pas installé "
                     "(paquet Debian/Ubuntu «jing», ou depuis "
                     "https://github.com/relaxng/jing-trang).")
        if not schema_path.is_file():
            sys.exit(f"Erreur: le schéma «{schema_path}» est "
                     f"introuvable.")
        return main_jing(dossier, schema_path, skip_metadata,
                        validateur_sch)

    if etree is None:
        sys.exit("Erreur: lxml est requis hors du mode --jing "
                 "(pip install lxml), ou utilisez --jing.")

    # Cache des schémas RelaxNG (chemin -> validateur)
    schemas = {}

    def charger(chemin: Path):
        """Charge et met en cache le validateur pour un schéma.
        Renvoie None si le schéma est introuvable/invalide."""
        cle = str(chemin)
        if cle in schemas:
            return schemas[cle]
        if not chemin.is_file():
            schemas[cle] = None
            return None
        try:
            schemas[cle] = etree.RelaxNG(etree.parse(str(chemin)))
        except (etree.RelaxNGError, etree.XMLSyntaxError) as err:
            print(f"Erreur: schéma RelaxNG invalide ({chemin}): {err}")
            schemas[cle] = None
        return schemas[cle]

    if not use_xml_model:
        if not schema_path.is_file():
            sys.exit(f"Erreur: le schéma «{schema_path}» est introuvable."
                     "\nAstuce: --xml-model valide chaque document contre"
                     " le schéma declare dans son propre PI xml-model"
                     " (comportement oXygen).")
        if charger(schema_path) is None:
            sys.exit("Erreur: impossible de charger le schéma.")

    total = 0
    valides = 0
    invalides = 0
    sans_schema = 0

    for fichier in iterer_fichiers(dossier, skip_metadata):
        total += 1
        try:
            doc = etree.parse(str(fichier))
        except etree.XMLSyntaxError as err:
            invalides += 1
            print(f"✗ {fichier}: XML invalide ({err})")
            continue

        # Schéma: celui du PI xml-model du document, ou le schéma passe
        # en argument
        if use_xml_model:
            chemin_schema = schema_de_xml_model(doc, fichier)
            if chemin_schema is None:
                sans_schema += 1
                print(f"– {fichier}: aucune association xml-model "
                      f"(RelaxNG) déclarée [ignoré]")
                continue
            relaxng = charger(chemin_schema)
            if relaxng is None:
                invalides += 1
                print(f"✗ {fichier}: schéma déclaré introuvable "
                      f"({chemin_schema})")
                continue
        else:
            relaxng = schemas[str(schema_path)]

        ok_rng = relaxng.validate(doc)
        avert, err_sch = [], []
        if validateur_sch is not None:
            for message, role in vérifier_schematron(doc, validateur_sch):
                if role == "warn":
                    avert.append(message)
                else:
                    err_sch.append(message)

        if ok_rng and not err_sch:
            valides += 1
            for a in avert:
                print(f"⚠ {fichier}: [Schematron/warn] {a}")
            print(f"✓ {fichier}"
                  + (" (avertissements)" if avert else ""))
            continue

        invalides += 1
        print(f"✗ {fichier}:")
        if not ok_rng:
            for entree in relaxng.error_log:
                print(f"    ligne {entree.line}: {entree.message}")
        for m in err_sch:
            print(f"    [Schematron] {m}")

    print()
    print(f"Documents validés : {total}")
    print(f"Valides           : {valides}")
    print(f"Invalides         : {invalides}")
    if sans_schema:
        print(f"Sans xml-model    : {sans_schema}")
    if invalides:
        sys.exit(1)


if __name__ == "__main__":
    main()