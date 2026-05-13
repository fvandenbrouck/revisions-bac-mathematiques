#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_normalize_latex_exercises.py

Normalise le LaTeX des aides, méthodes, points de vigilance et corrigés des
exercices générés pour le site de révisions bac mathématiques.

Le script travaille sur les fichiers individuels :
    site/data/generated/exercises/*.json

Il ne corrige pas le fond mathématique. Il ne doit servir qu'à rendre les
formules compatibles KaTeX/MathJax et plus lisibles dans le site.

Exemples :
    python scripts/07_normalize_latex_exercises.py --dry-run --limit 3
    python scripts/07_normalize_latex_exercises.py --limit 5
    python scripts/07_normalize_latex_exercises.py --only ex-2024-jour-1-me-exercice-4
    python scripts/07_normalize_latex_exercises.py --ids-file ids.txt --force

Après exécution :
    python scripts/06_build_site_data.py --force
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
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:
    import anthropic
except Exception:
    print(
        "ERREUR : module anthropic absent. Lance : python -m pip install anthropic python-dotenv",
        file=sys.stderr,
    )
    raise


# =============================================================================
# Chemins
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
GENERATED_EX_DIR = DATA / "generated" / "exercises"
GENERATED_EX_AGGREGATE = DATA / "generated" / "exercices.json"
SITE_EXERCISES_JSON = DATA / "exercices.json"
BACKUP_ROOT = DATA / "generated" / "exercises_latex_backup"
REPORTS_DIR = DATA / "rapports"

if load_dotenv:
    load_dotenv(ROOT / ".env")

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
DEFAULT_MAX_TOKENS = int(
    os.getenv("CLAUDE_MAX_TOKENS_LATEX", os.getenv("CLAUDE_MAX_TOKENS", "32000"))
)
DEFAULT_TEMPERATURE = float(os.getenv("CLAUDE_TEMPERATURE_LATEX", "0"))


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# JSON Claude
# =============================================================================

def json_from_text(text: str) -> Any:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    try:
        return json.loads(s)
    except Exception:
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
# Comptages et diagnostics LaTeX
# =============================================================================

LATEX_COMMAND_RE = re.compile(
    r"\\(?:frac|sqrt|vec|overrightarrow|mathbb|mathrm|cdot|times|leq?|geq?|neq|"
    r"infty|lim|to|int|sum|binom|ln|exp|sin|cos|tan|left|right|cap|cup|"
    r"subset|in|notin|forall|exists|alpha|beta|gamma|delta|varepsilon|theta|"
    r"lambda|mu|sigma|Omega|mathcal|overline)\b"
)


def as_json_string(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def count_latex_markers(obj: Any) -> dict[str, int]:
    s = as_json_string(obj)
    return {
        "dollars": s.count("$"),
        "display_dollars": s.count("$$"),
        "backslash": s.count("\\"),
        "frac": s.count("\\frac"),
        "sqrt": s.count("\\sqrt"),
        "vec": s.count("\\vec") + s.count("\\overrightarrow"),
        "mathbb": s.count("\\mathbb"),
        "binom": s.count("\\binom"),
        "integral": s.count("\\int"),
        "sum": s.count("\\sum"),
        "bracket_display": s.count("\\[") + s.count("\\]"),
        "paren_inline": s.count("\\(") + s.count("\\)"),
    }


def extract_math_fragments_without_delimiters(text: str) -> list[str]:
    """Repère quelques commandes LaTeX manifestement hors $...$.

    Ce diagnostic reste volontairement approximatif : il sert à repérer des fichiers
    qui méritent une relecture, pas à refuser automatiquement une sortie.
    """
    s = str(text or "")
    fragments: list[str] = []

    # Supprime grossièrement les zones déjà délimitées.
    stripped = re.sub(r"\$\$.*?\$\$", " ", s, flags=re.DOTALL)
    stripped = re.sub(r"\$.*?\$", " ", stripped, flags=re.DOTALL)

    for match in LATEX_COMMAND_RE.finditer(stripped):
        start = max(0, match.start() - 35)
        end = min(len(stripped), match.end() + 35)
        fragments.append(stripped[start:end].strip())
        if len(fragments) >= 10:
            break

    return fragments


# =============================================================================
# Payload ciblé
# =============================================================================

NORMALIZED_FIELDS = [
    "resume_enonce",
    "methodes",
    "aide",
    "corrige",
    "points_vigilance",
    "liens_programme",
]


def extract_relevant_payload(ex: dict[str, Any]) -> dict[str, Any]:
    """Extrait uniquement les champs textuels à normaliser."""
    payload = {
        "id": ex.get("id"),
        "titre": ex.get("titre"),
        "thematique_id": ex.get("thematique_id"),
        "domaine_id": ex.get("domaine_id"),
    }

    for field in NORMALIZED_FIELDS:
        if field in ex:
            payload[field] = ex.get(field)

    return payload


def merge_normalized(original: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    """Réinjecte uniquement les champs normalisés dans l'exercice original."""
    out = dict(original)

    for field in NORMALIZED_FIELDS:
        if field in normalized:
            out[field] = normalized[field]

    meta = dict(out.get("_normalisation_latex") or {})
    meta.update(
        {
            "status": "done",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "script": "07_normalize_latex_exercises.py",
            "discipline": "mathématiques",
        }
    )
    out["_normalisation_latex"] = meta

    return out


# =============================================================================
# Validation de structure
# =============================================================================

def list_len(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def corrige_questions(value: Any) -> list[Any]:
    if isinstance(value, dict) and isinstance(value.get("questions"), list):
        return value["questions"]
    return []


def validate_shape(original: dict[str, Any], normalized: Any) -> list[str]:
    errors: list[str] = []

    if not isinstance(normalized, dict):
        return ["reponse_non_dict"]

    if normalized.get("id") != original.get("id"):
        errors.append("id_modifie")

    if "titre" in normalized and normalized.get("titre") != original.get("titre"):
        errors.append("titre_modifie")

    # Les champs de liste ne doivent pas changer de longueur.
    for field in ["aide", "methodes", "points_vigilance", "liens_programme"]:
        if field in original and field in normalized:
            lo = list_len(original.get(field))
            ln = list_len(normalized.get(field))
            if lo is not None and ln is not None and lo != ln:
                errors.append(f"nombre_items_{field}_modifie:{lo}->{ln}")

    # Corrigé : nombre et numéros de questions inchangés.
    if "corrige" in original:
        corrige_new = normalized.get("corrige")
        if not isinstance(corrige_new, dict):
            errors.append("corrige_non_dict")
        else:
            q_orig = corrige_questions(original.get("corrige"))
            q_new = corrige_questions(corrige_new)

            if len(q_new) != len(q_orig):
                errors.append(f"nombre_questions_modifie:{len(q_orig)}->{len(q_new)}")
            else:
                for i, (qo, qn) in enumerate(zip(q_orig, q_new), start=1):
                    if not isinstance(qo, dict) or not isinstance(qn, dict):
                        continue
                    no = str(qo.get("numero", qo.get("numéro", "")))
                    nn = str(qn.get("numero", qn.get("numéro", "")))
                    if no != nn:
                        errors.append(f"numero_question_modifie:{i}:{no}->{nn}")
                        break

    return errors


# =============================================================================
# Prompt
# =============================================================================

def prompt_for_exercise(ex: dict[str, Any]) -> str:
    payload = extract_relevant_payload(ex)

    return f"""
Tu dois normaliser le LaTeX dans un exercice de mathématiques du baccalauréat.

TÂCHE STRICTE :
- Ne modifie PAS le raisonnement mathématique.
- Ne corrige PAS le fond, même si tu vois une erreur possible : tu ne fais qu'une normalisation typographique LaTeX.
- Ne modifie PAS les valeurs numériques.
- Ne modifie PAS les notations mathématiques choisies, sauf pour les écrire proprement en LaTeX.
- Ne modifie PAS les numéros de questions.
- Ne modifie PAS le nombre de questions.
- Ne modifie PAS la structure JSON.
- Ne reformule pas les phrases si ce n'est pas nécessaire pour intégrer une formule.
- Corrige uniquement l'écriture des expressions mathématiques.

RÈGLES LATEX GÉNÉRALES :
- Toute formule en ligne doit être entre $...$.
- Toute équation longue, suite de calculs, système, intégrale ou limite importante doit être entre $$...$$.
- Aucune commande LaTeX ne doit apparaître hors délimiteurs.
- Utilise un LaTeX compatible KaTeX.
- Remplace les blocs \\[ ... \\] par $$ ... $$.
- Remplace les blocs \\( ... \\) par $ ... $.
- N'utilise jamais \\cdotp ; utilise \\cdot si un point de multiplication est nécessaire.
- Dans du texte, n'écris pas \"R\" pour l'ensemble des réels si une notation mathématique est attendue : écris $\\mathbb{{R}}$.

NOTATIONS MATHÉMATIQUES À NORMALISER PROPREMENT QUAND ELLES APPARAISSENT :
- suites : $u_n$, $u_{{n+1}}$, $(u_n)$ ;
- limites : $\\lim_{{n \\to +\\infty}} u_n$, $\\lim_{{x \\to 0^+}} f(x)$ ;
- fonctions : $f'(x)$, $f''(x)$, $e^x$, $\\ln(x)$, $\\sin(x)$, $\\cos(x)$ ;
- dérivées composées : $(v \\circ u)' = (v' \\circ u) \\times u'$ ;
- intégrales : $\\int_a^b f(x)\\,dx$, $F(b)-F(a)$ ;
- probabilités : $P(A)$, $P(A \\cap B)$, $P_A(B)$, $\\binom{{n}}{{k}} p^k(1-p)^{{n-k}}$ ;
- géométrie : $\\vec{{u}}$, $\\overrightarrow{{AB}}$, $\\vec{{u}} \\cdot \\vec{{v}}$, $ax+by+cz+d=0$ ;
- logique et ensembles : $x \\in A$, $A \\subset B$, $A \\cup B$, $A \\cap B$, $\\overline{{A}}$.

IMPORTANT JSON :
- Tous les antislashs LaTeX doivent être échappés dans le JSON.
  Exemple valide : "$\\frac{{1}}{{2}}$".
  Exemple invalide : "$\frac{{1}}{{2}}$".
- Retourne uniquement un JSON valide, sans Markdown.
- Retourne exactement les clés présentes dans le JSON fourni ci-dessous pour les champs à normaliser.

FORMAT DE SORTIE ATTENDU :
- un objet JSON ;
- même id ;
- même titre ;
- mêmes champs textuels que dans le JSON fourni ;
- même nombre d'éléments dans les listes ;
- même nombre de questions dans corrige.questions.

JSON À NORMALISER :
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


# =============================================================================
# Appel Claude
# =============================================================================

def call_claude_json(
    client: anthropic.Anthropic,
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    max_retries: int,
) -> Any:
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            chunks: list[str] = []

            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=(
                    "Tu es un relecteur expert en mathématiques de Terminale et en LaTeX KaTeX. "
                    "Tu corriges uniquement la syntaxe LaTeX dans des JSON existants. "
                    "Tu ne modifies jamais le raisonnement ni les réponses mathématiques."
                ),
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)

            return json_from_text("".join(chunks))

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            wait = min(30, 3 * attempt)
            print(
                f"Erreur Claude tentative {attempt}/{max_retries}: {exc}. Pause {wait}s",
                file=sys.stderr,
            )
            time.sleep(wait)

    raise RuntimeError(f"Échec normalisation LaTeX : {last_error}")


# =============================================================================
# Sélection des fichiers
# =============================================================================

def load_ids_file(path: Path | None) -> set[str] | None:
    if path is None:
        return None

    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line)
    return ids


def select_files(
    *,
    limit: int | None,
    only_ids: set[str] | None,
    force: bool,
) -> list[Path]:
    if not GENERATED_EX_DIR.exists():
        raise FileNotFoundError(f"Dossier introuvable : {GENERATED_EX_DIR}")

    files = sorted(GENERATED_EX_DIR.glob("*.json"))
    selected: list[Path] = []

    for p in files:
        ex = read_json(p)
        if not isinstance(ex, dict):
            continue

        ex_id = ex.get("id")
        if only_ids and ex_id not in only_ids:
            continue

        if not force and (ex.get("_normalisation_latex") or {}).get("status") == "done":
            continue

        selected.append(p)

        if limit is not None and len(selected) >= limit:
            break

    return selected


# =============================================================================
# Synchronisation optionnelle des agrégats
# =============================================================================

def update_aggregate_file(path: Path, updated_by_id: dict[str, dict[str, Any]]) -> int:
    data = read_json(path)
    if not isinstance(data, list):
        return 0

    changed = 0
    new_data: list[Any] = []

    for item in data:
        if isinstance(item, dict) and item.get("id") in updated_by_id:
            new_data.append(updated_by_id[item["id"]])
            changed += 1
        else:
            new_data.append(item)

    if changed:
        write_json(path, new_data)

    return changed


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalise le LaTeX des aides, méthodes et corrigés des exercices générés."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Nombre d'exercices à traiter.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="ID exact d'un exercice à traiter. Répétable.",
    )
    parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="Fichier contenant une liste d'IDs à traiter.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retraiter même les exercices déjà normalisés.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prévisualise les fichiers sélectionnés sans appeler Claude.",
    )
    parser.add_argument(
        "--write-prompts",
        action="store_true",
        help="Écrit les prompts dans site/data/rapports pour inspection.",
    )
    parser.add_argument(
        "--sync-aggregates",
        action="store_true",
        help="Met aussi à jour site/data/generated/exercices.json et site/data/exercices.json si présents.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-retries", type=int, default=3)

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    ids_from_file = load_ids_file(args.ids_file) or set()
    ids_from_cli = set(args.only or [])
    only_ids = ids_from_file | ids_from_cli
    only_ids_or_none = only_ids if only_ids else None

    files = select_files(
        limit=args.limit,
        only_ids=only_ids_or_none,
        force=args.force,
    )

    print(f"Projet : {ROOT}")
    print(f"Dossier exercices : {GENERATED_EX_DIR}")
    print(f"Modèle Claude : {args.model}")
    print(f"Max tokens : {args.max_tokens}")
    print(f"Exercices sélectionnés : {len(files)}")

    if not files:
        print("Aucun exercice à traiter.")
        return 0

    for p in files:
        ex = read_json(p)
        print("-", ex.get("id"), "|", ex.get("titre"))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.write_prompts or args.dry_run:
        for p in files:
            ex = read_json(p)
            prompt_path = REPORTS_DIR / f"dry_run_latex_prompt_{ex.get('id', p.stem)}.txt"
            prompt_path.write_text(prompt_for_exercise(ex), encoding="utf-8")

    if args.dry_run:
        print("Dry-run : aucun appel Claude.")
        print(f"Prompts écrits dans : {REPORTS_DIR}")
        return 0

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY introuvable dans .env")

    client = anthropic.Anthropic(api_key=api_key)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    report_rows: list[dict[str, Any]] = []
    updated_by_id: dict[str, dict[str, Any]] = {}

    for i, p in enumerate(files, start=1):
        original = read_json(p)
        ex_id = original.get("id")
        print(f"\n[{i}/{len(files)}] {ex_id} — {original.get('titre')}")

        before_payload = extract_relevant_payload(original)
        before_counts = count_latex_markers(before_payload)
        before_outside = []
        for value in before_payload.values():
            if isinstance(value, str):
                before_outside.extend(extract_math_fragments_without_delimiters(value))

        backup_path = backup_dir / p.name
        shutil.copy2(p, backup_path)

        try:
            normalized_payload = call_claude_json(
                client,
                prompt_for_exercise(original),
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )

            errors = validate_shape(original, normalized_payload)
            if errors:
                print("  -> ERREUR validation :", " | ".join(errors))
                report_rows.append(
                    {
                        "id": ex_id,
                        "status": "validation_error",
                        "errors": " | ".join(errors),
                        "file": str(p),
                        "backup": str(backup_path),
                    }
                )
                continue

            updated = merge_normalized(original, normalized_payload)
            after_payload = extract_relevant_payload(updated)
            after_counts = count_latex_markers(after_payload)

            write_json(p, updated)
            updated_by_id[str(ex_id)] = updated

            print("  -> OK")
            print(f"     dollars: {before_counts['dollars']} -> {after_counts['dollars']}")
            print(f"     backslash: {before_counts['backslash']} -> {after_counts['backslash']}")

            report_rows.append(
                {
                    "id": ex_id,
                    "status": "ok",
                    "errors": "",
                    "file": str(p),
                    "backup": str(backup_path),
                    "dollars_before": before_counts["dollars"],
                    "dollars_after": after_counts["dollars"],
                    "backslash_before": before_counts["backslash"],
                    "backslash_after": after_counts["backslash"],
                    "frac_before": before_counts["frac"],
                    "frac_after": after_counts["frac"],
                    "vec_before": before_counts["vec"],
                    "vec_after": after_counts["vec"],
                    "outside_latex_fragments_before": " | ".join(before_outside[:5]),
                }
            )

        except KeyboardInterrupt:
            print("\nInterruption utilisateur.")
            raise
        except Exception as exc:  # noqa: BLE001
            print("  -> ERREUR :", exc)
            report_rows.append(
                {
                    "id": ex_id,
                    "status": "error",
                    "errors": str(exc),
                    "file": str(p),
                    "backup": str(backup_path),
                }
            )

    aggregate_updates = {}
    if args.sync_aggregates and updated_by_id:
        aggregate_updates[str(GENERATED_EX_AGGREGATE)] = update_aggregate_file(
            GENERATED_EX_AGGREGATE,
            updated_by_id,
        )
        aggregate_updates[str(SITE_EXERCISES_JSON)] = update_aggregate_file(
            SITE_EXERCISES_JSON,
            updated_by_id,
        )

    report_json = {
        "timestamp": stamp,
        "model": args.model,
        "max_tokens": args.max_tokens,
        "backup_dir": str(backup_dir),
        "sync_aggregates": args.sync_aggregates,
        "aggregate_updates": aggregate_updates,
        "rows": report_rows,
    }

    report_path = REPORTS_DIR / f"latex_normalization_{stamp}.json"
    csv_path = REPORTS_DIR / f"latex_normalization_{stamp}.csv"

    write_json(report_path, report_json)
    write_csv(csv_path, report_rows)

    ok = sum(1 for r in report_rows if r.get("status") == "ok")
    errors = len(report_rows) - ok

    print("\nNormalisation terminée.")
    print("OK       :", ok)
    print("Erreurs  :", errors)
    print("Backup   :", backup_dir)
    print("Rapport  :", report_path)
    print("CSV      :", csv_path)

    print("\nÉtape suivante recommandée :")
    print("python scripts/06_build_site_data.py --force")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
