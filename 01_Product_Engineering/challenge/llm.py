"""The model call.

Two jobs live here:
  - stream_reply:   take a conversation, stream back the assistant's reply.
  - extract_record: take a timesheet scan, return a validated schema.Response.

Everything provider-specific lives in environment variables, so switching from
OpenAI to Azure to a self-hosted model is a config change, not a code change.
"""

import base64
import json
import os

from litellm import completion
from pydantic import ValidationError

import schema

# LiteLLM picks the provider from the model string:
#   "gpt-4.1-mini"                    -> OpenAI
#   "azure/my-deployment"             -> Azure OpenAI
#   "anthropic/claude-sonnet-5"       -> Anthropic
#   "ollama/llama3.2"                 -> a model running on your laptop
#   "openai/my-model" + LLM_API_BASE  -> any OpenAI-compatible server (vLLM, LM Studio, ...)
MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
API_BASE = os.getenv("LLM_API_BASE")  # only needed for self-hosted / gateway endpoints
EXTRA_HEADERS = json.loads(os.getenv("LLM_EXTRA_HEADERS") or "{}")

# Extraction needs a model that accepts images. Defaults to LLM_MODEL.
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", MODEL)
# Set to 0 for models that do not support JSON-schema response formats; the
# prompt then carries the instruction and the reply is validated afterwards.
STRUCTURED_OUTPUT = os.getenv("EXTRACT_STRUCTURED_OUTPUT", "1") == "1"
EXTRACT_ATTEMPTS = int(os.getenv("EXTRACT_ATTEMPTS", "2"))

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful assistant. Be concise and concrete.",
)

EXTRACT_PROMPT = """\
You read scanned payroll timesheet pages ("Payroll Timesheet Report") and
return the fields of one employee row as JSON that matches the supplied schema.

Layout. Each employee occupies a block of rows. The block holds the typed name,
employee number, department, rate or salary, and permanent deductions. Hours
and earnings columns may hold handwritten entries.

Field mapping.
- emp_num, emp_first_name, emp_last_name, department: from the block header.
- pay_rate: Rate 1 for hourly employees; the Salary value when Rate 1 is empty.
- reg_hours, ot_hours: the handwritten Regular and Overtime columns.
- Hours with a code in the hours "code" column go to the field for that code:
  23 -> vac_hours, 24 -> holiday_hours, 26 -> pto_hours, 28 -> other_hours.
- Earnings with a code and amount: 21 (Tips) -> cash_tip_amt (the form has no
  cash or credit split, so leave credit_tip_amt null), 19 (Bonus) -> bonus_amt.
- deduct_code, deduction_amt: the first permanent deduction listed for the row.
- sick_hours, comm_amt, other_amt, project, job: only when the form shows them.

Values and status.
- value is a string for text and codes and a number for hours and amounts.
- A cell that is empty on the form gets value null and status "read".
- Use status "read" when you are confident of the value.
- Use "low_confidence" when you can read a value but it may be wrong, including
  a struck-through entry with a rewritten value. Report the rewritten value.
- Use "unreadable" when an entry exists but cannot be read; value is null.
- Do not infer values that are not written on the page.

why: one or two sentences naming where each non-empty value came from on the
page and anything that affected confidence.

Extract the first employee row on the page.
"""


def _provider_kwargs() -> dict:
    """Settings shared by every completion call."""
    kwargs = {"model": MODEL}
    if API_BASE:
        kwargs["api_base"] = API_BASE
    if EXTRA_HEADERS:
        kwargs["extra_headers"] = EXTRA_HEADERS
    return kwargs


def stream_reply(message: str, history: list[dict] | None = None):
    """Yield the assistant's reply as it arrives, one growing string at a time.

    `history` is a list of {"role": ..., "content": ...} dicts. We rebuild the
    full message list on every turn -- the model is stateless, so the
    conversation only exists because we keep sending it.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": message})

    kwargs = {**_provider_kwargs(), "messages": messages, "stream": True}

    reply = ""
    for chunk in completion(**kwargs):
        piece = chunk.choices[0].delta.content
        if piece:
            reply += piece
            yield reply


def extract_record(request: schema.Request) -> schema.Response:
    """Send one timesheet image to the model and return a validated Response."""
    data_url = (
        f"data:{request.media_type};base64,"
        + base64.b64encode(request.scan).decode("ascii")
    )
    messages = [
        {"role": "system", "content": EXTRACT_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract the record from this timesheet page."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    kwargs = {
        **_provider_kwargs(),
        "model": EXTRACT_MODEL,
        "messages": messages,
        "temperature": 0,
    }
    if STRUCTURED_OUTPUT:
        kwargs["response_format"] = schema.Response
    else:
        messages[0]["content"] += (
            "\nReturn only a JSON object matching this schema:\n"
            + json.dumps(schema.Response.model_json_schema())
        )

    last_error: Exception | None = None
    for _ in range(EXTRACT_ATTEMPTS):
        content = completion(**kwargs).choices[0].message.content or ""
        try:
            return schema.Response.model_validate_json(_strip_fences(content))
        except ValidationError as err:
            last_error = err
    raise ValueError(f"The model reply did not match the Response schema: {last_error}")


def _strip_fences(text: str) -> str:
    """Remove a ```json ... ``` wrapper if the model added one."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()