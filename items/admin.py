from django.contrib import admin

from .models import AuditEvent, Batch, Invoice, InvoiceLine, VatReturn


class InvoiceInline(admin.TabularInline):
    model = Invoice
    extra = 0


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("id", "period_label", "status", "created")
    inlines = [InvoiceInline]


admin.site.register(InvoiceLine)
admin.site.register(AuditEvent)
admin.site.register(VatReturn)
