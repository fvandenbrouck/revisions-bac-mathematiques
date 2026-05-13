#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_segment_exercises.py

Segmente les sujets de spécialité mathématiques en exercices.

Entrée : site/data/raw/pages/<source_id>.json
Sorties :
- site/data/intermediate/exercises_raw.json
- site/data/rapports/segmentation_diagnostic.json
- site/data/rapports/segmentation_diagnostic.csv
- site/data/rapports/segmentation_counts.csv

Version corrigée :
- détecte les exercices numérotés : Exercice 1, Exercice 2, etc. ;
- détecte les exercices au choix : EXERCICE A / EXERCICE - A / Exercice B ;
- utilise "EXERCICE au choix du candidat" comme frontière de segmentation sans en faire un exercice ;
- évite de fusionner le dernier exercice commun avec les exercices A/B dans les sujets anciens.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from config import EXERCISES_RAW_JSON, MANIFEST_CSV, PAGES_DIR, REPORTS_DIR
from utils import detect_question_ids, normalize_space, read_csv_dicts, read_json, write_json

try:
    from config import ALLOWED_THEMES, THEME_TO_DOMAIN
except Exception:  # compatibilité si config.py n'a pas encore été remplacé
    ALLOWED_THEMES = {}
    THEME_TO_DOMAIN = {}


# ---------------------------------------------------------------------------
# Regex principales
# ---------------------------------------------------------------------------

# Expressions de cadrage qui ne sont PAS des débuts d'exercice.
EXCLUSION_RE = re.compile(
    r"("
    r"exercices?\s+propos[ée]s?|"
    r"choisit\s+3\s+exercices|"
    r"choisir\s+3\s+exercices|"
    r"parmi\s+les\s+4\s+exercices|"
    r"quatre\s+exercices\s+propos[ée]s"
    r")",
    re.IGNORECASE,
)

CHOICE_INTRO_RE = re.compile(
    r"^\s*(?:EXERCICE|Exercice)s?\s+au\s+choix\s+du\s+candidat\b.*$",
    re.IGNORECASE,
)

CHOICE_INSTRUCTION_RE = re.compile(
    r"^\s*Le\s+candidat\s+doit\s+traiter\s+(?:UN\s+SEUL|un\s+seul)\b.*$",
    re.IGNORECASE,
)

# Préfixes possibles issus de l'extraction texte :
# 24-MATJ1ME1 page 2sur 6 ; 22 – MATJ1ME1 page 2 /5 ; 22-MATJ1AN1 Page : 2/5
PAGE_PREFIX_RE = re.compile(
    r"^\s*"
    r"(?:\d{2}\s*[-–— ]?\s*MAT[A-Z0-9\-–— ]{0,25}\s+)?"
    r"(?:Page\s*:?[\s]*(?:sur\s*\d+\s*)?\d+\s*(?:/\s*\d+)?\s*)?",
    re.IGNORECASE,
)

PAGE_HEADER_RE = re.compile(
    r"^\s*\d{2}\s*[-–— ]?\s*MAT[A-Z0-9\-–— ]{0,25}\s+page\b.*$",
    re.IGNORECASE,
)

NUMBERED_MARKER_RE = re.compile(
    r"^\s*(?:EXERCICE|Exercice)\s+"
    # Accepte : Exercice 1, EXERCICE 1, Exercice n°1, Exercice nº 1, Exercice no 1
    r"(?:n\s*[°ºo]\s*)?"
    r"(?P<num>[1-9]|IV|III|II|I|V)\b"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)

CHOICE_LETTER_MARKER_RE = re.compile(
    r"^\s*(?:EXERCICE|Exercice)\s*[-–—:]?\s*(?P<letter>[AB])\b(?P<rest>.*)$",
    re.IGNORECASE,
)

