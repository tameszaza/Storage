#!/usr/bin/env python3
"""Local, text-only ChatGPT export collector. Python standard library only."""
import argparse, hashlib, hmac, json, math, os, re, secrets, socket, sqlite3, tempfile, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8766
MAX_BODY = 64 * 1024 * 1024

def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError("Invalid identifier")
    return value

def text_value(value):
    return value if isinstance(value, str) else ""

def clean(doc):
    """An explicit allowlist: never store media blobs, auth, or full remote metadata."""
    cid = valid_id(doc.get("id"))
    msgs = []
    for msg in doc.get("messages", []):
        if not isinstance(msg, dict) or not isinstance(msg.get("text"), str):
            raise ValueError("Invalid text message")
        msgs.append({
            "id": valid_id(msg["id"]), "parent_id": text_value(msg.get("parent_id")),
            "role": text_value(msg.get("role")), "name": text_value(msg.get("name")),
            "created_at": msg.get("created_at") if isinstance(msg.get("created_at"), (int,float,str)) else None,
            "on_current_branch": bool(msg.get("on_current_branch")), "text": msg["text"],
        })
    return {
        "id": cid, "title": text_value(doc.get("title")),
        "created_at": doc.get("created_at") if isinstance(doc.get("created_at"), (int,float,str)) else None,
        "updated_at": doc.get("updated_at") if isinstance(doc.get("updated_at"), (int,float,str)) else None,
        "source_updated": text_value(doc.get("source_updated")),
        "sources": [text_value(s) for s in doc.get("sources", [])],
        "current_node": text_value(doc.get("current_node")),
        "omitted_nontext_parts": max(0, int(doc.get("omitted_nontext_parts", 0))),
        "messages": msgs,
    }

