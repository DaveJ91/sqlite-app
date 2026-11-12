from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("original_name", "created", "error")
    search_fields = ("original_name", "extracted_text")
