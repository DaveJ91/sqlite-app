import json
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .httpapi import output_text, parse_json_content
from .models import AuditEvent, Batch, InvoiceLine
from .understanding import understand_invoice

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
MEDIA = tempfile.mkdtemp()

EXTRACTED = {
    "supplier": "Acme Ltd",
    "invoice_number": "100",
    "invoice_date": "2026-03-01",
    "currency": "EUR",
    "lines": [
        {
            "description": "Consulting",
            "net_amount": "100.00",
            "vat_amount": "23.00",
            "gross_amount": "123.00",
        }
    ],
}


def pdf_upload(name):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


def line_post(line, **overrides):
    data = {
        "supplier": line.supplier,
        "invoice_number": line.invoice_number,
        "invoice_date": line.invoice_date,
        "currency": line.currency,
        "description": line.description,
        "net_amount": line.net_amount,
        "vat_amount": line.vat_amount,
        "gross_amount": line.gross_amount,
    }
    data.update(overrides)
    payload = {
        "action": data.pop("action", "save"),
        "form-TOTAL_FORMS": "1",
        "form-INITIAL_FORMS": "1",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-id": str(line.id),
    }
    for key, value in data.items():
        payload[f"form-0-{key}"] = value
    return payload


@override_settings(MEDIA_ROOT=MEDIA, VAT_LOW_CONFIDENCE="0.7")
class BatchWorkflowTests(TestCase):
    def test_rejects_an_eleventh_file(self):
        files = [pdf_upload(f"inv-{index}.pdf") for index in range(11)]
        response = self.client.post("/", {"files": files})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10 invoices or fewer")
        self.assertEqual(Batch.objects.count(), 0)

    @patch("items.graph.understand_invoice", side_effect=lambda path, filename: (EXTRACTED, "grok"))
    def test_two_files_become_rows(self, _understand):
        response = self.client.post("/", {"files": [pdf_upload("a.pdf"), pdf_upload("b.pdf")], "period_label": "March 2026"})
        batch = Batch.objects.get()
        self.assertRedirects(response, f"/batches/{batch.pk}/")
        self.assertEqual(batch.invoices.count(), 2)
        self.assertEqual(InvoiceLine.objects.count(), 2)
        page = self.client.get(f"/batches/{batch.pk}/")
        self.assertContains(page, "Consulting")
        self.assertContains(page, "Acme Ltd")

    @patch("items.graph.understand_invoice", side_effect=lambda path, filename: (EXTRACTED, "grok"))
    def test_edit_keeps_extracted_value(self, _understand):
        self.client.post("/", {"files": [pdf_upload("a.pdf")]})
        line = InvoiceLine.objects.get()
        response = self.client.post(f"/batches/{line.invoice.batch_id}/", line_post(line, net_amount="80.00"))
        self.assertRedirects(response, f"/batches/{line.invoice.batch_id}/")
        line.refresh_from_db()
        self.assertEqual(line.net_amount, "80.00")
        self.assertEqual(line.extracted_net_amount, "100.00")
        event = AuditEvent.objects.get(kind="edited")
        self.assertEqual(event.payload["field"], "net_amount")
        self.assertEqual(event.payload["old"], "100.00")
        self.assertEqual(event.payload["new"], "80.00")

    @patch("items.graph.classify_lines")
    @patch("items.graph.understand_invoice", side_effect=lambda path, filename: (EXTRACTED, "content_understanding"))
    def test_accept_calls_vat_once(self, _understand, classify):
        def _classify(lines):
            return [
                {
                    "line_id": line.id,
                    "category": "standard",
                    "confidence": 0.91,
                    "reason": "Professional services.",
                    "error": "",
                }
                for line in lines
            ]

        classify.side_effect = _classify
        self.client.post("/", {"files": [pdf_upload("a.pdf"), pdf_upload("b.pdf")]})
        batch = Batch.objects.get()
        ids = list(InvoiceLine.objects.values_list("id", flat=True))
        first = InvoiceLine.objects.order_by("id").first()
        second = InvoiceLine.objects.order_by("id").last()
        response = self.client.post(
            f"/batches/{batch.pk}/",
            _two_line_post(first, second, action="accept"),
        )
        self.assertRedirects(response, f"/batches/{batch.pk}/vat/")
        classify.assert_called_once()
        sent_ids = [line.id for line in classify.call_args.args[0]]
        self.assertEqual(sent_ids, ids)
        page = self.client.get(f"/batches/{batch.pk}/vat/")
        self.assertContains(page, "Professional services.")
        self.assertContains(page, "0.91")

    def test_low_confidence_is_highlighted(self):
        batch = Batch.objects.create(status=Batch.CLASSIFIED)
        invoice = batch.invoices.create(original_name="a.pdf", file=pdf_upload("a.pdf"))
        invoice.lines.create(description="Fuzzy line", net_amount="10.00", vat_amount="2.30", category="standard", confidence=0.4, reason="Unclear")
        invoice.lines.create(description="Clear line", net_amount="10.00", vat_amount="2.30", category="standard", confidence=0.9, reason="Clear")
        page = self.client.get(f"/batches/{batch.pk}/vat/")
        html = page.content.decode()
        self.assertEqual(html.count("Low confidence"), 1)
        self.assertIn("Fuzzy line", html)
        self.assertIn("Clear line", html)
        self.assertIn("flag", html)

    def test_return_totals_list_each_line(self):
        batch = Batch.objects.create(status=Batch.CLASSIFIED)
        invoice = batch.invoices.create(original_name="a.pdf", file=pdf_upload("a.pdf"))
        invoice.lines.create(description="Consulting", net_amount="100.00", vat_amount="23.00", gross_amount="123.00", category="standard", rate="23%", confidence=0.95, reason="Services")
        invoice.lines.create(description="Heat", net_amount="40.00", vat_amount="5.40", gross_amount="45.40", category="reduced", rate="13.5%", confidence=0.88, reason="Fuel")
        first, second = invoice.lines.order_by("id")
        response = self.client.post(
            f"/batches/{batch.pk}/vat/",
            {
                "action": "accept",
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "2",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(first.id),
                "form-0-category": "standard",
                "form-1-id": str(second.id),
                "form-1-category": "reduced",
            },
        )
        self.assertRedirects(response, f"/batches/{batch.pk}/return/")
        page = self.client.get(f"/batches/{batch.pk}/return/")
        self.assertContains(page, "Standard 23%")
        self.assertContains(page, "100.00")
        self.assertContains(page, "Reduced 13.5%")
        self.assertContains(page, "5.40")
        self.assertContains(page, "Consulting")
        self.assertContains(page, "Heat")
        self.assertContains(page, "return_prepared")


