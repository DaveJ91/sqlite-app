import json
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from types import SimpleNamespace

from .httpapi import output_text, parse_json_content
from .models import AuditEvent, Batch, InvoiceLine, VatRun
from .understanding import understand_invoice
from .vat3 import anomalies, payment_boxes, vat3_boxes

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
        self.assertEqual(batch.runs.filter(kind="classify").count(), 1)
        self.assertEqual(batch.runs.filter(kind="extract").count(), 1)

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
        self.assertContains(page, "Refund due")
        self.assertContains(page, "28.40")
        self.assertEqual(batch.vat_return.boxes["T1"], "0.00")
        self.assertEqual(batch.vat_return.boxes["T2"], "28.40")
        self.assertEqual(batch.vat_return.boxes["T3"], "0.00")
        self.assertEqual(batch.vat_return.boxes["T4"], "28.40")
        self.assertEqual(batch.runs.filter(kind="return").count(), 1)

    def test_mock_batch_needs_no_files(self):
        response = self.client.post("/", {"action": "mock_batch", "period_label": "March 2026"})
        batch = Batch.objects.get()
        self.assertRedirects(response, f"/batches/{batch.pk}/")
        self.assertEqual(InvoiceLine.objects.count(), 50)
        self.assertEqual(batch.invoices.count(), 10)
        self.assertFalse(batch.invoices.exclude(file="").exists())
        page = self.client.get(f"/batches/{batch.pk}/")
        self.assertContains(page, "Review extraction")
        self.assertContains(page, "✓")
        self.assertContains(page, "Harbour Consulting Ltd")

    @patch("items.graph.understand_invoice", side_effect=AssertionError("api"))
    def test_mock_extraction_skips_the_api(self, understand):
        response = self.client.post("/", {"files": [pdf_upload("a.pdf")], "action": "mock"})
        batch = Batch.objects.get()
        self.assertRedirects(response, f"/batches/{batch.pk}/")
        understand.assert_not_called()
        line = InvoiceLine.objects.get(description="Management consulting")
        self.assertEqual(line.supplier, "Harbour Consulting Ltd")
        self.assertEqual(line.invoice.read_source, "mock")

    @patch("items.graph.classify_lines", side_effect=AssertionError("api"))
    @patch("items.graph.understand_invoice", side_effect=lambda path, filename: (EXTRACTED, "grok"))
    def test_mock_vat_assigns_a_random_category(self, _understand, classify):
        self.client.post("/", {"files": [pdf_upload("a.pdf")]})
        line = InvoiceLine.objects.get()
        with patch("items.mocks.random.choice", return_value="reduced"), patch(
            "items.mocks.random.uniform", return_value=0.42
        ):
            response = self.client.post(
                f"/batches/{line.invoice.batch_id}/",
                line_post(line, action="mock_vat"),
            )
        classify.assert_not_called()
        line.refresh_from_db()
        self.assertRedirects(response, f"/batches/{line.invoice.batch_id}/vat/")
        self.assertEqual(line.category, "reduced")
        self.assertEqual(line.rate, "13.5%")
        self.assertEqual(line.confidence, 0.42)
        self.assertEqual(line.reason, "Mock classification.")
        page = self.client.get(f"/batches/{line.invoice.batch_id}/vat/")
        self.assertContains(page, "Low confidence")


def _two_line_post(first, second, action):
    payload = line_post(first, action=action)
    payload["form-TOTAL_FORMS"] = "2"
    payload["form-INITIAL_FORMS"] = "2"
    payload["form-1-id"] = str(second.id)
    for field in ["supplier", "invoice_number", "invoice_date", "currency", "description", "net_amount", "vat_amount", "gross_amount"]:
        payload[f"form-1-{field}"] = getattr(second, field)
    return payload


