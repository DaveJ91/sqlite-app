from django import forms

from .models import Item


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["title", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Title", "autofocus": True}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Notes"}),
        }