def _two_line_post(first, second, action):
    payload = line_post(first, action=action)
    payload["form-TOTAL_FORMS"] = "2"
    payload["form-INITIAL_FORMS"] = "2"
    payload["form-1-id"] = str(second.id)
    for field in ["supplier", "invoice_number", "invoice_date", "currency", "description", "net_amount", "vat_amount", "gross_amount"]:
        payload[f"form-1-{field}"] = getattr(second, field)
    return payload


class ReaderFallbackTests(TestCase):
    @override_settings(
        CONTENT_UNDERSTANDING_BASE_URL="",
        CONTENT_UNDERSTANDING_ENDPOINT="",
        XAI_API_KEY="test-key",
        GROK_MODEL="grok-4.7",
    )
    @patch("items.understanding.post_json")
    @patch("items.understanding.post_file")
    def test_empty_content_understanding_uses_grok(self, post_file, post_json):
        post_file.return_value = {"id": "file_123"}
        post_json.return_value = {
            "output": [
                {"content": [{"text": json.dumps(EXTRACTED)}]}
            ]
        }
        data, source = understand_invoice("invoice.pdf", "invoice.pdf")
        self.assertEqual(source, "grok")
        self.assertEqual(data["supplier"], "Acme Ltd")
        self.assertEqual(post_json.call_args.args[0], "https://api.x.ai/v1/responses")
        self.assertEqual(post_file.call_args.args[0], "https://api.x.ai/v1/files")

    def test_output_text_reads_grok_response(self):
        text = output_text({"output": [{"content": [{"text": "{\"supplier\": \"A\"}"}]}]})
        self.assertEqual(parse_json_content(text)["supplier"], "A")
