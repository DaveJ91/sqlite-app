from django.urls import path

from . import views

urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("batches/<int:pk>/desk/", views.batch_desk, name="batch_desk"),
    path("batches/<int:pk>/vat/", views.batch_vat, name="batch_vat"),
    path("batches/<int:pk>/deduct/", views.batch_deduct, name="batch_deduct"),
    path("batches/<int:pk>/sign/", views.batch_signoff, name="batch_signoff"),
    path("batches/<int:pk>/return/", views.batch_return, name="batch_return"),
]
