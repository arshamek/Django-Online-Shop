from django.urls import path

from .views import customer as c
from .views import staff as s

urlpatterns = [
    # customer
    path("checkout/preview", c.CheckoutPreviewView.as_view(), name="checkout-preview"),
    path("checkout", c.CheckoutCreateView.as_view(), name="checkout-create"),
    path("checkout/<uuid:checkout_id>", c.CheckoutDetailView.as_view(), name="checkout-detail"),
    path("checkout/<uuid:checkout_id>/payment", c.CheckoutPaymentView.as_view(), name="checkout-payment"),
    path("orders", c.OrdersView.as_view(), name="orders"),
    path("orders/<str:ref>", c.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:ref>/tracking", c.OrderTrackingView.as_view(), name="order-tracking"),
    path("orders/<str:ref>/history", c.OrderHistoryView.as_view(), name="order-history"),
    path("orders/<str:ref>/payments", c.OrderPaymentsView.as_view(), name="order-payments"),
    path("orders/<str:ref>/shipments", c.OrderShipmentsView.as_view(), name="order-shipments"),
    path("orders/<str:ref>/cancel", c.OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<str:ref>/refunds", c.OrderRefundsView.as_view(), name="order-refunds"),
    # staff
    path("staff/orders", s.StaffOrdersView.as_view(), name="staff-orders"),
    path("staff/orders/<uuid:order_id>", s.StaffOrderView.as_view(), name="staff-order"),
    path("staff/orders/<uuid:order_id>/transition", s.StaffTransitionView.as_view(), name="staff-order-transition"),
    path("staff/orders/<uuid:order_id>/refunds", s.StaffOrderRefundView.as_view(), name="staff-order-refund"),
    path("staff/refunds", s.StaffRefundsView.as_view(), name="staff-refunds"),
    path("staff/refunds/<uuid:refund_id>/approve", s.StaffRefundActionView.as_view(action="approve"), name="staff-refund-approve"),
    path("staff/refunds/<uuid:refund_id>/reject", s.StaffRefundActionView.as_view(action="reject"), name="staff-refund-reject"),
    path("staff/refunds/<uuid:refund_id>/complete", s.StaffRefundActionView.as_view(action="complete"), name="staff-refund-complete"),
    path("staff/refunds/<uuid:refund_id>/execute", s.StaffRefundActionView.as_view(action="execute"), name="staff-refund-execute"),
    path("staff/inventory/receive", s.StaffStockReceiveView.as_view(), name="staff-stock-receive"),
    path("staff/inventory/adjust", s.StaffStockAdjustView.as_view(), name="staff-stock-adjust"),
]
