#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_build_site_data.py

Construit les fichiers JSON consommés par le site statique :
- site/data/exercices.json
- site/data/data.json
- site/data/rapports/build_site_data_report.json
- site/data/rapports/build_site_data_report.csv

Version mathématiques :
- agrège les exercices générés individuellement ;
- normalise les champs nécessaires au frontend ;
- injecte les métadonnées disciplinaires et la navigation par thèmes ;
- conserve la compatibilité avec les anciens champs attendus par index.html ;
- ne dépend pas d'un appel API.

À lancer depuis la racine du projet :
    python scripts/06_build_site_data.py

Options utiles :
    python scripts/06_build_site_data.py --allow-empty-exercises
    python scripts/06_build_site_data.py --source auto
    python scripts/06_build_site_data.py --source individual
    python scripts/06_build_site_data.py --source aggregate
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


# =============================================================================
# Import config avec repli robuste
# =============================================================================


def find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "site").exists() and (parent / "scripts").exists():
            return parent
    return Path.cwd()


ROOT = find_project_root()

# Garantit que `from config import ...` fonctionne quand le script est lancé
# depuis la racine du projet.
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import config as cfg  # type: ignore
except Exception:  # pragma: no cover - repli de secours
    cfg = None  # type: ignore


SITE = ROOT / "site"
DATA = SITE / "data"
GENERATED = DATA / "generated"
REPORTS = DATA / "rapports"

PROGRAMME_PATH = DATA / "programme.json"
PROGRAMME_OFFICIEL_PATH = DATA / "programme_officiel.json"
MANIFEST_JSON_PATH = DATA / "manifest.json"
MANIFEST_CSV_PATH = DATA / "manifest.csv"
COURS_PATH = DATA / "cours.json"
QUIZ_PATH = DATA / "quiz.json"

GENERATED_EXERCISES_DIR = GENERATED / "exercises"
GENERATED_EXERCISES_AGGREGATE = GENERATED / "exercices.json"
SITE_EXERCISES_PATH = DATA / "exercices.json"
DATA_JSON = DATA / "data.json"


# =============================================================================
# Constantes issues de config.py, avec replis
# =============================================================================


def cfg_value(name: str, default: Any) -> Any:
    if cfg is not None and hasattr(cfg, name):
        return getattr(cfg, name)
    return default


DISCIPLINE = cfg_value("DISCIPLINE", "mathématiques")
NIVEAU = cfg_value("NIVEAU", "Terminale générale")
ENSEIGNEMENT = cfg_value("ENSEIGNEMENT", "Spécialité mathématiques")
SITE_TITLE = cfg_value("SITE_TITLE", "Révisions bac mathématiques")
SITE_SUBTITLE = cfg_value("SITE_SUBTITLE", "Terminale générale — spécialité mathématiques")
PROGRAMME_VERSION = cfg_value("PROGRAMME_VERSION", "2019")
BAC_TARGET = cfg_value("BAC_TARGET", "bac_2026_2027")

PROGRAMME_DOMAINS: dict[str, str] = cfg_value(
    "PROGRAMME_DOMAINS",
    {
        "algebre-geometrie": "Algèbre et géométrie",
        "analyse": "Analyse",
        "probabilites": "Probabilités",
        "algorithmique-programmation": "Algorithmique et programmation",
        "logique-ensembles": "Vocabulaire ensembliste et logique",
    },
)

DOMAIN_ORDER: list[str] = cfg_value(
    "DOMAIN_ORDER",
    list(PROGRAMME_DOMAINS.keys()),
)

ALLOWED_THEMES: dict[str, str] = cfg_value("ALLOWED_THEMES", {})
THEME_ORDER: list[str] = cfg_value("THEME_ORDER", list(ALLOWED_THEMES.keys()))
THEME_TO_DOMAIN: dict[str, str] = cfg_value("THEME_TO_DOMAIN", {})
THEME_DESCRIPTIONS: dict[str, str] = cfg_value("THEME_DESCRIPTIONS", {})
THEME_KEY_RELATIONS: dict[str, list[str]] = cfg_value("THEME_KEY_RELATIONS", {})


