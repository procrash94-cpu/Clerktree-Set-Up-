#!/usr/bin/env python3
"""
Persistent state for the workspace: inquiries, drafts, corrections, triage.

SQLite, single file, no server. Seeded from the real corpus — the projects in
the queue are the projects on disk, not fixtures.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
DB = OUT / "workspace.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS inquiry (
    project_no   TEXT PRIMARY KEY,
    project_name TEXT,
    customer     TEXT,
    stage        TEXT,
    status       TEXT,
    confidence   TEXT,
    assigned_to  TEXT,
    doc_count    INTEGER DEFAULT 0,
    chunk_count  INTEGER DEFAULT 0,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS correction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_no  TEXT,
    artifact    TEXT,
    field       TEXT,
    action      TEXT,
    route_tag   TEXT,
    reviewer    TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS decision (
    project_no  TEXT PRIMARY KEY,
    verdict     TEXT,
    criteria    TEXT,
    assigned_to TEXT,
    note        TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS review (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_no  TEXT,
    artifact    TEXT,
    outcome     TEXT,          -- approved | corrected
    reviewer    TEXT,
    created_at  TEXT
);
"""

# Ownership is derived from the customer, not from a table of names. The UI
# renders this as "Vertrieb <customer>" / "Sales <customer>", so no person is
# invented anywhere in the system. Replace with real names only once they are
# confirmed by Gluth.
UNASSIGNED = ""


def route_for(customer: str) -> str:
    """Owning team for a customer. Empty string means: not assigned."""
    return (customer or "").strip() or UNASSIGNED


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def phase_no(phase: str) -> int:
    """Leading number of a phase folder, or -1 if it has none."""
    head = (phase or "").split("_", 1)[0]
    return int(head) if head.isdigit() else -1


def highest_phase(phases: set[str]) -> str:
    """
    The furthest phase folder that actually holds extracted documents.

    This is a statement of fact about the corpus, not an inferred workflow
    stage. Folder 8 is named per project (8_Nachträge, 8_Konzept, …), so it
    is ranked by its number alone and never matched by name.
    """
    real = [p for p in phases if phase_no(p) >= 0]
    return max(real, key=lambda p: (phase_no(p), p)) if real else ""


def project_status(project_no: str) -> str:
    """
    Outcome, taken from Gluth's own project-number prefix.

        A…  the project was not completed          -> OPEN
        R…  the project was fully realised         -> REALISED

    This is NOT derived from the folders. The corpus holds no order
    documents at all (7_Auftrag is empty in every project), so the file
    tree cannot tell won from lost — the prefix is the only signal there is.
    """
    p = (project_no or "").strip().upper()
    if p.startswith("R"):
        return "REALISED"
    if p.startswith("A"):
        return "OPEN"
    return "UNKNOWN"


def seed_from_corpus(conn: sqlite3.Connection, chunks: list[dict]) -> int:
    """Create one inquiry per project actually present in the corpus."""
    stats: dict[str, dict] = {}
    for c in chunks:
        pno = c.get("project_no")
        if not pno:
            continue
        s = stats.setdefault(pno, {
            "project_name": c.get("project_name", ""),
            "customer": c.get("customer", ""),
            "files": set(), "chunks": 0, "phases": set(),
        })
        s["files"].add(c["source_path"])
        s["chunks"] += 1
        if c.get("phase_no"):
            s["phases"].add(f'{c["phase_no"]}_{c["phase_name"]}')

    added = 0
    for pno, s in stats.items():
        stage = highest_phase(s["phases"])
        status = project_status(pno)
        existing = conn.execute(
            "SELECT 1 FROM inquiry WHERE project_no=?", (pno,)).fetchone()
        if existing:
            # Always refresh: an older workspace.db still carries the old
            # folder-derived values ("Won", "Awaiting review").
            conn.execute(
                "UPDATE inquiry SET doc_count=?, chunk_count=?, stage=?,"
                " status=?, updated_at=? WHERE project_no=?",
                (len(s["files"]), s["chunks"], stage, status, now(), pno))
            continue
        conn.execute(
            "INSERT INTO inquiry (project_no, project_name, customer, stage,"
            " status, confidence, assigned_to, doc_count, chunk_count, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pno, s["project_name"], s["customer"], stage, status, "",
             route_for(s["customer"]),
             len(s["files"]), s["chunks"], now()))
        added += 1
    conn.commit()
    refresh_routing(conn)
    return added


