from django.conf import settings
from django.db import models


class Batch(models.Model):
    EXTRACTED = "extracted"
    EXTRACTION_ACCEPTED = "extraction_accepted"
    CLASSIFIED = "classified"
    RETURN_READY = "return_ready"
    STATUS_CHOICES = [
        (EXTRACTED, "Review extraction"),
        (EXTRACTION_ACCEPTED, "Classifying"),
        (CLASSIFIED, "Review VAT"),
        (RETURN_READY, "VAT return"),
    ]

    created = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=EXTRACTED)
    period_label = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.period_label or f"Batch {self.pk}"


class Invoice(models.Model):
    batch = models.ForeignKey(Batch, related_name="invoices", on_delete=models.CASCADE)
    file = models.FileField(upload_to="invoices/", blank=True)
    original_name = models.CharField(max_length=255)
    raw_response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    read_source = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return self.original_name


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="lines", on_delete=models.CASCADE)
    supplier = models.CharField(max_length=255, blank=True)
    extracted_supplier = models.CharField(max_length=255, blank=True)
    invoice_number = models.CharField(max_length=64, blank=True)
    extracted_invoice_number = models.CharField(max_length=64, blank=True)
    invoice_date = models.CharField(max_length=32, blank=True)
    extracted_invoice_date = models.CharField(max_length=32, blank=True)
    currency = models.CharField(max_length=8, blank=True)
    extracted_currency = models.CharField(max_length=8, blank=True)
    description = models.TextField(blank=True)
    extracted_description = models.TextField(blank=True)
    net_amount = models.CharField(max_length=32, blank=True)
    extracted_net_amount = models.CharField(max_length=32, blank=True)
    vat_amount = models.CharField(max_length=32, blank=True)
    extracted_vat_amount = models.CharField(max_length=32, blank=True)
    gross_amount = models.CharField(max_length=32, blank=True)
    extracted_gross_amount = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=32, blank=True)
    rate = models.CharField(max_length=32, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    reason = models.TextField(blank=True)
    classification_error = models.TextField(blank=True)

    class Meta:
        ordering = ["invoice_id", "id"]

    @property
    def low_confidence(self):
        if self.classification_error:
            return True
        if self.confidence is None:
            return False
        return self.confidence < float(settings.VAT_LOW_CONFIDENCE)


class AuditEvent(models.Model):
    batch = models.ForeignKey(Batch, related_name="events", on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=40)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created", "id"]


class VatReturn(models.Model):
    batch = models.OneToOneField(Batch, related_name="vat_return", on_delete=models.CASCADE)
    totals = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)
