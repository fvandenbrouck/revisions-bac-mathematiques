#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_generate_exercises.py

Génère, à partir de site/data/intermediate/exercises_raw.json :
- une classification thématique ;
- une fiche de révision par exercice ;
- des aides méthodologiques ;
- un corrigé question par question ;
- des points de vigilance.

Version mathématiques — Terminale générale, spécialité mathématiques.

À lancer depuis la racine du projet :
    python scripts/03_generate_exercises.py

Options utiles :
    python scripts/03_generate_exercises.py --limit 2
    python scripts/03_generate_exercises.py --force
    python scripts/03_generate_exercises.py --vision
    python scripts/03_generate_exercises.py --vision --max-images 3
    python scripts/03_generate_exercises.py --only ex-2024-jour-1-me-24matj1me1-exercice-3
    python scripts/03_generate_exercises.py --dry-run --limit 1

Remarques :
- Le mode --vision est recommandé pour les exercices avec figure, graphique,
  arbre pondéré, tableau ou formules mal extraites.
- Le script sauvegarde après chaque exercice, pour éviter de perdre un lot long.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    import anthropic
except Exception:  # pragma: no cover
    print(
        "ERREUR : module anthropic absent. Lance : "
        "python -m pip install anthropic python-dotenv",
        file=sys.stderr,
    )
    raise


# =============================================================================
# Configuration projet
# =============================================================================

def find_project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "site").exists() and (parent / "scripts").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = find_project_root()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SITE_DIR = PROJECT_ROOT / "site"
DATA_DIR = SITE_DIR / "data"
RAW_EXERCISES_PATH = DATA_DIR / "intermediate" / "exercises_raw.json"
GENERATED_DIR = DATA_DIR / "generated"
GENERATED_EXERCISES_PATH = GENERATED_DIR / "exercices.json"
SITE_EXERCISES_PATH = DATA_DIR / "exercices.json"
REPORTS_DIR = DATA_DIR / "rapports"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env")

try:
    import config  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Impossible d’importer scripts/config.py. "
        "Vérifie que le fichier existe et ne contient pas d’erreur Python."
    ) from exc


DISCIPLINE = getattr(config, "DISCIPLINE", "mathématiques")
NIVEAU = getattr(config, "NIVEAU", "Terminale générale")
ENSEIGNEMENT = getattr(config, "ENSEIGNEMENT", "Spécialité mathématiques")

VALID_THEMES: dict[str, str] = dict(getattr(config, "ALLOWED_THEMES", {}))
THEME_DESCRIPTIONS: dict[str, str] = dict(getattr(config, "THEME_DESCRIPTIONS", {}))
THEME_TO_DOMAIN: dict[str, str] = dict(getattr(config, "THEME_TO_DOMAIN", {}))
PROGRAMME_DOMAINS: dict[str, str] = dict(getattr(config, "PROGRAMME_DOMAINS", {}))
THEME_KEY_RELATIONS: dict[str, list[str]] = dict(getattr(config, "THEME_KEY_RELATIONS", {}))
THEME_KEYWORDS: dict[str, list[str]] = dict(getattr(config, "THEME_KEYWORDS", {}))
DIFFICULTY_MIN = int(getattr(config, "DIFFICULTY_MIN", 1))
DIFFICULTY_MAX = int(getattr(config, "DIFFICULTY_MAX", 3))
MAX_EXERCISE_TEXT_CHARS = int(getattr(config, "MAX_EXERCISE_TEXT_CHARS", 42000))

if not VALID_THEMES:
    raise RuntimeError("Aucun thème trouvé dans config.ALLOWED_THEMES.")

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-6"))
DEFAULT_MAX_TOKENS = int(
    os.getenv(
        "CLAUDE_MAX_TOKENS_EXERCISE",
        os.getenv("CLAUDE_MAX_TOKENS", str(getattr(config, "CLAUDE_MAX_TOKENS_EXERCISE", 64000))),
    )
)
DEFAULT_TEMPERATURE = float(os.getenv("CLAUDE_TEMPERATURE", str(getattr(config, "CLAUDE_TEMPERATURE", 0.1))))


# =============================================================================
# JSON et fichiers
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