POINTS_RE = re.compile(r"\((?P<points>\d{1,2})\s*points?\)", re.IGNORECASE)
THEME_RE = re.compile(r"Th[èe]mes?\s*:\s*(?P<theme>.+)$", re.IGNORECASE)
DROP_LINE_RE = re.compile(r"^(?:Tourner\s+la\s+page\.?|Fin\s+du\s+sujet\.?)$", re.IGNORECASE)

ROMAN_TO_INT = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}

THEME_ALIASES = {
    "probabilite": "bernoulli-binomiale",
    "probabilites": "bernoulli-binomiale",
    "denombrement": "combinatoire-denombrement",
    "combinatoire": "combinatoire-denombrement",
    "suites": "suites",
    "suite": "suites",
    "geometrie dans l espace": "geometrie-reperage",
    "geometrie": "geometrie-reperage",
    "fonction exponentielle": "derivation-convexite",
    "fonctions numeriques": "derivation-convexite",
    "fonction numerique": "derivation-convexite",
    "convexite": "derivation-convexite",
    "logarithme": "logarithme",
    "integrale": "calcul-integral",
    "equation differentielle": "primitives-equations-differentielles",
    "algorithmique": "algorithmique-python",
}


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def enabled(row: dict[str, str]) -> bool:
    return str(row.get("enabled", "1")).strip().lower() not in {"0", "false", "non", "no"}


