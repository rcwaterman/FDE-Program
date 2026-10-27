"""Stage 8: totals vs pay items."""
from __future__ import annotations

from .base import Context, StageSkip

COUNTED = {"submitted", "printed_default", "added_from_email"}
TOL = 0.011


def reconcile(rec: dict) -> dict:
    items = rec.get("pay_items", [])
    rec["review_flags"] = [f for f in rec.get("review_flags", []) if f.get("code") != "TOTAL_MISMATCH"]
    for t in rec.get("totals", []):
        t["extracted_sum"] = t["difference"] = t["reconciles"] = None
        if t.get("measure") not in ("hours", "amount") or not isinstance(t.get("document_value"), (int, float)):
            continue
        if t.get("scope") == "employee":
            continue
        sel = [p for p in items if p["status"] in COUNTED and p["provenance"].get("source") != "email"]
        if t.get("scope") == "page" and t.get("page") is not None:
            sel = [p for p in sel if p["provenance"].get("page") == t["page"]]
        if t.get("code"):
            codes = {c.strip().upper() for c in str(t["code"]).split(",")}
            sel = [p for p in sel if (p.get("code") or "").upper() in codes]
        col = t.get("column")
        if col == "deduction":
            sel = [p for p in sel if p["item_type"] == "deduction"]
        elif col == "earnings" or (t["measure"] == "amount" and col in (None, "total_amount")):
            sel = [p for p in sel if p["item_type"] in ("earning", "reimbursement")]
        if t["measure"] == "hours":
            vals = [p["quantity"]["value"] for p in sel if p.get("quantity") and p["quantity"]["unit"] == "hours"]
        else:
            vals = [p["amount"] for p in sel if p.get("amount") is not None]
        s = round(sum(vals), 4)
        t["extracted_sum"], t["difference"] = s, round(s - t["document_value"], 4)
        t["reconciles"] = abs(t["difference"]) < TOL
        if not t["reconciles"]:
            rec["review_flags"].append({"code": "TOTAL_MISMATCH", "severity": "warning",
                                        "message": f"{t['label']} (p{t.get('page')}, {t['scope']}) = {t['document_value']}; pay items sum to {s}",
                                        "refs": [p["item_id"] for p in sel]})
    return rec


def run(ctx: Context, rec, entry: dict) -> None:
    if not rec.has("normalized.json"):
        raise StageSkip("not normalized")
    rec.write("reconciled.json", reconcile(rec.read("normalized.json")))
