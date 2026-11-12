import sqlite3
from collections import defaultdict
from typing import TypedDict

from django.conf import settings
from django.db import connection
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .classify import classify_lines
from .deduct import deduct_lines
from .httpapi import ClientError
from .mocks import mock_classification, mock_deductibility, mock_invoice
from .models import AuditEvent, Batch, InvoiceLine, VatReturn, VatRun
from .understanding import understand_invoice
from .vat import LABELS, RATES, format_amount, parse_amount
from .vat3 import period_date_from_label, recovery_from_decision, vat3_boxes


class GraphState(TypedDict):
    batch_id: int
    step: str
    mock: bool


def extract_batch(state: GraphState) -> GraphState:
    batch = Batch.objects.get(pk=state["batch_id"])
    for index, invoice in enumerate(batch.invoices.all()):
        invoice.lines.all().delete()
        try:
            if state.get("mock"):
                data, source = mock_invoice(index), "mock"
            else:
                data, source = understand_invoice(invoice.file.path, invoice.original_name)
        except Exception as exc:
            invoice.error = str(exc) if isinstance(exc, ClientError) else "Could not read this invoice."
            invoice.raw_response = {}
            invoice.read_source = ""
            invoice.save()
            _event(batch, "extracted", {"invoice_id": invoice.id, "filename": invoice.original_name, "ok": False, "error": str(exc)})
            continue
        invoice.raw_response = data
        invoice.error = ""
        invoice.read_source = source
        invoice.save()
        for row in data["lines"]:
            InvoiceLine.objects.create(
                invoice=invoice,
                supplier=data["supplier"],
                extracted_supplier=data["supplier"],
                invoice_number=data["invoice_number"],
                extracted_invoice_number=data["invoice_number"],
                invoice_date=data["invoice_date"],
                extracted_invoice_date=data["invoice_date"],
                currency=data["currency"],
                extracted_currency=data["currency"],
                description=row["description"],
                extracted_description=row["description"],
                net_amount=row["net_amount"],
                extracted_net_amount=row["net_amount"],
                vat_amount=row["vat_amount"],
                extracted_vat_amount=row["vat_amount"],
                gross_amount=row["gross_amount"],
                extracted_gross_amount=row["gross_amount"],
            )
        _event(
            batch,
            "extracted",
            {
                "invoice_id": invoice.id,
                "filename": invoice.original_name,
                "ok": True,
                "source": source,
                "line_count": len(data["lines"]),
            },
        )
    batch.status = Batch.EXTRACTED
    batch.save(update_fields=["status"])
    _record_run(
        batch,
        VatRun.EXTRACT,
        bool(state.get("mock")),
        {"line_count": InvoiceLine.objects.filter(invoice__batch=batch).count()},
    )
    return state


def classify_batch(state: GraphState) -> GraphState:
    batch = Batch.objects.get(pk=state["batch_id"])
    lines = list(InvoiceLine.objects.filter(invoice__batch=batch))
    if state.get("mock"):
        classified = mock_classification(lines)
    else:
        classified = classify_lines(lines)
    by_id = {row["line_id"]: row for row in classified}
    low = 0
    for line in lines:
        row = by_id.get(line.id)
        if row is None:
            line.classification_error = "This line was missing from the classification response."
            line.confidence = 0
            line.category = ""
            line.rate = ""
            line.reason = ""
        elif row["error"]:
            line.classification_error = row["error"]
            line.confidence = row["confidence"]
            line.category = ""
            line.rate = ""
            line.reason = row["reason"]
            line.classification_chain = row.get("chain") or []
        else:
            line.classification_error = ""
            line.category = row["category"]
            line.rate = RATES[row["category"]]
            line.confidence = row["confidence"]
            line.reason = row["reason"]
            line.supply = row.get("supply") or ""
            line.nature = row.get("nature") or ""
            line.place_of_supply = row.get("place") or ""
            line.who_accounts = row.get("who_accounts") or ""
            line.classification_chain = row.get("chain") or [
                {"step": "Rate", "finding": LABELS.get(line.category, line.category), "because": line.reason}
            ]
            line.model_category = line.category
            line.model_supply = line.supply
            line.model_nature = line.nature
            line.model_place = line.place_of_supply
            line.model_who = line.who_accounts
        if line.low_confidence:
            low += 1
        line.save()
    batch.status = Batch.CLASSIFIED
    batch.save(update_fields=["status"])
    _event(
        batch,
        "classified",
        {"line_count": len(lines), "low_confidence_count": low, "mock": bool(state.get("mock"))},
    )
    _record_run(
        batch,
        VatRun.CLASSIFY,
        bool(state.get("mock")),
        {"line_count": len(lines), "low_confidence_count": low},
    )
    return state