def ascii_fold(value: str) -> str:
    value = value.lower().replace("œ", "oe").replace("æ", "ae")
    replacements = {
        "à": "a", "â": "a", "ä": "a",
        "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ÿ": "y",
        "`": "",
        "´": "",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_marker_line(line: str) -> str:
    line = normalize_space(line or "")
    line = PAGE_PREFIX_RE.sub("", line).strip()
    line = re.sub(r"^\s*[-–—:]+\s*", "", line)
    return line


def clean_text_block(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if PAGE_HEADER_RE.match(line) or DROP_LINE_RE.match(line):
            continue
        lines.append(line)

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def slice_lines(lines: list[str], start: int | None = None, end: int | None = None) -> str:
    s = 0 if start is None else max(0, start)
    e = len(lines) if end is None else max(0, end)
    return "\n".join(lines[s:e])


def all_source_text(source: dict[str, Any]) -> str:
    return "\n\n".join(str(p.get("text", "")) for p in source.get("pages", []))


def marker_sort_key(marker: dict[str, Any]) -> tuple[int, int, str]:
    return (int(marker.get("page", 0)), int(marker.get("line_index", 0)), str(marker.get("kind", "")))


def is_after(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (int(a["page"]), int(a["line_index"])) > (int(b["page"]), int(b["line_index"]))


# ---------------------------------------------------------------------------
# Détection des marqueurs d'exercices et frontières
# ---------------------------------------------------------------------------

def parse_marker(line: str, page_no: int, line_index: int) -> dict[str, Any] | None:
    original = line
    line = clean_marker_line(line)

    if not line or len(line) > 280:
        return None

    if CHOICE_INTRO_RE.match(line):
        return {
            "page": page_no,
            "line_index": line_index,
            "line": original,
            "label": line,
            "kind": "choice_intro",
            "number": None,
            "type": "choice_intro",
            "is_exercise_start": False,
        }

    if CHOICE_INSTRUCTION_RE.match(line):
        return {
            "page": page_no,
            "line_index": line_index,
            "line": original,
            "label": line,
            "kind": "choice_instruction",
            "number": None,
            "type": "choice_instruction",
            "is_exercise_start": False,
        }

    if EXCLUSION_RE.search(line):
        return None

    m_letter = CHOICE_LETTER_MARKER_RE.match(line)
    if m_letter:
        letter = m_letter.group("letter").strip().upper()
        return {
            "page": page_no,
            "line_index": line_index,
            "line": original,
            "label": line,
            "kind": "choice_letter",
            "number": letter,
            "type": letter,
            "is_exercise_start": True,
        }

    m_num = NUMBERED_MARKER_RE.match(line)
    if m_num:
        token = m_num.group("num").strip().upper()
        number = int(token) if token.isdigit() else ROMAN_TO_INT.get(token)
        if number is None or not (1 <= number <= 9):
            return None
        return {
            "page": page_no,
            "line_index": line_index,
            "line": original,
            "label": line,
            "kind": "numbered",
            "number": number,
            "type": str(number),
            "is_exercise_start": True,
        }

    return None


def find_candidates(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for p in pages:
        page_no = int(p.get("page", 0))
        for idx, line in enumerate((p.get("text", "") or "").splitlines()):
            marker = parse_marker(line, page_no, idx)
            if marker:
                candidates.append(marker)

    seen: set[tuple[int, int, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for c in candidates:
        sig = (
            int(c["page"]),
            int(c["line_index"]),
            str(c.get("kind", "")),
            str(c.get("label", "")).lower(),
        )
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(c)

    return sorted(deduped, key=marker_sort_key)


def select_starts(candidates: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Sélectionne les vrais débuts d'exercices.

    Les marqueurs choice_intro / choice_instruction restent des frontières, mais ne
    deviennent pas des exercices. Les exercices A/B ne sont retenus que si un contexte
    d'exercice au choix est détecté dans le sujet.
    """
    subject_has_choice = any(c.get("kind") in {"choice_intro", "choice_instruction"} for c in candidates)

    selected: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    seen_letters: set[str] = set()
    choice_context_seen = False

    for c in candidates:
        kind = c.get("kind")

        if kind in {"choice_intro", "choice_instruction"}:
            choice_context_seen = True
            continue

        if kind == "numbered":
            n = int(c["number"])
            if n not in seen_numbers:
                seen_numbers.add(n)
                selected.append(dict(c))
            continue

        if kind == "choice_letter":
            letter = str(c["number"]).upper()
            if letter not in seen_letters and (subject_has_choice or choice_context_seen):
                seen_letters.add(letter)
                selected.append(dict(c))
            continue

    if not selected:
        return "unsegmented", []

    selected.sort(key=marker_sort_key)
    numeric = [int(s["number"]) for s in selected if s.get("kind") == "numbered"]
    letters = [str(s["number"]) for s in selected if s.get("kind") == "choice_letter"]

    if letters:
        if numeric == list(range(1, max(numeric) + 1)) and set(letters) == {"A", "B"}:
            mode = "numbered_plus_choice_ab"
        else:
            mode = "mixed_numbered_choice"
    elif numeric == [1, 2, 3, 4]:
        mode = "numbered_4"
    elif numeric == list(range(min(numeric), max(numeric) + 1)):
        mode = "numbered_continuous"
    else:
        mode = "numbered_discontinuous"

    return mode, selected


def next_boundary_after(start_marker: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for c in sorted(candidates, key=marker_sort_key):
        if is_after(c, start_marker):
            return c
    return None


def previous_choice_intro(start_marker: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    prev = None
    for c in sorted(candidates, key=marker_sort_key):
        if not is_after(start_marker, c):
            break
        if c.get("kind") == "choice_intro":
            prev = c
    return prev


# ---------------------------------------------------------------------------
# Métadonnées d'exercice
# ---------------------------------------------------------------------------

def infer_points(label: str) -> int | str:
    m = POINTS_RE.search(label or "")
    return int(m.group("points")) if m else ""


def strip_exercise_label(label: str) -> str:
    s = POINTS_RE.sub("", label or "")
    s = re.sub(r"^(?:EXERCICE|Exercice)\s+(?:n\s*[°ºo]\s*)?(?:[1-9]|IV|III|II|I|V)\b", "", s, flags=re.I)
    s = re.sub(r"^(?:EXERCICE|Exercice)\s*[-–—:]?\s*[AB]\b", "", s, flags=re.I)
    s = re.sub(r"^[\s:;.,\-–—]+", "", s)
    return normalize_space(s)


def explicit_theme_from_label(label: str) -> str:
    m = THEME_RE.search(label or "")
    return normalize_space(m.group("theme").strip(" .")) if m else ""


def infer_theme_hint(label: str, text: str) -> str:
    explicit = ascii_fold(explicit_theme_from_label(label))
    haystack = ascii_fold("\n".join([label or "", text or ""]))

    for key, theme in THEME_ALIASES.items():
        if (explicit and key in explicit) or key in haystack:
            if not ALLOWED_THEMES or theme in ALLOWED_THEMES:
                return theme

    signals = [
        ("concentration-grands-nombres", ["bienayme", "tchebychev", "loi des grands nombres"]),
        ("variables-aleatoires", ["esperance", "variance", "ecart type", "variable aleatoire"]),
        ("bernoulli-binomiale", ["loi binomiale", "bernoulli", "schema de bernoulli"]),
        ("combinatoire-denombrement", ["denombrement", "combinaison", "tirages possibles", "coefficient binomial"]),
        ("geometrie-orthogonalite-distances", ["produit scalaire", "orthogonal", "projection", "distance", "vecteur normal"]),
        ("geometrie-reperage", ["representation parametrique", "equation cartesienne", "coordonnees"]),
        ("logarithme", [" ln ", "logarithme"]),
        ("trigonometrie", ["sinus", "cosinus", "trigonometr"]),
        ("calcul-integral", ["integrale", "valeur moyenne", "integration par parties"]),
        ("primitives-equations-differentielles", ["primitive", "equation differentielle"]),
        ("derivation-convexite", ["derivee", "convexe", "tangente", "inflexion"]),
        ("limites-fonctions", ["limite", "asymptote"]),
        ("suites", [" suite ", "recurrence", "u n", "u n 1"]),
        ("algorithmique-python", ["python", "while", "for ", "def ", "return"]),
    ]
    padded = f" {haystack} "
    for theme, needles in signals:
        if ALLOWED_THEMES and theme not in ALLOWED_THEMES:
            continue
        if any(needle in padded for needle in needles):
            return theme

    return ""


def infer_title(start_label: str, exercise_text: str, numero: int | str) -> str:
    rest = strip_exercise_label(start_label)
    if rest and not THEME_RE.search(rest) and 5 <= len(rest) <= 170:
        return rest

    if str(numero).upper() in {"A", "B"}:
        # Cherche les domaines principaux juste après le marqueur A/B.
        lines = [l.strip() for l in exercise_text.splitlines() if l.strip()]
        joined = " ".join(lines[:8])
        if "Principaux domaines" in joined or "Principaux domaines abord" in joined:
            return f"Exercice {str(numero).upper()} au choix"
        return f"Exercice {str(numero).upper()}"

    explicit_theme = explicit_theme_from_label(start_label)
    if explicit_theme:
        return explicit_theme[:170]

    bad = (
        "durée de l’épreuve",
        "usage de la calculatrice",
        "le candidat",
        "questionnaire à choix multiple",
        "chaque réponse",
        "exercice au choix du candidat",
    )
    for line in [l.strip() for l in exercise_text.splitlines() if l.strip()][:30]:
        candidate = strip_exercise_label(clean_marker_line(line))
        if 5 <= len(candidate) <= 170 and not any(b in candidate.lower() for b in bad):
            return candidate[:170]

    return "Exercice sans titre détecté"


def infer_subject_policy(source: dict[str, Any]) -> str:
    text = ascii_fold(all_source_text(source))
    if "exercice au choix du candidat" in text or "le candidat doit traiter un seul" in text:
        return "common_plus_choice_a_b"
    if "choisit 3 exercices parmi les 4" in text or "ne doit traiter que ces 3 exercices" in text:
        return "choice_3_of_4"
    if "doit traiter les quatre exercices" in text or "traite les quatre exercices" in text:
        return "all_4_exercises"
    if "le sujet propose 4 exercices" in text:
        return "four_exercises_policy_unclear"
    return "unknown"


def exercise_suffix(numero: int | str) -> str:
    return str(numero).strip().lower()


# ---------------------------------------------------------------------------
# Construction des segments
# ---------------------------------------------------------------------------

def build_exercise_text(
    pages_by_no: dict[int, dict[str, Any]],
    start_marker: dict[str, Any],
    next_marker: dict[str, Any] | None,
) -> tuple[list[int], list[str], str]:
    start_page = int(start_marker["page"])
    next_page = int(next_marker["page"]) if next_marker else None
    end_page = next_page if next_page is not None else max(pages_by_no)

    selected_pages: list[int] = []
    page_images: list[str] = []
    parts: list[str] = []

    for page_no in range(start_page, end_page + 1):
        page = pages_by_no.get(page_no)
        if not page:
            continue

        lines = (page.get("text", "") or "").splitlines()
        start_idx = int(start_marker["line_index"]) if page_no == start_page else None
        end_idx = int(next_marker["line_index"]) if next_marker and page_no == next_page else None
        chunk = clean_text_block(slice_lines(lines, start_idx, end_idx))

        if not chunk.strip():
            continue

        selected_pages.append(page_no)
        parts.append(chunk)
        img = page.get("image")
        if img and img not in page_images:
            page_images.append(img)

    return selected_pages, page_images, "\n\n".join(parts).strip()


def build_fallback_exercise(source: dict[str, Any], mode: str) -> dict[str, Any]:
    source_id = source["source_id"]
    pages = source.get("pages", [])
    text = clean_text_block(all_source_text(source))
    theme_hint = infer_theme_hint("", text)
    return {
        "id": f"ex-{source_id}-full",
        "source_id": source_id,
        "annee": source.get("annee", ""),
        "session": source.get("session", ""),
        "zone": source.get("zone", ""),
        "titre": source.get("titre", "Sujet complet non segmenté"),
        "type": "à découper",
        "numero": "",
        "points": "",
        "pages": [p.get("page") for p in pages],
        "page_images": [p["image"] for p in pages if p.get("image")],
        "texte_extrait": text,
        "questions_detectees": detect_question_ids(text),
        "theme_explicit": "",
        "theme_hint": theme_hint,
        "theme_domain_hint": THEME_TO_DOMAIN.get(theme_hint, "") if theme_hint else "",
        "segmentation_warning": "Aucun début d'exercice détecté automatiquement.",
        "segmentation_mode": mode,
        "subject_policy": infer_subject_policy(source),
    }


def segment_one(source: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_id = source["source_id"]
    pages = source.get("pages", [])
    pages_by_no = {int(p["page"]): p for p in pages}

    candidates = find_candidates(pages)
    mode, starts = select_starts(candidates)
    policy = infer_subject_policy(source)

    diagnostic: dict[str, Any] = {
        "source_id": source_id,
        "page_count": len(pages),
        "candidates": len(candidates),
        "selected": len(starts),
        "mode": mode,
        "subject_policy": policy,
        "candidate_labels": [c["label"] for c in candidates[:60]],
        "selected_labels": [s["label"] for s in starts],
    }

    if not starts:
        diagnostic["warning"] = "Aucun début d'exercice détecté."
        return [build_fallback_exercise(source, mode)], diagnostic

    exercises: list[dict[str, Any]] = []
    for start in starts:
        next_marker = next_boundary_after(start, candidates)
        selected_pages, page_images, text = build_exercise_text(pages_by_no, start, next_marker)

        numero: int | str = start["number"]
        label = start["label"]
        explicit_theme = explicit_theme_from_label(label)
        theme_hint = infer_theme_hint(label, text)

        points = infer_points(label)
        if points == "" and start.get("kind") == "choice_letter":
            intro = previous_choice_intro(start, candidates)
            if intro:
                points = infer_points(str(intro.get("label", "")))

        warning = ""
        if not text.strip():
            warning = "Texte extrait vide pour cet exercice."
        elif len(text) < 150:
            warning = "Texte extrait très court ; segmentation à vérifier."
        elif str(numero).upper() not in {"A", "B"} and (
            "EXERCICE au choix du candidat" in text
            or "Le candidat doit traiter UN SEUL" in text
            or "Le candidat doit traiter un seul" in text
        ):
            warning = "Exercice commun contenant encore un bloc au choix ; segmentation à vérifier."

        suffix = exercise_suffix(numero)
        exercises.append(
            {
                "id": f"ex-{source_id}-exercice-{suffix}",
                "source_id": source_id,
                "annee": source.get("annee", ""),
                "session": source.get("session", ""),
                "zone": source.get("zone", ""),
                "titre": infer_title(label, text, numero),
                "type": str(numero),
                "numero": numero,
                "points": points,
                "pages": selected_pages,
                "page_images": page_images,
                "texte_extrait": text,
                "questions_detectees": detect_question_ids(text),
                "theme_explicit": explicit_theme,
                "theme_hint": theme_hint,
                "theme_domain_hint": THEME_TO_DOMAIN.get(theme_hint, "") if theme_hint else "",
                "start_label": label,
                "start_page": int(start["page"]),
                "start_line_index": int(start["line_index"]),
                "segmentation_mode": mode,
                "segmentation_warning": warning,
                "subject_policy": policy,
            }
        )

    numeric = [ex["numero"] for ex in exercises if isinstance(ex.get("numero"), int)]
    letters = [str(ex["numero"]).upper() for ex in exercises if str(ex.get("numero", "")).upper() in {"A", "B"}]

    if numeric and numeric != list(range(min(numeric), max(numeric) + 1)):
        diagnostic["warning"] = f"Numérotation discontinue : {numeric}"
    elif policy == "common_plus_choice_a_b" and not {"A", "B"}.issubset(set(letters)):
        diagnostic["warning"] = f"Sujet avec exercices au choix, mais lettres détectées : {letters}"
    elif len(exercises) != 4 and policy in {"choice_3_of_4", "all_4_exercises", "four_exercises_policy_unclear"}:
        diagnostic["warning"] = f"Sujet annoncé avec 4 exercices, mais {len(exercises)} détecté(s)."

    return exercises, diagnostic


# ---------------------------------------------------------------------------
# Chargement et rapports
# ---------------------------------------------------------------------------

def load_manifest_index(manifest_path: Path) -> dict[str, dict[str, str]]:
    if not manifest_path.exists():
        return {}
    rows = [row for row in read_csv_dicts(manifest_path) if enabled(row)]
    return {row["id"]: row for row in rows if row.get("id")}


def page_json_files(pages_dir: Path, manifest_index: dict[str, dict[str, str]], only: str | None) -> list[Path]:
    if only:
        p = pages_dir / f"{only}.json"
        return [p] if p.exists() else []
    if manifest_index:
        return [pages_dir / f"{sid}.json" for sid in manifest_index if (pages_dir / f"{sid}.json").exists()]
    return sorted(pages_dir.glob("*.json"), key=lambda p: p.name.lower())


def enrich_source(source: dict[str, Any], manifest_index: dict[str, dict[str, str]]) -> dict[str, Any]:
    row = manifest_index.get(source.get("source_id", ""), {})
    for key in ["titre", "annee", "session", "zone", "pdf_path", "sha256"]:
        if not source.get(key) and row.get(key):
            source[key] = row[key]
    return source


def write_report(diagnostics: list[dict[str, Any]], all_exercises: list[dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(REPORTS_DIR / "segmentation_diagnostic.json", diagnostics)

    with (REPORTS_DIR / "segmentation_diagnostic.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["source_id", "mode", "subject_policy", "page_count", "candidates", "selected", "selected_labels", "warning"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in diagnostics:
            writer.writerow({
                "source_id": d.get("source_id", ""),
                "mode": d.get("mode", ""),
                "subject_policy": d.get("subject_policy", ""),
                "page_count": d.get("page_count", ""),
                "candidates": d.get("candidates", ""),
                "selected": d.get("selected", ""),
                "selected_labels": " | ".join(d.get("selected_labels", [])),
                "warning": d.get("warning", ""),
            })

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ex in all_exercises:
        by_source[ex["source_id"]].append(ex)

    with (REPORTS_DIR / "segmentation_counts.csv").open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["source_id", "nb_exercices", "numeros", "points", "pages", "theme_hints", "titres", "warnings"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for source_id, exercises in sorted(by_source.items()):
            writer.writerow({
                "source_id": source_id,
                "nb_exercices": len(exercises),
                "numeros": " | ".join(str(ex.get("numero", "")) for ex in exercises),
                "points": " | ".join(str(ex.get("points", "")) for ex in exercises),
                "pages": " | ".join(",".join(str(p) for p in ex.get("pages", [])) for ex in exercises),
                "theme_hints": " | ".join(str(ex.get("theme_hint", "")) for ex in exercises),
                "titres": " | ".join(str(ex.get("titre", "")) for ex in exercises),
                "warnings": " | ".join(str(ex.get("segmentation_warning", "")) for ex in exercises if ex.get("segmentation_warning")),
            })


def print_summary(diagnostics: list[dict[str, Any]], all_exercises: list[dict[str, Any]]) -> None:
    print(f"\nSujets segmentés : {len(diagnostics)}")
    print(f"Exercices produits : {len(all_exercises)}")
    print("\nModes :")
    for key, count in sorted(Counter(d.get("mode", "unknown") for d in diagnostics).items()):
        print(f"  {key}: {count}")
    print("\nRépartition du nombre d'exercices par sujet :")
    by_source = Counter(ex["source_id"] for ex in all_exercises)
    for nb, count in sorted(Counter(by_source.values()).items()):
        print(f"  {nb} exercice(s): {count} sujet(s)")
    warnings = [d for d in diagnostics if d.get("warning")]
    if warnings:
        print(f"\nAvertissements : {len(warnings)}")
        for d in warnings[:20]:
            print(f"  - {d.get('source_id')}: {d.get('warning')}")
    else:
        print("\nAucun avertissement de segmentation.")
    print(f"\nJSON exercices : {EXERCISES_RAW_JSON}")
    print(f"Rapports : {REPORTS_DIR}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Segmente les JSON de pages en exercices de mathématiques.")
    parser.add_argument("--pages-dir", type=Path, default=PAGES_DIR)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_CSV)
    parser.add_argument("--output", type=Path, default=EXERCISES_RAW_JSON)
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Conservé pour cohérence ; la sortie est toujours réécrite.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.pages_dir.exists():
        print(f"ERREUR dossier de pages introuvable : {args.pages_dir}", file=sys.stderr)
        return 2

    manifest_index = load_manifest_index(args.manifest)
    files = page_json_files(args.pages_dir, manifest_index, args.only)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("ERREUR aucun JSON de pages à segmenter.", file=sys.stderr)
        return 2

    diagnostics: list[dict[str, Any]] = []
    all_exercises: list[dict[str, Any]] = []
    errors = 0

    for i, file in enumerate(files, start=1):
        try:
            source = enrich_source(read_json(file), manifest_index)
            source_id = source.get("source_id", file.stem)
            print(f"[{i:03d}/{len(files):03d}] segmentation : {source_id}")
            exercises, diagnostic = segment_one(source)
            diagnostics.append(diagnostic)
            all_exercises.extend(exercises)
            print({
                "source_id": source_id,
                "status": "ok",
                "exercices": len(exercises),
                "mode": diagnostic.get("mode"),
                "selected": diagnostic.get("selected"),
                "warning": diagnostic.get("warning", ""),
            })
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print({"file": str(file), "status": "error", "error": str(exc)}, file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, all_exercises)
    write_report(diagnostics, all_exercises)
    print_summary(diagnostics, all_exercises)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
