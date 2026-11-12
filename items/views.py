from django.shortcuts import get_object_or_404, redirect, render

from .forms import ItemForm
from .models import Item


def item_list(request):
    if request.method == "POST":
        form = ItemForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("item_list")
    else:
        form = ItemForm()
    return render(
        request,
        "items/list.html",
        {"items": Item.objects.all(), "form": form},
    )


def item_edit(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if request.method == "POST":
        form = ItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            return redirect("item_list")
    else:
        form = ItemForm(instance=item)
    return render(request, "items/edit.html", {"item": item, "form": form})


def item_delete(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if request.method == "POST":
        item.delete()
    return redirect("item_list")
