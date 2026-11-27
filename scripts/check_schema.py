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
             avertissements non bloquants, les autres échouent.
             Moteur: pipeline ISO XSLT 2.0 via Saxon-HE, comme
             oXygen. Jar: SAXON_JAR ou saxon.jar dans le dossier
             du script (scripts/). Squelette ISO dans
             schematron-xslt2/ à côté du script (ou
             --squelette DOSSIER). Si le .rng est donné, les règles
             embarquées sont extraites par Saxon (XSLT), sans lxml ;
             - --squelette DOSSIER : dossier des feuilles ISO XSLT 2.0 ;
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
SCRIPT_DIR = Path(__file__).resolve().parent


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


# Extraction des regles Schematron embarquees d'un .rng genere par
# l'ODD: copie tous les <pattern> du namespace Schematron dans un
# <sch:schema> autonome. Executee par Saxon (aucun lxml requis).
XSLT_EXTRACTION = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="2.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:sch="http://purl.oclc.org/dsdl/schematron">
  <xsl:output indent="yes"/>
  <xsl:template match="/">
    <sch:schema>
      <sch:ns prefix="tei"
              uri="http://www.tei-c.org/ns/1.0"/>
      <xsl:for-each select="//sch:pattern">
        <xsl:copy-of select="." copy-namespaces="yes"/>
      </xsl:for-each>
    </sch:schema>
  </xsl:template>
</xsl:stylesheet>
"""


class Schematron:
    """Exécute les règles Schematron, entièrement via le pipeline
    ISO XSLT et Saxon-HE (XPath 2.0, comme oXygen).

    En entrée: un .rng généré par l'ODD (les règles embarquées sont
    extraites par XSLT) ou un .sch autonome.

    Configuration de Saxon: saxon_jar (par défaut SAXON_JAR, ou
    saxon.jar dans le dossier du script), squelette (dossier des
    feuilles iso_dsdl_include.xsl, iso_abstract_expand.xsl et
    iso_svrl_for_xslt2.xsl, par défaut schematron-xslt2/).
    """

    ETAPES_XSLT2 = ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl",
                    "iso_svrl_for_xslt2.xsl")

    def __init__(self, fichier_règles: Path, squelette: Path = None,
                 saxon_jar: Path = None):
        import os
        import shutil
        import tempfile
        import xml.etree.ElementTree as ET
        self.validateur_xsl = None
        self.saxon_jar = None
        self._tmpdir = Path(tempfile.mkdtemp(prefix="schematron_"))

        if saxon_jar is None:
            env = os.environ.get("SAXON_JAR")
            saxon_jar = Path(env) if env else SCRIPT_DIR / "saxon.jar"
        if squelette is None:
            squelette = SCRIPT_DIR / "schematron-xslt2"
        if shutil.which("java") is None or not Path(saxon_jar).is_file():
            raise RuntimeError(
                "Saxon-HE est requis pour le Schematron.\n"
                "  - java doit être disponible\n"
                "  - jar Saxon: SAXON_JAR=/chemin/vers/saxon.jar, "
                "ou saxon.jar dans le dossier du script "
                f"({SCRIPT_DIR})\n"
                "  - squelette ISO XSLT 2.0 dans schematron-xslt2/ "
                "(dossier du script), ou --squelette DOSSIER")
        for feuille in self.ETAPES_XSLT2:
            if not (Path(squelette) / feuille).is_file():
                raise RuntimeError(
                    f"feuille XSLT absente: "
                    f"{Path(squelette) / feuille}")
        self.saxon_jar = Path(saxon_jar)

        # .sch autonome ou .rng (extraction necessaire)?
        racine = ET.parse(str(fichier_règles)).getroot()
        est_sch = racine.tag == "{%s}schema" % SCH_NS
        self.validateur_xsl = self._compiler_saxon(
            Path(fichier_règles), Path(squelette), est_sch)

    def _saxon(self, source, feuille_xsl, sortie):
        """Une transformation Saxon; raise en cas d'erreur."""
        import subprocess
        r = subprocess.run(
            ["java", "-cp", str(self.saxon_jar),
             "net.sf.saxon.Transform",
             f"-s:{source}", f"-xsl:{feuille_xsl}",
             f"-o:{sortie}"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"erreur Saxon ({feuille_xsl}): "
                f"{(r.stderr or '')[:400]}")
        return sortie

    def _compiler_saxon(self, source, squelette, est_sch):
        """Pipeline ISO: extraction (si .rng) -> includes ->
        abstract -> SVRL, jusqu'a la feuille de validation."""
        if not est_sch:
            extraction = self._tmpdir / "extraction.xsl"
            extraction.write_text(XSLT_EXTRACTION, encoding="utf-8")
            source = self._saxon(source, extraction,
                                self._tmpdir / "extrait.sch")
        for feuille in self.ETAPES_XSLT2:
            source = self._saxon(source, squelette / feuille,
                                self._tmpdir / (feuille + ".out"))
        return source

    def echecs(self, fichier):
        """Renvoie [(message, role)] des asserts échoués."""
        import os
        import tempfile
        import xml.etree.ElementTree as ET
        fd, sortie = tempfile.mkstemp(suffix=".svrl.xml")
        os.close(fd)
        try:
            self._saxon(fichier, self.validateur_xsl, sortie)
        except RuntimeError as err:
            os.unlink(sortie)
            return [(str(err), None)]
        out = []
        try:
            racine = ET.parse(sortie).getroot()
            for fa in racine.iter("{%s}failed-assert" % SVRL_NS):
                message = " ".join(
                    " ".join(fa.itertext()).split())
                out.append((message, fa.get("role")))
        finally:
            os.unlink(sortie)
        return out


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
            for message, role in validateur_sch.echecs(f):
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
    # défaut le même schéma), ou un .sch autonome.
    # --squelette DOSSIER: feuilles ISO XSLT 2.0 (mode Saxon)
    validateur_sch = None
    squelette_dir = None
    if "--squelette" in sys.argv:
        i = sys.argv.index("--squelette")
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            squelette_dir = Path(sys.argv[i + 1])
            args = [a for a in args if a != str(squelette_dir)]
        else:
            sys.exit("Erreur: --squelette exige un dossier.")
    if "--schematron" in sys.argv:
        i = sys.argv.index("--schematron")
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--") \
                and sys.argv[i + 1] != str(squelette_dir):
            sch_path = Path(sys.argv[i + 1])
            args = [a for a in args if a != str(sch_path)]
        else:
            sch_path = schema_path
        if not sch_path.is_file():
            sys.exit(f"Erreur: le fichier de règles "
                     f"«{sch_path}» est introuvable.")
        try:
            validateur_sch = Schematron(sch_path,
                                        squelette=squelette_dir)
        except RuntimeError as err:
            sys.exit(f"Erreur Schematron: {err}")
        print("Schematron: moteur Saxon (ISO XSLT 2.0)")

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
            for message, role in validateur_sch.echecs(fichier):
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