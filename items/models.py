from django.db import models


class Item(models.Model):
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated"]

    def __str__(self):
        return self.title
