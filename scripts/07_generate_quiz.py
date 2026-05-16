#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_generate_quiz.py

Génère des quiz de révision pour la spécialité mathématiques de Terminale,
à partir des fiches de cours produites dans site/data/cours.json.

Sortie principale :
    site/data/quiz.json

Options utiles :
    python scripts/07_generate_quiz.py --dry-run --only suites
    python scripts/07_generate_quiz.py --only suites
    python scripts/07_generate_quiz.py --questions-per-theme 10
    python scripts/07_generate_quiz.py --force
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    from anthropic import Anthropic
except Exception:  # pragma: no cover
    Anthropic = None  # type: ignore

from config import (  # type: ignore
    ALLOWED_THEMES,
    CLAUDE_MAX_TOKENS_QUIZ,
    CLAUDE_MODEL,
    CLAUDE_TEMPERATURE,
    COURSES_JSON,
    QUIZ_JSON,
    THEME_DESCRIPTIONS,
    THEME_KEY_RELATIONS,
    THEME_ORDER,
)

try:
    from config import (  # type: ignore
        DEFAULT_QUESTIONS_PER_THEME,
        PROGRAMME_DOMAINS,
        REPORTS_DIR,
        THEME_TO_DOMAIN,
    )
except Exception:  # pragma: no cover
    DEFAULT_QUESTIONS_PER_THEME = 10
    PROGRAMME_DOMAINS: dict[str, str] = {}
    THEME_TO_DOMAIN: dict[str, str] = {}
    REPORTS_DIR = QUIZ_JSON.parent / "rapports"

try:
    from config import QUIZ_OPTIONS_COUNT  # type: ignore
except Exception:  # pragma: no cover
    QUIZ_OPTIONS_COUNT = 4


SYSTEM_PROMPT = """Tu es un professeur français de mathématiques de Terminale générale, spécialité mathématiques.
Tu crées des QCM de révision pour le baccalauréat.

Principes impératifs :
- répondre uniquement en JSON valide, sans Markdown ;
- produire des questions exactes, non ambiguës et conformes au programme ;
- une seule réponse correcte par question ;
- quatre options exactement par question ;
- les distracteurs doivent correspondre à des erreurs fréquentes d'élèves, pas à des absurdités ;
- éviter toute question dépendant d'une figure, d'un graphique ou d'un tableau non fourni ;
- éviter les questions purement lexicales quand une propriété, une relation ou un raisonnement peut être testé ;
- écrire les formules en LaTeX simple dans les chaînes JSON ;
- ne pas utiliser de notation instable ou trop universitaire hors programme.

Types de pièges mathématiques attendus quand le thème s'y prête :
- confusion entre dérivée et primitive ;
- oubli du facteur u' dans une dérivée ou une primitive de composée ;
- confusion entre signe de f, signe de f' et signe de f'' ;
- confusion entre croissance, convexité et positivité ;
- confusion entre condition nécessaire et condition suffisante ;
- confusion entre implication et réciproque ;
- confusion entre indépendance et incompatibilité ;
- confusion entre probabilité conditionnelle et intersection ;
- confusion entre suite arithmétique et suite géométrique ;
- erreur d'indice dans une somme ou une suite ;
- confusion entre vecteur directeur et vecteur normal ;
- confusion entre droite paramétrée et plan cartésien ;
- oubli d'une hypothèse de continuité ou de monotonie ;
- emploi d'une formule hors de son domaine de validité, par exemple ln(x) hors de ]0,+∞[.
"""


# =============================================================================
# Entrées / sorties JSON et CSV
# =============================================================================

def project_root_from_script() -> Path:
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "site").exists() and (parent / "scripts").exists():
            return parent
    return Path.cwd()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# Extraction JSON Claude
# =============================================================================

def parse_json_object(text: str) -> Any:
    """Extrait du JSON depuis une réponse éventuellement entourée de texte."""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    starts = [x for x in [s.find("{"), s.find("[")] if x >= 0]
    if not starts:
        raise ValueError("Aucun début JSON trouvé dans la réponse Claude.")

    start = min(starts)
    opener = s[start]
    closer = "}" if opener == "{" else "]"
    end = s.rfind(closer)

    if end <= start:
        raise ValueError("Aucune fin JSON trouvée dans la réponse Claude.")

    return json.loads(s[start : end + 1])