def load_existing_generated() -> list[dict[str, Any]]:
    """Charge les exercices déjà générés, priorité au dossier generated/."""
    for path in [GENERATED_EXERCISES_PATH, SITE_EXERCISES_PATH]:
        data = read_json(path)
        if isinstance(data, list):
            return data
    return []


def save_generated(
    raw_exercises: list[dict[str, Any]],
    generated_by_id: dict[str, dict[str, Any]],
) -> None:
    """Sauvegarde dans l’ordre des exercices bruts."""
    ordered = [generated_by_id[e.get("id")] for e in raw_exercises if e.get("id") in generated_by_id]
    write_json(GENERATED_EXERCISES_PATH, ordered)
    write_json(SITE_EXERCISES_PATH, ordered)


# =============================================================================
# Nettoyage / normalisation
# =============================================================================

def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in as_list(value) if str(v).strip()]


def normalize_space(text: str) -> str:
    text = text or ""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clamp_difficulty(value: Any) -> int | str:
    if value in {"", None}:
        return ""
    try:
        n = int(value)
    except Exception:
        return str(value)
    return max(DIFFICULTY_MIN, min(DIFFICULTY_MAX, n))


def clean_theme_id(value: Any) -> str:
    theme_id = str(value or "").strip()
    if theme_id in VALID_THEMES:
        return theme_id

    # Tentative légère de normalisation : espaces/underscores -> tirets.
    candidate = (
        theme_id.lower()
        .replace("_", "-")
        .replace(" ", "-")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ç", "c")
    )
    candidate = re.sub(r"[^a-z0-9-]+", "-", candidate)
    candidate = re.sub(r"-+", "-", candidate).strip("-")

    if candidate in VALID_THEMES:
        return candidate

    return ""


def normalize_secondary_themes(value: Any, primary: str) -> list[str]:
    out: list[str] = []
    for item in as_list(value):
        theme = clean_theme_id(item)
        if theme and theme != primary and theme not in out:
            out.append(theme)
    return out[:4]


def choose_fallback_theme(ex: dict[str, Any]) -> str:
    """Choisit un thème de repli si Claude renvoie un identifiant invalide."""
    for key in ["theme_hint", "thematique_id", "theme_id"]:
        theme = clean_theme_id(ex.get(key))
        if theme:
            return theme

    text = " ".join(
        str(ex.get(k, "")) for k in ["titre", "theme_explicit", "texte_extrait"]
    ).lower()

    best_theme = ""
    best_score = 0
    for theme_id, keywords in THEME_KEYWORDS.items():
        score = sum(1 for kw in keywords if str(kw).lower() in text)
        if score > best_score:
            best_score = score
            best_theme = theme_id

    if best_theme:
        return best_theme

    # Dernier repli : thème transversal plutôt que faux thème disciplinaire.
    return "logique-raisonnement" if "logique-raisonnement" in VALID_THEMES else next(iter(VALID_THEMES))


# =============================================================================
# Extraction JSON Claude
# =============================================================================

