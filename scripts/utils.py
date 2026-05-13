from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable


# =============================================================================
# Fichiers et dossiers
# =============================================================================

def ensure_dirs(*paths: Path) -> None:
    """Crée les dossiers passés en argument s'ils n'existent pas."""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    """Lit un fichier JSON. Retourne default si le fichier est absent."""
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    """Écrit un fichier JSON UTF-8 lisible et stable."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    """Lit un CSV en liste de dictionnaires. Compatible BOM UTF-8."""
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_dicts(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    """Écrit un CSV en ignorant les clés non déclarées dans fieldnames."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    """Calcule le SHA-256 d'un fichier."""
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)

    return h.hexdigest()


# =============================================================================
# Chaînes, slugs et chemins
# =============================================================================

def strip_accents(value: str) -> str:
    """Supprime les accents en conservant une chaîne ASCII approximative."""
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")


def slugify(value: str, max_len: int = 80) -> str:
    """Transforme une chaîne en identifiant court compatible fichiers/URL."""
    value = strip_accents(str(value or ""))
    value = value.lower()
    value = value.replace("œ", "oe").replace("æ", "ae")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")

    if not value:
        value = "sujet"

    return value[:max_len].strip("-") or "sujet"


def normalize_space(text: str) -> str:
    """Normalise les espaces sans détruire les retours à la ligne utiles."""
    text = str(text or "")
    text = text.replace("\u00a0", " ")
    text = text.replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_pdf_text(text: str) -> str:
    """Nettoie les artefacts fréquents issus de l'extraction PDF.

    La fonction reste prudente : elle ne tente pas de reconstruire les formules,
    car les sujets de mathématiques contiennent souvent des fractions, exposants,
    vecteurs, tableaux ou figures qui doivent rester vérifiés sur les images.
    """
    if not text:
        return ""

    replacements = {
        "\u000c": "\n",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "￾": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "’": "'",
    }

    for src, dst in replacements.items():
        text = text.replace(src, dst)

    return normalize_space(text)


def site_relative(path: Path, site_root: Path) -> str:
    """Retourne un chemin relatif au dossier site/.

    Les JSON du site ne doivent pas contenir de chemins absolus.
    """
    p = Path(path)
    root = Path(site_root)

    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Chemin hors du dossier site : {p}") from exc

    rel = p.as_posix()

    if rel.startswith("site/"):
        rel = rel[len("site/"):]

    if rel.startswith("/"):
        raise ValueError(f"Chemin absolu interdit : {rel}")

    return rel