def refresh_routing(conn: sqlite3.Connection) -> int:
    """
    Re-apply ownership to rows that still carry a hard-coded person's name.

    Existing workspace.db files were seeded with invented names (M. Weber,
    K. Schmidt, H. Becker, "Admin triage"). Those must not survive into a
    demo, so they are rewritten to the customer-derived team.
    """
    legacy = ("M. Weber", "K. Schmidt", "H. Becker", "Admin triage")
    changed = 0
    for r in conn.execute("SELECT project_no, customer, assigned_to"
                          " FROM inquiry").fetchall():
        if r["assigned_to"] in legacy:
            conn.execute("UPDATE inquiry SET assigned_to=? WHERE project_no=?",
                         (route_for(r["customer"]), r["project_no"]))
            changed += 1
    if changed:
        conn.commit()
    return changed


def add_inquiry(conn: sqlite3.Connection, project_no: str, customer: str = "",
                project_name: str = "") -> bool:
    """
    Create an empty project row by hand.

    This records a project so it can be triaged. It does NOT read any
    document — documents only enter the system through extract.py.
    Returns False if the project number already exists.
    """
    project_no = (project_no or "").strip()
    if not project_no:
        raise ValueError("project_no required")
    if conn.execute("SELECT 1 FROM inquiry WHERE project_no=?",
                    (project_no,)).fetchone():
        return False
    conn.execute(
        "INSERT INTO inquiry (project_no, project_name, customer, stage,"
        " status, confidence, assigned_to, doc_count, chunk_count, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (project_no, (project_name or "").strip(), (customer or "").strip(),
         "", project_status(project_no), "", route_for(customer), 0, 0, now()))
    conn.commit()
    return True


# ------------------------------------------------------------------ queries
def list_inquiries(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM inquiry ORDER BY project_no").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        dec = conn.execute(
            "SELECT verdict FROM decision WHERE project_no=?",
            (d["project_no"],)).fetchone()
        d["verdict"] = dec["verdict"] if dec else ""
        d["corrections"] = conn.execute(
            "SELECT COUNT(*) c FROM correction WHERE project_no=?",
            (d["project_no"],)).fetchone()["c"]
        out.append(d)
    return out


def add_correction(conn, **kw) -> int:
    cur = conn.execute(
        "INSERT INTO correction (project_no, artifact, field, action,"
        " route_tag, reviewer, created_at) VALUES (?,?,?,?,?,?,?)",
        (kw.get("project_no", ""), kw.get("artifact", ""), kw.get("field", ""),
         kw.get("action", ""), kw.get("route_tag", "Content"),
         kw.get("reviewer", "local"), now()))
    conn.execute(
        "INSERT INTO review (project_no, artifact, outcome, reviewer, created_at)"
        " VALUES (?,?,?,?,?)",
        (kw.get("project_no", ""), kw.get("artifact", ""), "corrected",
         kw.get("reviewer", "local"), now()))
    conn.commit()
    return cur.lastrowid or 0


def list_corrections(conn, route_tag: str | None = None) -> list[dict]:
    sql = "SELECT * FROM correction"
    args: tuple = ()
    if route_tag and route_tag != "ALL":
        sql += " WHERE route_tag=?"
        args = (route_tag,)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def approve(conn, project_no: str, artifact: str, reviewer: str = "local"):
    conn.execute(
        "INSERT INTO review (project_no, artifact, outcome, reviewer, created_at)"
        " VALUES (?,?,?,?,?)", (project_no, artifact, "approved", reviewer, now()))
    # status now carries the project outcome (A/R prefix) and must not be
    # overwritten by a review. Approvals live in the review table.
    conn.execute("UPDATE inquiry SET updated_at=? WHERE project_no=?",
                 (now(), project_no))
    conn.commit()


def set_decision(conn, project_no: str, verdict: str, criteria: list,
                 assigned_to: str = "", note: str = ""):
    conn.execute(
        "INSERT INTO decision (project_no, verdict, criteria, assigned_to, note,"
        " created_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(project_no) DO UPDATE SET verdict=excluded.verdict,"
        " criteria=excluded.criteria, assigned_to=excluded.assigned_to,"
        " note=excluded.note, created_at=excluded.created_at",
        (project_no, verdict, json.dumps(criteria), assigned_to, note, now()))
    if assigned_to:
        conn.execute("UPDATE inquiry SET assigned_to=? WHERE project_no=?",
                     (assigned_to, project_no))
    conn.commit()


def review_stats(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) c FROM review").fetchone()["c"]
    approved = conn.execute(
        "SELECT COUNT(*) c FROM review WHERE outcome='approved'").fetchone()["c"]
    corr = conn.execute("SELECT COUNT(*) c FROM correction").fetchone()["c"]
    drift = conn.execute(
        "SELECT COUNT(*) c FROM correction WHERE route_tag='Drift'").fetchone()["c"]
    top = conn.execute(
        "SELECT field, COUNT(*) c FROM correction GROUP BY field"
        " ORDER BY c DESC LIMIT 1").fetchone()
    return {
        "reviews": total, "approved": approved, "corrections": corr,
        "drift": drift,
        "top_field": top["field"] if top else "—",
        "top_field_count": top["c"] if top else 0,
    }
