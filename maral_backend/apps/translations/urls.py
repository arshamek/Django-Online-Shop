from django.urls import path

from .views import staff as v

urlpatterns = [
    path("staff/translations/summary", v.SummaryView.as_view(), name="staff-translation-summary"),
    path("staff/translations/jobs", v.JobListView.as_view(), name="staff-translation-jobs"),
    path("staff/translations/jobs/<int:pk>", v.JobDetailView.as_view(), name="staff-translation-job"),
    path("staff/translations/jobs/<int:pk>/retry", v.JobRetryView.as_view(), name="staff-translation-job-retry"),
    path("staff/translations/products/<uuid:id>", v.ProductStatusView.as_view(), name="staff-translation-product"),
    path(
        "staff/translations/products/<uuid:id>/<str:language>",
        v.CorrectionView.as_view(),
        name="staff-translation-correct",
    ),
    path(
        "staff/translations/products/<uuid:id>/<str:language>/approve",
        v.ApproveView.as_view(),
        name="staff-translation-approve",
    ),
]
