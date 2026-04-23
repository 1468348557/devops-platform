from django.urls import path

from .views import (
    RegisterView,
    admin_config,
    role_permissions_config,
    approval_bulk_action,
    UserLoginView,
    UserLogoutView,
    approval_action,
    approval_list,
    dashboard,
    my_password,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", RegisterView.as_view(), name="register"),
    path("approval/", approval_list, name="approval_list"),
    path("approval/<int:profile_id>/action/", approval_action, name="approval_action"),
    path("approval/bulk-action/", approval_bulk_action, name="approval_bulk_action"),
    path("admin-config/", admin_config, name="admin_config"),
    path("role-permissions/", role_permissions_config, name="role_permissions_config"),
    path("my-password/", my_password, name="my_password"),
]