# =============================================================================
# Lecture / écriture
# =============================================================================


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# Normalisations générales
# =============================================================================


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_str_list(value: Any) -> list[str]:
    return [str(x).strip() for x in as_list(value) if str(x).strip()]


def normalize_int(value: Any, default: int | str = "") -> int | str:
    if value in {None, ""}:
        return default
    try:
        return int(value)
    except Exception:
        return default


def site_relative_or_keep(value: Any) -> str:
    """Normalise un chemin destiné au site sans imposer une existence locale."""
    s = str(value or "").strip()
    if not s:
        return ""

    s = s.replace("\\", "/")

    # Interdit les chemins absolus dans les données exposées au site.
    if s.startswith("/"):
        try:
            p = Path(s).resolve()
            rel = p.relative_to(SITE.resolve()).as_posix()
            return rel
        except Exception:
            return ""

    if s.startswith("site/"):
        s = s[len("site/"):]

    return s


def normalize_page_images(value: Any) -> list[str]:
    images = []
    for item in as_list(value):
        rel = site_relative_or_keep(item)
        if rel and rel not in images:
            images.append(rel)
    return images


def normalize_corrige(corrige: Any) -> dict[str, Any]:
    """Garantit corrige.questions sous forme de liste de dictionnaires."""
    if isinstance(corrige, dict):
        qs = corrige.get("questions")
        if isinstance(qs, list):
            questions = qs
        else:
            questions = []
            for k, v in corrige.items():
                if k == "questions":
                    continue
                if isinstance(v, dict):
                    item = {"numero": str(k)}
                    item.update(v)
                    questions.append(item)
                else:
                    questions.append(
                        {
                            "numero": str(k),
                            "reponse": str(v),
                            "points_attention": "",
                        }
                    )
    elif isinstance(corrige, list):
        questions = corrige
    elif isinstance(corrige, str):
        questions = [
            {
                "numero": "global",
                "reponse": corrige,
                "points_attention": "",
            }
        ]
    else:
        questions = []

    normalized: list[dict[str, Any]] = []
    for q in questions:
        if isinstance(q, dict):
            normalized.append(
                {
                    "numero": str(q.get("numero") or q.get("numéro") or q.get("question") or "").strip(),
                    "reponse": str(q.get("reponse") or q.get("réponse") or "").strip(),
                    "points_attention": str(q.get("points_attention") or q.get("vigilance") or "").strip(),
                }
            )
        else:
            normalized.append(
                {
                    "numero": "",
                    "reponse": str(q).strip(),
                    "points_attention": "",
                }
            )

    return {"questions": normalized}


def theme_label(theme_id: str) -> str:
    return ALLOWED_THEMES.get(theme_id, theme_id)


def domain_id_for_theme(theme_id: str) -> str:
    return THEME_TO_DOMAIN.get(theme_id, "")


def domain_label(domain_id: str) -> str:
    return PROGRAMME_DOMAINS.get(domain_id, domain_id)


def normalize_secondary_themes(value: Any) -> list[str]:
    out = []
    for item in as_list(value):
        tid = str(item).strip()
        if tid and tid in ALLOWED_THEMES and tid not in out:
            out.append(tid)
    return out


# =============================================================================
# Exercices
# =============================================================================


