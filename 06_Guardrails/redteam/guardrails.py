"""The ladder for the email-amendments door, as plain Python wrapped around the real apply_amendments().

Untracked prototype: install() monkeypatches stages.amendments.apply_amendments so the REAL stage run() is what gets
tested. A shipped version would be a small change inside amendments.py (pass the snapshot's contacts in).

Rungs (each can be switched on alone, which is how Step 5 prices them):
  L1_rules      regex over the cleaned email text for text addressed to a model / forged headers / encoded blobs
  L4_authority  the sender must be a known contact of the case's account (policy layer: an `if`, not a prompt)
  L4_grounding  the quote must really be in that email; add/modify must name an employee who is on the payroll
  L4_cap        a single amount/hours value above a ceiling is held for a person

A held amendment is never silently dropped: it becomes action "info" plus an AMENDMENT_HELD review flag, so the
specialist sees it. Fail closed: any exception in a rung holds the amendment.
"""

from __future__ import annotations

import re

from payroll_timesheet_pipeline.stages import amendments as A
from payroll_timesheet_pipeline.values import name_key, parse_amount, parse_quantity

RULES = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"\b(admin|developer|system|operator) (mode|override|prompt|instruction)",
    r"^\s*system\s*:",
    r"(note|message|instruction)s? (to|for) (the )?(payroll )?(ai|assistant|model|llm|processor|bot)",
    r"\brecord_amendments\b|\btool call\b|\bcall (the )?\w+ tool\b",
    r"do not (mention|tell|flag|report)",
    r"\[email_message_id=",  # forged thread delimiter
    r"-{3,}\s*end of",  # fake end of input
    r"[A-Za-z0-9+/]{24,}={0,2}",  # long base64-ish blob
]
RULES_RE = [re.compile(p, re.I | re.M) for p in RULES]
CEILING_AMOUNT = 5000.0
CEILING_HOURS = 120.0

ON = {"L1_rules", "L4_authority", "L4_grounding", "L4_cap"}
CONTACTS: list[dict] = []  # set by the harness / caller from the snapshot
LOG: list[dict] = []  # which rung held what (for the before/after table)
_orig = A.apply_amendments


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def check(a: dict, em: dict, rec: dict, contacts: list[dict], on=None) -> str | None:
    """Reason this amendment must be held, or None. Only add/modify/exclude can change pay, so only they are checked."""
    on = ON if on is None else on
    if a.get("action") not in ("add", "modify", "exclude"):
        return None
    try:
        cleaned = A.clean_email(em.get("text"))
        if "L1_rules" in on:
            for rx in RULES_RE:
                if rx.search(cleaned):
                    return f"L1_rules: pattern {rx.pattern[:40]!r}"
        if "L4_authority" in on:
            known = {(c.get("email") or "").lower() for c in contacts if c.get("email")}
            # `incoming` is set by Salesforce from the mail direction, so an outside sender cannot claim to be staff;
            # staff-written mail (incoming False) may record client-requested changes, as AMEND_SYSTEM already says.
            if em.get("incoming") and (em.get("from") or "").lower() not in known:
                return "L4_authority: sender is not a contact on the account"
        if "L4_grounding" in on:
            if _norm(a.get("instruction_quote")) not in _norm(cleaned):
                return "L4_grounding: quote is not in the email"
            names = {name_key(e["name"]["full"]) for e in rec["employees"]}
            nm = a.get("employee_name")
            if a["action"] in ("add", "modify") and not a.get("target_item_ids") and name_key(nm or "") not in names:
                return "L4_grounding: employee is not on this payroll"
            if nm and name_key(nm) not in names:
                return "L4_grounding: employee is not on this payroll"
        if "L4_cap" in on:
            nv = a.get("new_value") or {}
            amt = parse_amount(nv.get("amount_text")) if nv.get("amount_text") else None
            q = parse_quantity(nv.get("hours_text")) if nv.get("hours_text") else None
            if (amt is not None and abs(float(amt)) > CEILING_AMOUNT) or (
                q is not None and q.value is not None and abs(float(q.value)) > CEILING_HOURS
            ):
                return "L4_cap: value above ceiling"
    except Exception as e:  # fail closed
        return f"error: {type(e).__name__}"
    return None


def guarded(rec, amendments, snap_emails):
    kept = []
    for a in amendments:
        why = check(a, snap_emails.get(a["email_message_id"], {}), rec, CONTACTS)
        if why:
            LOG.append({"held": why, "quote": a.get("instruction_quote", "")[:80]})
            rec["review_flags"].append(
                {"code": "AMENDMENT_HELD", "severity": "warning", "refs": [],
                 "message": f"held for a person ({why}): {a.get('instruction_quote', '')[:160]}"}
            )
            a = {**a, "action": "info"}
        kept.append(a)
    return _orig(rec, kept, snap_emails)


def install(on=None):
    global ON
    ON = set(ON if on is None else on)
    A.apply_amendments = guarded


def uninstall():
    A.apply_amendments = _orig
