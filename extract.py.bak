#!/usr/bin/env python3
"""
Stage 0 — corpus extraction + normalisation.

Walks the Gluth project folders, extracts text from every machine-readable
document, tags each chunk with metadata derived from the folder taxonomy,
and writes:

    out/chunks.jsonl    one JSON object per chunk (the retrieval corpus)
    out/manifest.json   per-file extraction status (what worked, what didn't)

No LLM, no network. Deterministic.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "out"

# Files that are never content.
JUNK_NAMES = {"Thumbs.db", ".DS_Store", "desktop.ini", ".gitattributes",
              ".gitignore", "BUILD_MANUAL.md", "README.md"}
JUNK_SUFFIX = {".lnk", ".url", ".ini", ".db"}
# Geometry, not text. Out of scope by decision.
CAD_SUFFIX = {".stp", ".step", ".ipt", ".iges", ".igs", ".sldprt", ".catpart"}
# Needs OCR before it holds any text.
IMAGE_SUFFIX = {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp"}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
# Below this many chars per page, a PDF is almost certainly a scan.
SCAN_CHARS_PER_PAGE = 100

# Project number -> customer. Derived from the corpus + interview.
CUSTOMER_BY_PROJECT = {
    "A07614000": "BMW",
    "A07616000": "Dräxlmaier",
    "A07622000": "BMG / ArGe",
    "R07591": "Bosch",
}

PHASE_RE = re.compile(r"^(\d)_(.+)$")
PROJECT_RE = re.compile(r"^([AR]\d{5,8})[_\s]*(.*)$")


def derive_meta(path: Path) -> dict:
    """Folder taxonomy is free, high-quality metadata. Use it."""
    rel = path.relative_to(ROOT)
    parts = rel.parts

    project_no, project_name = "", ""
    if parts:
        m = PROJECT_RE.match(parts[0])
        if m:
            project_no, project_name = m.group(1), m.group(2)

    phase_no, phase_name = "", ""
    for p in parts:
        m = PHASE_RE.match(p)
        if m:
            phase_no, phase_name = m.group(1), m.group(2)
            break

    return {
        "project_no": project_no,
        "project_name": project_name,
        "customer": CUSTOMER_BY_PROJECT.get(project_no, ""),
        "phase_no": phase_no,
        "phase_name": phase_name,
        "source_path": str(rel),
        "filename": path.name,
    }


# ---------------------------------------------------------------- extractors
# Each returns list[(page_label, text)].

def extract_pdf(path: Path):
    import pymupdf
    pages = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, 1):
            pages.append((str(i), page.get_text("text") or ""))
    return pages


def extract_docx(path: Path):
    from docx import Document
    doc = Document(str(path))
    body = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                body.append(" | ".join(cells))
    return [("", "\n".join(body))]


def extract_pptx(path: Path):
    from pptx import Presentation
    prs = Presentation(str(path))
    out = []
    for i, slide in enumerate(prs.slides, 1):
        buf = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                buf.append(shape.text_frame.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        buf.append(" | ".join(cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            buf.append("[Notiz] " + slide.notes_slide.notes_text_frame.text)
        out.append((f"Folie {i}", "\n".join(buf)))
    return out


def extract_xlsx(path: Path):
    from openpyxl import load_workbook
    wb = load_workbook(str(path), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            out.append((ws.title, "\n".join(rows)))
    wb.close()
    return out


def extract_msg(path: Path):
    import extract_msg as em
    m = em.Message(str(path))
    head = "\n".join(filter(None, [
        f"Von: {m.sender}" if m.sender else "",
        f"An: {m.to}" if m.to else "",
        f"Datum: {m.date}" if m.date else "",
        f"Betreff: {m.subject}" if m.subject else "",
    ]))
    body = m.body or ""
    attachments = []
    try:
        attachments = [a.longFilename or a.shortFilename or "" for a in m.attachments]
    except Exception:
        pass
    m.close()
    text = head + "\n\n" + body
    if attachments:
        text += "\n\n[Anhänge] " + ", ".join(a for a in attachments if a)
    return [("", text)]


def extract_doc(path: Path):
    """Legacy Word .doc via macOS textutil (built in, no install)."""
    import subprocess
    r = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)],
        capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:160]
                           or "textutil failed")
    return [("", r.stdout.decode("utf-8", "replace"))]


def extract_xml(path: Path):
    """Any XML: all element text plus attribute values."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    lines = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        bits = []
        if el.text and el.text.strip():
            bits.append(el.text.strip())
        for k, v in (el.attrib or {}).items():
            if v and v.strip():
                bits.append(f'{k.split("}")[-1]}={v.strip()}')
        if bits:
            lines.append(f'{tag}: ' + " | ".join(bits))
    return [("", "\n".join(lines))]


