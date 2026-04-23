from django.urls import path
from . import views
from . import release_entry_views
from . import hobo_ledger_views

app_name = "branch_create"

urlpatterns = [
    path("", views.branch_create_index, name="index"),
    path("execute/", views.branch_create_execute, name="execute"),
    path("release-entry/", release_entry_views.release_entry_page, name="release_entry_page"),
    # API
    path("api/precheck/", views.branch_create_api_precheck, name="api_precheck"),
    path("api/create/", views.branch_create_api_create, name="api_create"),
    path("api/branch-tasks/preview/", views.branch_task_preview_api, name="branch_task_preview_api"),
    path("api/branch-tasks/execute/", views.branch_task_execute_api, name="branch_task_execute_api"),
    path("api/branch-tasks/execute/start/", views.branch_task_execute_start_api, name="branch_task_execute_start_api"),
    path("api/branch-tasks/execute/progress/", views.branch_task_execute_progress_api, name="branch_task_execute_progress_api"),
    path("api/schedules/", views.schedule_list_api, name="schedule_list_api"),
    path("api/schedules/save/", views.schedule_save_api, name="schedule_save_api"),
    path("api/schedules/delete/", views.schedule_delete_api, name="schedule_delete_api"),
    path("api/schedules/run/", views.schedule_run_api, name="schedule_run_api"),
    # Release entry APIs
    path("release-entry/api/batches/", release_entry_views.release_entry_batch_list, name="release_entry_batch_list"),
    path("release-entry/api/batches/create/", release_entry_views.release_entry_batch_create, name="release_entry_batch_create"),
    path("release-entry/api/batches/delete/", release_entry_views.release_entry_batch_delete, name="release_entry_batch_delete"),
    path("release-entry/api/items/", release_entry_views.release_entry_item_list, name="release_entry_item_list"),
    path("release-entry/api/items/last-by-project/", release_entry_views.release_entry_item_last_by_project, name="release_entry_item_last_by_project"),
    path("release-entry/api/items/create/", release_entry_views.release_entry_item_create, name="release_entry_item_create"),
    path("release-entry/api/items/update/", release_entry_views.release_entry_item_update, name="release_entry_item_update"),
    path("release-entry/api/items/submit/", release_entry_views.release_entry_item_submit, name="release_entry_item_submit"),
    path("release-entry/api/items/delete/", release_entry_views.release_entry_item_delete, name="release_entry_item_delete"),
    # HOBO 需求登记台账
    path("hobo-ledger/", hobo_ledger_views.hobo_ledger_page, name="hobo_ledger_page"),
    path("hobo-ledger/api/projects/", hobo_ledger_views.hobo_ledger_project_list, name="hobo_ledger_project_list"),
    path("hobo-ledger/api/items/", hobo_ledger_views.hobo_ledger_item_list, name="hobo_ledger_item_list"),
    path("hobo-ledger/api/items/create/", hobo_ledger_views.hobo_ledger_item_create, name="hobo_ledger_item_create"),
    path("hobo-ledger/api/items/update/", hobo_ledger_views.hobo_ledger_item_update, name="hobo_ledger_item_update"),
    path("hobo-ledger/api/items/delete/", hobo_ledger_views.hobo_ledger_item_delete, name="hobo_ledger_item_delete"),
]
