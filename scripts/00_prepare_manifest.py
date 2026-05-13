#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Construit site/data/manifest.csv et manifest.json à partir des PDF de sujets de mathématiques."""
from __future__ import annotations
import argparse, csv, hashlib, json, re, shutil, sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ZONE_LABELS = {
    "ME": "Métropole, Antilles-Guyane, La Réunion, Mayotte",
    "AN": "Amérique du Nord", "JA": "Asie", "G1": "Centres étrangers groupe 1",
    "G2": "Centres étrangers groupe 2", "PO": "Polynésie", "AS": "Amérique du Sud",
    "NC": "Nouvelle-Calédonie", "LR": "La Réunion", "AG": "Antilles-Guyane",
    "LI": "Centres étrangers", "BI": "Centres étrangers", "PE": "Centres étrangers",
}
ZONE_CODES = sorted(ZONE_LABELS.keys(), key=len, reverse=True)
ZONE_ALIASES = {
    "metropole": "ME", "france": "ME", "antilles-guyane": "AG", "amerique-nord": "AN",
    "amerique-du-nord": "AN", "asie": "JA", "centres-etrangers-groupe-1": "G1",
    "centres-etrangers-1": "G1", "centres-etrangers-groupe-2": "G2",
    "centres-etrangers-2": "G2", "polynesie": "PO", "amerique-sud": "AS",
    "amerique-du-sud": "AS", "nouvelle-caledonie": "NC", "reunion": "LR",
}
CSV_COLUMNS = ["enabled", "id", "titre", "annee", "session", "zone", "pdf_path", "sha256", "notes"]

@dataclass
class SubjectMeta:
    code: str = ""
    year: str = ""
    day: str = ""
    zone_code: str = ""
    zone_label: str = "À préciser"
    confidence: str = "faible"
    source: str = ""
    notes: str = ""
    @property
    def session(self) -> str:
        if self.day in {"1", "2"}:
            return f"Jour {self.day}"
        if self.day:
            return f"Session {self.day}"
        return "À préciser"

def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()

def slugify(value: str, max_len: int = 90) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-+", "-", value)
    return (value[:max_len].strip("-") or "sujet")

def safe_relative_to(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()

def extract_first_page_text(pdf_path: Path) -> str:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(pdf_path)) as pdf:
            if pdf.pages:
                text = pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=3) or ""
                if text.strip():
                    return text
    except Exception:
        pass
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf_path))
        try:
            if doc.page_count:
                return doc.load_page(0).get_text("text") or ""
        finally:
            doc.close()
    except Exception:
        pass
    return ""

def normalize_for_code_search(text: str) -> str:
    text = str(text).upper()
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", "", text)

def find_subject_code(raw_text_or_name: str) -> Optional[str]:
    s = normalize_for_code_search(raw_text_or_name)
    m = re.search(r"(?P<yy>\d{2})[-_]?MATJ(?P<day>[12])(?P<tail>[A-Z0-9]{2,8})", s)
    if m:
        return m.group(0).replace("-", "").replace("_", "")
    return None

def decode_math_code(code: str, source: str) -> SubjectMeta:
    code = normalize_for_code_search(code)
    meta = SubjectMeta(code=code, source=source)
    m = re.match(r"(?P<yy>\d{2})MATJ(?P<day>[12])(?P<tail>[A-Z0-9]{2,8})", code)
    if not m:
        meta.notes = f"Code non décodable : {code}."
        return meta
    meta.year = f"20{m.group('yy')}"
    meta.day = m.group('day')
    tail = m.group('tail')
    for zc in ZONE_CODES:
        if tail.startswith(zc):
            meta.zone_code = zc
            meta.zone_label = ZONE_LABELS[zc]
            meta.confidence = "forte"
            meta.notes = f"Code détecté ({source}) : {code} ; zone={zc}."
            return meta
    meta.confidence = "moyenne"
    meta.notes = f"Code détecté ({source}) : {code}, zone inconnue dans '{tail}'."
    return meta

def infer_year_from_filename(stem: str) -> str:
    for pat in [r"(?<!\d)(20[2-9]\d)(?!\d)", r"(?<!\d)(2[2-9])(?!\d)"]:
        m = re.search(pat, stem)
        if m:
            v = m.group(1)
            return v if len(v) == 4 else f"20{v}"
    return ""

def infer_day_from_filename(stem: str) -> str:
    s = slugify(stem, 300)
    m = re.search(r"(?:matj|jour|j|sujet)-?([12])", s, flags=re.I)
    return m.group(1) if m else ""

def infer_zone_from_filename(stem: str) -> tuple[str, str]:
    s = slugify(stem, 300)
    for alias, zc in ZONE_ALIASES.items():
        if alias in s:
            return zc, ZONE_LABELS[zc]
    m = re.search(r"matj[12]([a-z]{2})", s)
    if m:
        zc = m.group(1).upper()
        if zc in ZONE_LABELS:
            return zc, ZONE_LABELS[zc]
    return "", "À préciser"

def infer_meta_from_filename(pdf_path: Path) -> SubjectMeta:
    year = infer_year_from_filename(pdf_path.stem)
    day = infer_day_from_filename(pdf_path.stem)
    zc, zl = infer_zone_from_filename(pdf_path.stem)
    missing = []
    if not year: missing.append("année")
    if not day: missing.append("session/jour")
    if not zc: missing.append("zone")
    return SubjectMeta(year=year, day=day, zone_code=zc, zone_label=zl, confidence="moyenne" if not missing else "faible", source="nom du fichier", notes=("Métadonnées inférées depuis le nom." if not missing else "À relire : " + ", ".join(missing)))

