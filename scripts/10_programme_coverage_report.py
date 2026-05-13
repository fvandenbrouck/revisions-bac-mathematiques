#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10_programme_coverage_report.py

Produit des rapports de couverture du programme par les fiches de cours,
les exercices classés et les quiz.

Le script accepte deux situations :
1. un programme officiel structuré existe dans site/data/programme_officiel.json ;
2. ce fichier est absent ou trop peu structuré : le rapport se replie alors sur les
   thèmes et relations clés définis dans scripts/config.py.

Sorties :
- site/data/rapports/programme_coverage.json
- site/data/rapports/programme_coverage.csv
- site/data/rapports/programme_coverage_by_theme.csv
- site/data/rapports/programme_coverage_missing.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from config import (  # type: ignore
        ALLOWED_THEMES,
        COURSES_JSON,
        PROGRAMME_COVERAGE_JSON,
        PROGRAMME_DOMAINS,
        PROGRAMME_OFFICIEL_JSON,
        REPORTS_DIR,
        THEME_DESCRIPTIONS,
        THEME_KEY_RELATIONS,
        THEME_ORDER,
        THEME_TO_DOMAIN,
    )
except Exception:  # pragma: no cover - repli si le script est déplacé
    ALLOWED_THEMES = {}
    COURSES_JSON = None
    PROGRAMME_COVERAGE_JSON = None
    PROGRAMME_DOMAINS = {}
    PROGRAMME_OFFICIEL_JSON = None
    REPORTS_DIR = None
    THEME_DESCRIPTIONS = {}
    THEME_KEY_RELATIONS = {}
    THEME_ORDER = []
    THEME_TO_DOMAIN = {}


# =============================================================================
# Chemins et JSON
# =============================================================================

def find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "site").exists() and (parent / "scripts").exists():
            return parent
    return Path.cwd()


ROOT = find_project_root()
SITE = ROOT / "site"
DATA = SITE / "data"

DEFAULT_PROGRAMME_OFFICIEL_JSON = Path(PROGRAMME_OFFICIEL_JSON) if PROGRAMME_OFFICIEL_JSON else DATA / "programme_officiel.json"
DEFAULT_COURSES_JSON = Path(COURSES_JSON) if COURSES_JSON else DATA / "cours.json"
DEFAULT_EXERCISES_JSON = DATA / "exercices.json"
DEFAULT_QUIZ_JSON = DATA / "quiz.json"
DEFAULT_REPORTS_DIR = Path(REPORTS_DIR) if REPORTS_DIR else DATA / "rapports"
DEFAULT_COVERAGE_JSON = Path(PROGRAMME_COVERAGE_JSON) if PROGRAMME_COVERAGE_JSON else DEFAULT_REPORTS_DIR / "programme_coverage.json"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# =============================================================================
# Utilitaires de normalisation
# =============================================================================