def normalize_exercise(ex: dict[str, Any]) -> dict[str, Any]:
    out = dict(ex)

    out.setdefault("id", "")
    out.setdefault("source_id", "")
    out.setdefault("titre", "")
    out.setdefault("annee", "")
    out.setdefault("session", "")
    out.setdefault("zone", "")
    out.setdefault("numero", out.get("type", ""))
    out.setdefault("type", out.get("numero", ""))
    out.setdefault("points", "")
    out.setdefault("pages", [])

    # Champs de classement.
    theme_id = str(out.get("thematique_id") or out.get("theme_id") or out.get("theme_hint") or "").strip()
    if theme_id not in ALLOWED_THEMES:
        # On conserve la valeur si elle existe, mais on la signale implicitement
        # par un libellé vide dans les rapports. Cela évite de casser le site.
        theme_id = str(out.get("thematique_id") or out.get("theme_id") or "").strip()

    out["thematique_id"] = theme_id
    out["thematique_label"] = theme_label(theme_id) if theme_id else ""

    domain_id = str(out.get("domaine_id") or out.get("domain_id") or domain_id_for_theme(theme_id) or "").strip()
    out["domaine_id"] = domain_id
    out["domaine_label"] = domain_label(domain_id) if domain_id else ""

    out["themes_secondaires"] = normalize_secondary_themes(
        out.get("themes_secondaires") or out.get("secondary_themes")
    )

    # Champs pédagogiques.
    out["notions"] = as_str_list(out.get("notions"))
    out["mots_cles"] = as_str_list(out.get("mots_cles") or out.get("mots_clés"))
    out["competences"] = as_str_list(out.get("competences") or out.get("compétences"))
    out["aide"] = as_str_list(out.get("aide"))
    out["methodes"] = as_list(out.get("methodes") or out.get("méthodes"))
    out["points_vigilance"] = as_str_list(out.get("points_vigilance"))
    out["liens_programme"] = as_str_list(out.get("liens_programme"))
    out["resume_enonce"] = str(out.get("resume_enonce") or out.get("résumé_énoncé") or "").strip()

    out["difficulte"] = normalize_int(out.get("difficulte") or out.get("difficulté"), default="")

    # Questions et corrigé.
    out["questions_detectees"] = as_str_list(out.get("questions_detectees"))
    out["corrige"] = normalize_corrige(out.get("corrige"))

    # Images.
    page_images = normalize_page_images(out.get("page_images"))
    out["page_images"] = page_images
    out["image_page"] = site_relative_or_keep(out.get("image_page")) or (page_images[0] if page_images else "")

    # Texte extrait : utile pour recherche locale, mais potentiellement lourd.
    # On le conserve dans exercices.json pour compatibilité avec l'interface existante.
    out["texte_extrait"] = str(out.get("texte_extrait") or out.get("enonce") or "")

    # Métadonnées de génération.
    generation = out.get("generation")
    out["generation"] = generation if isinstance(generation, dict) else {}

    return out


