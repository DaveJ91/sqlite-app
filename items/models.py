from django.conf import settings
from django.db import models


class Batch(models.Model):
    EXTRACTED = "extracted"
    EXTRACTION_ACCEPTED = "extraction_accepted"
    CLASSIFIED = "classified"
    DEDUCTIBILITY = "deductibility"
    DEDUCTION_ACCEPTED = "deduction_accepted"
    RETURN_READY = "return_ready"
    STATUS_CHOICES = [
        (EXTRACTED, "Review extraction"),
        (EXTRACTION_ACCEPTED, "Classifying"),
        (CLASSIFIED, "Review VAT"),
        (DEDUCTIBILITY, "Review deductibility"),
        (DEDUCTION_ACCEPTED, "Sign off"),
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
    supply = models.CharField(max_length=16, blank=True)
    nature = models.CharField(max_length=16, blank=True)
    place_of_supply = models.CharField(max_length=16, blank=True)
    who_accounts = models.CharField(max_length=16, blank=True)
    classification_chain = models.JSONField(default=list, blank=True)
    model_category = models.CharField(max_length=32, blank=True)
    model_supply = models.CharField(max_length=16, blank=True)
    model_nature = models.CharField(max_length=16, blank=True)
    model_place = models.CharField(max_length=16, blank=True)
    model_who = models.CharField(max_length=16, blank=True)
    deductible = models.CharField(max_length=16, blank=True)
    deductible_percent = models.CharField(max_length=16, blank=True)
    deductible_vat = models.CharField(max_length=32, blank=True)
    blocked_reason = models.CharField(max_length=32, blank=True)
    deductibility_chain = models.JSONField(default=list, blank=True)
    deductibility_confidence = models.FloatField(null=True, blank=True)
    deductibility_reason = models.TextField(blank=True)
    deductibility_error = models.TextField(blank=True)
    model_deductible = models.CharField(max_length=16, blank=True)
    model_blocked = models.CharField(max_length=32, blank=True)
    model_percent = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["invoice_id", "id"]

    @property
    def low_confidence(self):
        if self.classification_error:
            return True
        if self.confidence is None:
            return False
        return self.confidence < float(settings.VAT_LOW_CONFIDENCE)

    @property
    def deductibility_low(self):
        if self.deductibility_error:
            return True
        if self.deductibility_confidence is None:
            return False
        return self.deductibility_confidence < float(settings.VAT_LOW_CONFIDENCE)

    @property
    def classification_overridden(self):
        return _differs(
            (self.model_category, self.category),
            (self.model_supply, self.supply),
            (self.model_nature, self.nature),
            (self.model_place, self.place_of_supply),
            (self.model_who, self.who_accounts),
        )

    @property
    def deductibility_overridden(self):
        return _differs(
            (self.model_deductible, self.deductible),
            (self.model_blocked, self.blocked_reason),
            (self.model_percent, self.deductible_percent),
        )


def _differs(*pairs):
    for original, current in pairs:
        if original and original != current:
            return True
    return False


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
    boxes = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)


class VatRun(models.Model):
    EXTRACT = "extract"
    CLASSIFY = "classify"
    DEDUCT = "deduct"
    RETURN = "return"
    KIND_CHOICES = [
        (EXTRACT, "Extract"),
        (CLASSIFY, "Classify"),
        (DEDUCT, "Deduct"),
        (RETURN, "Return"),
    ]

    batch = models.ForeignKey(Batch, related_name="runs", null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    created = models.DateTimeField(auto_now_add=True)
    period_label = models.CharField(max_length=64, blank=True)
    period_date = models.DateField(null=True, blank=True)
    mock = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period_date", "-created", "-id"]

    def __str__(self):
        return f"{self.kind} {self.period_label or self.pk}"


class SignOff(models.Model):
    batch = models.OneToOneField(Batch, related_name="sign_off", on_delete=models.CASCADE)
    reviewer = models.CharField(max_length=120)
    note = models.TextField(blank=True)
    acknowledged = models.BooleanField(default=False)
    flag_count = models.PositiveIntegerField(default=0)
    flags = models.JSONField(default=list, blank=True)
    extraction_edits = models.PositiveIntegerField(default=0)
    classification_overrides = models.PositiveIntegerField(default=0)
    deductibility_overrides = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.reviewer} on batch {self.batch_id}"


class DeskTurn(models.Model):
    batch = models.ForeignKey(Batch, related_name="desk_turns", on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    question = models.TextField()
    blocks = models.JSONField(default=list, blank=True)
    tool_names = models.JSONField(default=list, blank=True)
    mock = models.BooleanField(default=True)

    class Meta:
        ordering = ["created", "id"]

    def __str__(self):
        return f"Desk {self.batch_id} · {self.created:%Y-%m-%d %H:%M}"
