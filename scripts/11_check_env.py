#!/usr/bin/env python3
"""Vérifie que .env est lisible et que la configuration locale du projet est cohérente."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit("python-dotenv n'est pas installé. Lance : pip install -r requirements.txt")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

# Ne surcharge pas brutalement l'environnement courant : utile en CI ou en shell déjà configuré.
load_dotenv(ENV_PATH, override=False)


def masked(value: str) -> str:
    """Masque une valeur sensible sans l'afficher en clair."""
    if not value:
        return "ABSENTE"
    if len(value) <= 12:
        return "présente"
    return value[:10] + "..." + value[-4:]


def check_int_env(name: str, default: str, minimum: int | None = None) -> tuple[bool, str]:
    """Valide une variable d'environnement entière."""
    value = os.getenv(name, default)

    try:
        parsed = int(value)
    except ValueError:
        return False, f"ERREUR : {name} doit être un entier. Valeur actuelle : {value!r}"

    if minimum is not None and parsed < minimum:
        return False, f"ERREUR : {name} est trop faible : {parsed}. Minimum recommandé : {minimum}."

    return True, f"{name} : {parsed}"


def check_float_env(name: str, default: str, minimum: float | None = None, maximum: float | None = None) -> tuple[bool, str]:
    """Valide une variable d'environnement décimale."""
    value = os.getenv(name, default)

    try:
        parsed = float(value)
    except ValueError:
        return False, f"ERREUR : {name} doit être un nombre. Valeur actuelle : {value!r}"

    if minimum is not None and parsed < minimum:
        return False, f"ERREUR : {name} est trop faible : {parsed}. Minimum : {minimum}."

    if maximum is not None and parsed > maximum:
        return False, f"ERREUR : {name} est trop élevée : {parsed}. Maximum : {maximum}."

    return True, f"{name} : {parsed}"


def check_optional_path_env(name: str) -> tuple[bool, str]:
    """Vérifie un chemin optionnel si la variable est définie."""
    value = os.getenv(name, "").strip()

    if not value:
        return True, f"{name} : non défini"

    path = Path(value).expanduser()

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        return False, f"ERREUR : {name} pointe vers un chemin introuvable : {path}"

    return True, f"{name} : {path}"


def check_expected_directories() -> list[tuple[bool, str]]:
    """Vérifie la présence des dossiers structurants du projet."""
    expected = [
        "site",
        "site/pdf",
        "site/programme",
        "site/img",
        "site/data",
        "site/data/raw",
        "site/data/raw/pages",
        "site/data/intermediate",
        "site/data/generated",
        "site/data/rapports",
        "scripts",
    ]

    results: list[tuple[bool, str]] = []

    for rel in expected:
        path = PROJECT_ROOT / rel
        if path.exists() and path.is_dir():
            results.append((True, f"OK  {rel}/"))
        else:
            results.append((False, f"ERREUR dossier manquant : {rel}/"))

    return results


def check_programme_file() -> tuple[bool, str]:
    """Vérifie la présence du programme officiel recommandé."""
    configured = os.getenv("PROGRAMME_PDF_PATH", "").strip()

    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    else:
        path = PROJECT_ROOT / "site" / "programme" / "terminale-specialite-mathematiques-2019.pdf"

    if path.exists():
        return True, f"Programme officiel : OK ({path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path})"

    return False, (
        "ATTENTION : programme officiel non trouvé. Chemin attendu : "
        f"{path.relative_to(PROJECT_ROOT) if path.is_absolute() and PROJECT_ROOT in path.parents else path}"
    )


def check_subject_pdfs() -> tuple[bool, str]:
    """Compte les PDF de sujets."""
    pdf_dir = PROJECT_ROOT / "site" / "pdf"
    pdfs = sorted(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []

    if pdfs:
        return True, f"PDF sujets : {len(pdfs)} détecté(s) dans site/pdf/"

    return False, "ATTENTION : aucun PDF sujet détecté dans site/pdf/"


def main() -> int:
    errors = 0
    warnings = 0

    key = os.getenv("ANTHROPIC_API_KEY", "")
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    programme_version = os.getenv("PROGRAMME_VERSION", "2019")
    bac_target = os.getenv("BAC_TARGET", "bac_2026_2027")

    print(f"Projet : {PROJECT_ROOT}")
    print(f".env : {'OK' if ENV_PATH.exists() else 'ABSENT'}")
    print(f"ANTHROPIC_API_KEY : {masked(key)}")
    print(f"CLAUDE_MODEL : {model}")
    print(f"PROGRAMME_VERSION : {programme_version}")
    print(f"BAC_TARGET : {bac_target}")

    if not key.startswith("sk-ant-"):
        print("ERREUR : ANTHROPIC_API_KEY est absente ou ne ressemble pas à une clé Anthropic.")
        errors += 1

    if not model.strip():
        print("ERREUR : CLAUDE_MODEL est vide.")
        errors += 1

    if programme_version != "2019":
        print(
            "ATTENTION : PROGRAMME_VERSION n'est pas 2019. "
            "Vérifie que les sujets exploités correspondent bien au programme choisi."
        )
        warnings += 1

    if bac_target not in {"bac_2026_2027", "bac_2028_plus"}:
        print(
            "ATTENTION : BAC_TARGET inhabituel. Valeurs recommandées : "
            "bac_2026_2027 ou bac_2028_plus."
        )
        warnings += 1

    checks = [
        check_int_env("CLAUDE_MAX_TOKENS", "64000", minimum=4096),
        check_int_env("CLAUDE_MAX_TOKENS_PROGRAMME", os.getenv("CLAUDE_MAX_TOKENS", "64000"), minimum=4096),
        check_int_env("CLAUDE_MAX_TOKENS_EXERCISE", os.getenv("CLAUDE_MAX_TOKENS", "64000"), minimum=4096),
        check_int_env("CLAUDE_MAX_TOKENS_COURSE", os.getenv("CLAUDE_MAX_TOKENS", "64000"), minimum=4096),
        check_int_env("CLAUDE_MAX_TOKENS_QUIZ", os.getenv("CLAUDE_MAX_TOKENS", "64000"), minimum=2048),
        check_int_env("CLAUDE_MAX_TOKENS_LATEX", os.getenv("CLAUDE_MAX_TOKENS", "32000"), minimum=2048),
        check_float_env("CLAUDE_TEMPERATURE", "0.1", minimum=0.0, maximum=1.0),
        check_float_env("CLAUDE_TEMPERATURE_LATEX", "0", minimum=0.0, maximum=1.0),
        check_int_env("PDF_RENDER_DPI", "144", minimum=72),
        check_int_env("MAX_EXERCISE_TEXT_CHARS", "42000", minimum=5000),
        check_int_env("QUIZ_QUESTIONS_PER_THEME", "10", minimum=1),
        check_optional_path_env("BAC_SITE_ROOT"),
        check_optional_path_env("PROGRAMME_PDF_PATH"),
    ]

    for ok, message in checks:
        print(message)
        if not ok:
            errors += 1

    for ok, message in check_expected_directories():
        print(message)
        if not ok:
            errors += 1

    ok, message = check_programme_file()
    print(message)
    if not ok:
        warnings += 1

    ok, message = check_subject_pdfs()
    print(message)
    if not ok:
        warnings += 1

    if errors:
        print(f"Configuration invalide : {errors} erreur(s), {warnings} avertissement(s).")
        return 2

    if warnings:
        print(f"Configuration utilisable, mais {warnings} avertissement(s) à traiter.")
        return 0

    print("Configuration locale cohérente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
