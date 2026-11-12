from django.urls import path

from . import views

urlpatterns = [
    path("", views.item_list, name="item_list"),
    path("<int:pk>/edit/", views.item_edit, name="item_edit"),
    path("<int:pk>/delete/", views.item_delete, name="item_delete"),
]
