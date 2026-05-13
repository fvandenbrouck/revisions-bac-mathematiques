#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_generate_exercise_titles.py

Génère des titres courts, explicites et homogènes pour les exercices de mathématiques.

Par défaut, le script ne traite que les titres faibles :
- titre vide ;
- "commun à tous les candidats" ;
- "exercice", "exercice au choix", "partie A/B" ;
- "Principaux domaines abordés" ;
- titres manifestement non discriminants.

Les fichiers individuels dans site/data/generated/exercises/*.json sont la source principale.
Le script met aussi à jour, si présents :
- site/data/generated/exercices.json
- site/data/exercices.json

Usage conseillé :
    python scripts/08_generate_exercise_titles.py --dry-run --limit 5
    python scripts/08_generate_exercise_titles.py --limit 20
    python scripts/08_generate_exercise_titles.py

Options utiles :
    --all          traiter tous les exercices, pas seulement les titres faibles
    --only ID      traiter un exercice précis
    --force        régénérer même si titre_genere existe déjà
    --no-replace   conserver le champ titre original et écrire seulement titre_genere/titre_court
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from anthropic import Anthropic  # type: ignore
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore

try:
    from config import (
        ALLOWED_THEMES,
        CLAUDE_MAX_TOKENS,
        CLAUDE_MODEL,
        CLAUDE_TEMPERATURE,
        DATA_DIR,
        GENERATED_DIR,
        REPORTS_DIR,
        SITE_ROOT,
        THEME_DESCRIPTIONS,
        THEME_TO_DOMAIN,
        PROGRAMME_DOMAINS,
        ensure_directories,
    )
except Exception as exc:  # noqa: BLE001
    print(f"ERREUR import config.py : {exc}", file=sys.stderr)
    raise


EXERCISES_DIR = GENERATED_DIR / "exercises"
GENERATED_AGGREGATE = GENERATED_DIR / "exercices.json"
SITE_EXERCISES_JSON = DATA_DIR / "exercices.json"
REPORT_JSON = REPORTS_DIR / "titles_generation_summary.json"
REPORT_CSV = REPORTS_DIR / "titles_generation_summary.csv"

SYSTEM_PROMPT = """Tu es un éditeur pédagogique pour un site de révision du baccalauréat de mathématiques.

Ta tâche : proposer un titre court et utile pour choisir un exercice.

Contraintes strictes :
- Le titre doit être en français.
- Le titre doit faire idéalement 4 à 8 mots.
- Le titre doit être informatif et discriminant.
- Ne commence jamais par « Exercice », « Sujet », « Partie », « Bac ».
- N'utilise jamais « commun à tous les candidats ».
- N'utilise pas de point final.
- Évite les titres trop génériques comme « Suites » ou « Probabilités » seuls.
- Préfère un titre concret : objet mathématique + contexte ou méthode.
- Ne mets pas de markdown.
- Évite le LaTeX dans le titre, sauf si une notation est indispensable.
- Si une notation mathématique est indispensable, utilise $...$ pour une formule en ligne, jamais \\( ... \\) ni \\[ ... \\].

Exemples de bons titres :
- Refroidissement d’un gâteau
- QCM sur convexité et primitives
- Projection orthogonale dans l’espace
- Loi binomiale et gain algébrique
- Tangente et minimum d’une distance
- Suite récurrente et point fixe
- Intégrale et aire entre deux courbes
- Logarithme et convergence d’une suite

Réponds uniquement par un objet JSON strict :
{
  "titre": "...",
  "justification": "..."
}
"""


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("’", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_weak_title(title: str | None, ex: dict[str, Any] | None = None) -> bool:
    t = (title or "").strip()
    n = normalize_text(t)

    if not n:
        return True

    weak_exact = {
        "commun a tous les candidats",
        "commun à tous les candidats",
        "exercice",
        "exercice au choix",
        "partie a",
        "partie b",
        "partie c",
        "partie d",
        "principaux domaines abordes",
        "principaux domaines abordes :",
        "principaux domaines abordés",
        "principaux domaines abordés :",
    }

    if n in {normalize_text(x) for x in weak_exact}:
        return True

    if n.startswith("commun "):
        return True
    if n.startswith("exercice ") and len(n.split()) <= 4:
        return True
    if n.startswith("partie ") and len(n.split()) <= 4:
        return True
    if n.startswith("principaux domaines"):
        return True

    # Titre réduit à un thème trop large.
    theme_labels = {normalize_text(v) for v in ALLOWED_THEMES.values()}
    if n in theme_labels:
        return True

    if ex:
        theme_id = str(ex.get("thematique_id") or "")
        theme_label = normalize_text(ALLOWED_THEMES.get(theme_id, ""))
        if theme_label and n == theme_label:
            return True

    return False


def clean_title(title: str) -> str:
    title = title.strip()
    title = re.sub(r"^[-–—•\s]+", "", title)
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[.!;:]+$", "", title).strip()
    title = re.sub(r"^(Exercice|Sujet|Partie|Bac)\s*[:\-–—]?\s*", "", title, flags=re.IGNORECASE).strip()
    return title[:120].strip()


def fallback_title(ex: dict[str, Any]) -> str:
    notions = [str(x).strip() for x in ex.get("notions", []) if str(x).strip()]
    if len(notions) >= 2:
        return clean_title(f"{notions[0]} et {notions[1]}")
    if len(notions) == 1:
        mots = [str(x).strip() for x in ex.get("mots_cles", []) if str(x).strip()]
        if mots:
            return clean_title(f"{notions[0]} et {mots[0]}")
        return clean_title(notions[0])

    theme_id = str(ex.get("thematique_id") or "")
    return clean_title(ALLOWED_THEMES.get(theme_id, "Exercice de mathématiques"))


def short_text(value: Any, limit: int = 1600) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(x) for x in value)
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    else:
        value = str(value)

    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[:limit].rstrip() + "…"
    return value


def extract_question_overview(ex: dict[str, Any], limit: int = 1200) -> str:
    corrige = ex.get("corrige") or {}
    questions = corrige.get("questions") or []
    parts = []
    for q in questions[:8]:
        numero = q.get("numero", "")
        reponse = short_text(q.get("reponse", ""), 140)
        if numero or reponse:
            parts.append(f"{numero}: {reponse}")
    return short_text(" | ".join(parts), limit)


def build_prompt(ex: dict[str, Any]) -> str:
    theme_id = str(ex.get("thematique_id") or "")
    domain_id = THEME_TO_DOMAIN.get(theme_id, "")

    payload = {
        "id": ex.get("id", ""),
        "titre_actuel": ex.get("titre", ""),
        "theme_id": theme_id,
        "theme_label": ALLOWED_THEMES.get(theme_id, theme_id),
        "domain_label": PROGRAMME_DOMAINS.get(domain_id, domain_id),
        "theme_description": THEME_DESCRIPTIONS.get(theme_id, ""),
        "notions": ex.get("notions", []),
        "mots_cles": ex.get("mots_cles", []),
        "competences": ex.get("competences", []),
        "resume_enonce": short_text(ex.get("resume_enonce") or ex.get("enonce_court") or ex.get("resume"), 1800),
        "methodes": short_text(ex.get("methodes", []), 1200),
        "points_vigilance": short_text(ex.get("points_vigilance", []), 1000),
        "aperçu_corrige": extract_question_overview(ex),
    }

    return (
        "Propose un titre court pour cet exercice de mathématiques.\n"
        "Le titre doit aider un élève à choisir rapidement l'exercice à travailler.\n\n"
        "Données de l'exercice :\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Réponds uniquement par l'objet JSON demandé."
    )


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Aucun objet JSON trouvé dans la réponse Claude")

    return json.loads(text[start : end + 1])


def call_claude(prompt: str, max_retries: int = 5) -> dict[str, Any]:
    if Anthropic is None:
        raise RuntimeError("Le paquet anthropic n'est pas installé")

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY absent. Vérifie le fichier .env")

    client = Anthropic(api_key=key)
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            text_parts: list[str] = []
            with client.messages.stream(
                model=CLAUDE_MODEL,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=min(int(CLAUDE_MAX_TOKENS), 1200),
                temperature=float(CLAUDE_TEMPERATURE),
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if getattr(delta, "type", None) == "text_delta":
                            text_parts.append(delta.text)

            return parse_json_object("".join(text_parts))

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = min(60, 5 * attempt)
            print(f"Erreur Claude tentative {attempt}/{max_retries}: {exc}. Pause {wait}s")
            time.sleep(wait)

    raise RuntimeError(f"Échec Claude après {max_retries} tentatives : {last_exc}")


def load_exercise_files() -> list[tuple[Path, dict[str, Any]]]:
    if not EXERCISES_DIR.exists():
        raise FileNotFoundError(f"Dossier introuvable : {EXERCISES_DIR}")

    out: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(EXERCISES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                out.append((path, data))
        except Exception as exc:  # noqa: BLE001
            print(f"ERREUR lecture {path}: {exc}", file=sys.stderr)
    return out


def should_process(ex: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.only and ex.get("id") != args.only:
        return False
    if not args.force and ex.get("titre_genere"):
        return False
    if args.all:
        return True
    return is_weak_title(ex.get("titre"), ex)


def update_exercise(ex: dict[str, Any], new_title: str, replace_title: bool = True) -> dict[str, Any]:
    new_title = clean_title(new_title) or fallback_title(ex)

    original = ex.get("titre", "")
    if replace_title and is_weak_title(original, ex):
        if original and not ex.get("titre_original"):
            ex["titre_original"] = original
        ex["titre"] = new_title

    ex["titre_genere"] = new_title
    ex["titre_court"] = new_title
    return ex


def sync_aggregates(updated_by_id: dict[str, dict[str, Any]]) -> None:
    for path in [GENERATED_AGGREGATE, SITE_EXERCISES_JSON]:
        if not path.exists():
            continue

        backup = path.with_suffix(path.suffix + f".bak-titles-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)

        data = json.loads(path.read_text(encoding="utf-8"))
        changed = 0

        if isinstance(data, list):
            for i, ex in enumerate(data):
                ex_id = ex.get("id") if isinstance(ex, dict) else None
                if ex_id in updated_by_id:
                    data[i] = updated_by_id[ex_id]
                    changed += 1
        elif isinstance(data, dict):
            for ex_id, ex in updated_by_id.items():
                if ex_id in data:
                    data[ex_id] = ex
                    changed += 1
        else:
            print(f"Agrégat ignoré, format inattendu : {path}")
            continue

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Agrégat synchronisé : {path} ({changed} entrée(s), backup {backup.name})")


def write_reports(rows: list[dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    REPORT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fieldnames = ["id", "status", "old_title", "new_title", "theme", "reason", "error"]
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    print(f"Rapport JSON : {REPORT_JSON}")
    print(f"Rapport CSV  : {REPORT_CSV}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Génère des titres courts pour les exercices.")
    parser.add_argument("--all", action="store_true", help="Traite tous les exercices, pas seulement les titres faibles.")
    parser.add_argument("--only", type=str, default="", help="ID exact d’un exercice à traiter.")
    parser.add_argument("--limit", type=int, default=0, help="Nombre maximum d’exercices à traiter.")
    parser.add_argument("--force", action="store_true", help="Régénère même si titre_genere existe déjà.")
    parser.add_argument("--dry-run", action="store_true", help="Écrit les prompts sans appeler Claude.")
    parser.add_argument("--no-replace", action="store_true", help="Ne remplace pas le champ titre ; écrit seulement titre_genere/titre_court.")
    parser.add_argument("--no-sync", action="store_true", help="Ne synchronise pas les agrégats exercices.json.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_directories()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    files = load_exercise_files()
    targets = [(p, ex) for p, ex in files if should_process(ex, args)]

    if args.limit:
        targets = targets[: args.limit]

    print(f"Exercices individuels : {len(files)}")
    print(f"Exercices ciblés      : {len(targets)}")

    if not targets:
        print("Aucun exercice à traiter.")
        return 0

    rows: list[dict[str, Any]] = []
    updated: dict[str, dict[str, Any]] = {}

    for idx, (path, ex) in enumerate(targets, start=1):
        ex_id = str(ex.get("id"))
        old_title = str(ex.get("titre", ""))
        theme = str(ex.get("thematique_id", ""))
        print(f"[{idx}/{len(targets)}] {ex_id} — {old_title}")

        prompt = build_prompt(ex)

        if args.dry_run:
            prompt_path = REPORTS_DIR / f"dry_run_title_prompt_{ex_id}.txt"
            prompt_path.write_text(SYSTEM_PROMPT + "\n\n--- USER ---\n" + prompt, encoding="utf-8")
            print(f"  dry-run : {prompt_path}")
            rows.append({
                "id": ex_id,
                "status": "dry-run",
                "old_title": old_title,
                "new_title": "",
                "theme": theme,
                "reason": "prompt écrit",
                "error": "",
            })
            continue

        try:
            resp = call_claude(prompt)
            title = clean_title(str(resp.get("titre", "")))
            if not title:
                title = fallback_title(ex)

            ex = update_exercise(ex, title, replace_title=not args.no_replace)
            path.write_text(json.dumps(ex, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            updated[ex_id] = ex

            print(f"  -> {title}")
            rows.append({
                "id": ex_id,
                "status": "ok",
                "old_title": old_title,
                "new_title": title,
                "theme": theme,
                "reason": resp.get("justification", ""),
                "error": "",
            })

        except Exception as exc:  # noqa: BLE001
            fallback = fallback_title(ex)
            ex = update_exercise(ex, fallback, replace_title=not args.no_replace)
            path.write_text(json.dumps(ex, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            updated[ex_id] = ex

            print(f"  -> ERREUR Claude ; fallback local : {fallback} | {exc}")
            rows.append({
                "id": ex_id,
                "status": "fallback",
                "old_title": old_title,
                "new_title": fallback,
                "theme": theme,
                "reason": "fallback local après erreur Claude",
                "error": str(exc),
            })

    if updated and not args.no_sync:
        sync_aggregates(updated)

    write_reports(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