def extract_vsdx(path: Path):
    """Visio 2013+ is an OPC package — one page per XML part."""
    import re as _re
    import zipfile
    out = []
    with zipfile.ZipFile(path) as z:
        pages = sorted(n for n in z.namelist()
                       if _re.match(r"visio/pages/page\d+\.xml$", n))
        for name in pages:
            xml = z.read(name).decode("utf-8", "replace")
            # shape text lives in <Text> elements
            texts = _re.findall(r"<Text[^>]*>(.*?)</Text>", xml, _re.S)
            buf = []
            for t in texts:
                t = _re.sub(r"<[^>]+>", " ", t)
                t = (t.replace("&amp;", "&").replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&quot;", '"')
                      .replace("&#39;", "'"))
                t = " ".join(t.split())
                if t:
                    buf.append(t)
            if buf:
                num = _re.search(r"page(\d+)", name)
                out.append((f'Seite {num.group(1) if num else "?"}',
                            "\n".join(buf)))
    return out


def extract_7z(path: Path):
    """Unpack to a temp dir and run each member through its own handler."""
    import shutil
    import tempfile

    import py7zr
    out = []
    tmp = Path(tempfile.mkdtemp(prefix="gluth7z_"))
    try:
        with py7zr.SevenZipFile(path, mode="r") as z:
            z.extractall(path=str(tmp))
        for member in sorted(tmp.rglob("*")):
            if not member.is_file():
                continue
            suffix = member.suffix.lower()
            fn = EXTRACTORS.get(suffix)
            if fn is None or suffix == ".7z":
                continue
            try:
                for page, text in fn(member):
                    if not (text or "").strip():
                        continue
                    label = member.name + (f" · {page}" if page else "")
                    out.append((label, text))
            except Exception:
                continue          # one bad member must not kill the archive
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def extract_text(path: Path):
    return [("", path.read_text(encoding="utf-8", errors="replace"))]


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx, ".doc": extract_doc,
    ".pptx": extract_pptx,
    ".xlsx": extract_xlsx, ".xlsm": extract_xlsx,
    ".msg": extract_msg,
    ".xml": extract_xml,
    ".vsdx": extract_vsdx,
    ".7z": extract_7z,
    ".txt": extract_text, ".csv": extract_text, ".md": extract_text,
}


