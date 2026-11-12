"""Turn a question into blocks. The templates render the blocks. The model does not send HTML."""

import json

from django.conf import settings

from . import agent_tools
from .flags import batch_flags
from .forms import ClassificationForm, DeductibilityForm, LineForm
from .httpapi import ClientError, output_text, post_json, require_api_key
from .models import AuditEvent, DeskTurn
from .training import DESCRIPTION_TREATMENT
from .vat import LABELS
from .vat3 import box_rows, vat3_boxes

MAX_ROUNDS = 4
MAX_LINE_CARDS = 6

DESK_INSTRUCTIONS = """You are the desk on a local Irish VAT working paper. You do not file with Revenue.
Read the batch with the tools. Amounts, flags, and chains come from the tools.
Finish by calling render once. prose is plain text. The other blocks are filled from the database.
Pass line_id values the tools returned. A preview override is not saved.
Do not invent a reviewer name. Do not say a person has signed.
assess_deductibility stays on the mock unless the user asked for a live API call."""

TOOLS = [
    {
        "type": "function",
        "name": "list_lines",
        "description": "Every line on this batch, with amounts, category, deductibility, and flag texts.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "get_return",
        "description": "VAT3 boxes from the lines as they stand. Nothing is saved.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "preview_return",
        "description": "VAT3 boxes if deductibility overrides were applied. Nothing is saved.",
        "parameters": {
            "type": "object",
            "properties": {
                "overrides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line_id": {"type": "integer"},
                            "deductible": {"type": "string", "enum": ["full", "partial", "none"]},
                            "deductible_percent": {"type": "string"},
                            "blocked_reason": {"type": "string"},
                        },
                        "required": ["line_id", "deductible"],
                    },
                }
            },
            "required": ["overrides"],
        },
    },
    {
        "type": "function",
        "name": "lookup_treatment",
        "description": "The labelled training row for one description, including both chains.",
        "parameters": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        },
    },
    {
        "type": "function",
        "name": "list_audit",
        "description": "Recent audit events for this batch.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "assess_deductibility",
        "description": "Run deductibility on a batch whose VAT categories are already accepted. Defaults to the mock.",
        "parameters": {
            "type": "object",
            "properties": {"mock": {"type": "boolean"}},
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "render",
        "description": "Finish the turn. Choose the blocks the person should see.",
        "parameters": {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["prose", "line", "flags", "boxes", "comparison", "edit", "signoff"],
                            },
                            "text": {"type": "string"},
                            "line_id": {"type": "integer"},
                            "note": {"type": "string"},
                            "preview": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["type"],
                    },
                }
            },
            "required": ["blocks"],
        },
    },
]


def ask(batch, question, use_grok):
    tool_names = []
    mock = not use_grok
    if use_grok:
        try:
            specs, tool_names = grok_blocks(batch, question)
        except ClientError as exc:
            specs = [{"type": "prose", "text": str(exc)}]
    else:
        specs = local_blocks(batch, question)
    turn = DeskTurn.objects.create(
        batch=batch,
        question=question,
        blocks=specs,
        tool_names=tool_names,
        mock=mock,
    )
    AuditEvent.objects.create(
        batch=batch,
        kind="agent_turn",
        payload={"question": question, "mock": mock, "tools": tool_names, "turn_id": turn.id},
    )
    return turn


def local_blocks(batch, question):
    lines = agent_tools.lines_for(batch)
    text = (question or "").lower()
    specs = []
    wants_deduct = any(word in text for word in ("deduct", "petrol", "blocked", "reclaim", "entertainment", "hotel", "food"))
    wants_flags = any(word in text for word in ("flag", "blank", "arithmetic", "wrong", "duplicate"))
    wants_return = any(word in text for word in ("refund", "payment", "vat3", "return", "box"))
    wants_sign = "sign" in text or "note" in text
    wants_edit = any(word in text for word in ("edit", "override", "change"))
    if wants_deduct:
        blocked = [line for line in lines if _non_recoverable(line)]
        shown = blocked[:MAX_LINE_CARDS]
        sentence = _deduct_sentence(lines, blocked, len(shown))
        specs.append({"type": "prose", "text": sentence})
        specs.extend({"type": "line", "line_id": line.id} for line in shown)
        if "what if" in text or "preview" in text:
            specs.append({"type": "boxes", "preview": _preview_overrides(text, lines)})
        else:
            specs.append({"type": "boxes"})
    if wants_flags:
        count = len(batch_flags(lines))
        specs.append({"type": "prose", "text": f"{count} flag{'s' if count != 1 else ''} open on this batch."})
        specs.append({"type": "flags"})
    if wants_return:
        if not any(spec["type"] == "boxes" for spec in specs):
            specs.append({"type": "prose", "text": "These are the VAT3 boxes for this batch."})
            specs.append({"type": "boxes"})
        specs.append({"type": "comparison"})
    if wants_sign:
        specs.append({"type": "prose", "text": "The note is a draft. The name has to be yours."})
        specs.append({"type": "signoff", "note": _draft_note(lines)})
    if wants_edit:
        target = _line_named(text, lines) or (lines[0] if lines else None)
        if target is not None:
            specs.append({"type": "edit", "line_id": target.id})
    if not specs:
        specs.append(
            {
                "type": "prose",
                "text": (
                    f"This batch has {len(lines)} lines and is at {agent_tools.batch_status_label(batch)}. "
                    "Ask about deductibility, the flags, the return, or a sign-off note."
                ),
            }
        )
        if lines:
            specs.append({"type": "line", "line_id": lines[0].id})
        specs.append({"type": "flags"})
    return specs[:12]


