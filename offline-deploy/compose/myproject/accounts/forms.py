from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User

from .models import RoleDefinition, UserProfile


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="用户名",
        widget=forms.TextInput(attrs={"class": "form-input", "placeholder": "请输入用户名"}),
    )
    password = forms.CharField(
        label="密码",
        widget=forms.PasswordInput(attrs={"class": "form-input", "placeholder": "请输入密码"}),
    )


class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        label="邮箱",
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-input", "placeholder": "请输入邮箱"}),
    )
    role = forms.ModelChoiceField(
        label="角色",
        queryset=RoleDefinition.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-input"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "role", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 注册页允许选择所有启用中的角色（含超管新增角色）。
        self.fields["role"].queryset = RoleDefinition.objects.filter(
            enabled=True
        ).order_by("id")
        self.fields["username"].label = "用户名"
        self.fields["username"].widget.attrs.update(
            {"class": "form-input", "placeholder": "请输入用户名"}
        )
        self.fields["password1"].label = "密码"
        self.fields["password1"].widget.attrs.update(
            {"class": "form-input", "placeholder": "请输入密码"}
        )
        self.fields["password2"].label = "确认密码"
        self.fields["password2"].widget.attrs.update(
            {"class": "form-input", "placeholder": "请再次输入密码"}
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.is_staff = False
        if commit:
            user.save()
            UserProfile.objects.create(
                user=user,
                role=self.cleaned_data["role"],
                approval_status=UserProfile.ApprovalStatus.PENDING,
            )
        return user