def chunk_text(text: str):
    """Fixed-size overlapping chunks, split on paragraph where possible."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_CHARS
        if end < len(text):
            brk = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if brk == -1:
                brk = text.rfind(" ", start + CHUNK_CHARS // 2, end)
            if brk != -1:
                end = brk
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = max(end - CHUNK_OVERLAP, end)
    return chunks


def load_ocr_sidecars() -> dict[str, list]:
    """OCR text produced by ocr.py, keyed by source_path."""
    ocr_dir = OUT / "ocr"
    if not ocr_dir.exists():
        return {}
    out = {}
    for f in ocr_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pages = [(p.get("page", ""), p.get("text", "")) for p in d.get("pages", [])]
        if pages:
            out[d["source_path"]] = pages
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, chunk_rows = [], []
    ocr_text = load_ocr_sidecars()
    if ocr_text:
        print(f"Found OCR sidecars for {len(ocr_text)} files\n", flush=True)
    t0 = time.time()

    files = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    files = [p for p in files if "pipeline" not in p.relative_to(ROOT).parts]
    files.sort()
    print(f"Scanning {len(files)} files under {ROOT}\n", flush=True)

    for path in files:
        suffix = path.suffix.lower()
        meta = derive_meta(path)
        size = path.stat().st_size
        row = {**meta, "bytes": size, "ext": suffix,
               "status": "", "reason": "", "chars": 0, "chunks": 0, "pages": 0}

        if path.name in JUNK_NAMES or path.name.startswith("~$") or suffix in JUNK_SUFFIX:
            row["status"] = "skipped"; row["reason"] = "junk/lock file"
            manifest.append(row); continue
        if suffix in CAD_SUFFIX:
            row["status"] = "out_of_scope"; row["reason"] = "CAD geometry, not text"
            manifest.append(row); continue
        rel = meta["source_path"]

        if suffix in IMAGE_SUFFIX:
            if rel in ocr_text:
                pages = ocr_text[rel]
                row["ocr"] = True
            else:
                row["status"] = "needs_ocr"; row["reason"] = "image — no text layer"
                manifest.append(row); continue
        elif suffix not in EXTRACTORS or EXTRACTORS[suffix] is None:
            row["status"] = "unsupported"; row["reason"] = f"no extractor for {suffix}"
            manifest.append(row); continue
        else:
            pages = None

        try:
            if pages is None:
                pages = EXTRACTORS[suffix](path)
        except Exception as e:
            row["status"] = "failed"
            row["reason"] = f"{type(e).__name__}: {str(e)[:160]}"
            manifest.append(row)
            print(f"  ✗ {meta['source_path']}  → {row['reason']}", flush=True)
            continue

        total_chars = sum(len(t) for _, t in pages)
        row["chars"] = total_chars
        row["pages"] = len(pages)

        if (suffix == ".pdf" and pages
                and total_chars / max(len(pages), 1) < SCAN_CHARS_PER_PAGE):
            if rel in ocr_text:
                # scanned, but ocr.py already recovered the text
                pages = ocr_text[rel]
                total_chars = sum(len(t) for _, t in pages)
                row["chars"] = total_chars
                row["pages"] = len(pages)
                row["ocr"] = True
            else:
                row["status"] = "needs_ocr"
                row["reason"] = (f"scanned PDF — "
                                 f"{total_chars // max(len(pages),1)} chars/page")
                manifest.append(row)
                continue
        if total_chars == 0:
            row["status"] = "empty"; row["reason"] = "no text found"
            manifest.append(row); continue

        n = 0
        for page_label, text in pages:
            for ci, piece in enumerate(chunk_text(text)):
                chunk_rows.append({
                    "id": f"{meta['source_path']}#{page_label}:{ci}",
                    **meta,
                    "page": page_label,
                    "text": piece,
                })
                n += 1
        row["chunks"] = n
        row["status"] = "ok"
        manifest.append(row)

    with (OUT / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunk_rows:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- report ----
    from collections import Counter
    by_status = Counter(r["status"] for r in manifest)
    print("\n" + "=" * 62)
    print(f"  Files scanned : {len(manifest)}")
    print(f"  Chunks written: {len(chunk_rows):,}")
    print(f"  Elapsed       : {time.time()-t0:.1f}s")
    print("=" * 62)
    for status, cnt in by_status.most_common():
        chars = sum(r["chars"] for r in manifest if r["status"] == status)
        print(f"  {status:<14} {cnt:>4} files   {chars:>12,} chars")
    print("=" * 62)

    content = [r for r in manifest if r["status"] not in ("skipped", "out_of_scope")]
    ok = [r for r in content if r["status"] == "ok"]
    print(f"  Extraction rate: {len(ok)}/{len(content)} "
          f"= {100*len(ok)/max(len(content),1):.0f}% of content files\n")

    by_ext = {}
    for r in content:
        d = by_ext.setdefault(r["ext"], {"ok": 0, "total": 0})
        d["total"] += 1
        d["ok"] += r["status"] == "ok"
    print("  By format:")
    for ext, d in sorted(by_ext.items(), key=lambda x: -x[1]["total"]):
        print(f"    {ext:<7} {d['ok']:>3}/{d['total']:<3} "
              f"{100*d['ok']/d['total']:>3.0f}%")
    print()


if __name__ == "__main__":
    sys.exit(main())
