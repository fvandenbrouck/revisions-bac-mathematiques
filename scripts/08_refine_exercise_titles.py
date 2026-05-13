#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_refine_exercise_titles.py

Passe éditoriale sur les titres d'exercices du site de révisions mathématiques.

But : remplacer les titres trop faibles ou trop descriptifs, par exemple :
- "Dans l'espace muni d'un repère orthonormé..."
- "On considère un cube ABCDEFGH..."
- "Soit la fonction définie..."
- "commun à tous les candidats"

Le script modifie uniquement les JSON individuels dans :
    site/data/generated/exercises/*.json

Il ne réécrit pas les corrigés, les aides, les résumés ou les images.
Après exécution, reconstruire les agrégats avec :
    python scripts/06_build_site_data.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXERCISES_DIR = PROJECT_ROOT / "site" / "data" / "generated" / "exercises"
REPORTS_DIR = PROJECT_ROOT / "site" / "data" / "rapports"

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS_TITLE", "800"))
TEMPERATURE = float(os.getenv("CLAUDE_TEMPERATURE_TITLE", "0.1"))


WEAK_EXACT = {
    "",
    "commun à tous les candidats",
    "commun a tous les candidats",
    "commun `a tous les candidats",
    "exercice",
    "exercice au choix",
    "exercice a au choix",
    "exercice b au choix",
    "partie a",
    "partie b",
}

WEAK_PREFIXES = (
    "commun ",
    "dans l'espace ",
    "dans un repère ",
    "dans le repère ",
    "l'espace est muni ",
    "on considère ",
    "on etudie ",
    "on étudie ",
    "soit ",
    "soit la fonction ",
    "pour chacune ",
    "une entreprise ",
    "un jeu ",
    "une urne ",
    "la fonction ",
    "les points ",
)

WEAK_SUBSTRINGS = (
    "muni d'un repère",
    "muni d’un repère",
    "repère orthonormé",
    "on considère",
    "on étudie",
    "soit la fonction",
    "définie sur l'intervalle",
    "définie pour tout réel",
    "pour chacune des affirmations",
)


def strip_accents_light(s: str) -> str:
    table = str.maketrans({
        "à": "a", "â": "a", "ä": "a", "á": "a", "ã": "a",
        "ç": "c",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "î": "i", "ï": "i", "í": "i",
        "ô": "o", "ö": "o", "ó": "o",
        "ù": "u", "û": "u", "ü": "u", "ú": "u",
        "ÿ": "y",
        "œ": "oe", "æ": "ae",
        "’": "'",
    })
    return s.translate(table)


def normalize_title_text(s: str) -> str:
    s = str(s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def is_weak_title(title: str) -> tuple[bool, str]:
    raw = normalize_title_text(title)
    low = strip_accents_light(raw.lower())

    if low in {strip_accents_light(x.lower()) for x in WEAK_EXACT}:
        return True, "titre générique"

    if any(low.startswith(strip_accents_light(p.lower())) for p in WEAK_PREFIXES):
        return True, "début de phrase, pas un titre"

    if any(strip_accents_light(sub.lower()) in low for sub in WEAK_SUBSTRINGS):
        return True, "formulation d'énoncé"

    if "$" in raw or "\\" in raw:
        return True, "formule brute dans le titre"

    # Un titre de carte doit être court. Au-delà, c'est souvent le début de l'énoncé.
    words = re.findall(r"\w+", raw, flags=re.UNICODE)
    if len(raw) > 68 or len(words) > 10:
        return True, "titre trop long"

    if raw.endswith(",") or raw.endswith(":"):
        return True, "titre incomplet"

    return False, ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact_list(items: Any, max_items: int = 8) -> str:
    if not isinstance(items, list):
        return ""
    vals = [str(x).strip() for x in items if str(x).strip()]
    return ", ".join(vals[:max_items])


def text_field(ex: dict[str, Any], *names: str, max_chars: int = 1600) -> str:
    for name in names:
        value = ex.get(name)
        if isinstance(value, str) and value.strip():
            value = re.sub(r"\s+", " ", value.strip())
            return value[:max_chars]
    return ""


def build_prompt(ex: dict[str, Any], reason: str) -> str:
    title = normalize_title_text(ex.get("titre", ""))
    theme = ex.get("thematique_label") or ex.get("thematique_id") or ""
    domain = ex.get("domaine_label") or ex.get("domaine_id") or ""
    notions = compact_list(ex.get("notions"))
    mots_cles = compact_list(ex.get("mots_cles"))
    competences = compact_list(ex.get("competences"))
    resume = text_field(ex, "resume_enonce", "enonce_court", "resume", max_chars=1800)
    aide = text_field(ex, "aide", max_chars=1000)

    return f"""
Tu es rédacteur éditorial pour un site de révisions du baccalauréat de spécialité mathématiques.

Ta tâche : proposer un titre court, clair et discriminant pour une carte d'exercice.

Titre actuel à remplacer : {title!r}
Raison du remplacement : {reason}

Contraintes impératives :
- 4 à 8 mots de préférence ; maximum 60 caractères ;
- pas de phrase complète ;
- ne commence pas par "Dans", "On considère", "Soit", "Pour chacune" ;
- ne contient pas "Exercice", "Partie", "commun à tous les candidats" ;
- pas de formule LaTeX, pas de symbole $...$, pas de coordonnées détaillées ;
- titre utile pour choisir rapidement l'exercice ;
- style nominal : "Projection orthogonale dans un cube", "Bayes et loi binomiale", "Suite récurrente et seuil" ;
- ne pas inventer un contexte absent.

Informations disponibles :
- Identifiant : {ex.get('id','')}
- Année/session : {ex.get('annee','')} {ex.get('session','')}
- Thème principal : {theme}
- Domaine : {domain}
- Notions : {notions}
- Mots-clés : {mots_cles}
- Compétences : {competences}
- Résumé de l'exercice : {resume}
- Aide/méthodes : {aide}

Réponds uniquement avec un objet JSON strict :
{{"titre":"..."}}
""".strip()


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Réponse JSON introuvable : {text[:200]!r}")
    return json.loads(text[start:end + 1])


def call_claude(prompt: str, model: str, retries: int = 4) -> str:
    if anthropic is None:
        raise RuntimeError("Le paquet anthropic n'est pas installé.")

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY absent. Vérifie le fichier .env.")

    client = anthropic.Anthropic(api_key=key)

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system="Tu produis des titres éditoriaux courts pour des exercices de mathématiques. Tu réponds en JSON strict uniquement.",
                messages=[{"role": "user", "content": prompt}],
            )
            parts = []
            for block in msg.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "".join(parts).strip()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = min(60, 8 * attempt)
            print(f"Erreur Claude tentative {attempt}/{retries}: {exc}. Pause {wait}s", file=sys.stderr)
            time.sleep(wait)

    raise RuntimeError(f"Échec Claude après {retries} tentatives: {last_exc}")


def clean_generated_title(title: str) -> str:
    title = normalize_title_text(title)
    title = title.strip(" \t\n\r\"'`.,;:")
    title = title.replace("$", "")
    title = re.sub(r"\s+", " ", title)
    return title[:80].strip()


def local_fallback_title(ex: dict[str, Any]) -> str:
    notions = ex.get("notions") if isinstance(ex.get("notions"), list) else []
    theme = ex.get("thematique_label") or ex.get("thematique_id") or "Exercice"
    vals = [str(x).strip() for x in notions if str(x).strip()]
    if len(vals) >= 2:
        return f"{vals[0]} et {vals[1]}"[:60]
    if len(vals) == 1:
        return vals[0][:60]
    return str(theme).strip()[:60]


def update_one(path: Path, model: str, dry_run: bool = False, use_fallback: bool = True) -> tuple[bool, str]:
    ex = load_json(path)
    title = normalize_title_text(ex.get("titre", ""))
    weak, reason = is_weak_title(title)
    if not weak:
        return False, "titre déjà acceptable"

    prompt = build_prompt(ex, reason)

    if dry_run:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORTS_DIR / f"dry_run_refine_title_{ex.get('id', path.stem)}.txt"
        out.write_text(prompt, encoding="utf-8")
        return True, f"dry-run -> {out}"

    try:
        response = call_claude(prompt, model=model)
        obj = parse_json_object(response)
        new_title = clean_generated_title(obj.get("titre", ""))
        if not new_title:
            raise ValueError("Titre vide dans la réponse Claude")
    except Exception as exc:  # noqa: BLE001
        if not use_fallback:
            raise
        print(f"  ! Fallback local à cause de : {exc}")
        new_title = local_fallback_title(ex)

    if "titre_original" not in ex:
        ex["titre_original"] = title
    ex["titre"] = new_title
    ex["titre_genere"] = new_title
    ex["titre_court"] = new_title
    ex["titre_generation_reason"] = reason

    write_json(path, ex)
    return True, new_title


def select_paths(args: argparse.Namespace) -> list[Path]:
    paths = sorted(EXERCISES_DIR.glob("*.json"))

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        paths = [p for p in paths if p.stem in wanted]

    if not args.force_all:
        selected = []
        for p in paths:
            try:
                ex = load_json(p)
            except Exception as exc:  # noqa: BLE001
                print(f"JSON illisible {p}: {exc}", file=sys.stderr)
                continue
            weak, _ = is_weak_title(ex.get("titre", ""))
            if weak:
                selected.append(p)
        paths = selected

    if args.limit and args.limit > 0:
        paths = paths[: args.limit]

    return paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Améliore les titres faibles des exercices.")
    parser.add_argument("--dry-run", action="store_true", help="Écrit les prompts sans appeler Claude.")
    parser.add_argument("--limit", type=int, default=0, help="Nombre maximum d'exercices à traiter.")
    parser.add_argument("--only", type=str, default="", help="ID unique ou liste d'IDs séparés par des virgules.")
    parser.add_argument("--force-all", action="store_true", help="Traite tous les exercices, même les titres déjà acceptables.")
    parser.add_argument("--no-fallback", action="store_true", help="Échoue au lieu d'utiliser un fallback local si Claude échoue.")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Modèle Claude à utiliser.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")
        load_dotenv(Path.cwd() / ".env", override=False)

    args = parse_args(argv)

    if not EXERCISES_DIR.exists():
        print(f"Dossier introuvable : {EXERCISES_DIR}", file=sys.stderr)
        return 2

    paths = select_paths(args)

    print(f"Exercices ciblés : {len(paths)}")
    if not paths:
        return 0

    updated = 0
    for i, path in enumerate(paths, start=1):
        ex = load_json(path)
        title = normalize_title_text(ex.get("titre", ""))
        print(f"[{i}/{len(paths)}] {path.stem} — {title}")
        changed, msg = update_one(
            path,
            model=args.model,
            dry_run=args.dry_run,
            use_fallback=not args.no_fallback,
        )
        if changed:
            updated += 1
            print(f"  -> {msg}")
        else:
            print(f"  -> ignoré : {msg}")

    print(f"Titres mis à jour : {updated}")
    print("Reconstruis ensuite avec : python scripts/06_build_site_data.py --force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