class Vat3Tests(TestCase):
    def test_reverse_charge_posts_t1_t2_and_es2(self):
        shown = SimpleNamespace(category="reverse_charge", net_amount="100.00", vat_amount="23.00")
        boxes = vat3_boxes([shown])
        self.assertEqual(boxes["T1"], "23.00")
        self.assertEqual(boxes["T2"], "23.00")
        self.assertEqual(boxes["T3"], "0.00")
        self.assertEqual(boxes["T4"], "0.00")
        self.assertEqual(boxes["ES2"], "100.00")
        self.assertEqual(boxes["E1"], "0.00")
        self.assertEqual(boxes["E2"], "0.00")
        self.assertEqual(boxes["ES1"], "0.00")
        self.assertEqual(boxes["PA1"], "0.00")
        self.assertEqual(boxes["outcome"], "clear")
        blank = SimpleNamespace(category="reverse_charge", net_amount="200.00", vat_amount="")
        boxes = vat3_boxes([blank])
        self.assertEqual(boxes["T1"], "46.00")
        self.assertEqual(boxes["ES2"], "200.00")

    def test_exempt_zero_and_outside_scope_do_not_enter_t2(self):
        lines = [
            SimpleNamespace(category="exempt", net_amount="50.00", vat_amount="10.00"),
            SimpleNamespace(category="zero", net_amount="80.00", vat_amount="0.00"),
            SimpleNamespace(category="outside_scope", net_amount="90.00", vat_amount="20.00"),
        ]
        boxes = vat3_boxes(lines)
        self.assertEqual(boxes["T1"], "0.00")
        self.assertEqual(boxes["T2"], "0.00")
        self.assertEqual(boxes["outcome"], "clear")

    def test_history_flags_a_purchase_refund(self):
        from .mocks import seed_return_history

        seed_return_history()
        priors = [
            run.payload["boxes"]
            for run in VatRun.objects.filter(kind="return", batch__isnull=True).order_by("-period_date", "-created")
        ]
        self.assertEqual(len(priors), 12)
        current = vat3_boxes(
            [
                SimpleNamespace(category="standard", net_amount="100.00", vat_amount="23.00"),
                SimpleNamespace(category="reduced", net_amount="40.00", vat_amount="5.40"),
            ]
        )
        text = " ".join(flag["text"] for flag in anomalies(current, priors))
        self.assertIn("more than 25%", text)
        self.assertIn("This return is a refund. The previous return was a payment.", text)

        batch = Batch.objects.create(status=Batch.CLASSIFIED, period_label="March 2026")
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
        self.assertContains(page, "Anomalies")
        self.assertContains(page, "Refund due")
        self.assertContains(page, "more than 25%")
        self.assertContains(page, "ES2")
        self.assertContains(page, "PA1")

    def test_new_eu_box_is_flagged_only_with_three_priors(self):
        refund = vat3_boxes([SimpleNamespace(category="standard", net_amount="100.00", vat_amount="23.00")])
        one = anomalies(refund, [payment_boxes("100.00", "40.00")])
        self.assertTrue(any(flag["code"] == "outcome" for flag in one))
        self.assertFalse(any("25%" in flag["text"] for flag in one))
        current = vat3_boxes([SimpleNamespace(category="reverse_charge", net_amount="100.00", vat_amount="23.00")])
        flagged = anomalies(current, [payment_boxes("4800.00", "2100.00") for _ in range(3)])
        self.assertTrue(any(flag["code"] == "ES2" for flag in flagged))

    def test_dashboard_shows_the_seeded_year(self):
        from .mocks import seed_return_history

        seed_return_history()
        page = self.client.get("/dashboard/")
        self.assertContains(page, "Latest return · payment due")
        self.assertContains(page, "Run history")
        self.assertContains(page, "mock")
        self.assertEqual(page.content.decode().count('class="col"'), 12)


class LoaderTests(TestCase):
    def test_step_changes_wait_and_save_does_not(self):
        home = self.client.get("/")
        html = home.content.decode()
        self.assertIn('data-step="load"', html)
        self.assertIn("WAIT_MS = 5000", html)
        self.assertIn("data-wait", _button(html, "mock_batch"))
        self.assertIn("data-wait", _button(html, "read"))
        self.assertIn("data-wait", _button(html, "mock"))
        self.assertIn('!submitter.hasAttribute("data-wait")', html)

        batch = Batch.objects.create(status=Batch.EXTRACTED, period_label="March 2026")
        invoice = batch.invoices.create(original_name="a.pdf", file=pdf_upload("a.pdf"))
        invoice.lines.create(description="Consulting", net_amount="100.00", vat_amount="23.00", gross_amount="123.00")
        review = self.client.get(f"/batches/{batch.pk}/").content.decode()
        self.assertIn('data-step="classify"', review)
        self.assertNotIn("data-wait", _button(review, "save"))
        self.assertIn("data-wait", _button(review, "accept"))
        self.assertIn("data-wait", _button(review, "mock_vat"))

        batch.status = Batch.CLASSIFIED
        batch.save(update_fields=["status"])
        invoice.lines.update(category="standard", confidence=0.95)
        vat = self.client.get(f"/batches/{batch.pk}/vat/").content.decode()
        self.assertIn('data-step="return"', vat)
        self.assertNotIn("data-wait", _button(vat, "save"))
        self.assertIn("data-wait", _button(vat, "accept"))


def _button(html, value):
    return html.split(f'value="{value}"')[0].rsplit("<button", 1)[-1]


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