def strip_code_fence(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def extract_json(text: str) -> Any:
    """Extrait le premier JSON plausible d’une réponse Claude."""
    s = strip_code_fence(text)

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

    candidate = s[start : end + 1]
    return json.loads(candidate)


# =============================================================================
# Normalisation de la sortie générée
# =============================================================================

def normalize_corrige_questions(corrige: Any) -> list[dict[str, str]]:
    questions: list[Any] = []

    if isinstance(corrige, str):
        questions = [{"numero": "global", "reponse": corrige}]
    elif isinstance(corrige, list):
        questions = corrige
    elif isinstance(corrige, dict):
        q = corrige.get("questions")
        if isinstance(q, list):
            questions = q
        else:
            for k, v in corrige.items():
                if isinstance(v, dict):
                    item = {"numero": str(k)}
                    item.update(v)
                    questions.append(item)
                else:
                    questions.append({"numero": str(k), "reponse": str(v)})

    normalized: list[dict[str, str]] = []
    for item in questions:
        if isinstance(item, dict):
            numero = str(item.get("numero", item.get("question", ""))).strip()
            methode = str(item.get("methode", item.get("méthode", ""))).strip()
            reponse = str(item.get("reponse", item.get("réponse", ""))).strip()
            vigilance = str(item.get("points_attention", item.get("vigilance", ""))).strip()
        else:
            numero = ""
            methode = ""
            reponse = str(item).strip()
            vigilance = ""

        if not numero and not reponse:
            continue

        normalized.append(
            {
                "numero": numero or "question non numérotée",
                "methode": methode,
                "reponse": reponse,
                "points_attention": vigilance,
            }
        )

    return normalized


def normalize_generated(ex: dict[str, Any], obj: Any, model: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        obj = {"corrige": str(obj)}

    out = dict(ex)

    primary_theme = clean_theme_id(
        obj.get("thematique_id")
        or obj.get("theme_id")
        or obj.get("theme")
        or obj.get("thème")
    )
    if not primary_theme:
        primary_theme = choose_fallback_theme(ex)

    domain_id = THEME_TO_DOMAIN.get(primary_theme, "")
    domain_label = PROGRAMME_DOMAINS.get(domain_id, "") if domain_id else ""

    corrige = obj.get("corrige") or obj.get("corrigé") or {}

    out["thematique_id"] = primary_theme
    out["thematique_label"] = VALID_THEMES.get(primary_theme, primary_theme)
    out["domaine_id"] = domain_id
    out["domaine_label"] = domain_label
    out["themes_secondaires"] = normalize_secondary_themes(
        obj.get("themes_secondaires") or obj.get("thèmes_secondaires"),
        primary=primary_theme,
    )

    out["resume_enonce"] = str(obj.get("resume_enonce", obj.get("résumé_énoncé", ""))).strip()
    out["notions"] = as_str_list(obj.get("notions"))
    out["mots_cles"] = as_str_list(obj.get("mots_cles") or obj.get("mots_clés"))
    out["difficulte"] = clamp_difficulty(obj.get("difficulte", obj.get("difficulté", "")))
    out["competences"] = as_str_list(obj.get("competences") or obj.get("compétences"))
    out["methodes"] = as_str_list(obj.get("methodes") or obj.get("méthodes"))
    out["aide"] = as_str_list(obj.get("aide"))
    out["points_vigilance"] = as_str_list(obj.get("points_vigilance"))
    out["liens_programme"] = as_str_list(obj.get("liens_programme"))

    out["corrige"] = {"questions": normalize_corrige_questions(corrige)}

    out["generation"] = {
        "modele": model,
        "source": "Claude API",
        "statut": "genere",
        "discipline": DISCIPLINE,
    }

    return out


# =============================================================================
# Construction du prompt
# =============================================================================

def compact_json_for_prompt(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def format_themes_for_prompt() -> str:
    lines = []
    for theme_id, label in VALID_THEMES.items():
        domain_id = THEME_TO_DOMAIN.get(theme_id, "")
        domain_label = PROGRAMME_DOMAINS.get(domain_id, "") if domain_id else ""
        desc = THEME_DESCRIPTIONS.get(theme_id, "")
        relations = THEME_KEY_RELATIONS.get(theme_id, [])[:8]
        lines.append(
            f"- {theme_id} — {label}"
            + (f" [domaine : {domain_label}]" if domain_label else "")
            + (f". {desc}" if desc else "")
            + (f" Points clés : {' ; '.join(relations)}" if relations else "")
        )
    return "\n".join(lines)


def truncate_text(text: str, max_chars: int) -> str:
    text = normalize_space(text)
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + "\n\n[TRONCATURE TECHNIQUE : exercice très long ; "
        + "certaines valeurs peuvent devoir être lues sur les pages images.]"
    )


def build_prompt(ex: dict[str, Any]) -> str:
    questions = ex.get("questions_detectees") or []
    if isinstance(questions, str):
        questions = [questions]
    questions_text = ", ".join(str(q) for q in questions) if questions else "non détectées automatiquement"

    texte = truncate_text(
        ex.get("texte_extrait") or ex.get("enonce") or "",
        MAX_EXERCISE_TEXT_CHARS,
    )

    raw_meta = {
        "id": ex.get("id", ""),
        "annee": ex.get("annee", ""),
        "session": ex.get("session", ""),
        "zone": ex.get("zone", ""),
        "titre": ex.get("titre", ""),
        "numero": ex.get("numero", ex.get("type", "")),
        "points": ex.get("points", ""),
        "pages": ex.get("pages", ""),
        "theme_explicit": ex.get("theme_explicit", ""),
        "theme_hint": ex.get("theme_hint", ""),
        "theme_domain_hint": ex.get("theme_domain_hint", ""),
        "questions_detectees": questions_text,
    }

    return fr"""
Tu dois produire une fiche complète de révision pour un exercice de baccalauréat français de spécialité mathématiques, niveau Terminale générale.

CONTRAINTES ABSOLUES :
- Réponds uniquement en JSON valide, sans Markdown, sans texte avant ou après.
- N’invente pas de valeur absente de l’énoncé.
- Si une valeur doit être lue sur une figure, un graphe, un arbre ou un tableau peu lisible, écris explicitement : "valeur à lire sur la figure".
- Le corrigé doit être complet, question par question.
- Les questions détectées dans l’énoncé doivent toutes apparaître dans corrige.questions, sauf si la détection automatique est manifestement erronée.
- Conserve la numérotation de l’énoncé : "1.", "1.a", "Partie A - 2.b", etc.
- Les formules utiles doivent être écrites en LaTeX simple et lisible.
- RÈGLES LATEX STRICTES : les formules en ligne doivent être entourées par $...$.
- Les formules affichées doivent être entourées par $$...$$.
- N’utilise jamais \( ... \).
- N’utilise jamais \[ ... \].
- Aucune commande LaTeX ne doit apparaître hors délimiteurs $...$ ou $$...$$.
- Le LaTeX doit être compatible KaTeX.
- Dans le JSON, les antislashs LaTeX doivent être échappés : "\\frac", "\\ln", "\\sqrt", "\\vec".
- Ne produis pas de carte mentale.
- Ne produis pas de barème détaillé si l’énoncé ne le donne pas.
- Ne transforme pas l’exercice en cours général : reste centré sur cet exercice.
- Pour un QCM, indique la bonne réponse et la justification mathématique.
- Pour les probabilités, distingue clairement conditionnement, indépendance et incompatibilité.
- Pour les suites, distingue monotonie, majoration/minoration, convergence et limite.
- Pour l’analyse, distingue signe de f, signe de f' et signe de f''.
- Pour la géométrie, distingue vecteur directeur, vecteur normal, droite, plan et projeté orthogonal.

THÈMES AUTORISÉS POUR thematique_id, un seul thème principal obligatoire :
{format_themes_for_prompt()}

COMPÉTENCES MATHÉMATIQUES POSSIBLES :
chercher, modéliser, représenter, raisonner, calculer, communiquer.

FORMAT JSON STRICT :
{{
  "thematique_id": "un identifiant exactement parmi les thèmes autorisés",
  "themes_secondaires": ["identifiants éventuels parmi les thèmes autorisés"],
  "resume_enonce": "résumé neutre de l’exercice en 2 ou 3 phrases",
  "notions": ["notion 1", "notion 2"],
  "mots_cles": ["mot clé 1", "mot clé 2"],
  "difficulte": 1,
  "competences": ["chercher", "raisonner", "calculer"],
  "methodes": [
    "Méthode générale réellement utile pour cet exercice"
  ],
  "aide": [
    "1.a — Piste de résolution sans remplacer le corrigé",
    "1.b — Piste de résolution"
  ],
  "corrige": {{
    "questions": [
      {{
        "numero": "1.a",
        "methode": "Méthode utilisée",
        "reponse": "Réponse complète avec démarche, calculs utiles et conclusion.",
        "points_attention": "Erreur fréquente ou vigilance."
      }}
    ]
  }},
  "points_vigilance": [
    "point de vigilance"
  ],
  "liens_programme": [
    "lien explicite avec une capacité ou notion du programme"
  ]
}}

MÉTADONNÉES DE L’EXERCICE :
{compact_json_for_prompt(raw_meta)}

ÉNONCÉ EXTRAIT :
--- DÉBUT ÉNONCÉ ---
{texte}
--- FIN ÉNONCÉ ---
""".strip()


SYSTEM_PROMPT = (
    "Tu es un professeur français de mathématiques de Terminale générale, "
    "spécialiste de l’épreuve de spécialité mathématiques du baccalauréat. "
    "Tu produis uniquement du JSON valide. Tu es rigoureux sur les notations, "
    "les hypothèses, les intervalles de définition, les justifications et les pièges classiques."
)


# =============================================================================
# Images optionnelles
# =============================================================================

def image_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    suffix = path.suffix.lower()
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix)

    if not media:
        return None

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
    }