def infer_meta(pdf_path: Path, read_pdf_text: bool = True) -> SubjectMeta:
    if read_pdf_text:
        code = find_subject_code(extract_first_page_text(pdf_path))
        if code:
            return decode_math_code(code, "première page")
    code = find_subject_code(pdf_path.name)
    if code:
        return decode_math_code(code, "nom du fichier")
    return infer_meta_from_filename(pdf_path)

def build_row(pdf_path: Path, site_dir: Path, read_pdf_text: bool = True) -> dict[str, str]:
    meta = infer_meta(pdf_path, read_pdf_text)
    rel_pdf = safe_relative_to(pdf_path, site_dir)
    subject_id = slugify("-".join([meta.year or "annee", slugify(meta.session), slugify(meta.zone_code or meta.zone_label), slugify(meta.code, 35) if meta.code else slugify(pdf_path.stem, 55)]), 130)
    title = f"Mathématiques {meta.year} — {meta.session} — {meta.zone_label}" if meta.year and meta.session != "À préciser" and meta.zone_label != "À préciser" else pdf_path.stem
    notes = meta.notes + (" À relire manuellement." if meta.confidence != "forte" else "")
    return {"enabled": "1" if meta.year else "0", "id": subject_id, "titre": title, "annee": meta.year, "session": meta.session, "zone": meta.zone_label, "pdf_path": rel_pdf, "sha256": sha256_file(pdf_path), "notes": notes.strip()}

def backup_existing(path: Path) -> None:
    if path.exists():
        backup_path = path.with_suffix(path.suffix + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        shutil.copy2(path, backup_path)
        print(f"Backup créé : {backup_path}")

def write_manifest_csv(rows: list[dict[str, str]], path: Path, backup: bool, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup: backup_existing(path)
    if path.exists() and not force: print(f"INFO manifest existant remplacé : {path}")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS); w.writeheader(); w.writerows(rows)

def write_manifest_json(rows: list[dict[str, str]], path: Path, backup: bool, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup: backup_existing(path)
    if path.exists() and not force: print(f"INFO manifest JSON existant remplacé : {path}")
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def print_summary(rows: list[dict[str, str]], csv_path: Path, json_path: Path) -> None:
    zones = Counter(r["zone"] or "À préciser" for r in rows)
    years = Counter(r["annee"] or "À préciser" for r in rows)
    sessions = Counter(r["session"] or "À préciser" for r in rows)
    unresolved = [r for r in rows if r["enabled"] != "1" or r["session"] == "À préciser" or r["zone"] == "À préciser" or "À relire" in r["notes"]]
    print(f"\nManifest CSV généré : {csv_path}")
    print(f"Manifest JSON généré : {json_path}")
    print(f"Sujets détectés : {len(rows)}")
    print("\nRépartition par année :"); [print(f"  {k}: {v}") for k,v in sorted(years.items())]
    print("\nRépartition par session :"); [print(f"  {k}: {v}") for k,v in sorted(sessions.items())]
    print("\nRépartition par zone :"); [print(f"  {k}: {v}") for k,v in sorted(zones.items())]
    if unresolved:
        print(f"\nLignes à relire : {len(unresolved)}")
        for r in unresolved[:20]: print(f"  - {r['pdf_path']} | annee={r['annee'] or '??'} | session={r['session']} | zone={r['zone']}")

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Génère site/data/manifest.csv et manifest.json à partir de site/pdf/*.pdf")
    p.add_argument("--root", type=Path, default=project_root_from_script())
    p.add_argument("--pdf-dir", type=Path, default=None)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--json-output", type=Path, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--no-pdf-text", action="store_true")
    return p.parse_args(argv)

def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve(); site_dir = root / "site"
    pdf_dir = args.pdf_dir.resolve() if args.pdf_dir else site_dir / "pdf"
    csv_path = args.output.resolve() if args.output else site_dir / "data" / "manifest.csv"
    json_path = args.json_output.resolve() if args.json_output else site_dir / "data" / "manifest.json"
    if not pdf_dir.exists(): print(f"ERREUR dossier PDF introuvable : {pdf_dir}", file=sys.stderr); return 2
    pdfs = sorted(pdf_dir.glob("*.pdf"), key=lambda p: p.name.lower())
    if not pdfs: print(f"ERREUR aucun PDF trouvé dans : {pdf_dir}", file=sys.stderr); return 2
    rows=[]
    for i,pdf_path in enumerate(pdfs, 1):
        print(f"[{i:03d}/{len(pdfs):03d}] analyse : {pdf_path.name}")
        try: rows.append(build_row(pdf_path, site_dir, not args.no_pdf_text))
        except Exception as exc:
            print(f"  ERREUR sur {pdf_path.name}: {exc}", file=sys.stderr)
            rows.append({"enabled":"0","id":slugify(pdf_path.stem),"titre":pdf_path.stem,"annee":"","session":"À préciser","zone":"À préciser","pdf_path":safe_relative_to(pdf_path, site_dir),"sha256":sha256_file(pdf_path) if pdf_path.exists() else "","notes":f"ERREUR pendant l'analyse : {exc}"})
    write_manifest_csv(rows, csv_path, not args.no_backup, args.force)
    write_manifest_json(rows, json_path, not args.no_backup, args.force)
    print_summary(rows, csv_path, json_path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
