from django.urls import path

from . import views

app_name = "sql_execute"

urlpatterns = [
    path("", views.sql_execute_page, name="index"),
    path("api/repo/sync/", views.sql_repo_sync_api, name="sql_repo_sync_api"),
    path("api/repo/folders/", views.sql_repo_folders_api, name="sql_repo_folders_api"),
    path("api/repo/files/", views.sql_repo_files_api, name="sql_repo_files_api"),
    path(
        "api/repo/file-preview/",
        views.sql_repo_file_preview_api,
        name="sql_repo_file_preview_api",
    ),
    path("api/request/create/", views.sql_request_create_api, name="sql_request_create_api"),
    path("api/request/action/", views.sql_request_action_api, name="sql_request_action_api"),
    path(
        "api/request/auto-approve-all/",
        views.sql_request_auto_approve_all_api,
        name="sql_request_auto_approve_all_api",
    ),
    path("api/request/progress/", views.sql_request_progress_api, name="sql_request_progress_api"),
    path(
        "api/request/file-preview/",
        views.sql_request_file_preview_api,
        name="sql_request_file_preview_api",
    ),
]