# =============================================================================
# Normalisation et validation des questions
# =============================================================================

def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_question(raw: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    """Nettoie une question. Retourne (question, erreur)."""
    if not isinstance(raw, dict):
        return None, "question_non_dict"

    question = normalize_str(raw.get("question"))
    options = as_list(raw.get("options"))

    if len(options) != QUIZ_OPTIONS_COUNT:
        return None, f"options_nombre_invalide_{index}"

    options = [normalize_str(o) for o in options]

    if not question:
        return None, f"question_vide_{index}"

    if any(not o for o in options):
        return None, f"option_vide_{index}"

    if len(set(o.lower() for o in options)) != QUIZ_OPTIONS_COUNT:
        return None, f"options_doublons_{index}"

    try:
        correct = int(raw.get("correct"))
    except Exception:
        return None, f"correct_non_entier_{index}"

    if correct < 0 or correct >= QUIZ_OPTIONS_COUNT:
        return None, f"correct_hors_borne_{index}"

    explanation = normalize_str(raw.get("explanation") or raw.get("explication"))
    if len(explanation) < 12:
        return None, f"explication_trop_courte_{index}"

    item = {
        "question": question,
        "options": options,
        "correct": correct,
        "explanation": explanation,
    }

    # Champs enrichis facultatifs. Le frontend peut les ignorer sans dommage.
    qtype = normalize_str(raw.get("type") or raw.get("categorie") or raw.get("catégorie"))
    if qtype:
        item["type"] = qtype

    try:
        difficulty = int(raw.get("difficulte") or raw.get("difficulté") or raw.get("difficulty"))
        if difficulty in {1, 2, 3}:
            item["difficulte"] = difficulty
    except Exception:
        pass

    return item, None


def normalize_quiz(raw: Any, *, theme_id: str, expected_count: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise une réponse Claude en liste de questions."""
    errors: list[str] = []

    if isinstance(raw, dict):
        # Tolérance si Claude encapsule dans {"questions": [...]}.
        raw_questions = raw.get("questions") or raw.get("quiz") or []
    else:
        raw_questions = raw

    if not isinstance(raw_questions, list):
        return [], ["reponse_non_liste"]

    clean: list[dict[str, Any]] = []
    seen_questions: set[str] = set()

    for i, q in enumerate(raw_questions, start=1):
        item, error = normalize_question(q, i)
        if error:
            errors.append(error)
            continue
        assert item is not None

        signature = item["question"].lower()
        if signature in seen_questions:
            errors.append(f"question_doublon_{i}")
            continue
        seen_questions.add(signature)

        item.setdefault("theme_id", theme_id)
        clean.append(item)

    if len(clean) != expected_count:
        errors.append(f"nombre_questions_{len(clean)}_au_lieu_de_{expected_count}")

    return clean, errors


# =============================================================================
# Chargement cours et prompts
# =============================================================================

def theme_domain_label(theme_id: str) -> tuple[str, str]:
    domain_id = THEME_TO_DOMAIN.get(theme_id, "")
    domain_label = PROGRAMME_DOMAINS.get(domain_id, domain_id)
    return domain_id, domain_label


def compact_course(course: dict[str, Any], max_chars: int = 42000) -> dict[str, Any]:
    """Réduit légèrement une fiche de cours pour éviter un prompt inutilement énorme."""
    if not isinstance(course, dict):
        return {}

    keys = [
        "theme_id",
        "titre",
        "domaine_id",
        "domaine_label",
        "synthese",
        "objectifs_bac",
        "definitions",
        "proprietes_theoremes",
        "formules",
        "methodes",
        "automatismes",
        "erreurs_frequentes",
        "demonstrations_raisonnements",
        "algorithmes_python",
        "conseils_bac",
    ]

    out = {k: course.get(k) for k in keys if k in course}
    text = json.dumps(out, ensure_ascii=False, indent=2)

    if len(text) <= max_chars:
        return out

    # Repli encore plus compact.
    compact_keys = [
        "theme_id",
        "titre",
        "synthese",
        "definitions",
        "proprietes_theoremes",
        "formules",
        "methodes",
        "erreurs_frequentes",
    ]
    out = {k: course.get(k) for k in compact_keys if k in course}
    text = json.dumps(out, ensure_ascii=False, indent=2)

    if len(text) <= max_chars:
        return out

    return {
        "theme_id": course.get("theme_id"),
        "titre": course.get("titre"),
        "synthese": course.get("synthese", ""),
        "formules": course.get("formules", [])[:12],
        "methodes": course.get("methodes", [])[:8],
        "erreurs_frequentes": course.get("erreurs_frequentes", [])[:10],
    }


def fallback_course(theme_id: str) -> dict[str, Any]:
    """Fabrique un support minimal si le cours du thème est absent."""
    domain_id, domain_label = theme_domain_label(theme_id)
    return {
        "theme_id": theme_id,
        "titre": ALLOWED_THEMES.get(theme_id, theme_id),
        "domaine_id": domain_id,
        "domaine_label": domain_label,
        "synthese": THEME_DESCRIPTIONS.get(theme_id, ""),
        "relations_cles": THEME_KEY_RELATIONS.get(theme_id, []),
        "source": "config_fallback",
    }


def build_prompt(theme_id: str, course: dict[str, Any], n: int) -> str:
    label = ALLOWED_THEMES.get(theme_id, theme_id)
    description = THEME_DESCRIPTIONS.get(theme_id, "")
    relations = THEME_KEY_RELATIONS.get(theme_id, [])
    domain_id, domain_label = theme_domain_label(theme_id)
    course_payload = compact_course(course)

    return f"""
Produis exactement {n} questions de quiz pour réviser ce thème de spécialité mathématiques de Terminale.

THÈME :
- theme_id : {theme_id}
- libellé : {label}
- domaine officiel : {domain_label} ({domain_id})
- description : {description}

RÉPARTITION ATTENDUE POUR {n} QUESTIONS :
- 2 questions de connaissances ou définitions indispensables ;
- 3 questions sur relations, formules, propriétés ou théorèmes essentiels ;
- 2 questions de raisonnement mathématique qualitatif ;
- 2 questions d'application ou de calcul court ;
- 1 question ciblant explicitement une erreur fréquente.

POINTS À TESTER EN PRIORITÉ :
{json.dumps(relations, ensure_ascii=False, indent=2)}

FORMAT JSON STRICT ATTENDU :
[
  {{
    "question": "Question claire, autonome, sans référence à une figure absente.",
    "options": ["réponse A", "réponse B", "réponse C", "réponse D"],
    "correct": 0,
    "explanation": "Explication brève mais mathématiquement précise.",
    "type": "connaissance | relation | raisonnement | calcul | erreur-frequente",
    "difficulte": 1
  }}
]

CONTRAINTES :
- produire exactement {n} objets dans la liste JSON ;
- chaque question a exactement 4 options ;
- correct est un entier entre 0 et 3 ;
- une seule option doit être correcte ;
- les distracteurs doivent être plausibles et liés à des erreurs fréquentes ;
- ne pas poser de question qui dépend d’une image, d’un graphique, d’un tableau ou d’un arbre non fourni ;
- ne pas inventer de notion hors programme ;
- ne pas mettre de Markdown ;
- ne pas entourer la réponse de ```json ;
- utiliser LaTeX simple dans les chaînes quand une formule est nécessaire.

FICHE DE COURS DU THÈME :
{json.dumps(course_payload, ensure_ascii=False, indent=2)}
""".strip()


# =============================================================================
# Appel Claude
# =============================================================================

def call_claude_streaming(
    client: Anthropic,
    *,
    theme_id: str,
    course: dict[str, Any],
    n: int,
    model: str,
    max_tokens: int,
    temperature: float,
    max_retries: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            chunks: list[str] = []

            with client.messages.stream(
                model=model,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(theme_id, course, n)}],
                max_tokens=max_tokens,
                temperature=temperature,
            ) as stream:
                for text_delta in stream.text_stream:
                    chunks.append(text_delta)

            raw = parse_json_object("".join(chunks))
            clean, validation_errors = normalize_quiz(raw, theme_id=theme_id, expected_count=n)

            if len(clean) == n:
                return clean, validation_errors

            # On considère un nombre incorrect comme une erreur de génération pour retenter.
            raise ValueError("; ".join(validation_errors) or "quiz invalide")

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            sleep = min(30, 2 * attempt)
            print(
                f"Erreur quiz {theme_id} tentative {attempt}/{max_retries}: {exc}. Pause {sleep}s",
                file=sys.stderr,
            )
            time.sleep(sleep)

    raise RuntimeError(f"Échec génération quiz {theme_id}: {last_error}")


# =============================================================================
# Main
# =============================================================================

def load_courses() -> dict[str, Any]:
    data = read_json(COURSES_JSON, default={}) or {}
    return data if isinstance(data, dict) else {}


def select_theme_ids(only: str | None = None) -> list[str]:
    ordered = [tid for tid in THEME_ORDER if tid in ALLOWED_THEMES]
    missing = [tid for tid in ALLOWED_THEMES if tid not in ordered]
    ordered.extend(missing)

    if only:
        if only not in ALLOWED_THEMES:
            allowed = ", ".join(ordered)
            raise ValueError(f"Thème inconnu : {only}. Thèmes autorisés : {allowed}")
        return [only]

    return ordered


def load_existing_quiz() -> dict[str, list[dict[str, Any]]]:
    data = read_json(QUIZ_JSON, default={}) or {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for theme_id, questions in data.items():
        if isinstance(questions, list):
            out[str(theme_id)] = [q for q in questions if isinstance(q, dict)]
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère un quiz par thématique à partir des fiches de cours de mathématiques."
    )

    parser.add_argument("--model", default=CLAUDE_MODEL)
    parser.add_argument("--questions-per-theme", type=int, default=DEFAULT_QUESTIONS_PER_THEME)
    parser.add_argument("--max-tokens", type=int, default=CLAUDE_MAX_TOKENS_QUIZ)
    parser.add_argument("--temperature", type=float, default=CLAUDE_TEMPERATURE)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--only", type=str, default=None, help="Ne génère qu’un thème précis.")
    parser.add_argument("--force", action="store_true", help="Régénère même si le thème existe déjà.")
    parser.add_argument(
        "--allow-without-course",
        action="store_true",
        help="Autorise la génération à partir de config.py si cours.json ne contient pas le thème.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N’appelle pas Claude ; écrit le prompt dans site/data/rapports/.",
    )

    return parser.parse_args()



def shuffle_quiz_options_in_place(quiz_data, seed=20260513):
    """Mélange les options de chaque question et recalcule l'indice correct."""
    import random
    rng = random.Random(seed)

    for theme_id, questions in (quiz_data or {}).items():
        if not isinstance(questions, list):
            continue

        for q in questions:
            options = q.get("options")
            correct = q.get("correct")

            if not isinstance(options, list) or len(options) != 4:
                continue
            if correct not in [0, 1, 2, 3]:
                continue

            indexed = list(enumerate(options))
            rng.shuffle(indexed)

            q["options"] = [option for old_index, option in indexed]
            q["correct"] = next(
                i for i, (old_index, _) in enumerate(indexed)
                if old_index == correct
            )

    return quiz_data


def main() -> int:
    if load_dotenv:
        load_dotenv(project_root_from_script() / ".env")

    args = parse_args()

    if args.questions_per_theme <= 0:
        raise ValueError("--questions-per-theme doit être strictement positif.")

    courses = load_courses()
    existing_quiz = load_existing_quiz()
    theme_ids = select_theme_ids(args.only)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for theme_id in theme_ids:
            course = courses.get(theme_id) or fallback_course(theme_id)
            prompt = build_prompt(theme_id, course, args.questions_per_theme)
            out = REPORTS_DIR / f"dry_run_quiz_prompt_{theme_id}.txt"
            out.write_text(prompt, encoding="utf-8")
            print(f"Prompt écrit : {out}")
        return 0

    if Anthropic is None:
        print("ERREUR : module anthropic absent. Lance : python -m pip install anthropic python-dotenv", file=sys.stderr)
        return 2

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY absent.", file=sys.stderr)
        return 1

    client = Anthropic()
    quiz: dict[str, list[dict[str, Any]]] = dict(existing_quiz)
    summary_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    print(f"Modèle Claude : {args.model}")
    print(f"Questions par thème : {args.questions_per_theme}")
    print(f"Thèmes à considérer : {len(theme_ids)}")

    for idx, theme_id in enumerate(theme_ids, start=1):
        label = ALLOWED_THEMES.get(theme_id, theme_id)
        existing = quiz.get(theme_id, [])

        if existing and not args.force:
            print(f"[{idx}/{len(theme_ids)}] {theme_id} — déjà présent ({len(existing)} questions), ignoré")
            summary_rows.append(
                {
                    "theme_id": theme_id,
                    "label": label,
                    "status": "skipped_existing",
                    "questions": len(existing),
                    "errors": "",
                }
            )
            continue

        course = courses.get(theme_id)
        if not isinstance(course, dict) or not course:
            if not args.allow_without_course:
                msg = "cours_absent"
                print(f"[{idx}/{len(theme_ids)}] {theme_id} — ERREUR : {msg}", file=sys.stderr)
                quiz.setdefault(theme_id, [])
                errors.append({"theme_id": theme_id, "label": label, "error": msg})
                summary_rows.append(
                    {
                        "theme_id": theme_id,
                        "label": label,
                        "status": "error",
                        "questions": 0,
                        "errors": msg,
                    }
                )
                continue

            course = fallback_course(theme_id)

        print(f"[{idx}/{len(theme_ids)}] Génération quiz : {theme_id} — {label}")

        try:
            questions, validation_errors = call_claude_streaming(
                client,
                theme_id=theme_id,
                course=course,
                n=args.questions_per_theme,
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )
            quiz[theme_id] = questions
            write_json(QUIZ_JSON, quiz)

            summary_rows.append(
                {
                    "theme_id": theme_id,
                    "label": label,
                    "status": "ok",
                    "questions": len(questions),
                    "errors": " | ".join(validation_errors),
                }
            )
            print(f"  -> OK ({len(questions)} questions)")

        except KeyboardInterrupt:
            print("\nInterruption utilisateur. Les quiz déjà générés sont sauvegardés.", file=sys.stderr)
            write_json(QUIZ_JSON, quiz)
            raise

        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"  -> ERREUR : {msg}", file=sys.stderr)
            quiz.setdefault(theme_id, [])
            write_json(QUIZ_JSON, quiz)
            errors.append({"theme_id": theme_id, "label": label, "error": msg})
            summary_rows.append(
                {
                    "theme_id": theme_id,
                    "label": label,
                    "status": "error",
                    "questions": 0,
                    "errors": msg,
                }
            )

    # Garantir la présence de tous les thèmes dans quiz.json, même vides.
    for theme_id in select_theme_ids(None):
        quiz.setdefault(theme_id, [])

    # Réordonner selon THEME_ORDER.
    ordered_quiz = {theme_id: quiz.get(theme_id, []) for theme_id in select_theme_ids(None)}
    write_json(QUIZ_JSON, ordered_quiz)

    summary = {
        "questions_per_theme": args.questions_per_theme,
        "themes_total": len(select_theme_ids(None)),
        "themes_generated_or_present": sum(1 for qs in ordered_quiz.values() if qs),
        "questions_total": sum(len(qs) for qs in ordered_quiz.values()),
        "errors": errors,
    }

    write_json(REPORTS_DIR / "quiz_generation_summary.json", {"summary": summary, "themes": summary_rows})
    write_csv(REPORTS_DIR / "quiz_generation_summary.csv", summary_rows)

    print(f"\nQuiz écrit : {QUIZ_JSON}")
    print(f"Rapport JSON : {REPORTS_DIR / 'quiz_generation_summary.json'}")
    print(f"Rapport CSV  : {REPORTS_DIR / 'quiz_generation_summary.csv'}")
    print(summary)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
