from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import staff_views, views

router = SimpleRouter(trailing_slash=False)
router.register("staff/shipping/zones", staff_views.ZoneViewSet, basename="staff-shipping-zone")
router.register("staff/shipping/methods", staff_views.MethodViewSet, basename="staff-shipping-method")
router.register("staff/shipping/rates", staff_views.RateViewSet, basename="staff-shipping-rate")
router.register("staff/shipping/country-policies", staff_views.CountryPolicyViewSet, basename="staff-shipping-policy")

urlpatterns = [
    path("shipping/methods", views.ShippingMethodsView.as_view(), name="shipping-methods"),
    path("shipping/estimate", views.ShippingEstimateView.as_view(), name="shipping-estimate"),
    path("shipping/countries", views.ShippingCountriesView.as_view(), name="shipping-countries"),
    path(
        "shipping/address-requirements", views.AddressRequirementsView.as_view(), name="shipping-address-requirements"
    ),
    path("staff/shipments", staff_views.StaffShipmentsView.as_view(), name="staff-shipments"),
    path("staff/shipments/<uuid:shipment_id>", staff_views.StaffShipmentView.as_view(), name="staff-shipment"),
    path(
        "staff/shipments/<uuid:shipment_id>/status",
        staff_views.StaffShipmentStatusView.as_view(),
        name="staff-shipment-status",
    ),
    path(
        "staff/shipments/<uuid:shipment_id>/tracking",
        staff_views.StaffShipmentTrackingView.as_view(),
        name="staff-shipment-tracking",
    ),
    path(
        "staff/shipments/<uuid:shipment_id>/sync",
        staff_views.StaffShipmentSyncView.as_view(),
        name="staff-shipment-sync",
    ),
    path(
        "staff/orders/<uuid:order_id>/shipments",
        staff_views.StaffOrderShipmentsView.as_view(),
        name="staff-order-shipments",
    ),
    path("", include(router.urls)),
]