def resolve_site_path(rel_path: str) -> Path:
    rel = Path(str(rel_path))
    if rel.is_absolute():
        raise ValueError(f"Chemin image absolu interdit : {rel_path}")
    return SITE_DIR / rel


def build_content(ex: dict[str, Any], include_images: bool, max_images: int) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": build_prompt(ex)}]

    if not include_images:
        return content

    for rel in (ex.get("page_images") or [])[:max_images]:
        try:
            path = resolve_site_path(rel)
            payload = image_payload(path)
        except Exception:
            payload = None

        if payload:
            content.append(payload)

    return content


# =============================================================================
# Claude streaming
# =============================================================================

def call_claude_streaming(
    client: anthropic.Anthropic,
    ex: dict[str, Any],
    *,
    include_images: bool,
    max_images: int,
    model: str,
    max_tokens: int,
    temperature: float,
    max_retries: int,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            chunks: list[str] = []

            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": build_content(
                            ex,
                            include_images=include_images,
                            max_images=max_images,
                        ),
                    }
                ],
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)

            parsed = extract_json("".join(chunks))
            return normalize_generated(ex, parsed, model)

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            sleep = min(30, 2 * attempt)
            print(
                f"Erreur Claude pour {ex.get('id')} tentative {attempt}/{max_retries}: "
                f"{exc}. Pause {sleep}s",
                file=sys.stderr,
            )
            time.sleep(sleep)

    raise RuntimeError(f"Échec après {max_retries} tentatives: {last_error}")