def grok_blocks(batch, question):
    api_key = require_api_key()
    model = (settings.GROK_MODEL or "grok-4.7").strip()
    payload = {
        "model": model,
        "instructions": DESK_INSTRUCTIONS,
        "input": [{"role": "user", "content": question}],
        "tools": TOOLS,
    }
    tool_names = []
    rendered = None
    response_id = None
    for _round in range(MAX_ROUNDS):
        if response_id:
            payload["previous_response_id"] = response_id
        response = post_json("https://api.x.ai/v1/responses", payload, api_key)
        response_id = response.get("id")
        calls = [item for item in response.get("output") or [] if isinstance(item, dict) and item.get("type") == "function_call"]
        if not calls:
            prose = output_text(response) or "The desk had nothing to add."
            return [{"type": "prose", "text": prose}], tool_names
        outputs = []
        for call in calls:
            name = call.get("name") or ""
            tool_names.append(name)
            arguments = _arguments(call)
            if name == "render":
                rendered = arguments.get("blocks")
                result = {"shown": True}
            else:
                result = _run_tool(batch, name, arguments)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id") or name,
                    "output": json.dumps(result),
                }
            )
        if rendered is not None:
            specs = _clean_specs(rendered)
            return specs or [{"type": "prose", "text": "The desk did not choose any blocks."}], tool_names
        if not response_id:
            return [{"type": "prose", "text": "The desk stopped because the response had no id."}], tool_names
        payload = {"model": model, "instructions": DESK_INSTRUCTIONS, "tools": TOOLS, "input": outputs}
    return [{"type": "prose", "text": "The desk stopped after four tool rounds."}], tool_names


def present(batch, specs):
    shown = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        block = _present_one(batch, spec)
        if block:
            shown.append(block)
    return shown


def _present_one(batch, spec):
    kind = spec.get("type")
    lines = agent_tools.lines_for(batch)
    by_id = {line.id: line for line in lines}
    if kind == "prose":
        text = str(spec.get("text") or "").strip()
        if not text:
            return None
        return {"type": "prose", "text": text[:2000]}
    if kind == "line":
        line = by_id.get(_line_id(spec))
        if line is None:
            return None
        return _line_card(line)
    if kind == "flags":
        return {"type": "flags", "items": batch_flags(lines)}
    if kind == "boxes":
        preview = spec.get("preview")
        if preview:
            result = agent_tools.preview_return(batch, preview)
            title = "Preview, not saved"
        else:
            boxes = vat3_boxes(lines)
            result = {"boxes": boxes, "rows": box_rows(boxes)}
            title = "VAT3"
        return {
            "type": "boxes",
            "title": title,
            "rows": result["rows"],
            "outcome": result["boxes"].get("outcome", ""),
            "due": result["boxes"].get("due", "0.00"),
        }
    if kind == "comparison":
        boxes = vat3_boxes(lines)
        compared = agent_tools.comparison_for(batch, boxes)
        compared["type"] = "comparison"
        return compared
    if kind == "edit":
        line = by_id.get(_line_id(spec))
        if line is None:
            return None
        return _edit_card(batch, line)
    if kind == "signoff":
        return _signoff_card(batch, str(spec.get("note") or ""))
    return None


def _line_card(line):
    trained = lookup_known(line.description)
    class_chain = line.classification_chain or []
    deduct_chain = line.deductibility_chain or []
    class_note = ""
    deduct_note = ""
    if not class_chain and trained:
        class_chain = trained["class_chain"]
        class_note = "Training row. This line has not been classified."
    elif not class_chain:
        class_note = "No classification chain was stored."
    if not deduct_chain and trained:
        deduct_chain = trained["deduct_chain"]
        deduct_note = "Training row. Deductibility has not been assessed on this line."
    elif not deduct_chain:
        deduct_note = "No deductibility chain was stored."
    pills = []
    if line.deductible == "none":
        pills.append("Non-deductible")
    if line.low_confidence or line.deductibility_low:
        pills.append("Low confidence")
    if line.classification_overridden or line.deductibility_overridden:
        pills.append("Overridden")
    recoverable = line.deductible_vat or ("0.00" if line.deductible else "—")
    return {
        "type": "line",
        "description": line.description,
        "supplier": line.supplier,
        "net": line.net_amount,
        "vat": line.vat_amount,
        "gross": line.gross_amount,
        "category": LABELS.get(line.category, line.category),
        "deductible": line.deductible,
        "blocked_reason": (line.blocked_reason or "").replace("_", " "),
        "recoverable": recoverable,
        "pills": pills,
        "class_chain": class_chain,
        "deduct_chain": deduct_chain,
        "class_note": class_note,
        "deduct_note": deduct_note,
    }