def slugify(value: str, max_len: int = 120) -> str:
    value = str(value or "").lower()
    value = value.replace("œ", "oe").replace("æ", "ae")
    replacements = {
        "à": "a", "â": "a", "ä": "a",
        "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ÿ": "y",
        "’": "-", "'": "-",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-+", "-", value)
    if len(value) > max_len:
        value = value[:max_len].strip("-")
    return value or "item"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def stringify_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ; ".join(str(x) for x in value if str(x).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def first_non_empty(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        if s:
            return s
    return default


# =============================================================================
# Extraction des blocs du programme officiel
# =============================================================================

def make_block(
    *,
    domain_id: str,
    domain_title: str,
    theme_id: str,
    theme_title: str,
    part_id: str,
    part_title: str,
    bloc_id: str,
    bloc_title: str,
    notions: Any,
    capacites: Any,
    source: str,
) -> dict[str, Any]:
    return {
        "domain_id": domain_id,
        "domain_title": domain_title,
        "theme_id": theme_id,
        "theme_title": theme_title,
        "part_id": part_id,
        "part_title": part_title,
        "bloc_id": bloc_id,
        "bloc_title": bloc_title,
        "notions": stringify_list(notions),
        "capacites": stringify_list(capacites),
        "source": source,
    }


def domain_for_theme(theme_id: str, theme_title: str = "") -> tuple[str, str]:
    domain_id = THEME_TO_DOMAIN.get(theme_id, "") if isinstance(THEME_TO_DOMAIN, dict) else ""
    if not domain_id:
        # Repli approximatif si le programme officiel contient des thèmes plus larges.
        title = slugify(theme_title)
        if any(x in title for x in ["probabilite", "bernoulli", "binomiale", "aleatoire", "grands-nombres"]):
            domain_id = "probabilites"
        elif any(x in title for x in ["suite", "fonction", "limite", "derive", "continuite", "logarithme", "trigonometrie", "integral"]):
            domain_id = "analyse"
        elif any(x in title for x in ["geometrie", "vecteur", "droite", "plan", "orthogonal", "denombrement", "combinatoire"]):
            domain_id = "algebre-geometrie"
        elif any(x in title for x in ["python", "algorithm"]):
            domain_id = "algorithmique-programmation"
        else:
            domain_id = "logique-ensembles"

    domain_title = PROGRAMME_DOMAINS.get(domain_id, domain_id) if isinstance(PROGRAMME_DOMAINS, dict) else domain_id
    return domain_id, domain_title


def block_id_from_parts(theme_id: str, part_title: str, bloc_title: str, index: int) -> str:
    base = "-".join(x for x in [theme_id, slugify(part_title, 45), slugify(bloc_title, 45), f"{index:03d}"] if x)
    return slugify(base, 140)


def extract_block_from_dict(
    block: dict[str, Any],
    *,
    theme_id: str,
    theme_title: str,
    part_id: str,
    part_title: str,
    index: int,
    source: str,
) -> dict[str, Any]:
    domain_id, domain_title = domain_for_theme(theme_id, theme_title)
    bloc_title = first_non_empty(
        block.get("titre"),
        block.get("title"),
        block.get("nom"),
        block.get("name"),
        block.get("section"),
        default=f"Bloc {index}",
    )
    bloc_id = first_non_empty(
        block.get("id"),
        block.get("bloc_id"),
        block.get("block_id"),
        default=block_id_from_parts(theme_id, part_title, bloc_title, index),
    )
    notions = (
        block.get("notions_contenus")
        or block.get("notions")
        or block.get("contenus")
        or block.get("content")
        or block.get("objectifs")
        or block.get("description")
        or block.get("texte")
        or block.get("text")
        or ""
    )
    capacites = (
        block.get("capacites_exigibles")
        or block.get("capacites_attendues")
        or block.get("capacites")
        or block.get("capacités")
        or block.get("attendus")
        or ""
    )

    return make_block(
        domain_id=domain_id,
        domain_title=domain_title,
        theme_id=theme_id,
        theme_title=theme_title,
        part_id=part_id,
        part_title=part_title,
        bloc_id=bloc_id,
        bloc_title=bloc_title,
        notions=notions,
        capacites=capacites,
        source=source,
    )


def iter_theme_sections(theme: dict[str, Any]) -> Iterable[tuple[str, str, list[dict[str, Any]]]]:
    """Retourne des sections candidates d’un thème officiel."""
    section_keys = ["sous_parties", "sous-parties", "sections", "parties", "chapitres"]
    for key in section_keys:
        sections = theme.get(key)
        if isinstance(sections, list):
            for i, section in enumerate(sections, start=1):
                if not isinstance(section, dict):
                    continue
                part_title = first_non_empty(
                    section.get("titre"),
                    section.get("title"),
                    section.get("nom"),
                    default=f"Partie {i}",
                )
                part_id = first_non_empty(
                    section.get("id"),
                    section.get("part_id"),
                    default=slugify(part_title),
                )
                blocks = section.get("blocs") or section.get("blocks") or section.get("items")
                if isinstance(blocks, list):
                    yield part_id, part_title, [b for b in blocks if isinstance(b, dict)]
                else:
                    # La section elle-même peut être le bloc.
                    yield part_id, part_title, [section]
            return

    blocks = theme.get("blocs") or theme.get("blocks") or theme.get("items")
    if isinstance(blocks, list):
        yield "general", "Général", [b for b in blocks if isinstance(b, dict)]
    else:
        yield "general", "Général", [theme]


def extract_blocks_from_themes(themes: list[Any], *, source: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    for theme_index, theme in enumerate(themes, start=1):
        if not isinstance(theme, dict):
            continue

        theme_title = first_non_empty(
            theme.get("titre"),
            theme.get("title"),
            theme.get("label"),
            theme.get("nom"),
            default=f"Thème {theme_index}",
        )
        theme_id = first_non_empty(
            theme.get("id"),
            theme.get("theme_id"),
            theme.get("slug"),
            default=slugify(theme_title),
        )

        block_index = 0
        for part_id, part_title, section_blocks in iter_theme_sections(theme):
            for block in section_blocks:
                block_index += 1
                blocks.append(
                    extract_block_from_dict(
                        block,
                        theme_id=theme_id,
                        theme_title=theme_title,
                        part_id=part_id,
                        part_title=part_title,
                        index=block_index,
                        source=source,
                    )
                )

    # Déduplication par bloc_id.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for block in blocks:
        bid = str(block.get("bloc_id", "")).strip()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        deduped.append(block)
    return deduped


def official_blocks(programme: Any) -> list[dict[str, Any]]:
    """Extrait les blocs d’un programme officiel structuré, si possible."""
    if isinstance(programme, list):
        return extract_blocks_from_themes(programme, source="programme_officiel:list")

    if not isinstance(programme, dict):
        return []

    # Cas standard : {"themes": [...]}.
    themes = programme.get("themes")
    if isinstance(themes, list):
        blocks = extract_blocks_from_themes(themes, source="programme_officiel:themes")
        if blocks:
            return blocks

    # Cas : {"themes": {theme_id: {...}}}.
    if isinstance(themes, dict):
        as_list_themes = []
        for theme_id, theme in themes.items():
            if isinstance(theme, dict):
                item = dict(theme)
                item.setdefault("id", theme_id)
                as_list_themes.append(item)
        blocks = extract_blocks_from_themes(as_list_themes, source="programme_officiel:themes_dict")
        if blocks:
            return blocks

    # Cas : {"programme": {"themes": [...]}}.
    nested = programme.get("programme")
    if nested is not None:
        blocks = official_blocks(nested)
        if blocks:
            return blocks

    # Cas : clés directes par thèmes officiels ou thèmes du site.
    direct_themes: list[dict[str, Any]] = []
    for theme_id, theme_label in (ALLOWED_THEMES or {}).items():
        if theme_id in programme and isinstance(programme[theme_id], dict):
            item = dict(programme[theme_id])
            item.setdefault("id", theme_id)
            item.setdefault("titre", theme_label)
            direct_themes.append(item)

    if direct_themes:
        return extract_blocks_from_themes(direct_themes, source="programme_officiel:direct_theme_keys")

    return []


def fallback_blocks_from_config() -> list[dict[str, Any]]:
    """Construit des blocs synthétiques à partir de config.py."""
    blocks: list[dict[str, Any]] = []
    theme_ids = list(THEME_ORDER or ALLOWED_THEMES.keys())

    for theme_id in theme_ids:
        theme_title = ALLOWED_THEMES.get(theme_id, theme_id)
        domain_id, domain_title = domain_for_theme(theme_id, theme_title)
        description = THEME_DESCRIPTIONS.get(theme_id, "") if isinstance(THEME_DESCRIPTIONS, dict) else ""
        relations = THEME_KEY_RELATIONS.get(theme_id, []) if isinstance(THEME_KEY_RELATIONS, dict) else []

        blocks.append(
            make_block(
                domain_id=domain_id,
                domain_title=domain_title,
                theme_id=theme_id,
                theme_title=theme_title,
                part_id="theme",
                part_title="Thème de révision",
                bloc_id=f"{theme_id}::theme",
                bloc_title=theme_title,
                notions=description,
                capacites="",
                source="config:theme",
            )
        )

        for i, relation in enumerate(relations, start=1):
            blocks.append(
                make_block(
                    domain_id=domain_id,
                    domain_title=domain_title,
                    theme_id=theme_id,
                    theme_title=theme_title,
                    part_id="relations-cles",
                    part_title="Relations et propriétés clés",
                    bloc_id=f"{theme_id}::relation-{i:03d}",
                    bloc_title=f"Relation clé {i}",
                    notions=relation,
                    capacites="Identifier, utiliser et éviter les confusions classiques associées.",
                    source="config:theme_key_relations",
                )
            )

    return blocks


def load_programme_blocks(programme_path: Path, allow_fallback: bool) -> tuple[list[dict[str, Any]], str]:
    programme = read_json(programme_path, default={}) or {}
    blocks = official_blocks(programme)
    if blocks:
        return blocks, "programme_officiel"

    if allow_fallback:
        return fallback_blocks_from_config(), "config_fallback"

    return [], "none"


# =============================================================================
# Couverture par les cours
# =============================================================================

def iter_course_block_refs(course: dict[str, Any]) -> Iterable[tuple[str, str, str]]:
    """Produit des références de blocs déclarées par une fiche de cours.

    Rend : (bloc_id, statut, ou)
    """
    # Format explicite éventuel.
    for item in as_list(course.get("couverture_programme")):
        if not isinstance(item, dict):
            continue
        bid = first_non_empty(item.get("bloc_id"), item.get("id"), item.get("block_id"))
        if bid:
            yield bid, first_non_empty(item.get("statut"), default="couvert"), first_non_empty(item.get("ou"), item.get("where"), item.get("titre"))

    # Format produit par les générateurs de cours récents.
    for bid in as_list(course.get("blocs_programme_couverts")):
        if str(bid).strip():
            yield str(bid).strip(), "couvert", "blocs_programme_couverts"

    for item in as_list(course.get("liens_programme")):
        if isinstance(item, dict):
            bid = first_non_empty(item.get("bloc_id"), item.get("id"), item.get("reference"))
            if bid:
                yield bid, first_non_empty(item.get("statut"), default="couvert"), first_non_empty(item.get("ou"), item.get("titre"), item.get("description"))
        elif str(item).strip():
            yield str(item).strip(), "couvert", "liens_programme"

    # Modules éventuels.
    for module in as_list(course.get("modules")):
        if not isinstance(module, dict):
            continue
        module_title = first_non_empty(module.get("titre"), module.get("title"), default="module")
        for bid in as_list(module.get("bloc_ids_programme") or module.get("blocs_programme")):
            if str(bid).strip():
                yield str(bid).strip(), "couvert", module_title

    # Entrées pédagogiques portant un bloc_programme.
    for key in [
        "definitions",
        "proprietes",
        "propriétés",
        "theoremes",
        "théorèmes",
        "formules",
        "methodes",
        "méthodes",
        "automatismes",
        "erreurs_frequentes",
        "demonstrations",
        "démonstrations",
        "algorithmes_python",
    ]:
        for item in as_list(course.get(key)):
            if not isinstance(item, dict):
                continue
            bid = first_non_empty(
                item.get("bloc_programme"),
                item.get("bloc_id"),
                item.get("id_programme"),
            )
            if bid:
                yield bid, "couvert", first_non_empty(item.get("titre"), item.get("nom"), default=key)


def course_coverage_map(courses: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)

    for course_key, course in courses.items():
        if not isinstance(course, dict):
            continue

        theme_id = first_non_empty(course.get("theme_id"), course.get("thematique_id"), default=str(course_key))
        theme_label = ALLOWED_THEMES.get(theme_id, theme_id) if isinstance(ALLOWED_THEMES, dict) else theme_id

        for bid, statut, ou in iter_course_block_refs(course):
            out[bid].append(
                {
                    "theme_id": theme_id,
                    "theme_label": theme_label,
                    "statut": statut,
                    "ou": ou,
                }
            )

    return dict(out)


def themes_with_courses(courses: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key, course in courses.items():
        if isinstance(course, dict):
            out.add(first_non_empty(course.get("theme_id"), course.get("thematique_id"), default=str(key)))
        else:
            out.add(str(key))
    return out


# =============================================================================
# Couverture par exercices et quiz
# =============================================================================

def load_exercises(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, default=[])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def load_quiz(path: Path) -> dict[str, list[Any]]:
    data = read_json(path, default={})
    if isinstance(data, dict):
        return {str(k): as_list(v) for k, v in data.items()}
    return {}


def exercise_counts_by_theme(exercises: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for ex in exercises:
        theme = first_non_empty(ex.get("thematique_id"), ex.get("theme_id"))
        if theme:
            counts[theme] += 1
        for secondary in as_list(ex.get("themes_secondaires")):
            if isinstance(secondary, dict):
                sid = first_non_empty(secondary.get("thematique_id"), secondary.get("theme_id"), secondary.get("id"))
            else:
                sid = str(secondary).strip()
            if sid:
                counts[sid] += 1
    return counts


def quiz_counts_by_theme(quiz: dict[str, list[Any]]) -> Counter[str]:
    return Counter({theme_id: len(items) for theme_id, items in quiz.items()})


# =============================================================================
# Construction du rapport
# =============================================================================

def is_synthetic_block(block: dict[str, Any]) -> bool:
    return str(block.get("source", "")).startswith("config:")


def block_is_covered(
    block: dict[str, Any],
    coverage: dict[str, list[dict[str, str]]],
    course_theme_ids: set[str],
) -> tuple[bool, str, str]:
    bid = str(block.get("bloc_id", ""))
    theme_id = str(block.get("theme_id", ""))

    direct = coverage.get(bid, [])
    if direct:
        ou = " | ".join(first_non_empty(x.get("ou"), default="cours") for x in direct)
        course_themes = " | ".join(sorted({x.get("theme_id", "") for x in direct if x.get("theme_id")}))
        return True, course_themes, ou

    # Repli pour blocs synthétiques : si la fiche de cours du thème existe, on
    # considère que le thème est couvert, mais pas au niveau d’un bloc officiel.
    if is_synthetic_block(block) and theme_id in course_theme_ids:
        return True, theme_id, "fiche de cours du thème"

    return False, "", ""


def build_rows(
    blocks: list[dict[str, Any]],
    courses: dict[str, Any],
    exercises: list[dict[str, Any]],
    quiz: dict[str, list[Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    coverage = course_coverage_map(courses)
    course_theme_ids = themes_with_courses(courses)
    ex_counts = exercise_counts_by_theme(exercises)
    q_counts = quiz_counts_by_theme(quiz)

    rows: list[dict[str, Any]] = []
    per_theme: dict[str, dict[str, Any]] = {}

    for block in blocks:
        theme_id = str(block.get("theme_id", ""))
        theme_title = str(block.get("theme_title", ""))
        domain_id = str(block.get("domain_id", ""))
        domain_title = str(block.get("domain_title", ""))

        covered, course_theme_id, ou = block_is_covered(block, coverage, course_theme_ids)
        status = "couvert" if covered else "manquant"

        per_theme.setdefault(
            theme_id,
            {
                "domain_id": domain_id,
                "domain_title": domain_title,
                "theme_id": theme_id,
                "theme_title": theme_title,
                "total_blocs": 0,
                "couverts": 0,
                "manquants": 0,
                "has_course": theme_id in course_theme_ids,
                "nb_exercices": ex_counts.get(theme_id, 0),
                "nb_quiz": q_counts.get(theme_id, 0),
            },
        )

        per_theme[theme_id]["total_blocs"] += 1
        if covered:
            per_theme[theme_id]["couverts"] += 1
        else:
            per_theme[theme_id]["manquants"] += 1

        rows.append(
            {
                **block,
                "status": status,
                "course_theme_id": course_theme_id,
                "ou": ou,
                "has_course_for_theme": "1" if theme_id in course_theme_ids else "0",
                "nb_exercices_theme": ex_counts.get(theme_id, 0),
                "nb_quiz_theme": q_counts.get(theme_id, 0),
            }
        )

    theme_rows = []
    for theme_id, item in per_theme.items():
        total = item["total_blocs"]
        covered = item["couverts"]
        item["coverage_rate"] = round(covered / total, 4) if total else 0
        theme_rows.append(item)

    theme_order = list(THEME_ORDER or [])
    index = {tid: i for i, tid in enumerate(theme_order)}
    theme_rows.sort(key=lambda r: (r.get("domain_id", ""), index.get(r.get("theme_id", ""), 999), r.get("theme_id", "")))

    total_blocks = len(rows)
    total_covered = sum(1 for r in rows if r["status"] == "couvert")
    missing = total_blocks - total_covered

    summary = {
        "total_blocs": total_blocks,
        "couverts": total_covered,
        "manquants": missing,
        "coverage_rate": round(total_covered / total_blocks, 4) if total_blocks else 0,
        "total_themes": len(theme_rows),
        "themes_with_course": len(course_theme_ids),
        "themes_with_exercises": sum(1 for tid in {r["theme_id"] for r in theme_rows} if ex_counts.get(tid, 0) > 0),
        "themes_with_quiz": sum(1 for tid in {r["theme_id"] for r in theme_rows} if q_counts.get(tid, 0) > 0),
        "nb_exercices": len(exercises),
        "nb_questions_quiz": sum(q_counts.values()),
        "par_theme": {r["theme_id"]: r for r in theme_rows},
    }

    return rows, theme_rows, summary


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Produit un rapport de couverture programme -> cours/exercices/quiz.")
    parser.add_argument("--programme", type=Path, default=DEFAULT_PROGRAMME_OFFICIEL_JSON)
    parser.add_argument("--courses", type=Path, default=DEFAULT_COURSES_JSON)
    parser.add_argument("--exercises", type=Path, default=DEFAULT_EXERCISES_JSON)
    parser.add_argument("--quiz", type=Path, default=DEFAULT_QUIZ_JSON)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_COVERAGE_JSON)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--no-fallback", action="store_true", help="Ne pas utiliser config.py si programme_officiel.json est absent ou inutilisable.")
    parser.add_argument("--fail-on-missing", action="store_true", help="Retourne un code erreur s'il reste des blocs manquants.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    blocks, source_mode = load_programme_blocks(args.programme, allow_fallback=not args.no_fallback)
    if not blocks:
        print("ERREUR : aucun bloc de programme exploitable.", file=sys.stderr)
        return 2

    courses = read_json(args.courses, default={}) or {}
    if not isinstance(courses, dict):
        print(f"ATTENTION : cours invalide ou non-dict : {args.courses}", file=sys.stderr)
        courses = {}

    exercises = load_exercises(args.exercises)
    quiz = load_quiz(args.quiz)

    rows, theme_rows, summary = build_rows(blocks, courses, exercises, quiz)
    missing_rows = [r for r in rows if r.get("status") != "couvert"]

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.reports_dir / "programme_coverage.csv"
    theme_csv_path = args.reports_dir / "programme_coverage_by_theme.csv"
    missing_csv_path = args.reports_dir / "programme_coverage_missing.csv"

    payload = {
        "summary": summary,
        "source_mode": source_mode,
        "paths": {
            "programme": str(args.programme),
            "courses": str(args.courses),
            "exercises": str(args.exercises),
            "quiz": str(args.quiz),
        },
        "rows": rows,
        "missing": missing_rows,
        "themes": theme_rows,
    }

    write_json(args.output_json, payload)

    write_csv(
        csv_path,
        rows,
        [
            "domain_id",
            "domain_title",
            "theme_id",
            "theme_title",
            "part_id",
            "part_title",
            "bloc_id",
            "bloc_title",
            "status",
            "course_theme_id",
            "ou",
            "has_course_for_theme",
            "nb_exercices_theme",
            "nb_quiz_theme",
            "notions",
            "capacites",
            "source",
        ],
    )

    write_csv(
        theme_csv_path,
        theme_rows,
        [
            "domain_id",
            "domain_title",
            "theme_id",
            "theme_title",
            "total_blocs",
            "couverts",
            "manquants",
            "coverage_rate",
            "has_course",
            "nb_exercices",
            "nb_quiz",
        ],
    )

    write_csv(
        missing_csv_path,
        missing_rows,
        [
            "domain_id",
            "domain_title",
            "theme_id",
            "theme_title",
            "part_title",
            "bloc_id",
            "bloc_title",
            "notions",
            "capacites",
            "source",
        ],
    )

    print(f"Rapport programme JSON : {args.output_json}")
    print(f"Rapport programme CSV  : {csv_path}")
    print(f"Rapport par thème CSV  : {theme_csv_path}")
    print(f"Blocs manquants CSV    : {missing_csv_path}")
    print(f"Source des blocs       : {source_mode}")
    print(summary)

    if args.fail_on_missing and summary.get("manquants", 0):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
