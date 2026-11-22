from django.urls import path

from .views import staff as v

urlpatterns = [
    path("staff/imports/sources", v.SourcesView.as_view(), name="staff-import-sources"),
    path("staff/imports/jobs", v.JobListCreateView.as_view(), name="staff-import-jobs"),
    path("staff/imports/jobs/<uuid:id>", v.JobDetailView.as_view(), name="staff-import-job"),
    path("staff/imports/jobs/<uuid:id>/cancel", v.JobCancelView.as_view(), name="staff-import-job-cancel"),
    path("staff/imports/jobs/<uuid:id>/retry", v.JobRetryView.as_view(), name="staff-import-job-retry"),
    path("staff/imports/jobs/<uuid:id>/logs", v.JobLogsView.as_view(), name="staff-import-job-logs"),
    path("staff/imports/jobs/<uuid:id>/items", v.JobItemsView.as_view(), name="staff-import-job-items"),
    path("staff/imports/products", v.ProductListView.as_view(), name="staff-import-products"),
    path("staff/imports/products/<uuid:id>", v.ProductDetailView.as_view(), name="staff-import-product"),
    path("staff/imports/products/<uuid:id>/approve", v.ProductApproveView.as_view(), name="staff-import-approve"),
    path("staff/imports/products/<uuid:id>/reject", v.ProductRejectView.as_view(), name="staff-import-reject"),
    path("staff/imports/products/<uuid:id>/publish", v.ProductPublishView.as_view(), name="staff-import-publish"),
]