def assess_deductibility(state: GraphState) -> GraphState:
    batch = Batch.objects.get(pk=state["batch_id"])
    lines = list(InvoiceLine.objects.filter(invoice__batch=batch))
    if state.get("mock"):
        assessed = mock_deductibility(lines)
    else:
        assessed = deduct_lines(lines)
    by_id = {row["line_id"]: row for row in assessed}
    low = 0
    blocked = 0
    for line in lines:
        row = by_id.get(line.id)
        if row is None:
            line.deductibility_error = "This line was missing from the deductibility response."
            line.deductibility_confidence = 0
            line.deductible = ""
            line.deductible_percent = ""
            line.blocked_reason = ""
            line.deductible_vat = ""
            line.deductibility_reason = ""
            line.deductibility_chain = []
        elif row["error"]:
            line.deductibility_error = row["error"]
            line.deductibility_confidence = row["confidence"]
            line.deductible = ""
            line.deductible_percent = ""
            line.blocked_reason = ""
            line.deductible_vat = ""
            line.deductibility_reason = row["reason"]
            line.deductibility_chain = row.get("chain") or []
        else:
            line.deductibility_error = ""
            line.deductible = row["deductible"]
            line.deductible_percent = row["deductible_percent"]
            line.blocked_reason = row["blocked_reason"]
            line.deductibility_confidence = row["confidence"]
            line.deductibility_reason = row["reason"]
            line.deductibility_chain = row.get("chain") or []
            line.deductible_vat = format_amount(recovery_from_decision(line))
            line.model_deductible = line.deductible
            line.model_blocked = line.blocked_reason
            line.model_percent = line.deductible_percent
        if line.deductibility_low:
            low += 1
        if line.deductible == "none":
            blocked += 1
        line.save()
    batch.status = Batch.DEDUCTIBILITY
    batch.save(update_fields=["status"])
    _event(
        batch,
        "deductibility_assessed",
        {"line_count": len(lines), "non_deductible": blocked, "low_confidence_count": low, "mock": bool(state.get("mock"))},
    )
    _record_run(
        batch,
        VatRun.DEDUCT,
        bool(state.get("mock")),
        {"line_count": len(lines), "non_deductible": blocked, "low_confidence_count": low},
    )
    return state


def prepare_return(state: GraphState) -> GraphState:
    batch = Batch.objects.get(pk=state["batch_id"])
    lines = list(InvoiceLine.objects.filter(invoice__batch=batch).select_related("invoice"))
    grouped = defaultdict(list)
    for line in lines:
        grouped[line.category].append(line)
    totals = {}
    for category, rows in grouped.items():
        net = sum((parse_amount(row.net_amount) for row in rows), start=parse_amount("0"))
        vat = sum((parse_amount(row.vat_amount) for row in rows), start=parse_amount("0"))
        gross = sum((parse_amount(row.gross_amount) for row in rows), start=parse_amount("0"))
        totals[category] = {
            "label": LABELS.get(category, category),
            "rate": RATES.get(category, ""),
            "net": format_amount(net),
            "vat": format_amount(vat),
            "gross": format_amount(gross),
            "lines": [
                {
                    "id": row.id,
                    "filename": row.invoice.original_name,
                    "description": row.description,
                    "net": row.net_amount,
                    "vat": row.vat_amount,
                    "gross": row.gross_amount,
                    "confidence": row.confidence,
                    "reason": row.reason,
                }
                for row in rows
            ],
        }
    boxes = vat3_boxes(lines)
    vat_return, _created = VatReturn.objects.update_or_create(
        batch=batch,
        defaults={"totals": totals, "boxes": boxes},
    )
    batch.status = Batch.RETURN_READY
    batch.save(update_fields=["status"])
    mock = batch.invoices.exists() and not batch.invoices.exclude(read_source="mock").exists()
    _record_run(batch, VatRun.RETURN, mock, {"boxes": boxes, "categories": list(totals)})
    _event(
        batch,
        "return_prepared",
        {"return_id": vat_return.id, "categories": list(totals), "outcome": boxes["outcome"], "due": boxes["due"]},
    )
    return state


def run_step(batch_id, step, mock=False):
    checkpointer, conn = _checkpointer()
    try:
        graph = _compile(checkpointer)
        graph.invoke(
            {"batch_id": batch_id, "step": step, "mock": mock},
            {"configurable": {"thread_id": f"batch-{batch_id}-{step}"}},
        )
    finally:
        if conn is not None:
            conn.close()


def _compile(checkpointer):
    graph = StateGraph(GraphState)
    graph.add_node("extract_batch", extract_batch)
    graph.add_node("classify_batch", classify_batch)
    graph.add_node("assess_deductibility", assess_deductibility)
    graph.add_node("prepare_return", prepare_return)
    graph.add_conditional_edges(
        START,
        lambda state: state["step"],
        {
            "extract": "extract_batch",
            "classify": "classify_batch",
            "deduct": "assess_deductibility",
            "return": "prepare_return",
        },
    )
    graph.add_edge("extract_batch", END)
    graph.add_edge("classify_batch", END)
    graph.add_edge("assess_deductibility", END)
    graph.add_edge("prepare_return", END)
    return graph.compile(checkpointer=checkpointer)


def _checkpointer():
    if connection.in_atomic_block:
        return MemorySaver(), None
    db_path = settings.DATABASES["default"]["NAME"]
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver, conn


def _event(batch, kind, payload):
    AuditEvent.objects.create(batch=batch, kind=kind, payload=payload)


def _record_run(batch, kind, mock, payload):
    VatRun.objects.create(
        batch=batch,
        kind=kind,
        period_label=batch.period_label,
        period_date=period_date_from_label(batch.period_label),
        mock=mock,
        payload=payload,
    )
