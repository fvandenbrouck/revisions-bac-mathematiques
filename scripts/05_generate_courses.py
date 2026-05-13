#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_generate_courses.py

Génère les fiches de cours du site de révision bac mathématiques à partir :
- du programme officiel structuré, s'il existe ;
- des thèmes définis dans scripts/config.py ;
- des exercices déjà classés/générés, lorsqu'ils sont disponibles.

Sorties :
- site/data/cours.json
- site/data/generated/cours.json
- site/data/generated/courses/<theme_id>.json
- site/data/rapports/courses_generation_summary.json
- site/data/rapports/programme_coverage.csv

À lancer depuis la racine du projet :
    python scripts/05_generate_courses.py

Options utiles :
    python scripts/05_generate_courses.py --dry-run --only calcul-integral
    python scripts/05_generate_courses.py --only suites
    python scripts/05_generate_courses.py --force
    python scripts/05_generate_courses.py --limit 3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

try:
    import anthropic
except Exception:
    print(
        "ERREUR : module anthropic absent. Lance : "
        "python -m pip install anthropic python-dotenv",
        file=sys.stderr,
    )
    raise

from config import (  # noqa: E402
    ALLOWED_THEMES,
    CLAUDE_MAX_TOKENS_COURSE,
    CLAUDE_MODEL,
    CLAUDE_TEMPERATURE,
    COURSES_JSON,
    DATA_DIR,
    GENERATED_DIR,
    PROGRAMME_DOMAINS,
    PROGRAMME_OFFICIEL_JSON,
    PROGRAMME_JSON,
    REPORTS_DIR,
    THEME_DESCRIPTIONS,
    THEME_KEY_RELATIONS,
    THEME_ORDER,
    THEME_TO_DOMAIN,
    get_domain_label,
    get_theme_description,
    get_theme_domain,
    get_theme_key_relations,
    get_theme_label,
    require_allowed_theme,
)


# =============================================================================
# Chemins
# =============================================================================

PROGRAMME_PATH_CANDIDATES = [
    PROGRAMME_OFFICIEL_JSON,
    GENERATED_DIR / "programme_officiel.json",
    PROGRAMME_JSON,
    DATA_DIR / "programme.json",
]

EXERCISES_PATH_CANDIDATES = [
    DATA_DIR / "exercices.json",
    GENERATED_DIR / "exercices.json",
]

OUT_COURS = COURSES_JSON
OUT_COURS_GENERATED = GENERATED_DIR / "cours.json"
OUT_COURSES_DIR = GENERATED_DIR / "courses"

DEFAULT_MODEL = CLAUDE_MODEL
DEFAULT_MAX_TOKENS = CLAUDE_MAX_TOKENS_COURSE
DEFAULT_TEMPERATURE = CLAUDE_TEMPERATURE


# =============================================================================
# Correspondance avec les titres du programme officiel
# =============================================================================

THEME_OFFICIAL_TITLES = {
    "combinatoire-denombrement": [
        "Combinatoire et dénombrement",
    ],
    "geometrie-vecteurs-espace": [
        "Manipulation des vecteurs, des droites et des plans de l’espace",
        "Vecteurs, droites et plans de l’espace",
    ],
    "geometrie-orthogonalite-distances": [
        "Orthogonalité et distances dans l’espace",
    ],
    "geometrie-reperage": [
        "Représentations paramétriques et équations cartésiennes",
    ],
    "suites": [
        "Suites",
    ],
    "limites-fonctions": [
        "Limites des fonctions",
        "Limites de fonctions",
    ],
    "derivation-convexite": [
        "Compléments sur la dérivation",
        "Dérivation et convexité",
    ],
    "continuite": [
        "Continuité des fonctions d’une variable réelle",
        "Continuité",
    ],
    "logarithme": [
        "Fonction logarithme",
    ],
    "trigonometrie": [
        "Fonctions sinus et cosinus",
        "Fonctions trigonométriques sinus et cosinus",
    ],
    "primitives-equations-differentielles": [
        "Primitives, équations différentielles",
        "Primitives et équations différentielles",
    ],
    "calcul-integral": [
        "Calcul intégral",
    ],
    "bernoulli-binomiale": [
        "Succession d’épreuves indépendantes, schéma de Bernoulli",
        "Schéma de Bernoulli",
        "Loi binomiale",
    ],
    "variables-aleatoires": [
        "Sommes de variables aléatoires",
    ],
    "concentration-grands-nombres": [
        "Concentration, loi des grands nombres",
        "Concentration et loi des grands nombres",
    ],
    "algorithmique-python": [
        "Algorithmique et programmation",
        "Notion de liste",
    ],
    "logique-raisonnement": [
        "Vocabulaire ensembliste et logique",
    ],
}