def render(doc):
    lines = [doc["title"] or "(untitled)", "Conversation: " + doc["id"],
             "Created: " + str(doc.get("created_at")), "Updated: " + str(doc.get("updated_at")),
             "All available text messages; alternate branches are labelled.", ""]
    for m in doc["messages"]:
        branch = "current branch" if m["on_current_branch"] else "alternate branch"
        lines.extend([f'[{m["role"]} | {m["created_at"]} | {branch}]',
                      f'ID: {m["id"]}  Parent: {m["parent_id"]}', m["text"], ""])
    return "\n".join(lines)

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "progress.sqlite3", timeout=45)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("CREATE TABLE IF NOT EXISTS conversations(scope TEXT, id TEXT, updated TEXT, doc TEXT, PRIMARY KEY(scope,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS runs(scope TEXT PRIMARY KEY, state TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS queue(scope TEXT,id TEXT,item TEXT,PRIMARY KEY(scope,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS discovery(scope TEXT PRIMARY KEY,progress TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY,state TEXT)")
        self.db.commit()

    def scope_dir(self, scope):
        valid_id(scope)
        return self.root / "exports" / scope

    def state(self, scope):
        return {"known": {cid: updated for cid,updated in self.db.execute(
            "SELECT id,updated FROM conversations WHERE scope=?", (scope,))}, "discovery":self.discovery(scope)}


    def discovery(self, scope):
        row = self.db.execute("SELECT progress FROM discovery WHERE scope=?", (scope,)).fetchone()
        return {
            "items": [json.loads(raw) for raw, in self.db.execute(
                "SELECT item FROM queue WHERE scope=? ORDER BY rowid", (scope,))],
            "progress": json.loads(row[0]) if row else {"version":2,"lists":{},"projects":[]},
        }

    def checkpoint(self, scope, payload):
        progress = payload.get("progress", {})
        def safe_progress(value, is_recent=False):
            lists = {}
            for key, entry in value.get("lists", {}).items():
                if key not in ["regular","archived","projects"]:
                    if not key.startswith("project:"): raise ValueError("Unknown list")
                    valid_id(key.removeprefix("project:"))
                cursor = entry.get("cursor")
                if cursor is not None and (not isinstance(cursor,str) or len(cursor)>16000):
                    raise ValueError("Invalid cursor")
                seen = entry.get("seen",[])
                if not isinstance(seen,list) or any(not isinstance(v,str) or len(v)>4096 for v in seen):
                    raise ValueError("Invalid pagination history")
                clean_entry = {"offset":max(0,int(entry.get("offset",0))), "cursor":cursor,
                               "done":bool(entry.get("done")), "seen":seen}
                if is_recent:
                    clean_entry.update({"unchanged_pages":max(0,int(entry.get("unchanged_pages",0))),
                        "boundary":bool(entry.get("boundary")), "order_invalid":bool(entry.get("order_invalid")),
                        "ids":[valid_id(v) for v in entry.get("ids",[])]})
                    last = entry.get("last_updated")
                    if last is not None:
                        if not isinstance(last,(int,float)) or not math.isfinite(last):
                            raise ValueError("Invalid update boundary")
                        clean_entry["last_updated"]=last
                lists[key] = clean_entry
            safe = {"version":2,"lists":lists,
                    "projects":[valid_id(v) for v in value.get("projects",[])]}
            if is_recent: safe["done"]=bool(value.get("done"))
            return safe
        safe = safe_progress(progress)
        if isinstance(progress.get("recent"),dict):
            safe["recent"]=safe_progress(progress["recent"],True)
        items = []
        for item in payload.get("items",[]):
            sources = item.get("sources",[])
            if any(not isinstance(s,str) or (s not in ["regular","archived"] and
                not re.fullmatch(r"project:[A-Za-z0-9_-]+",s)) for s in sources):
                raise ValueError("Invalid source")
            items.append({
                "id":valid_id(item.get("id")), "title":text_value(item.get("title")),
                "create_time":item.get("create_time") if isinstance(item.get("create_time"),(str,int,float)) else None,
                "update_time":item.get("update_time") if isinstance(item.get("update_time"),(str,int,float)) else None,
                "sources":sources,
            })
        # Advance the offset/cursor only in the same transaction that preserves its IDs.
        with self.db:
            for item in items:
                row=self.db.execute("SELECT item FROM queue WHERE scope=? AND id=?",(scope,item["id"])).fetchone()
                if row:
                    old=json.loads(row[0])
                    item["sources"]=list(dict.fromkeys(old["sources"]+item["sources"]))
                    if item["update_time"] is None: item["update_time"]=old["update_time"]
                self.db.execute("INSERT OR REPLACE INTO queue VALUES(?,?,?)",
                                (scope,item["id"],json.dumps(item,ensure_ascii=False)))
                saved=self.db.execute("SELECT doc FROM conversations WHERE scope=? AND id=?",(scope,item["id"])).fetchone()
                if saved:
                    doc=json.loads(saved[0])
                    doc["sources"]=list(dict.fromkeys(doc.get("sources",[])+item["sources"]))
                    self.db.execute("UPDATE conversations SET doc=? WHERE scope=? AND id=?",
                                    (json.dumps(doc,ensure_ascii=False),scope,item["id"]))
            self.db.execute("INSERT OR REPLACE INTO discovery VALUES(?,?)",(scope,json.dumps(safe)))
        return {"queued":self.db.execute("SELECT count(*) FROM queue WHERE scope=?",(scope,)).fetchone()[0]}

    def throttle(self):
        row=self.db.execute("SELECT state FROM control WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"not_before":0,"next_request":0,"interval_ms":3000}

    def reserve(self, now=None):
        now=time.time()*1000 if now is None else now
        control=self.throttle()
        at=max(now,control["not_before"],control["next_request"])
        control["next_request"]=at+control["interval_ms"]
        with self.db:self.db.execute("INSERT OR REPLACE INTO control VALUES(1,?)",(json.dumps(control),))
        return {"at":at,**control}

    def cooldown(self, payload):
        until=float(payload.get("until",0))
        interval=float(payload.get("interval_ms",3000))
        if not math.isfinite(until) or not math.isfinite(interval):raise ValueError("Invalid cooldown")
        control=self.throttle()
        control["not_before"]=max(control["not_before"],until)
        control["interval_ms"]=max(control["interval_ms"],min(30000,max(3000,interval)))
        with self.db:self.db.execute("INSERT OR REPLACE INTO control VALUES(1,?)",(json.dumps(control),))
        return control

    def save(self, scope, doc):
        doc = clean(doc)
        folder = self.scope_dir(scope)
        atomic(folder / "conversations" / (doc["id"] + ".txt"), render(doc))
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?,?)",
                            (scope, doc["id"], doc["source_updated"], json.dumps(doc,ensure_ascii=False)))
        return {"saved": True, "messages": len(doc["messages"])}

    def report(self, scope, report):
        # Keep only diagnostic counts/IDs/categories, never request bodies or exceptions.
        safe = {
            "status": report.get("status") if report.get("status") in
                      ["running","waiting","paused","complete","incomplete"] else "incomplete",
            "discovered": int(report.get("discovered",0)),
            "saved_this_run": int(report.get("saved_this_run",0)),
            "rate_limits": int(report.get("rate_limits",0)),
            "skipped_unchanged": int(report.get("skipped_unchanged",0)),
            "errors": [{"stage": text_value(e.get("stage")), "id": text_value(e.get("id")),
                        "code": text_value(e.get("code"))} for e in report.get("errors",[])],
            "discovery_finished": bool(report.get("discovery_finished")),
            "scan_mode": "recent" if report.get("scan_mode")=="recent" else "full",
            "recent_finished": bool(report.get("recent_finished")),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
            "scope": scope,
            "limitations": [
                "Only conversations currently accessible to the selected account/workspace.",
                "Deleted and temporary chats cannot be recovered.",
                "No images, audio, videos, uploaded files, or linked file contents downloaded.",
                "Website endpoints are undocumented and may change.",
                "A live account is not an atomic snapshot; rerun to capture changes during export.",
                "Recent checks stop after two unchanged pages in update order; older gaps/moves need a full scan. Project lists are scanned fully.",
            ],
        }
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?)",(scope,json.dumps(safe)))
        atomic(self.scope_dir(scope) / "report.json",json.dumps(safe,ensure_ascii=False,indent=2)+"\n")
        return safe

    def finalize(self, scope):
        # Release the live reader lock before writing large combined exports.
        with tempfile.TemporaryDirectory(prefix='.finalize-',dir=self.root) as directory:
            snapshot=sqlite3.connect(Path(directory)/'source.sqlite3')
            self.db.backup(snapshot,pages=128,sleep=.02)
            original=self.db;self.db=snapshot
            try:return self._finalize(scope)
            finally:self.db=original;snapshot.close()

    def _finalize(self, scope):
        folder = self.scope_dir(scope)
        folder.mkdir(parents=True,exist_ok=True)
        # Streaming writes keep memory bounded even for large accounts.
        files = {}
        counts = {"conversations":0,"messages":0,"omitted_nontext_parts":0}
        try:
            for name in ["messages.jsonl","all-chats.txt","catalog.jsonl"]:
                fd,tmp = tempfile.mkstemp(dir=folder,prefix=".writing-")
                files[name] = (os.fdopen(fd,"w",encoding="utf-8"),tmp)
            for (raw,) in self.db.execute("SELECT doc FROM conversations WHERE scope=? ORDER BY id",(scope,)):
                doc = json.loads(raw)
                counts["conversations"] += 1
                counts["omitted_nontext_parts"] += doc["omitted_nontext_parts"]
                files["all-chats.txt"][0].write(render(doc)+"\n"+"="*72+"\n\n")
                files["catalog.jsonl"][0].write(json.dumps(
                    {k:v for k,v in doc.items() if k != "messages"},ensure_ascii=False)+"\n")
                for m in doc["messages"]:
                    files["messages.jsonl"][0].write(json.dumps(
                        {"conversation_id":doc["id"],"title":doc["title"],**m},ensure_ascii=False)+"\n")
                    counts["messages"] += 1
            for name,(f,tmp) in files.items():
                f.flush(); os.fsync(f.fileno()); f.close(); os.replace(tmp,folder/name)
        finally:
            for f,tmp in files.values():
                if not f.closed: f.close()
                if os.path.exists(tmp): os.unlink(tmp)
        atomic(folder/"summary.json",json.dumps(counts,indent=2)+"\n")
        return {**counts,"path":str(folder)}

    def overview(self):
        scopes=[s for s, in self.db.execute("SELECT scope FROM runs UNION SELECT scope FROM queue UNION SELECT scope FROM conversations")]
        result=[]
        for scope in scopes:
            conversations=self.db.execute("SELECT count(*) FROM conversations WHERE scope=?",(scope,)).fetchone()[0]
            queued=self.db.execute("SELECT count(*) FROM queue WHERE scope=?",(scope,)).fetchone()[0]
            row=self.db.execute("SELECT state FROM runs WHERE scope=?",(scope,)).fetchone()
            result.append({"scope":scope,"conversations":conversations,"discovered":queued,
                           "report":json.loads(row[0]) if row else {}})
        return result

