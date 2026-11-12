from django.db import models


class Invoice(models.Model):
    file = models.FileField(upload_to="invoices/")
    original_name = models.CharField(max_length=255)
    extracted_text = models.TextField(blank=True)
    classification = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.supplier or self.original_name

    @property
    def supplier(self):
        return (self.classification or {}).get("supplier") or ""
