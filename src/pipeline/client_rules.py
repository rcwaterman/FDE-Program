"""Per-client extraction conventions, looked up deterministically (no LLM call, no network).

client_rules.json (next to this module; empty by default) maps a client to rules such as "handwritten amounts in the Earnings column are tips".
A matching client's confirmed rules are added to the page prompt as a JSON block, so Claude applies the
client's habits instead of guessing. Clients with no rules send nothing extra.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def _norm(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"clients": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def lookup(rules: dict, client_number: str | None = None, client_name: str | None = None) -> dict | None:
    """The client's entry, matched on client number first, then on any normalized name or alias."""
    clients = rules.get("clients") or {}
    number = (client_number or "").strip()
    if number:
        for key, entry in clients.items():
            if number in (entry.get("numbers") or []):
                return {"key": key, **entry}
    wanted = _norm(client_name)
    if wanted:
        for key, entry in clients.items():
            names = [key, *(entry.get("names") or [])]
            if any(_norm(n) == wanted for n in names):
                return {"key": key, **entry}
    return None


def active_rules(entry: dict | None, include_candidates: bool = False) -> list[dict]:
    if not entry:
        return []
    keep = ("confirmed", "candidate") if include_candidates else ("confirmed",)
    return [r for r in entry.get("rules", []) if r.get("status") in keep]


def prompt_block(entry: dict | None, rules: list[dict]) -> str | None:
    """The block added to the page prompt. None when there is nothing to tell Claude."""
    if not entry or not rules:
        return None
    body = {"client": entry.get("key"), "rules": [
        {"id": r["id"], "applies_to": r.get("applies_to"), "meaning": r.get("meaning"), "text": r["text"]}
        for r in rules]}
    return ("CLIENT RULES (client-specific conventions from past payroll; authoritative for this client over general "
            "heuristics, but an explicit code or the printed legend on the page still wins). Set kind_basis to "
            "\"client_rule\" and list the rule ids you applied in client_rules_applied.\n"
            + json.dumps(body, indent=1, ensure_ascii=False))


def rules_hash(rules: list[dict]) -> str | None:
    """Cache key for extraction: changes whenever the rules sent to Claude change."""
    if not rules:
        return None
    return hashlib.sha1(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:12]