def now_timestamp() -> str:
    """Horodatage lisible pour logs et rapports."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def truncate_text(text: str, max_chars: int) -> str:
    """Tronque un texte en essayant de couper à un retour ligne."""
    text = str(text or "")

    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    last_break = cut.rfind("\n")

    if last_break > max_chars * 0.75:
        cut = cut[:last_break]

    return cut + "\n\n[TRONQUÉ POUR LE PROMPT — le texte brut complet reste dans data/raw/pages]"


def first_nonempty_line(lines: Iterable[str]) -> str:
    """Retourne la première ligne non vide."""
    for line in lines:
        line = str(line or "").strip()
        if line:
            return line
    return ""


def print_step(message: str) -> None:
    """Affiche une étape de pipeline."""
    print(f"\n=== {message} ===", flush=True)


# =============================================================================
# Inférences simples depuis les noms de fichiers
# =============================================================================

def extract_year_from_filename(name: str) -> int | None:
    """Extrait une année depuis un nom de fichier.

    Gère par exemple :
    - 24-matj1me1v1.pdf -> 2024
    - baccalaureat-2022-mathematiques.pdf -> 2022
    """
    s = str(name or "")

    m4 = re.search(r"(?<!\d)(20[2-9]\d)(?!\d)", s)
    if m4:
        y = int(m4.group(1))
        if 2000 <= y <= 2099:
            return y

    m2 = re.search(r"(?<!\d)(2[0-9])[-_ ]*MAT", s, flags=re.IGNORECASE)
    if m2:
        return 2000 + int(m2.group(1))

    m2 = re.search(r"(?<!\d)(2[0-9])(?!\d)", s)
    if m2:
        return 2000 + int(m2.group(1))

    return None


def guess_session_from_filename(name: str) -> str:
    """Déduit Jour 1 / Jour 2 / Remplacement depuis un nom de fichier."""
    n = slugify(str(name or ""), max_len=300)

    if re.search(r"(?:^|-)matj2|(?:^|-)j2(?:-|$)|jour-?2", n):
        return "Jour 2"

    if re.search(r"(?:^|-)matj1|(?:^|-)j1(?:-|$)|jour-?1", n):
        return "Jour 1"

    if "rattrap" in n or "remplacement" in n or "secours" in n:
        return "Remplacement"

    return "À préciser"


def guess_zone_from_filename(name: str) -> str:
    """Déduit une zone probable depuis un nom de fichier."""
    n = slugify(str(name or ""), max_len=300)

    aliases = [
        ("amerique-du-nord", "Amérique du Nord"),
        ("amerique-nord", "Amérique du Nord"),
        ("matj1an", "Amérique du Nord"),
        ("matj2an", "Amérique du Nord"),
        ("asie", "Asie"),
        ("matj1ja", "Asie"),
        ("matj2ja", "Asie"),
        ("metropole", "Métropole, Antilles-Guyane, La Réunion, Mayotte"),
        ("matj1me", "Métropole, Antilles-Guyane, La Réunion, Mayotte"),
        ("matj2me", "Métropole, Antilles-Guyane, La Réunion, Mayotte"),
        ("polynesie", "Polynésie"),
        ("matj1po", "Polynésie"),
        ("matj2po", "Polynésie"),
        ("amerique-du-sud", "Amérique du Sud"),
        ("amerique-sud", "Amérique du Sud"),
        ("matj1as", "Amérique du Sud"),
        ("matj2as", "Amérique du Sud"),
        ("nouvelle-caledonie", "Nouvelle-Calédonie"),
        ("matj1nc", "Nouvelle-Calédonie"),
        ("matj2nc", "Nouvelle-Calédonie"),
        ("centres-etrangers", "Centres étrangers"),
        ("matj1g1", "Centres étrangers groupe 1"),
        ("matj2g1", "Centres étrangers groupe 1"),
        ("matj1g2", "Centres étrangers groupe 2"),
        ("matj2g2", "Centres étrangers groupe 2"),
    ]

    for needle, label in aliases:
        if needle in n:
            return label

    return "À préciser"


# =============================================================================
# JSON provenant de modèles
# =============================================================================

def parse_json_object(text: str) -> Any:
    """Parse du JSON même si le modèle a ajouté du texte autour.

    Essaie successivement :
    - JSON brut ;
    - bloc ```json ... ``` ;
    - décodage à partir de chaque position { ou [ plausible.
    """
    text = str(text or "").strip()

    if not text:
        raise ValueError("Réponse vide : aucun JSON à parser.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S | re.I)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    starts = [m.start() for m in re.finditer(r"[\{\[]", text)]

    last_error: Exception | None = None

    for start in starts:
        try:
            obj, _end = decoder.raw_decode(text[start:])
            return obj
        except json.JSONDecodeError as exc:
            last_error = exc

    raise ValueError(f"Aucun objet JSON valide trouvé dans la réponse. Dernière erreur : {last_error}")


def json_from_text(text: str) -> Any:
    """Alias lisible pour les scripts qui utilisent ce nom."""
    return parse_json_object(text)


# =============================================================================
# Détection de questions dans les sujets de mathématiques
# =============================================================================

PART_RE = re.compile(
    r"(?im)^\s*partie\s+([A-D])\b(?:\s*[:\-–—]\s*(.*))?$"
)

MAIN_QUESTION_RE = re.compile(
    r"(?m)^\s*(\d{1,2})\s*[\.\)]\s+(?=\S)"
)

LETTER_QUESTION_RE = re.compile(
    r"(?m)^\s*([a-d])\s*[\.\)]\s+(?=\S)"
)

Q_QUESTION_RE = re.compile(
    r"(?im)^\s*(Q\s*\d{1,2})\s*[\.\)]\s+(?=\S)"
)


def _normalize_qid(qid: str) -> str:
    qid = str(qid or "").strip()
    qid = qid.replace(" ", "")
    qid = qid.replace(",", ".")
    qid = qid.replace("–", "-").replace("—", "-")
    qid = qid.strip(".:-")
    return qid


def _append_unique(values: list[str], value: str) -> None:
    value = _normalize_qid(value)
    if value and value not in values:
        values.append(value)


def detect_question_ids(text: str) -> list[str]:
    """Détecte les questions principales d'un exercice de mathématiques.

    La fonction est volontairement heuristique. Elle sert à signaler les oublis
    possibles dans les corrigés générés, pas à établir une vérité normative.

    Règles :
    - si Q1/Q2 existent, elles priment ;
    - si des « Partie A/B/C » existent, les questions sont notées A.1, B.1, etc. ;
    - sinon, les lignes 1., 2., 3. sont détectées ;
    - les sous-questions a., b., c. sont détectées seulement en complément,
      sous la forme 1.a ou A.1.a, lorsqu'un numéro principal les précède.
    """
    text = clean_pdf_text(text).replace("\r", "\n")

    q_matches = Q_QUESTION_RE.findall(text)
    if q_matches:
        out: list[str] = []
        for q in q_matches:
            _append_unique(out, re.sub(r"\s+", "", q.upper()))
        return out

    lines = text.splitlines()

    out: list[str] = []
    current_part = ""
    current_main = ""

    has_explicit_parts = bool(PART_RE.search(text))

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        part_match = re.match(
            r"(?i)^partie\s+([A-D])\b",
            line,
        )
        if part_match:
            current_part = part_match.group(1).upper()
            current_main = ""
            continue

        main_match = re.match(r"^(\d{1,2})\s*[\.\)]\s+(?=\S)", line)
        if main_match:
            n = int(main_match.group(1))
            if 1 <= n <= 30:
                current_main = str(n)
                qid = f"{current_part}.{n}" if has_explicit_parts and current_part else str(n)
                _append_unique(out, qid)
            continue

        letter_match = re.match(r"^([a-d])\s*[\.\)]\s+(?=\S)", line, flags=re.I)
        if letter_match and current_main:
            letter = letter_match.group(1).lower()
            if has_explicit_parts and current_part:
                qid = f"{current_part}.{current_main}.{letter}"
            else:
                qid = f"{current_main}.{letter}"
            _append_unique(out, qid)

    # Si l'on ne détecte qu'une seule entrée, c'est souvent une section isolée
    # ou une extraction imparfaite. Mieux vaut ne pas surinterpréter.
    return out if len(out) >= 2 else []


# =============================================================================
# Utilitaires de validation
# =============================================================================

def is_enabled_value(value: Any, default: bool = True) -> bool:
    """Interprète une valeur de manifest comme booléen d'activation."""
    if value is None or value == "":
        return default

    return str(value).strip().lower() not in {
        "0",
        "false",
        "faux",
        "non",
        "no",
        "n",
        "disabled",
        "désactivé",
    }


def listify(value: Any) -> list[Any]:
    """Transforme une valeur en liste sans éclater les chaînes."""
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def unique_preserve_order(values: Iterable[Any]) -> list[Any]:
    """Supprime les doublons en conservant l'ordre."""
    out: list[Any] = []
    seen = set()

    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)

    return out


class PipelineError(RuntimeError):
    """Erreur contrôlée du pipeline."""
    pass