def load_individual_generated_exercises() -> list[dict[str, Any]]:
    if not GENERATED_EXERCISES_DIR.exists():
        return []

    exercises: list[dict[str, Any]] = []
    for path in sorted(GENERATED_EXERCISES_DIR.glob("*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict):
            print(f"Ignore fichier non-dict : {path}")
            continue
        if not obj.get("id"):
            print(f"Ignore exercice sans id : {path}")
            continue
        exercises.append(normalize_exercise(obj))

    return exercises


def load_aggregate_generated_exercises() -> list[dict[str, Any]]:
    for path in [GENERATED_EXERCISES_AGGREGATE, SITE_EXERCISES_PATH]:
        obj = read_json(path)
        if isinstance(obj, list):
            return [normalize_exercise(ex) for ex in obj if isinstance(ex, dict)]
    return []


def load_exercises(source: str) -> tuple[list[dict[str, Any]], str]:
    """Charge les exercices selon la source demandée."""
    if source == "individual":
        exercises = load_individual_generated_exercises()
        return exercises, "individual"

    if source == "aggregate":
        exercises = load_aggregate_generated_exercises()
        return exercises, "aggregate"

    # auto : priorité aux fichiers individuels, puis agrégat.
    exercises = load_individual_generated_exercises()
    if exercises:
        return exercises, "individual"

    exercises = load_aggregate_generated_exercises()
    return exercises, "aggregate"


def exercise_sort_key(ex: dict) -> tuple:
    """Clé de tri robuste pour les exercices.

    Les numéros peuvent être numériques ("1", 2, "3") ou alphabétiques
    ("A", "B") depuis la séparation des exercices au choix. La clé retourne
    donc toujours des types comparables.
    """
    annee = str(ex.get("annee") or "")
    source_id = str(ex.get("source_id") or ex.get("sujet_id") or "")
    session = str(ex.get("session") or "")
    zone = str(ex.get("zone") or "")
    ex_id = str(ex.get("id") or "")

    numero_raw = ex.get("numero", ex.get("exercise_number", ""))

    try:
        numero_str = str(numero_raw).strip()
    except Exception:
        numero_str = ""

    if numero_str.isdigit():
        numero_key = (0, int(numero_str), "")
    elif numero_str.upper() in {"A", "B", "C", "D"}:
        numero_key = (1, 0, numero_str.upper())
    else:
        numero_key = (2, 0, numero_str)

    return (annee, source_id, session, zone, numero_key, ex_id)


def build_programme_navigation(programme_file_data: Any) -> dict[str, Any]:
    """Construit un objet programme stable pour le frontend."""
    base: dict[str, Any]
    if isinstance(programme_file_data, dict):
        base = dict(programme_file_data)
    else:
        base = {}

    thematiques = []
    for theme_id in THEME_ORDER:
        label = ALLOWED_THEMES.get(theme_id, theme_id)
        domain_id = THEME_TO_DOMAIN.get(theme_id, "")
        thematiques.append(
            {
                "id": theme_id,
                "theme_id": theme_id,
                "titre": label,
                "label": label,
                "description": THEME_DESCRIPTIONS.get(theme_id, ""),
                "domain_id": domain_id,
                "domaine_id": domain_id,
                "domain_label": domain_label(domain_id) if domain_id else "",
                "domaine_label": domain_label(domain_id) if domain_id else "",
                "relations_cles": THEME_KEY_RELATIONS.get(theme_id, []),
            }
        )

    domaines = []
    for domain_id in DOMAIN_ORDER:
        domaines.append(
            {
                "id": domain_id,
                "label": PROGRAMME_DOMAINS.get(domain_id, domain_id),
                "themes": [tid for tid in THEME_ORDER if THEME_TO_DOMAIN.get(tid) == domain_id],
            }
        )

    base.update(
        {
            "discipline": DISCIPLINE,
            "niveau": NIVEAU,
            "enseignement": ENSEIGNEMENT,
            "programme_version": PROGRAMME_VERSION,
            "bac_target": BAC_TARGET,
            "domains": PROGRAMME_DOMAINS,
            "domain_order": DOMAIN_ORDER,
            "domaines": domaines,
            "themes": ALLOWED_THEMES,
            "theme_order": THEME_ORDER,
            "theme_to_domain": THEME_TO_DOMAIN,
            "theme_descriptions": THEME_DESCRIPTIONS,
            "thematiques": thematiques,
        }
    )

    return base


def load_manifest() -> list[dict[str, Any]]:
    manifest_json = read_json(MANIFEST_JSON_PATH)
    if isinstance(manifest_json, list):
        return manifest_json
    return [dict(row) for row in read_csv_dicts(MANIFEST_CSV_PATH)]


def normalize_courses(cours: Any) -> dict[str, Any]:
    if not isinstance(cours, dict):
        return {}

    normalized = dict(cours)

    # Garantit au moins une entrée par thème si le cours n'est pas encore généré.
    for theme_id in THEME_ORDER:
        normalized.setdefault(
            theme_id,
            {
                "theme_id": theme_id,
                "titre": ALLOWED_THEMES.get(theme_id, theme_id),
                "domaine_id": THEME_TO_DOMAIN.get(theme_id, ""),
                "synthese": "",
                "definitions": [],
                "proprietes_theoremes": [],
                "formules": [],
                "methodes": [],
                "automatismes": [],
                "erreurs_frequentes": [],
                "conseils_bac": [],
            },
        )

    return normalized


def normalize_quiz(quiz: Any) -> dict[str, Any]:
    if isinstance(quiz, dict):
        normalized = dict(quiz)
    elif isinstance(quiz, list):
        # Ancien format éventuel : liste d'objets avec theme_id.
        normalized = {}
        for item in quiz:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("theme_id") or item.get("thematique_id") or "").strip()
            if not tid:
                continue
            normalized.setdefault(tid, []).append(item)
    else:
        normalized = {}

    for theme_id in THEME_ORDER:
        normalized.setdefault(theme_id, [])

    return normalized


# =============================================================================
# Rapports
# =============================================================================


def build_report(exercises: list[dict[str, Any]], source_used: str, cours: dict[str, Any], quiz: dict[str, Any]) -> dict[str, Any]:
    by_theme: dict[str, int] = {theme_id: 0 for theme_id in THEME_ORDER}
    by_domain: dict[str, int] = {domain_id: 0 for domain_id in DOMAIN_ORDER}
    invalid_theme: list[str] = []
    no_corrige: list[str] = []
    no_images: list[str] = []

    for ex in exercises:
        ex_id = str(ex.get("id", ""))
        theme_id = str(ex.get("thematique_id", ""))
        domain_id = str(ex.get("domaine_id", ""))

        if theme_id in by_theme:
            by_theme[theme_id] += 1
        elif theme_id:
            invalid_theme.append(ex_id)

        if domain_id in by_domain:
            by_domain[domain_id] += 1

        questions = (ex.get("corrige") or {}).get("questions") if isinstance(ex.get("corrige"), dict) else []
        if not questions:
            no_corrige.append(ex_id)

        if not ex.get("page_images"):
            no_images.append(ex_id)

    quiz_counts = {
        theme_id: len(items) if isinstance(items, list) else 0
        for theme_id, items in quiz.items()
    }

    course_status = {
        theme_id: bool(cours.get(theme_id, {}).get("synthese"))
        for theme_id in THEME_ORDER
    }

    return {
        "source_exercices": source_used,
        "total_exercices": len(exercises),
        "exercices_par_theme": by_theme,
        "exercices_par_domaine": by_domain,
        "themes_invalides": invalid_theme,
        "exercices_sans_corrige": no_corrige,
        "exercices_sans_images": no_images,
        "quiz_par_theme": quiz_counts,
        "cours_generes": course_status,
        "data_json": str(DATA_JSON),
        "exercices_json": str(SITE_EXERCISES_PATH),
    }


def write_report_csv(report: dict[str, Any]) -> None:
    rows = []
    by_theme = report.get("exercices_par_theme", {})
    quiz_counts = report.get("quiz_par_theme", {})
    course_status = report.get("cours_generes", {})

    for theme_id in THEME_ORDER:
        rows.append(
            {
                "theme_id": theme_id,
                "theme_label": ALLOWED_THEMES.get(theme_id, theme_id),
                "domain_id": THEME_TO_DOMAIN.get(theme_id, ""),
                "domain_label": domain_label(THEME_TO_DOMAIN.get(theme_id, "")),
                "nb_exercices": by_theme.get(theme_id, 0),
                "nb_quiz": quiz_counts.get(theme_id, 0),
                "cours_genere": "1" if course_status.get(theme_id) else "0",
            }
        )

    write_csv(
        REPORTS / "build_site_data_report.csv",
        rows,
        [
            "theme_id",
            "theme_label",
            "domain_id",
            "domain_label",
            "nb_exercices",
            "nb_quiz",
            "cours_genere",
        ],
    )


# =============================================================================
# Payload final
# =============================================================================


def build_payload(
    *,
    programme: dict[str, Any],
    programme_officiel: Any,
    manifest: list[dict[str, Any]],
    exercices: list[dict[str, Any]],
    cours: dict[str, Any],
    quiz: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "site_title": SITE_TITLE,
        "site_subtitle": SITE_SUBTITLE,
        "discipline": DISCIPLINE,
        "niveau": NIVEAU,
        "enseignement": ENSEIGNEMENT,
        "programme_version": PROGRAMME_VERSION,
        "bac_target": BAC_TARGET,
        "domains": PROGRAMME_DOMAINS,
        "domain_order": DOMAIN_ORDER,
        "themes": ALLOWED_THEMES,
        "theme_order": THEME_ORDER,
        "theme_to_domain": THEME_TO_DOMAIN,
        "theme_descriptions": THEME_DESCRIPTIONS,
        "programme": programme,
        "programme_officiel": programme_officiel if isinstance(programme_officiel, dict) else {},
        "manifest": manifest,
        "exercices": exercices,
        "cours": cours,
        "quiz": quiz,
        "build_report": report,
    }


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Construit site/data/exercices.json et site/data/data.json pour le site de révisions mathématiques."
    )
    parser.add_argument(
        "--source",
        choices=["auto", "individual", "aggregate"],
        default="auto",
        help="Source des exercices générés. auto privilégie les fichiers individuels.",
    )
    parser.add_argument(
        "--allow-empty-exercises",
        action="store_true",
        help="Autorise la génération de data.json même si aucun exercice n'est trouvé.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Accepté pour compatibilité ; les sorties sont toujours réécrites.",
    )
    return parser.parse_args()



