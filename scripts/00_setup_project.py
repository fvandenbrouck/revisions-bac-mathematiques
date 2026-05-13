#!/usr/bin/env python3
"""Crée ou vérifie l'architecture locale du projet bac mathématiques."""
from __future__ import annotations
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DIRS = [
    "site", "site/pdf", "site/programme", "site/img", "site/data",
    "site/data/raw", "site/data/raw/pages", "site/data/intermediate",
    "site/data/generated", "site/data/generated/exercises", "site/data/generated/courses",
    "site/data/rapports", "site/data/ignored",
]
RECOMMENDED_PROGRAMME_FILENAME = "terminale-specialite-mathematiques-2019.pdf"


def print_env_template() -> None:
    print("Crée-le ainsi, depuis la racine du projet :")
    print("cat > .env <<'EOF'")
    print("ANTHROPIC_API_KEY=VOTRE_CLE_ANTHROPIC_ICI")
    print("CLAUDE_MODEL=claude-sonnet-4-6")
    print("CLAUDE_MAX_TOKENS=64000")
    print("CLAUDE_TEMPERATURE=0.1")
    print("PROGRAMME_VERSION=2019")
    print("BAC_TARGET=bac_2026_2027")
    print("EOF")
    print("chmod 600 .env")


def main() -> int:
    print(f"Projet détecté : {PROJECT_ROOT}")
    for rel in REQUIRED_DIRS:
        path = PROJECT_ROOT / rel
        path.mkdir(parents=True, exist_ok=True)
        print(f"OK  {rel}/")
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        print("OK  .env présent à la racine du projet")
    else:
        print("ATTENTION  .env absent")
        print_env_template()
    programme = PROJECT_ROOT / "site" / "programme" / RECOMMENDED_PROGRAMME_FILENAME
    if programme.exists():
        print(f"OK  programme officiel trouvé : site/programme/{RECOMMENDED_PROGRAMME_FILENAME}")
    else:
        print(f"INFO programme officiel non trouvé : copie le PDF sous site/programme/{RECOMMENDED_PROGRAMME_FILENAME}")
    pdfs = sorted((PROJECT_ROOT / "site" / "pdf").glob("*.pdf"))
    print(f"PDF sujets détectés dans site/pdf/ : {len(pdfs)}")
    for pdf in pdfs:
        print(f"  - {pdf.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
