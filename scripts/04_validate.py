#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_validate.py

Validation des exercices générés pour le site de révision du baccalauréat
spécialité mathématiques.

Entrées principales :
- site/data/intermediate/exercises_raw.json
- site/data/generated/exercises/*.json
- à défaut : site/data/generated/exercices.json ou site/data/exercices.json

Sorties :
- site/data/rapports/validation.json
- site/data/rapports/validation.csv
- site/data/rapports/validation_theme_coverage.csv
- site/data/rapports/validation_cours.csv

Objectif : repérer les exercices absents, mal classés, incomplets ou dont le
corrigé ne couvre pas les questions détectées automatiquement.

Remarque : la validation des questions reste heuristique. En mathématiques,
l'extraction PDF peut mal segmenter les sous-questions ; le rapport signale les
écarts sans prétendre remplacer une relecture humaine.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# =============================================================================
# Projet / configuration
# =============================================================================

def find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "site").exists() and (parent / "scripts").exists():
            return parent
    return Path.cwd()


ROOT = find_project_root()
SCRIPTS_DIR = ROOT / "scripts"
SITE_DIR = ROOT / "site"
DATA_DIR = SITE_DIR / "data"
RAW_DEFAULT = DATA_DIR / "intermediate" / "exercises_raw.json"
GENERATED_DIR_DEFAULT = DATA_DIR / "generated" / "exercises"
GENERATED_AGGREGATE_DEFAULT = DATA_DIR / "generated" / "exercices.json"
SITE_EXERCISES_DEFAULT = DATA_DIR / "exercices.json"
COURSES_DEFAULT = DATA_DIR / "cours.json"
REPORTS_DIR = DATA_DIR / "rapports"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import config  # type: ignore
except Exception:
    config = None  # type: ignore

ALLOWED_THEMES: dict[str, str] = dict(getattr(config, "ALLOWED_THEMES", {})) if config else {}
THEME_TO_DOMAIN: dict[str, str] = dict(getattr(config, "THEME_TO_DOMAIN", {})) if config else {}
PROGRAMME_DOMAINS: dict[str, str] = dict(getattr(config, "PROGRAMME_DOMAINS", {})) if config else {}
THEME_ORDER: list[str] = list(getattr(config, "THEME_ORDER", list(ALLOWED_THEMES))) if config else list(ALLOWED_THEMES)
DIFFICULTY_MIN = int(getattr(config, "DIFFICULTY_MIN", 1)) if config else 1
DIFFICULTY_MAX = int(getattr(config, "DIFFICULTY_MAX", 3)) if config else 3
ALLOWED_DIFFICULTY = set(range(DIFFICULTY_MIN, DIFFICULTY_MAX + 1))

if not ALLOWED_THEMES:
    # Repli défensif : évite que le script plante si config.py n'a pas encore été remplacé.
    ALLOWED_THEMES = {
        "combinatoire-denombrement": "Combinatoire et dénombrement",
        "geometrie-vecteurs-espace": "Vecteurs, droites et plans de l’espace",
        "geometrie-orthogonalite-distances": "Orthogonalité et distances dans l’espace",
        "geometrie-reperage": "Représentations paramétriques et équations cartésiennes",
        "suites": "Suites",
        "limites-fonctions": "Limites de fonctions",
        "derivation-convexite": "Dérivation et convexité",
        "continuite": "Continuité et théorème des valeurs intermédiaires",
        "logarithme": "Fonction logarithme",
        "trigonometrie": "Fonctions sinus et cosinus",
        "primitives-equations-differentielles": "Primitives et équations différentielles",
        "calcul-integral": "Calcul intégral",
        "bernoulli-binomiale": "Schéma de Bernoulli et loi binomiale",
        "variables-aleatoires": "Sommes de variables aléatoires",
        "concentration-grands-nombres": "Concentration et loi des grands nombres",
        "algorithmique-python": "Algorithmique et Python",
        "logique-raisonnement": "Logique, ensembles et raisonnement",
    }
    THEME_ORDER = list(ALLOWED_THEMES)


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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


# =============================================================================
# Normalisation simple
# =============================================================================

def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def non_empty_list(value: Any) -> bool:
    return any(str(v).strip() for v in as_list(value))


def normalize_theme(value: Any) -> str:
    s = str(value or "").strip()
    if s in ALLOWED_THEMES:
        return s

    s = s.lower().replace("_", "-").replace(" ", "-")
    replacements = {
        "à": "a", "â": "a", "ä": "a",
        "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
    }
    for src, dst in replacements.items():
        s = s.replace(src, dst)
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s if s in ALLOWED_THEMES else ""


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def text_length(value: Any) -> int:
    return len(str(value or "").strip())


# =============================================================================
# Normalisation des identifiants de questions
# =============================================================================

_ROMAN = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
}


def norm_qid(value: Any) -> str:
    """
    Normalise les numéros de questions mathématiques.

    Exemples :
    - Q1 -> 1
    - Question 1.a -> 1.a
    - Partie A — 2.b -> a.2.b
    - A.1.b -> a.1.b
    - 1° a) -> 1.a
    - global -> global
    """
    s = str(value or "").strip().lower()
    if not s:
        return ""

    s = (
        s.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .replace("’", "'")
    )
    s = s.replace("°", ".")
    s = re.sub(r"\s+", "", s)

    # Quelques libellés globaux acceptés.
    compact = re.sub(r"[^a-z0-9]+", "", s)
    aliases = {
        "global": "global",
        "ensemble": "global",
        "exercice": "global",
        "probleme": "problème",
        "problem": "problème",
        "qcm": "qcm",
    }
    if compact in aliases:
        return aliases[compact]

    # Partie A / Partie B.
    part = ""
    m_part = re.match(r"(?:partie|part|part\.)?([abcde])[-_.:]?", s)
    if m_part and (s.startswith("part") or re.match(r"^[abcde][-.]", s)):
        part = m_part.group(1)
        s = s[m_part.end():]

    s = s.replace("question", "")
    s = re.sub(r"^q\.?", "", s)
    s = re.sub(r"^n[°o]\.?", "", s)

    # Roman seul ou en tête.
    for roman, arabic in sorted(_ROMAN.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.match(rf"^{roman}(?:[.\-:]|$)", s):
            s = re.sub(rf"^{roman}", arabic, s, count=1)
            break

    # Convertit 1a / 1.a / 1-a / 1)a en 1.a.
    tokens = re.findall(r"\d+|[a-z]", s)
    if not tokens:
        return part or aliases.get(compact, compact)

    # On limite aux motifs utiles : partie, nombre, lettre, nombre...
    useful: list[str] = []
    for token in tokens:
        if token.isdigit() or re.fullmatch(r"[a-z]", token):
            useful.append(token)
        if len(useful) >= 4:
            break

    if not useful:
        return part

    if part:
        useful.insert(0, part)

    return ".".join(useful).strip(".")


def split_question_string(value: Any) -> list[str]:
    """Convertit questions_detectees en liste normalisée."""
    if value is None:
        return []
    if isinstance(value, list):
        out = [norm_qid(x) for x in value]
        return [x for x in out if x]

    s = str(value)
    if not s.strip():
        return []

    parts = re.split(r"[;,|]", s)
    out = [norm_qid(p) for p in parts]
    return [x for x in out if x]


def corrige_question_ids(generated: dict[str, Any]) -> list[str]:
    corrige = generated.get("corrige") or {}
    questions = corrige.get("questions") if isinstance(corrige, dict) else None
    if not isinstance(questions, list):
        return []

    ids: list[str] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = norm_qid(q.get("numero") or q.get("numéro") or q.get("question"))
        if qid:
            ids.append(qid)
    return ids


def qid_covers(expected: str, got: str) -> bool:
    """Couverture souple d'une question détectée par une question corrigée."""
    expected = norm_qid(expected)
    got = norm_qid(got)

    if not expected or not got:
        return False

    if got in {"global", "qcm"}:
        return True

    if expected == got:
        return True

    # 1 est couvert par 1.a, mais 1.a peut aussi être couvert par 1 si Claude a regroupé.
    if got.startswith(expected + "."):
        return True
    if expected.startswith(got + "."):
        return True

    # Partie A - 1.a vs 1.a : on autorise la perte du préfixe de partie dans le rapport.
    expected_tail = ".".join(expected.split(".")[1:]) if re.match(r"^[a-z]\.", expected) else ""
    got_tail = ".".join(got.split(".")[1:]) if re.match(r"^[a-z]\.", got) else ""
    if expected_tail and expected_tail == got:
        return True
    if got_tail and got_tail == expected:
        return True

    return False


def expected_covered(expected: str, got_ids: list[str]) -> bool:
    return any(qid_covers(expected, got) for got in got_ids)


def got_is_excess(got: str, expected_ids: list[str]) -> bool:
    if got in {"global", "qcm"}:
        return False
    return not any(qid_covers(exp, got) for exp in expected_ids)


# =============================================================================
# Chargement des générations
# =============================================================================

def load_generated_from_dir(generated_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not generated_dir.exists():
        return out

    for p in sorted(generated_dir.glob("*.json")):
        data = read_json(p)
        if isinstance(data, dict) and data.get("id"):
            out[str(data["id"])] = data

    return out


def load_generated_from_aggregate(path: Path) -> dict[str, dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


def load_generated(generated_dir: Path, aggregate_paths: list[Path]) -> tuple[dict[str, dict[str, Any]], str]:
    """Charge les générations, priorité aux fichiers unitaires."""
    generated = load_generated_from_dir(generated_dir)
    if generated:
        return generated, str(generated_dir)

    for path in aggregate_paths:
        generated = load_generated_from_aggregate(path)
        if generated:
            return generated, str(path)

    return {}, ""


# =============================================================================
# Validation d'un exercice
# =============================================================================

def validate_theme(raw: dict[str, Any], generated: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    theme = normalize_theme(generated.get("thematique_id"))

    if not theme:
        errors.append("thematique_invalide")
        return

    if generated.get("thematique_id") != theme:
        warnings.append("thematique_normalisable")

    domain_expected = THEME_TO_DOMAIN.get(theme, "")
    domain_got = str(generated.get("domaine_id", "")).strip()
    if domain_expected and domain_got and domain_expected != domain_got:
        errors.append("domaine_incoherent")

    raw_hint = normalize_theme(raw.get("theme_hint"))
    if raw_hint and raw_hint != theme:
        warnings.append(f"theme_different_du_hint:{raw_hint}->{theme}")

    secondary = generated.get("themes_secondaires")
    for secondary_theme in as_list(secondary):
        if secondary_theme and not normalize_theme(secondary_theme):
            warnings.append(f"theme_secondaire_invalide:{secondary_theme}")


def validate_required_content(generated: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    diff = as_int(generated.get("difficulte"))
    if diff not in ALLOWED_DIFFICULTY:
        errors.append("difficulte_invalide")

    if text_length(generated.get("titre")) < 3:
        errors.append("titre_absent")

    if text_length(generated.get("resume_enonce")) < 40:
        warnings.append("resume_enonce_absent_ou_trop_court")

    if not non_empty_list(generated.get("notions")):
        errors.append("notions_absentes")

    if not non_empty_list(generated.get("mots_cles")):
        errors.append("mots_cles_absents")

    if not non_empty_list(generated.get("competences")):
        warnings.append("competences_absentes")

    if not non_empty_list(generated.get("aide")) and not non_empty_list(generated.get("methodes")):
        warnings.append("aide_et_methodes_absentes")

    if not non_empty_list(generated.get("points_vigilance")):
        warnings.append("points_vigilance_absents")

    if not generated.get("page_images"):
        errors.append("page_images_absentes")


def validate_corrige(raw: dict[str, Any], generated: dict[str, Any], errors: list[str], warnings: list[str], detail: dict[str, Any]) -> None:
    corrige = generated.get("corrige") or {}
    qs = corrige.get("questions") if isinstance(corrige, dict) else None

    if not isinstance(qs, list) or not qs:
        errors.append("corrige_absent")
        return

    expected = split_question_string(raw.get("questions_detectees"))
    got = corrige_question_ids(generated)

    detail["questions_detectees"] = ",".join(expected)
    detail["questions_corrigees"] = ",".join(got)

    if expected and got:
        missing = [q for q in expected if not expected_covered(q, got)]
        excess = [q for q in got if got_is_excess(q, expected)]

        if missing:
            warnings.append("questions_corrige_possiblement_manquantes:" + ",".join(missing))
            detail["questions_manquantes"] = ",".join(missing)

        if excess:
            detail["questions_exces"] = ",".join(excess)

    elif expected and not got:
        warnings.append("questions_corrigees_non_identifiables")

    empty_answers: list[str] = []
    short_answers: list[str] = []
    for q in qs:
        if not isinstance(q, dict):
            short_answers.append("question_non_structuree")
            continue

        qid = str(q.get("numero", "")).strip()
        answer = str(q.get("reponse", q.get("réponse", ""))).strip()
        method = str(q.get("methode", q.get("méthode", ""))).strip()

        if len(answer) == 0:
            empty_answers.append(qid or "?")
        elif len(answer) < 40:
            short_answers.append(qid or "?")

        if len(method) == 0:
            # Non bloquant : certaines réponses intègrent directement la méthode.
            pass

    if empty_answers:
        errors.append("reponses_vides:" + ",".join(empty_answers[:10]))
    if short_answers:
        warnings.append("reponses_tres_courtes:" + ",".join(short_answers[:10]))


def validate_raw(raw: dict[str, Any], warnings: list[str]) -> None:
    if not raw.get("page_images"):
        warnings.append("raw_page_images_absentes")
    if text_length(raw.get("texte_extrait")) < 100:
        warnings.append("raw_texte_extrait_court")
    if raw.get("segmentation_warning"):
        warnings.append("segmentation_warning:" + str(raw.get("segmentation_warning")))


def validate_exercise(raw: dict[str, Any], generated: dict[str, Any] | None) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    detail: dict[str, Any] = {}

    validate_raw(raw, warnings)

    if generated is None:
        return ["generation_absente"], warnings, detail

    if generated.get("id") != raw.get("id"):
        errors.append("id_mismatch")

    validate_theme(raw, generated, errors, warnings)
    validate_required_content(generated, errors, warnings)
    validate_corrige(raw, generated, errors, warnings, detail)

    generation = generated.get("generation") or {}
    if not isinstance(generation, dict) or generation.get("statut") not in {"genere", "généré", "generated"}:
        warnings.append("metadata_generation_absente")

    return errors, warnings, detail


# =============================================================================
# Validation des cours et couverture thématique
# =============================================================================

def validate_courses(courses_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not courses_path.exists():
        rows.append({"fichier": str(courses_path), "theme": "", "status": "a_revoir", "erreurs": "cours_absent"})
        return rows

    data = read_json(courses_path)
    if not isinstance(data, dict):
        rows.append({"fichier": str(courses_path), "theme": "", "status": "a_revoir", "erreurs": "cours_json_invalide"})
        return rows

    # On n'exige pas encore un cours pour tous les thèmes tant que 05_generate_courses.py n'est pas adapté.
    for theme_id in THEME_ORDER:
        item = data.get(theme_id)
        if item is None:
            rows.append({"fichier": str(courses_path), "theme": theme_id, "status": "a_revoir", "erreurs": "cours_theme_absent"})
            continue
        if isinstance(item, str):
            ok = len(item.strip()) >= 300
        elif isinstance(item, dict):
            ok = len(json.dumps(item, ensure_ascii=False)) >= 300
        else:
            ok = False
        rows.append({"fichier": str(courses_path), "theme": theme_id, "status": "ok" if ok else "a_revoir", "erreurs": "" if ok else "cours_trop_court"})

    return rows


def build_theme_coverage(raw_exercises: list[dict[str, Any]], generated_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_hint_counts = Counter()
    generated_counts = Counter()
    review_counts = Counter()

    for raw in raw_exercises:
        hint = normalize_theme(raw.get("theme_hint")) or "non_classe"
        raw_hint_counts[hint] += 1

        gen = generated_by_id.get(str(raw.get("id", "")))
        if gen:
            theme = normalize_theme(gen.get("thematique_id")) or "invalide"
            generated_counts[theme] += 1
        else:
            review_counts[hint] += 1

    keys = list(THEME_ORDER)
    for extra in sorted(set(raw_hint_counts) | set(generated_counts) | set(review_counts)):
        if extra not in keys:
            keys.append(extra)

    rows: list[dict[str, Any]] = []
    for theme in keys:
        rows.append(
            {
                "theme": theme,
                "label": ALLOWED_THEMES.get(theme, theme),
                "raw_hint_count": raw_hint_counts.get(theme, 0),
                "generated_count": generated_counts.get(theme, 0),
                "missing_generation_count": review_counts.get(theme, 0),
            }
        )
    return rows


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validation des exercices mathématiques générés.")
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--generated-dir", type=Path, default=GENERATED_DIR_DEFAULT)
    parser.add_argument(
        "--generated-aggregate",
        type=Path,
        default=GENERATED_AGGREGATE_DEFAULT,
        help="Fichier agrégé de secours. Par défaut : site/data/generated/exercices.json",
    )
    parser.add_argument(
        "--site-exercises",
        type=Path,
        default=SITE_EXERCISES_DEFAULT,
        help="Fichier site de secours. Par défaut : site/data/exercices.json",
    )
    parser.add_argument("--courses", type=Path, default=COURSES_DEFAULT)
    parser.add_argument("--fail-on-review", action="store_true", help="Retourne un code 1 s'il existe des exercices à revoir.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    raw_exercises = read_json(args.raw)
    if not isinstance(raw_exercises, list):
        raise RuntimeError(f"Fichier brut invalide : {args.raw}")

    generated_by_id, generated_source = load_generated(
        args.generated_dir,
        [args.generated_aggregate, args.site_exercises],
    )

    rows: list[dict[str, Any]] = []
    summary = {
        "raw_file": str(args.raw),
        "generated_source": generated_source,
        "total_exercices_bruts": len(raw_exercises),
        "fichiers_ou_entrees_generes": len(generated_by_id),
        "valides": 0,
        "a_revoir": 0,
        "bloquants": 0,
        "erreurs_par_type": {},
        "avertissements_par_type": {},
    }

    err_counter: Counter[str] = Counter()
    warn_counter: Counter[str] = Counter()

    for raw in raw_exercises:
        if not isinstance(raw, dict):
            continue

        ex_id = str(raw.get("id", ""))
        gen = generated_by_id.get(ex_id)
        errors, warnings, detail = validate_exercise(raw, gen)

        status = "ok" if not errors and not warnings else "a_revoir"
        if errors:
            summary["bloquants"] += 1

        if status == "ok":
            summary["valides"] += 1
        else:
            summary["a_revoir"] += 1

        for e in errors:
            err_counter[e.split(":")[0]] += 1
        for w in warnings:
            warn_counter[w.split(":")[0]] += 1

        theme = normalize_theme((gen or {}).get("thematique_id")) if gen else ""
        domain = (gen or {}).get("domaine_id", "") if gen else ""

        rows.append(
            {
                "id": ex_id,
                "source_id": raw.get("source_id", ""),
                "annee": raw.get("annee", ""),
                "session": raw.get("session", ""),
                "zone": raw.get("zone", ""),
                "numero": raw.get("numero", raw.get("type", "")),
                "pages": "-".join(map(str, raw.get("pages", []))),
                "titre": (gen or raw).get("titre", ""),
                "theme_hint": raw.get("theme_hint", ""),
                "thematique_id": theme,
                "domaine_id": domain,
                "difficulte": (gen or {}).get("difficulte", "") if gen else "",
                "questions_detectees": detail.get("questions_detectees", ",".join(split_question_string(raw.get("questions_detectees")))),
                "questions_corrigees": detail.get("questions_corrigees", ""),
                "questions_manquantes": detail.get("questions_manquantes", ""),
                "questions_exces": detail.get("questions_exces", ""),
                "status": status,
                "erreurs": " | ".join(errors),
                "avertissements": " | ".join(warnings),
            }
        )

    summary["erreurs_par_type"] = dict(sorted(err_counter.items()))
    summary["avertissements_par_type"] = dict(sorted(warn_counter.items()))

    course_rows = validate_courses(args.courses)
    coverage_rows = build_theme_coverage(raw_exercises, generated_by_id)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    write_json(
        REPORTS_DIR / "validation.json",
        {
            "summary": summary,
            "exercices": rows,
            "couverture_themes": coverage_rows,
            "cours": course_rows,
        },
    )

    write_csv(
        REPORTS_DIR / "validation.csv",
        rows,
        [
            "id",
            "source_id",
            "annee",
            "session",
            "zone",
            "numero",
            "pages",
            "titre",
            "theme_hint",
            "thematique_id",
            "domaine_id",
            "difficulte",
            "questions_detectees",
            "questions_corrigees",
            "questions_manquantes",
            "questions_exces",
            "status",
            "erreurs",
            "avertissements",
        ],
    )

    write_csv(
        REPORTS_DIR / "validation_theme_coverage.csv",
        coverage_rows,
        ["theme", "label", "raw_hint_count", "generated_count", "missing_generation_count"],
    )

    write_csv(
        REPORTS_DIR / "validation_cours.csv",
        course_rows,
        ["fichier", "theme", "status", "erreurs"],
    )

    print("Rapport JSON :", REPORTS_DIR / "validation.json")
    print("Rapport CSV  :", REPORTS_DIR / "validation.csv")
    print("Thèmes CSV   :", REPORTS_DIR / "validation_theme_coverage.csv")
    print("Cours CSV    :", REPORTS_DIR / "validation_cours.csv")
    print(summary)

    if args.fail_on_review and summary["a_revoir"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