def normalize_pdf_path_for_site(value):
    """Normalise un chemin PDF pour le site publié depuis le dossier site/."""
    value = str(value or "").strip()
    if not value:
        return ""
    value = value.replace("\\", "/")
    value = value.replace("site/", "")
    value = value.replace("./", "")
    value = value.lstrip("/")
    if not value.startswith("pdf/"):
        name = value.split("/")[-1]
        if name.lower().endswith(".pdf"):
            value = "pdf/" + name
    return value

def attach_pdf_paths_to_exercises(exercices, manifest_rows=None):
    """Ajoute pdf_path aux exercices à partir du manifest via source_id."""
    if manifest_rows is None:
        manifest = read_json(MANIFEST_JSON_PATH, default=[])
        if isinstance(manifest, dict):
            manifest_rows = list(manifest.values())
        elif isinstance(manifest, list):
            manifest_rows = manifest
        else:
            manifest_rows = []

    by_id = {}
    for row in manifest_rows or []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or row.get("source_id") or "").strip()
        if sid:
            by_id[sid] = row

    patched = 0

    for ex in exercices or []:
        if not isinstance(ex, dict):
            continue

        sid = str(ex.get("source_id") or ex.get("sujet_id") or "").strip()
        row = by_id.get(sid)

        if not row:
            continue

        pdf = normalize_pdf_path_for_site(row.get("pdf_path") or row.get("pdf") or "")
        if pdf:
            ex["pdf_path"] = pdf
            patched += 1

    return patched


