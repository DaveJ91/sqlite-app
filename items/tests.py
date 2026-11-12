from django.test import TestCase

from .models import Item


class ItemTests(TestCase):
    def test_create_edit_delete(self):
        response = self.client.post("/", {"title": "Milk", "notes": "2 litres"})
        self.assertRedirects(response, "/")
        item = Item.objects.get()
        self.assertEqual(item.title, "Milk")

        response = self.client.post(
            f"/{item.pk}/edit/",
            {"title": "Oat milk", "notes": "1 litre"},
        )
        self.assertRedirects(response, "/")
        item.refresh_from_db()
        self.assertEqual(item.title, "Oat milk")

        response = self.client.post(f"/{item.pk}/delete/")
        self.assertRedirects(response, "/")
        self.assertEqual(Item.objects.count(), 0)

    def test_list_shows_item(self):
        Item.objects.create(title="Bread")
        response = self.client.get("/")
        self.assertContains(response, "Bread")
