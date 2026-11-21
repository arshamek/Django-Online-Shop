from celery import shared_task

from apps.common.tasks import MaralTask

from .models import Product
from .services import projection


@shared_task(base=MaralTask, name="catalog.refresh_brand_projections")
def refresh_brand_projections(brand_id: int) -> int:
    return projection.refresh_many(Product.objects.filter(brand_id=brand_id).values_list("pk", flat=True))


@shared_task(base=MaralTask, name="catalog.refresh_category_projections")
def refresh_category_projections(category_id: int) -> int:
    from apps.categories.models import Category

    category = Category.objects.filter(pk=category_id).first()
    if category is None:
        return 0
    ids = Category.objects.filter(path__startswith=category.path).values_list("pk", flat=True)
    return projection.refresh_many(Product.objects.filter(category_id__in=list(ids)).values_list("pk", flat=True))


@shared_task(base=MaralTask, name="catalog.rebuild_projection")
def rebuild_projection() -> dict:
    return projection.rebuild_all()