def main() -> int:
    args = parse_args()

    exercices, source_used = load_exercises(args.source)
    exercices.sort(key=exercise_sort_key)
    pdf_patched = attach_pdf_paths_to_exercises(exercices)

    if not exercices and not args.allow_empty_exercises:
        print(
            "ERREUR aucun exercice généré trouvé. "
            "Lance d'abord scripts/03_generate_exercises.py ou utilise --allow-empty-exercises.",
            file=sys.stderr,
        )
        return 2

    programme_file_data = read_json(PROGRAMME_PATH, default={})
    programme = build_programme_navigation(programme_file_data)
    programme_officiel = read_json(PROGRAMME_OFFICIEL_PATH, default={})
    manifest = load_manifest()
    cours = normalize_courses(read_json(COURS_PATH, default={}))
    quiz = normalize_quiz(read_json(QUIZ_PATH, default={}))

    report = build_report(exercices, source_used=source_used, cours=cours, quiz=quiz)

    payload = build_payload(
        programme=programme,
        programme_officiel=programme_officiel,
        manifest=manifest,
        exercices=exercices,
        cours=cours,
        quiz=quiz,
        report=report,
    )

    write_json(SITE_EXERCISES_PATH, exercices)
    write_json(DATA_JSON, payload)
    write_json(REPORTS / "build_site_data_report.json", report)
    write_report_csv(report)

    print(f"Source exercices : {source_used}")
    print(f"Exercices écrits : {SITE_EXERCISES_PATH} ({len(exercices)})")
    print(f"PDF rattachés    : {pdf_patched}")
    print(f"Data site écrit  : {DATA_JSON}")
    print(f"Manifest         : {len(manifest)} sujet(s)")
    print(f"Cours            : {sum(1 for v in cours.values() if isinstance(v, dict) and v.get('synthese'))}/{len(THEME_ORDER)} thème(s) généré(s)")
    print(f"Quiz             : {sum(len(v) for v in quiz.values() if isinstance(v, list))} question(s)")
    print(f"Rapport JSON     : {REPORTS / 'build_site_data_report.json'}")
    print(f"Rapport CSV      : {REPORTS / 'build_site_data_report.csv'}")

    if report["themes_invalides"]:
        print(f"ATTENTION thèmes invalides : {len(report['themes_invalides'])}")
        print("Exemples :", report["themes_invalides"][:5])

    if report["exercices_sans_corrige"]:
        print(f"ATTENTION exercices sans corrigé : {len(report['exercices_sans_corrige'])}")
        print("Exemples :", report["exercices_sans_corrige"][:5])

    if report["exercices_sans_images"]:
        print(f"ATTENTION exercices sans images : {len(report['exercices_sans_images'])}")
        print("Exemples :", report["exercices_sans_images"][:5])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