def _edit_card(batch, line):
    form_class = {
        "extracted": LineForm,
        "classified": ClassificationForm,
        "deductibility": DeductibilityForm,
    }.get(batch.status)
    if form_class is None:
        return {
            "type": "edit",
            "open": False,
            "description": line.description,
            "text": "That edit is closed on this batch.",
        }
    bound = form_class(instance=line)
    fields = []
    for name, field in bound.fields.items():
        value = bound[name].value()
        choices = getattr(field, "choices", None)
        fields.append(
            {
                "name": name,
                "label": field.label or name.replace("_", " "),
                "value": "" if value is None else str(value),
                "choices": [(str(choice), label) for choice, label in choices] if choices else None,
            }
        )
    return {"type": "edit", "open": True, "line_id": line.id, "description": line.description, "fields": fields}


def _signoff_card(batch, note):
    signed = getattr(batch, "sign_off", None)
    flags = batch_flags(agent_tools.lines_for(batch))
    return {
        "type": "signoff",
        "note": note.strip() or _draft_note(agent_tools.lines_for(batch)),
        "open": batch.status == "deduction_accepted" and signed is None,
        "needs_ack": bool(flags),
        "reviewer": signed.reviewer if signed else "",
    }


def lookup_known(description):
    if description not in DESCRIPTION_TREATMENT:
        return None
    return agent_tools.lookup_treatment(description)


def _run_tool(batch, name, arguments):
    if name == "list_lines":
        return agent_tools.list_lines(batch)
    if name == "get_return":
        return agent_tools.get_return(batch)
    if name == "preview_return":
        return agent_tools.preview_return(batch, arguments.get("overrides"))
    if name == "lookup_treatment":
        return agent_tools.lookup_treatment(str(arguments.get("description") or ""))
    if name == "list_audit":
        return agent_tools.list_audit(batch)
    if name == "assess_deductibility":
        mock = arguments.get("mock", True)
        try:
            return agent_tools.assess_deductibility(batch, mock=mock)
        except ClientError as exc:
            return {"error": str(exc)}
    return {"error": f"Unknown tool {name}."}


def _arguments(call):
    raw = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _clean_specs(blocks):
    if not isinstance(blocks, list):
        return []
    allowed = {"prose", "line", "flags", "boxes", "comparison", "edit", "signoff"}
    specs = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") in allowed:
            specs.append(block)
    return specs[:12]


def _line_id(spec):
    try:
        return int(spec.get("line_id"))
    except (TypeError, ValueError):
        return None


def _non_recoverable(line):
    if line.deductible == "none":
        return True
    if line.deductible:
        return False
    trained = lookup_known(line.description)
    return bool(trained and trained["deductible"] == "none")


def _deduct_sentence(lines, blocked, shown):
    assessed = any(line.deductible for line in lines)
    source = "the deductibility step" if assessed else "the training set"
    extra = f" Showing {shown} of {len(blocked)}." if len(blocked) > shown else ""
    if not blocked:
        return "Nothing on this batch is marked non-deductible."
    return f"{len(blocked)} lines have no recoverable VAT, from {source}.{extra}"


def _draft_note(lines):
    names = [line.description for line in lines if line.deductible == "none"]
    flags = batch_flags(lines)
    parts = []
    if names:
        parts.append("Left non-deductible: " + ", ".join(names[:8]) + ".")
    else:
        parts.append("No line is marked non-deductible.")
    if flags:
        parts.append(f"{len(flags)} flag{'s' if len(flags) != 1 else ''} still open.")
    else:
        parts.append("No flags open.")
    parts.append("I reviewed the chains.")
    return " ".join(parts)


def _line_named(text, lines):
    for line in lines:
        if line.description and line.description.lower() in text:
            return line
    return None


def _preview_overrides(text, lines):
    named = _line_named(text, lines)
    target = named
    if target is None:
        target = next((line for line in lines if line.deductible != "none"), None)
    if target is None:
        return []
    return [{"line_id": target.id, "deductible": "none", "blocked_reason": "non_business", "deductible_percent": "0"}]
