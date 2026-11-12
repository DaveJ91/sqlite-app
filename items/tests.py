import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Invoice

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def pdf_upload(name="invoice.pdf", content=PDF):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InvoiceUploadTests(TestCase):
    def test_rejects_non_pdf(self):
        upload = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        response = self.client.post("/", {"file": upload})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a PDF invoice.")
        self.assertEqual(Invoice.objects.count(), 0)

    @patch("items.views.classify_invoice")
    @patch("items.views.extract_text", return_value="")
    def test_scanned_pdf_skips_classification(self, _extract, classify):
        response = self.client.post("/", {"file": pdf_upload()})
        invoice = Invoice.objects.get()
        self.assertRedirects(response, f"/{invoice.pk}/")
        classify.assert_not_called()
        self.assertIn("text layer", invoice.error)

    @patch("items.views.classify_invoice")
    @patch("items.views.extract_text", return_value="Acme invoice 100")
    def test_saves_classification(self, _extract, classify):
        classify.return_value = {
            "supplier": "Acme Ltd",
            "invoice_number": "100",
            "invoice_date": "2026-01-02",
            "currency": "EUR",
            "net_total": "100.00",
            "vat_total": "23.00",
            "gross_total": "123.00",
            "summary": "Standard-rated consulting.",
            "lines": [
                {
                    "description": "Consulting",
                    "net_amount": "100.00",
                    "vat_amount": "23.00",
                    "category": "standard",
                    "rate": "23%",
                    "reason": "Professional services.",
                }
            ],
        }
        response = self.client.post("/", {"file": pdf_upload()})
        invoice = Invoice.objects.get()
        self.assertRedirects(response, f"/{invoice.pk}/")
        detail = self.client.get(f"/{invoice.pk}/")
        self.assertContains(detail, "Acme Ltd")
        self.assertContains(detail, "23%")
        self.assertContains(detail, "Professional services.")
        self.assertEqual(invoice.error, "")
