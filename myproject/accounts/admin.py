from django.contrib import admin

from .models import RoleDefinition, RolePermissionPolicy, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "approval_status", "approved_by", "approved_at")
    search_fields = ("user__username", "user__email")
    list_filter = ("role", "approval_status")


@admin.register(RolePermissionPolicy)
class RolePermissionPolicyAdmin(admin.ModelAdmin):
    list_display = ("role", "updated_at")
    search_fields = ("role",)


@admin.register(RoleDefinition)
class RoleDefinitionAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "enabled", "can_be_registered", "is_staff_role", "is_system")
    search_fields = ("key", "name")
    list_filter = ("enabled", "can_be_registered", "is_staff_role", "is_system")