# =============================================================================
# Rapports
# =============================================================================

def write_generation_report(
    generated_by_id: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    generated = list(generated_by_id.values())

    theme_counts = Counter(str(ex.get("thematique_id", "")) for ex in generated)
    domain_counts = Counter(str(ex.get("domaine_id", "")) for ex in generated)

    summary = {
        "total_generated": len(generated),
        "total_errors": len(errors),
        "themes": dict(sorted(theme_counts.items())),
        "domains": dict(sorted(domain_counts.items())),
        "errors_path": str(REPORTS_DIR / "generation_exercises_errors.json") if errors else "",
    }

    write_json(REPORTS_DIR / "generation_exercises_summary.json", summary)

    rows = []
    for ex in generated:
        rows.append(
            {
                "id": ex.get("id", ""),
                "source_id": ex.get("source_id", ""),
                "annee": ex.get("annee", ""),
                "session": ex.get("session", ""),
                "zone": ex.get("zone", ""),
                "numero": ex.get("numero", ex.get("type", "")),
                "points": ex.get("points", ""),
                "thematique_id": ex.get("thematique_id", ""),
                "domaine_id": ex.get("domaine_id", ""),
                "difficulte": ex.get("difficulte", ""),
                "nb_questions_corrige": len((ex.get("corrige") or {}).get("questions") or []),
                "titre": ex.get("titre", ""),
            }
        )

    write_csv(
        REPORTS_DIR / "generation_exercises_summary.csv",
        rows,
        fieldnames=[
            "id",
            "source_id",
            "annee",
            "session",
            "zone",
            "numero",
            "points",
            "thematique_id",
            "domaine_id",
            "difficulte",
            "nb_questions_corrige",
            "titre",
        ],
    )

    if errors:
        write_json(REPORTS_DIR / "generation_exercises_errors.json", errors)


# =============================================================================
# Sélection des exercices
# =============================================================================

def load_raw_exercises() -> list[dict[str, Any]]:
    raw = read_json(RAW_EXERCISES_PATH)
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"Aucun exercice brut trouvé : {RAW_EXERCISES_PATH}")
    return [ex for ex in raw if isinstance(ex, dict)]


