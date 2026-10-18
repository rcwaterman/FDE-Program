# Decision log

One row per architectural decision, with the option you rejected and why.

> **Week 5 asks you to defend your agent architecture against the alternatives.**
> **Week 10's handoff doc is largely this file.** Both are much easier if you
> wrote the reasoning down while you still remembered it.

A decision without a rejected alternative is not a decision, it is a default.
If you cannot name what you did not do, you did not choose.

| Week | Decision | What you rejected | Why | What would change your mind |
| --- | --- | --- | --- | --- |
| 1 | OCR pipeline or agent | I rejected the agent approach | The OCR approach will be less semantically accurate, but it will run locally more reliably and it is more deterministic. | If the OCR model does not extract semantic variance in the handwritten instructions, an agentic VLM approach can fill that gap. |


---

The last column is the one people skip and the one clients ask about. "We chose
dense retrieval; we would switch to hybrid if exact-match queries went above ~20%
of traffic" is an engineering position. "We chose dense retrieval" is a
preference.
