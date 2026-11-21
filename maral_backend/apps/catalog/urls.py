"""
Storefront routes. Canonical form has no trailing slash (docs/api/API_CONVENTIONS.md); the slash form used in the
Phase 4 brief is accepted too. Fixed `products/<word>` routes come before `products/<slug>`.
"""

from django.urls import path, re_path

from .views import public as v
from .views import staff as sv


def route(pattern, view, name):
    return re_path(rf"^{pattern}/?$", view, name=name)


urlpatterns = [
    route("storefront/homepage", v.HomepageView.as_view(), "storefront-homepage"),
    route("categories", v.CategoryListView.as_view(), "categories"),
    route(r"categories/(?P<slug>[-\w]+)", v.CategoryDetailView.as_view(), "category"),
    route("brands", v.BrandListView.as_view(), "brands"),
    route(r"brands/(?P<slug>[-\w]+)", v.BrandDetailView.as_view(), "brand"),
    route("products", v.ProductListView.as_view(), "products"),
    route("products/search", v.ProductSearchView.as_view(), "products-search"),
    route("products/filters", v.ProductFiltersView.as_view(), "products-filters"),
    route("products/recently-viewed", v.RecentlyViewedView.as_view(), "products-recently-viewed"),
    route(r"products/related/(?P<ref>[-\w]+)", v.ProductRelatedView.as_view(), "products-related"),
    route(r"products/(?P<slug>[-\w]+)", v.ProductDetailView.as_view(), "product"),
    # staff (accounts.catalog_manage)
    path("staff/products", sv.StaffProductListCreateView.as_view(), name="staff-products"),
    path("staff/products/<uuid:id>", sv.StaffProductDetailView.as_view(), name="staff-product"),
    path("staff/products/<uuid:id>/status", sv.StaffProductStatusView.as_view(), name="staff-product-status"),
    path(
        "staff/products/<uuid:id>/translations/<str:language>",
        sv.StaffProductTranslationView.as_view(),
        name="staff-product-translation",
    ),
    path("staff/products/<uuid:id>/variants", sv.StaffVariantCreateView.as_view(), name="staff-product-variants"),
    path("staff/variants/<uuid:id>", sv.StaffVariantDetailView.as_view(), name="staff-variant"),
    path("staff/products/<uuid:id>/images", sv.StaffImageUploadView.as_view(), name="staff-product-images"),
    path(
        "staff/products/<uuid:id>/images/reorder",
        sv.StaffImageReorderView.as_view(),
        name="staff-product-images-reorder",
    ),
    path("staff/images/<uuid:id>", sv.StaffImageDetailView.as_view(), name="staff-image"),
]
