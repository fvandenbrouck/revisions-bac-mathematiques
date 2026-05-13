#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extrait le texte et les images PNG de chaque page des PDF de sujets."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from typing import Iterable
import fitz
import pdfplumber
from config import DEFAULT_DPI, DISCIPLINE, ENSEIGNEMENT, IMG_DIR, MANIFEST_CSV, NIVEAU, PAGES_DIR, SITE_ROOT
from utils import clean_pdf_text, ensure_dirs, read_csv_dicts, site_relative, write_json

def enabled(row: dict[str, str]) -> bool:
    return str(row.get("enabled", "1")).strip().lower() not in {"0", "false", "non", "no"}

def resolve_pdf_path(row: dict[str, str]) -> Path:
    pdf_rel = row.get("pdf_path", "").strip()
    if not pdf_rel:
        raise ValueError(f"pdf_path vide pour le sujet {row.get('id', 'sans id')}")
    pdf_path = Path(pdf_rel)
    if pdf_path.is_absolute():
        raise ValueError(f"Chemin absolu interdit dans le manifest : {pdf_path}")
    return SITE_ROOT / pdf_path

def extract_text_with_pdfplumber(pdf_path: Path) -> dict[int, str]:
    out = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try: text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            except Exception as exc: text = f"[ERREUR EXTRACTION TEXTE PDFPLUMBER PAGE {i}: {exc}]"
            out[i] = clean_pdf_text(text)
    return out

def extract_text_with_pymupdf(pdf_path: Path) -> dict[int, str]:
    out = {}
    doc = fitz.open(str(pdf_path))
    try:
        for index in range(doc.page_count):
            page_no = index + 1
            try: text = doc.load_page(index).get_text("text") or ""
            except Exception as exc: text = f"[ERREUR EXTRACTION TEXTE PYMUPDF PAGE {page_no}: {exc}]"
            out[page_no] = clean_pdf_text(text)
    finally:
        doc.close()
    return out

def extract_text_by_page(pdf_path: Path) -> dict[int, str]:
    primary = extract_text_with_pdfplumber(pdf_path)
    if all(text.strip() for text in primary.values()):
        return primary
    fallback = extract_text_with_pymupdf(pdf_path)
    return {i: (primary.get(i) or fallback.get(i) or "") for i in sorted(set(primary) | set(fallback))}

def render_pages_to_png(pdf_path: Path, source_id: str, dpi: int, force: bool) -> list[dict]:
    out_img_dir = IMG_DIR / source_id
    ensure_dirs(out_img_dir)
    doc = fitz.open(str(pdf_path)); zoom = dpi / 72.0; matrix = fitz.Matrix(zoom, zoom); pages=[]
    try:
        for index in range(doc.page_count):
            page_no = index + 1
            img_path = out_img_dir / f"page_{page_no:03d}.png"
            if force or not img_path.exists():
                pix = doc.load_page(index).get_pixmap(matrix=matrix, alpha=False)
                pix.save(str(img_path))
            pages.append({"page": page_no, "image": site_relative(img_path, SITE_ROOT)})
    finally:
        doc.close()
    return pages

def extract_one(row: dict[str, str], dpi: int = DEFAULT_DPI, force: bool = False) -> dict:
    source_id = row["id"]; pdf_path = resolve_pdf_path(row)
    if not pdf_path.exists(): raise FileNotFoundError(f"PDF introuvable pour {source_id}: {pdf_path}")
    out_json = PAGES_DIR / f"{source_id}.json"
    ensure_dirs(PAGES_DIR, IMG_DIR / source_id)
    if out_json.exists() and not force:
        return {"id": source_id, "status": "skipped", "reason": "already extracted", "json": site_relative(out_json, SITE_ROOT)}
    text_by_page = extract_text_by_page(pdf_path)
    pages = render_pages_to_png(pdf_path, source_id, dpi, force)
    for page in pages:
        page["text"] = text_by_page.get(page["page"], "")
    result = {"source_id": source_id, "discipline": DISCIPLINE, "niveau": NIVEAU, "enseignement": ENSEIGNEMENT, "titre": row.get("titre", ""), "annee": row.get("annee", ""), "session": row.get("session", ""), "zone": row.get("zone", ""), "pdf_path": row.get("pdf_path", ""), "sha256": row.get("sha256", ""), "page_count": len(pages), "dpi": dpi, "pages": pages}
    write_json(out_json, result)
    return {"id": source_id, "status": "ok", "pages": len(pages), "json": site_relative(out_json, SITE_ROOT)}

def filter_rows(rows: Iterable[dict[str, str]], only: str | None = None) -> list[dict[str, str]]:
    filtered = [r for r in rows if enabled(r)]
    if only: filtered = [r for r in filtered if r.get("id") == only]
    return filtered

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrait le texte des PDF et rend chaque page en PNG.")
    p.add_argument("--manifest", type=Path, default=MANIFEST_CSV)
    p.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--only", type=str, default=None)
    return p.parse_args()

def main() -> int:
    args = parse_args()
    if not args.manifest.exists(): print(f"ERREUR manifest introuvable : {args.manifest}", file=sys.stderr); return 2
    rows = filter_rows(read_csv_dicts(args.manifest), args.only)
    if args.limit: rows = rows[:args.limit]
    if not rows: print("Aucun sujet activé dans le manifest.", file=sys.stderr); return 1
    ok=skipped=errors=0
    for row in rows:
        try:
            res = extract_one(row, args.dpi, args.force); print(res)
            ok += res["status"] == "ok"; skipped += res["status"] == "skipped"
        except Exception as exc:
            errors += 1; print({"id": row.get("id"), "status": "error", "error": str(exc)}, file=sys.stderr)
    print(f"Extraction terminée : {ok} ok, {skipped} ignoré(s), {errors} erreur(s), {len(rows)} sujet(s) considéré(s).")
    return 0 if errors == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
