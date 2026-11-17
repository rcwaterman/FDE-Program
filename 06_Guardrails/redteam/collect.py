"""One pass of the local model over every attack AND every legitimate email; raw outputs saved so any guardrail config
can be replayed on exactly the same outputs (the two numbers cannot drift). Usage: collect.py [runs]"""
import json, sys
from pathlib import Path
import harness as h
from attacks import ATTACKS, BENIGN

OUT = Path(__file__).parent / "samples.jsonl"
runs = int(sys.argv[1]) if len(sys.argv) > 1 else 8
client = h.OllamaClient()
cases = [(n, "attack", k, s, i, t) for n, k, s, i, t in ATTACKS] + \
        [(n, "benign", "benign", s, True, t) for n, s, t in BENIGN]
for name, side, kind, sender, incoming, text in cases:
    for r in range(runs):
        emails = [h.email("02s1", sender, text, incoming)]
        out = h.run_case(client, emails)
        raw = (out["raw"] or {}).get("amendments") if isinstance(out["raw"], dict) else None
        OUT.open("a").write(json.dumps({"name": name, "side": side, "kind": kind, "run": r, "sender": sender,
            "incoming": incoming, "text": text, "amendments": raw, "error": out["error"]}) + "\n")
    print("done", name, flush=True)
