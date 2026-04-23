from django.urls import path
from . import views

app_name = "release_track"

urlpatterns = [
    path("", views.release_track_index, name="index"),
    path("execute/", views.release_track_execute, name="execute"),
    # API
    path("api/batches/", views.release_track_api_batches, name="api_batches"),
    path("api/batch-detail/", views.release_track_api_batch_detail, name="api_batch_detail"),
    path("api/run/start/", views.release_track_api_run_start, name="api_run_start"),
    path("api/run/progress/", views.release_track_api_run_progress, name="api_run_progress"),
    path("api/run/approve/", views.release_track_api_run_approve, name="api_run_approve"),
    path("api/precheck/", views.release_track_api_precheck, name="api_precheck"),
    path("api/create-mr/", views.release_track_api_create_mr, name="api_create_mr"),
    path("api/create-tag/", views.release_track_api_create_tag, name="api_create_tag"),
]
