from django.contrib import admin

from .models import AuditEvent, Batch, DeskTurn, Invoice, InvoiceLine, SignOff, VatReturn, VatRun


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
admin.site.register(SignOff)
admin.site.register(DeskTurn)


@admin.register(VatRun)
class VatRunAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "period_label", "period_date", "mock", "batch", "created")
    list_filter = ("kind", "mock")