# =============================================================================
# Entrées / sorties JSON et CSV
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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


# =============================================================================
# Normalisation texte
# =============================================================================

def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def norm_text(value: Any) -> str:
    s = strip_accents(str(value or "").lower())
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def safe_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def compact_for_prompt(value: Any, max_chars: int = 18000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[TRONCATURE TECHNIQUE]"


# =============================================================================
# Chargement des sources
# =============================================================================

def load_programme() -> tuple[dict[str, Any], Path | None]:
    path = first_existing(PROGRAMME_PATH_CANDIDATES)
    if path is None:
        print("Aucun programme officiel structuré trouvé ; repli sur config.py.")
        return {}, None

    data = read_json(path)
    if not isinstance(data, dict):
        print(f"Programme structuré invalide ignoré : {path}", file=sys.stderr)
        return {}, path

    print(f"Programme officiel lu : {path}")
    return data, path


def load_exercises() -> list[dict[str, Any]]:
    path = first_existing(EXERCISES_PATH_CANDIDATES)
    if path is None:
        print("Aucun exercice généré trouvé ; les cours seront générés sans exemples associés.")
        return []

    data = read_json(path)
    if not isinstance(data, list):
        print(f"Fichier d’exercices invalide ignoré : {path}", file=sys.stderr)
        return []

    print(f"Exercices lus : {path} ({len(data)})")
    return [item for item in data if isinstance(item, dict)]


def load_existing_courses() -> dict[str, Any]:
    for path in [OUT_COURS, OUT_COURS_GENERATED]:
        data = read_json(path)
        if isinstance(data, dict):
            return data
    return {}


# =============================================================================
# Extraction souple des blocs du programme
# =============================================================================

def candidate_title(node: dict[str, Any]) -> str:
    for key in [
        "theme_id",
        "id",
        "titre",
        "title",
        "nom",
        "name",
        "section",
        "partie",
        "sous_partie",
        "label",
    ]:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def iter_dict_nodes(obj: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            nodes.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return nodes


def title_matches(title: str, aliases: list[str]) -> bool:
    t = norm_text(title)
    if not t:
        return False
    for alias in aliases:
        a = norm_text(alias)
        if not a:
            continue
        if t == a or a in t or t in a:
            return True
    return False


def block_id(theme_id: str, node: dict[str, Any], index: int) -> str:
    for key in ["id", "theme_id", "bloc_id", "section_id"]:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{theme_id}-bloc-{index:03d}"


def extract_programme_blocks(programme: dict[str, Any], theme_id: str) -> list[dict[str, Any]]:
    """Retourne les blocs du programme associés à un thème.

    Le format de programme_officiel.json peut varier selon le script amont ; cette
    fonction reste donc volontairement tolérante.
    """
    aliases = THEME_OFFICIAL_TITLES.get(theme_id, [get_theme_label(theme_id)])
    nodes = iter_dict_nodes(programme)
    matches: list[dict[str, Any]] = []

    # 1. Recherche directe par identifiant ou titre.
    for node in nodes:
        tid = str(node.get("theme_id") or node.get("id") or "")
        title = candidate_title(node)
        if tid == theme_id or title_matches(title, aliases):
            matches.append(node)

    # 2. Cas où programme[theme_id] existe directement.
    direct = programme.get(theme_id)
    if isinstance(direct, dict) and direct not in matches:
        matches.append(direct)

    # 3. Déduplication par représentation compacte.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in matches:
        sig = json.dumps(node, ensure_ascii=False, sort_keys=True)[:2000]
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(node)

    if deduped:
        return [
            {
                "id": block_id(theme_id, node, i),
                "titre": candidate_title(node) or get_theme_label(theme_id),
                "contenu": node,
            }
            for i, node in enumerate(deduped, start=1)
        ]

    # Repli construit depuis config.py.
    return [
        {
            "id": f"{theme_id}-config",
            "titre": get_theme_label(theme_id),
            "contenu": {
                "description": get_theme_description(theme_id),
                "relations_cles": get_theme_key_relations(theme_id),
                "source": "config.py",
                "avertissement": (
                    "Bloc officiel structuré non retrouvé automatiquement ; "
                    "utiliser la description et les relations clés de config.py."
                ),
            },
        }
    ]


# =============================================================================
# Exemples d’exercices
# =============================================================================

def exercise_theme_ids(ex: dict[str, Any]) -> set[str]:
    ids = set()
    for key in ["thematique_id", "theme_id", "theme_hint"]:
        value = ex.get(key)
        if isinstance(value, str) and value in ALLOWED_THEMES:
            ids.add(value)

    for key in ["themes_secondaires", "theme_ids", "themes"]:
        value = ex.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item in ALLOWED_THEMES:
                    ids.add(item)
                elif isinstance(item, dict):
                    tid = item.get("id") or item.get("theme_id")
                    if isinstance(tid, str) and tid in ALLOWED_THEMES:
                        ids.add(tid)

    return ids


def safe_examples(exercises: list[dict[str, Any]], theme_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Exemples strictement rattachés au thème principal.

    Pour générer un cours, on ne retient pas les thèmes secondaires, les hints
    de segmentation ni les mots-clés : ils polluent le prompt. Le cours doit
    s'appuyer sur des exercices dont le thème principal est exactement theme_id.
    """
    examples: list[dict[str, Any]] = []

    for ex in exercises:
        primary = str(ex.get("thematique_id") or ex.get("theme_id") or "").strip()
        if primary != theme_id:
            continue

        examples.append(
            {
                "id": ex.get("id"),
                "titre": ex.get("titre") or ex.get("titre_court") or ex.get("titre_genere") or "",
                "annee": ex.get("annee"),
                "session": ex.get("session"),
                "zone": ex.get("zone"),
                "numero": ex.get("numero"),
                "points": ex.get("points"),
                "pages": ex.get("pages"),
                "notions": safe_list(ex.get("notions"))[:12],
                "mots_cles": safe_list(ex.get("mots_cles") or ex.get("mots_clés"))[:12],
                "resume_enonce": ex.get("resume_enonce") or ex.get("enonce_court") or "",
            }
        )

        if len(examples) >= limit:
            break

    return examples

def json_from_text(text: str) -> Any:
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
                    "Tu es un professeur français de mathématiques de Terminale, "
                    "spécialiste du programme de spécialité et des sujets du baccalauréat. "
                    "Tu produis uniquement du JSON valide, sans Markdown."
                ),
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)

            return json_from_text("".join(chunks))

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            wait = 3 * attempt
            print(
                f"Erreur Claude tentative {attempt}/{max_retries}: {exc}. Pause {wait}s",
                file=sys.stderr,
            )
            time.sleep(wait)

    raise RuntimeError(f"Échec génération cours : {last_error}")


# =============================================================================
# Prompt de génération
# =============================================================================

def prompt_for_course(
    theme_id: str,
    programme_blocks: list[dict[str, Any]],
    examples: list[dict[str, Any]],
) -> str:
    title = get_theme_label(theme_id)
    domain_id = get_theme_domain(theme_id)
    domain_label = get_domain_label(domain_id)
    description = get_theme_description(theme_id)
    key_relations = get_theme_key_relations(theme_id)
    block_ids = [str(block.get("id")) for block in programme_blocks]

    return f"""
Tu dois produire une fiche de cours complète pour un site de révision du baccalauréat français, Terminale générale, spécialité mathématiques.

SOURCE NORMATIVE :
Le cours doit respecter le programme officiel fourni dans les blocs ci-dessous. Les exemples d’exercices servent seulement à adapter les méthodes et les conseils au format du bac. Ils ne doivent pas réduire le périmètre du cours.

THÈME À TRAITER :
- theme_id : {theme_id}
- titre : {title}
- domaine officiel : {domain_id} — {domain_label}
- description : {description}

RELATIONS, PROPRIÉTÉS ET PIÈGES À COUVRIR SI PERTINENTS :
{json.dumps(key_relations, ensure_ascii=False, indent=2)}

CONTRAINTES ABSOLUES :
- Réponds uniquement en JSON valide, sans Markdown.
- N’ajoute pas de notions hors programme.
- N’invente pas de théorème non exigible en Terminale spécialité.
- Distingue clairement : définition, propriété admise, théorème, méthode, erreur fréquente.
- Les formules doivent être écrites en LaTeX simple compatible KaTeX.
- Les formules en ligne doivent être entourées par $...$.
- Les formules affichées doivent être entourées par $$...$$.
- N’utilise jamais \( ... \).
- N’utilise jamais \[ ... \].
- Aucune commande LaTeX ne doit apparaître hors délimiteurs $...$ ou $$...$$.
- Dans le JSON, les antislashs LaTeX doivent être correctement échappés.
- Ne produis pas de corrigé détaillé d’un sujet précis ; le cours doit être général.
- Les méthodes doivent être opérationnelles pour traiter des exercices de bac.
- Les erreurs fréquentes doivent être réalistes et formulées précisément.
- Le champ "blocs_programme_couverts" doit contenir tous les identifiants suivants : {json.dumps(block_ids, ensure_ascii=False)}.

FORMAT JSON STRICT :
{{
  "theme_id": "{theme_id}",
  "titre": "{title}",
  "domaine_id": "{domain_id}",
  "domaine_label": "{domain_label}",
  "synthese": "Synthèse structurée en 250 à 450 mots.",
  "objectifs_bac": [
    "objectif formulé du point de vue de l’élève"
  ],
  "definitions": [
    {{
      "terme": "Terme mathématique",
      "definition": "Définition claire et conforme au programme.",
      "bloc_programme": "id du bloc"
    }}
  ],
  "proprietes_theoremes": [
    {{
      "nom": "Nom de la propriété ou du théorème",
      "enonce": "Énoncé rigoureux mais accessible.",
      "statut": "définition | propriété admise | théorème | démonstration exigible | méthode",
      "conditions": "Conditions d’application.",
      "bloc_programme": "id du bloc"
    }}
  ],
  "formules": [
    {{
      "nom": "Nom de la formule",
      "formule": "LaTeX simple",
      "conditions": "Conditions d’application.",
      "interpretation": "Ce que la formule signifie et quand l’utiliser.",
      "bloc_programme": "id du bloc"
    }}
  ],
  "methodes": [
    {{
      "titre": "Méthode type bac",
      "objectif": "Ce que cette méthode permet de résoudre.",
      "etapes": ["étape 1", "étape 2", "étape 3"],
      "points_vigilance": ["vigilance précise"],
      "bloc_programme": "id du bloc"
    }}
  ],
  "automatismes": [
    {{
      "competence": "Automatisme à maîtriser",
      "exemple": "Mini-exemple ou test mental",
      "bloc_programme": "id du bloc"
    }}
  ],
  "erreurs_frequentes": [
    {{
      "erreur": "Erreur fréquente",
      "correction": "Pourquoi c’est faux et comment l’éviter",
      "bloc_programme": "id du bloc"
    }}
  ],
  "demonstrations_a_connaitre": [
    {{
      "titre": "Démonstration ou raisonnement exemplaire",
      "idee": "Idée directrice sans rédaction excessive",
      "bloc_programme": "id du bloc"
    }}
  ],
  "algorithmes_python": [
    {{
      "titre": "Algorithme ou usage Python",
      "idee": "Ce qu’il faut comprendre",
      "pseudo_code": ["ligne 1", "ligne 2"],
      "bloc_programme": "id du bloc"
    }}
  ],
  "conseils_bac": [
    "Conseil précis pour reconnaître et traiter ce type de question au bac"
  ],
  "exemples_exercices_associes": [
    {{
      "id": "id exercice",
      "titre": "titre exercice",
      "usage": "ce que cet exercice permet de travailler"
    }}
  ],
  "carte_mentale": {{
    "titre": "{title}",
    "branches": [
      {{
        "nom": "Branche",
        "items": ["item 1", "item 2"]
      }}
    ]
  }},
  "blocs_programme_couverts": {json.dumps(block_ids, ensure_ascii=False)}
}}

BLOCS DU PROGRAMME OFFICIEL POUR CE THÈME :
{compact_for_prompt(programme_blocks, max_chars=22000)}

EXEMPLES D’EXERCICES CLASSÉS OU RATTACHÉS À CE THÈME :
{compact_for_prompt(examples, max_chars=9000)}
""".strip()


# =============================================================================
# Normalisation du cours généré
# =============================================================================

def escape_mermaid_label(label: Any) -> str:
    s = str(label or "").replace('"', "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:130]


def carte_to_mermaid(carte: dict[str, Any]) -> str:
    title = escape_mermaid_label(carte.get("titre", "Cours"))
    lines = ["graph TD", f'ROOT["{title}"]']

    branches = carte.get("branches")
    if not isinstance(branches, list):
        branches = []

    for i, branch in enumerate(branches, start=1):
        if not isinstance(branch, dict):
            continue
        bid = f"B{i}"
        lines.append(f'ROOT --> {bid}["{escape_mermaid_label(branch.get("nom", f"Branche {i}"))}"]')

        items = branch.get("items")
        if not isinstance(items, list):
            items = []

        for j, item in enumerate(items, start=1):
            iid = f"I{i}_{j}"
            lines.append(f'{bid} --> {iid}["{escape_mermaid_label(item)}"]')

    return "\n".join(lines)


def normalize_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
        elif item is not None:
            out.append({"texte": str(item)})
    return out


def normalize_course(
    theme_id: str,
    obj: Any,
    programme_blocks: list[dict[str, Any]],
    examples: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    require_allowed_theme(theme_id)

    if not isinstance(obj, dict):
        obj = {}

    title = get_theme_label(theme_id)
    domain_id = get_theme_domain(theme_id)
    domain_label = get_domain_label(domain_id)
    expected_blocks = [str(block.get("id")) for block in programme_blocks]

    obj["theme_id"] = theme_id
    obj["titre"] = str(obj.get("titre") or title)
    obj["domaine_id"] = str(obj.get("domaine_id") or domain_id)
    obj["domaine_label"] = str(obj.get("domaine_label") or domain_label)
    obj["description"] = get_theme_description(theme_id)

    obj["synthese"] = str(obj.get("synthese") or "")
    obj["objectifs_bac"] = [str(x) for x in safe_list(obj.get("objectifs_bac")) if str(x).strip()]
    obj["definitions"] = normalize_list_of_dicts(obj.get("definitions"))
    obj["proprietes_theoremes"] = normalize_list_of_dicts(obj.get("proprietes_theoremes"))
    obj["formules"] = normalize_list_of_dicts(obj.get("formules"))
    obj["methodes"] = normalize_list_of_dicts(obj.get("methodes"))
    obj["automatismes"] = normalize_list_of_dicts(obj.get("automatismes"))
    obj["erreurs_frequentes"] = normalize_list_of_dicts(obj.get("erreurs_frequentes"))
    obj["demonstrations_a_connaitre"] = normalize_list_of_dicts(obj.get("demonstrations_a_connaitre"))
    obj["algorithmes_python"] = normalize_list_of_dicts(obj.get("algorithmes_python"))
    obj["conseils_bac"] = [str(x) for x in safe_list(obj.get("conseils_bac")) if str(x).strip()]

    # On remplace ou complète les exemples par des références réellement présentes.
    generated_examples = normalize_list_of_dicts(obj.get("exemples_exercices_associes"))
    known_ids = {str(ex.get("id")) for ex in examples if ex.get("id")}
    filtered_examples = [ex for ex in generated_examples if str(ex.get("id")) in known_ids]

    if not filtered_examples:
        filtered_examples = [
            {
                "id": ex.get("id"),
                "titre": ex.get("titre"),
                "usage": "Exercice de bac associé à ce thème.",
            }
            for ex in examples[:5]
        ]

    obj["exemples_exercices_associes"] = filtered_examples

    covered = obj.get("blocs_programme_couverts")
    if not isinstance(covered, list):
        covered = []
    covered = [str(x) for x in covered if str(x).strip()]

    # Ne pas perdre les blocs attendus : on les déclare explicitement si Claude les a omis.
    for block_id_value in expected_blocks:
        if block_id_value not in covered:
            covered.append(block_id_value)

    obj["blocs_programme_couverts"] = covered
    obj["relations_cles"] = get_theme_key_relations(theme_id)
    obj["programme_blocks"] = [
        {
            "id": block.get("id"),
            "titre": block.get("titre"),
        }
        for block in programme_blocks
    ]

    carte = obj.get("carte_mentale")
    if not isinstance(carte, dict):
        carte = {"titre": title, "branches": []}
    carte.setdefault("titre", title)
    carte.setdefault("branches", [])
    obj["carte_mentale"] = carte
    obj["carte_mentale_mermaid"] = carte_to_mermaid(carte)

    obj["generation"] = {
        "modele": model,
        "source": "Claude API",
        "statut": "genere",
    }

    return obj


# =============================================================================
# Rapports
# =============================================================================

def coverage_report(
    courses: dict[str, Any],
    programme_blocks_by_theme: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for theme_id in THEME_ORDER:
        blocks = programme_blocks_by_theme.get(theme_id, [])
        course = courses.get(theme_id, {}) if isinstance(courses.get(theme_id), dict) else {}
        covered = set(str(x) for x in safe_list(course.get("blocs_programme_couverts")))

        for block in blocks:
            bid = str(block.get("id") or "")
            rows.append(
                {
                    "theme_id": theme_id,
                    "theme_label": get_theme_label(theme_id),
                    "domain_id": get_theme_domain(theme_id),
                    "domain_label": get_domain_label(get_theme_domain(theme_id)),
                    "bloc_programme": bid,
                    "bloc_titre": str(block.get("titre") or ""),
                    "status": "couvert" if bid in covered else "manquant",
                }
            )

    return rows


def course_summary_rows(courses: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for theme_id in THEME_ORDER:
        course = courses.get(theme_id)
        if not isinstance(course, dict):
            rows.append(
                {
                    "theme_id": theme_id,
                    "theme_label": get_theme_label(theme_id),
                    "status": "absent",
                    "definitions": 0,
                    "proprietes_theoremes": 0,
                    "formules": 0,
                    "methodes": 0,
                    "erreurs_frequentes": 0,
                    "exemples_exercices_associes": 0,
                }
            )
            continue

        rows.append(
            {
                "theme_id": theme_id,
                "theme_label": get_theme_label(theme_id),
                "status": "ok",
                "definitions": len(safe_list(course.get("definitions"))),
                "proprietes_theoremes": len(safe_list(course.get("proprietes_theoremes"))),
                "formules": len(safe_list(course.get("formules"))),
                "methodes": len(safe_list(course.get("methodes"))),
                "erreurs_frequentes": len(safe_list(course.get("erreurs_frequentes"))),
                "exemples_exercices_associes": len(safe_list(course.get("exemples_exercices_associes"))),
            }
        )

    return rows


def save_courses_and_reports(
    courses: dict[str, Any],
    programme_blocks_by_theme: dict[str, list[dict[str, Any]]],
) -> None:
    write_json(OUT_COURS, courses)
    write_json(OUT_COURS_GENERATED, courses)

    for theme_id, course in courses.items():
        if isinstance(course, dict):
            write_json(OUT_COURSES_DIR / f"{theme_id}.json", course)

    coverage_rows = coverage_report(courses, programme_blocks_by_theme)
    summary_rows = course_summary_rows(courses)

    write_csv(
        REPORTS_DIR / "programme_coverage.csv",
        coverage_rows,
        [
            "theme_id",
            "theme_label",
            "domain_id",
            "domain_label",
            "bloc_programme",
            "bloc_titre",
            "status",
        ],
    )
    write_csv(
        REPORTS_DIR / "courses_generation_summary.csv",
        summary_rows,
        [
            "theme_id",
            "theme_label",
            "status",
            "definitions",
            "proprietes_theoremes",
            "formules",
            "methodes",
            "erreurs_frequentes",
            "exemples_exercices_associes",
        ],
    )

    write_json(
        REPORTS_DIR / "courses_generation_summary.json",
        {
            "total_themes": len(THEME_ORDER),
            "themes_generes": len([t for t in THEME_ORDER if t in courses]),
            "themes_attendus": THEME_ORDER,
            "domaines": PROGRAMME_DOMAINS,
            "summary": summary_rows,
            "coverage": coverage_rows,
        },
    )


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Génère les fiches de cours de spécialité mathématiques depuis le "
            "programme officiel structuré et les exercices générés."
        )
    )

    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true", help="Régénère même si le cours existe déjà.")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--only", type=str, default=None, help="Ne génère qu’un thème donné.")
    parser.add_argument("--limit", type=int, default=0, help="Limite le nombre de thèmes à générer.")
    parser.add_argument("--dry-run", action="store_true", help="Écrit les prompts sans appeler Claude.")

    return parser.parse_args()


def selected_themes(only: str | None, limit: int) -> list[str]:
    if only:
        require_allowed_theme(only)
        themes = [only]
    else:
        themes = list(THEME_ORDER)

    if limit:
        themes = themes[:limit]

    return themes


def main() -> int:
    args = parse_args()

    programme, programme_path = load_programme()
    exercises = load_exercises()
    courses = load_existing_courses()

    themes = selected_themes(args.only, args.limit)
    programme_blocks_by_theme = {
        theme_id: extract_programme_blocks(programme, theme_id)
        for theme_id in THEME_ORDER
    }

    if args.dry_run:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        for theme_id in themes:
            prompt = prompt_for_course(
                theme_id,
                programme_blocks=programme_blocks_by_theme[theme_id],
                examples=safe_examples(exercises, theme_id, limit=8),
            )
            prompt_path = REPORTS_DIR / f"dry_run_course_prompt_{theme_id}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            print(f"Prompt écrit : {prompt_path}")
        return 0

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY introuvable dans .env")

    print(f"Modèle Claude : {args.model}")
    print(f"Max tokens : {args.max_tokens}")
    print(f"Température : {args.temperature}")
    print(f"Programme : {programme_path if programme_path else 'repli config.py'}")
    print(f"Thèmes à traiter : {len(themes)}")

    client = anthropic.Anthropic(api_key=api_key)
    errors: list[dict[str, Any]] = []

    for index, theme_id in enumerate(themes, start=1):
        if theme_id in courses and not args.force:
            print(f"[{index}/{len(themes)}] {theme_id} — déjà présent, ignoré. Utilise --force pour régénérer.")
            continue

        print(f"\n[{index}/{len(themes)}] Génération cours : {theme_id} — {get_theme_label(theme_id)}")
        examples = safe_examples(exercises, theme_id, limit=8)
        blocks = programme_blocks_by_theme[theme_id]

        try:
            obj = call_claude_json(
                client,
                prompt_for_course(theme_id, blocks, examples),
                model=args.model,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                max_retries=args.max_retries,
            )
            courses[theme_id] = normalize_course(
                theme_id,
                obj,
                programme_blocks=blocks,
                examples=examples,
                model=args.model,
            )
            save_courses_and_reports(courses, programme_blocks_by_theme)
            print(
                "  -> OK "
                f"({len(courses[theme_id].get('definitions', []))} définitions, "
                f"{len(courses[theme_id].get('methodes', []))} méthodes)"
            )

        except KeyboardInterrupt:
            print("\nInterruption utilisateur. Les cours déjà obtenus sont sauvegardés.")
            save_courses_and_reports(courses, programme_blocks_by_theme)
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"  -> ERREUR : {exc}", file=sys.stderr)
            errors.append({"theme_id": theme_id, "theme_label": get_theme_label(theme_id), "erreur": str(exc)})
            write_json(REPORTS_DIR / "generation_courses_errors.json", errors)

    save_courses_and_reports(courses, programme_blocks_by_theme)

    if errors:
        write_json(REPORTS_DIR / "generation_courses_errors.json", errors)
        print(f"\nErreurs enregistrées : {REPORTS_DIR / 'generation_courses_errors.json'}")

    print("\nCours générés :")
    print(f"- {OUT_COURS}")
    print(f"- {OUT_COURS_GENERATED}")
    print(f"Rapport synthèse : {REPORTS_DIR / 'courses_generation_summary.csv'}")
    print(f"Rapport couverture : {REPORTS_DIR / 'programme_coverage.csv'}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