def select_to_process(
    raw: list[dict[str, Any]],
    generated_by_id: dict[str, dict[str, Any]],
    *,
    force: bool,
    limit: int,
    only: str | None,
    theme: str | None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []

    for ex in raw:
        ex_id = str(ex.get("id", ""))

        if only and ex_id != only:
            continue

        if theme:
            hinted = clean_theme_id(ex.get("theme_hint")) or clean_theme_id(ex.get("thematique_id"))
            if hinted != theme:
                continue

        if force or ex_id not in generated_by_id:
            selected.append(ex)

    if limit:
        selected = selected[:limit]

    return selected


# =============================================================================
# Dry run
# =============================================================================

def write_dry_run(raw: list[dict[str, Any]], limit: int) -> None:
    sample = raw[: limit or 1]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    for i, ex in enumerate(sample, start=1):
        ex_id = str(ex.get("id") or f"sample-{i}")
        path = REPORTS_DIR / f"dry_run_prompt_{ex_id}.txt"
        path.write_text(build_prompt(ex), encoding="utf-8")
        print(f"Prompt écrit : {path}")


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère aides, classifications et corrigés mathématiques avec Claude en streaming."
    )

    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Régénère même si l'exercice existe déjà.")
    parser.add_argument("--only", type=str, default=None, help="Identifiant exact d’un exercice à générer.")
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help="Ne traite que les exercices dont le theme_hint correspond à ce thème.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--vision", action="store_true", help="Ajoute les images de pages. Plus lent et coûteux.")
    parser.add_argument("--max-images", type=int, default=4, help="Nombre maximum d’images envoyées par exercice.")
    parser.add_argument("--dry-run", action="store_true", help="Écrit les prompts dans rapports/ sans appeler Claude.")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    theme_filter = clean_theme_id(args.theme) if args.theme else None
    if args.theme and not theme_filter:
        allowed = ", ".join(VALID_THEMES)
        print(f"ERREUR thème inconnu pour --theme : {args.theme}. Thèmes autorisés : {allowed}", file=sys.stderr)
        return 2

    raw = load_raw_exercises()

    if args.dry_run:
        write_dry_run(raw, limit=args.limit)
        return 0

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY introuvable. Vérifie .env à la racine du projet.")

    existing = load_existing_generated()
    generated_by_id = {
        ex.get("id"): ex
        for ex in existing
        if isinstance(ex, dict) and ex.get("id")
    }

    to_process = select_to_process(
        raw,
        generated_by_id,
        force=args.force,
        limit=args.limit,
        only=args.only,
        theme=theme_filter,
    )

    print(f"Projet : {PROJECT_ROOT}")
    print(f"Discipline : {DISCIPLINE} — {ENSEIGNEMENT}")
    print(f"Modèle Claude : {args.model}")
    print(f"Max tokens : {args.max_tokens}")
    print(f"Vision images : {'oui' if args.vision else 'non'}")
    print(f"Images max/exercice : {args.max_images}")
    print(f"Exercices bruts : {len(raw)}")
    print(f"Déjà générés : {len(generated_by_id)}")
    print(f"Exercices à générer : {len(to_process)}")

    if not to_process:
        print("Rien à générer.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)
    errors: list[dict[str, Any]] = []

    for idx, ex in enumerate(to_process, start=1):
        ex_id = ex.get("id", f"ex-{idx}")
        print(f"[{idx}/{len(to_process)}] {ex_id} — {ex.get('titre', '')}")

        try:
            generated = call_claude_streaming(
                client,
                ex,
                include_images=args.vision,
                max_images=max(0, args.max_images),
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )

            generated_by_id[str(ex_id)] = generated
            save_generated(raw, generated_by_id)

            one_path = GENERATED_DIR / "exercises" / f"{ex_id}.json"
            write_json(one_path, generated)

            print(
                "  -> OK | "
                f"theme={generated.get('thematique_id')} | "
                f"questions={len((generated.get('corrige') or {}).get('questions') or [])}"
            )

        except KeyboardInterrupt:
            print("\nInterruption utilisateur. Les résultats déjà obtenus sont sauvegardés.")
            write_generation_report(generated_by_id, errors)
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  -> ERREUR : {exc}", file=sys.stderr)
            errors.append({"id": ex_id, "titre": ex.get("titre", ""), "erreur": str(exc)})
            write_generation_report(generated_by_id, errors)

    write_generation_report(generated_by_id, errors)

    print("\nGénération terminée.")
    print(f"Fichier principal : {GENERATED_EXERCISES_PATH}")
    print(f"Copie site       : {SITE_EXERCISES_PATH}")
    print(f"Rapport          : {REPORTS_DIR / 'generation_exercises_summary.json'}")
    print(f"Exercices générés disponibles : {len(generated_by_id)}")

    if errors:
        print(f"Erreurs : {len(errors)} — voir {REPORTS_DIR / 'generation_exercises_errors.json'}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
