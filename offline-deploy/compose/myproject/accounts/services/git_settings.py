from __future__ import annotations

import os
import re
import base64
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from accounts.models import GitPlatformConfig
from django.conf import settings

DEFAULT_GIT_BASE_URL = "https://gitlab.spdb.com"
DEFAULT_GIT_GROUP = "zh-1087"
DEFAULT_WORK_BASE_DIR = "/workspace/repos"


@dataclass
class RuntimeGitSettings:
    git_base_url: str
    git_group: str
    work_base_dir: str
    git_username: str
    git_password: str
    git_pat: str

    @property
    def parsed_base_url(self):
        return urlsplit(self.git_base_url)

    @property
    def host(self) -> str:
        return self.parsed_base_url.netloc

    @property
    def work_base_path(self) -> Path:
        return Path(self.work_base_dir)

    def resolve_writable_work_base_path(self) -> tuple[Path, str]:
        preferred = self.work_base_path
        try:
            preferred.mkdir(parents=True, exist_ok=True)
            return preferred, "configured"
        except OSError:
            fallback = settings.BASE_DIR / ".runtime" / "repos"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback, "fallback"

    def preferred_auth(self) -> str:
        if self.git_pat:
            return "pat"
        if self.git_username and self.git_password:
            return "basic"
        return "none"

    def with_credentials_url(self, project: str) -> str:
        path = f"{self.git_group}/{project}.git".strip("/")
        parsed = self.parsed_base_url
        auth_mode = self.preferred_auth()
        netloc = parsed.netloc
        if auth_mode == "pat":
            user = quote("oauth2", safe="")
            password = quote(self.git_pat, safe="")
            netloc = f"{user}:{password}@{netloc}"
        elif auth_mode == "basic":
            user = quote(self.git_username, safe="")
            password = quote(self.git_password, safe="")
            netloc = f"{user}:{password}@{netloc}"
        new_path = f"{parsed.path.rstrip('/')}/{path}"
        return urlunsplit((parsed.scheme, netloc, new_path, "", ""))

    def repo_url(self, project: str) -> str:
        path = f"{self.git_group}/{project}.git".strip("/")
        parsed = self.parsed_base_url
        new_path = f"{parsed.path.rstrip('/')}/{path}"
        return urlunsplit((parsed.scheme, parsed.netloc, new_path, "", ""))

    def git_auth_config_args(self) -> list[str]:
        auth_mode = self.preferred_auth()
        username = ""
        secret = ""
        if auth_mode == "pat":
            username = "oauth2"
            secret = self.git_pat
        elif auth_mode == "basic":
            username = self.git_username
            secret = self.git_password
        if not username or not secret:
            return []
        token_raw = f"{username}:{secret}".encode("utf-8")
        token = base64.b64encode(token_raw).decode("ascii")
        return ["-c", f"http.extraHeader=Authorization: Basic {token}"]

    def masked_remote_url(self, project: str) -> str:
        path = f"{self.git_group}/{project}.git".strip("/")
        parsed = self.parsed_base_url
        auth_mode = self.preferred_auth()
        netloc = parsed.netloc
        if auth_mode == "pat":
            netloc = f"oauth2:***@{netloc}"
        elif auth_mode == "basic":
            masked_user = self.git_username or "***"
            netloc = f"{masked_user}:***@{netloc}"
        new_path = f"{parsed.path.rstrip('/')}/{path}"
        return urlunsplit((parsed.scheme, netloc, new_path, "", ""))


def _normalize_base_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return DEFAULT_GIT_BASE_URL
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    return f"https://{raw.rstrip('/')}"


def _env_fallback() -> RuntimeGitSettings:
    env_base_url = os.getenv("GIT_BASE_URL", "").strip()
    env_host = os.getenv("GIT_HOST", "").strip()
    base_url = env_base_url or env_host
    git_base_url = _normalize_base_url(base_url or DEFAULT_GIT_BASE_URL)
    git_group = os.getenv("GIT_GROUP", os.getenv("GITLAB_GROUP", DEFAULT_GIT_GROUP)).strip()
    work_base_dir = os.getenv("WORK_BASE_DIR", DEFAULT_WORK_BASE_DIR).strip() or DEFAULT_WORK_BASE_DIR
    git_username = os.getenv("GIT_USERNAME", "").strip()
    git_password = os.getenv("GIT_PASSWORD", "").strip()
    git_pat = os.getenv("GIT_PAT", os.getenv("GITLAB_TOKEN", "")).strip()
    return RuntimeGitSettings(
        git_base_url=git_base_url,
        git_group=git_group or DEFAULT_GIT_GROUP,
        work_base_dir=work_base_dir,
        git_username=git_username,
        git_password=git_password,
        git_pat=git_pat,
    )


def get_runtime_git_settings() -> RuntimeGitSettings:
    env_defaults = _env_fallback()
    config = GitPlatformConfig.get_solo_safe()
    return RuntimeGitSettings(
        git_base_url=_normalize_base_url(config.git_base_url or env_defaults.git_base_url),
        git_group=(config.git_group or env_defaults.git_group).strip() or DEFAULT_GIT_GROUP,
        work_base_dir=(config.work_base_dir or env_defaults.work_base_dir).strip() or DEFAULT_WORK_BASE_DIR,
        git_username=(config.git_username or env_defaults.git_username).strip(),
        git_password=(config.git_password or env_defaults.git_password).strip(),
        git_pat=(config.git_pat or env_defaults.git_pat).strip(),
    )


_URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/\s:@]+):([^@\s/]+)@")
_GITLAB_TOKEN_RE = re.compile(r"(?i)(glpat-[a-z0-9\-_]+)")
_GENERIC_SECRET_RE = re.compile(
    r"(?i)\b(token|password|passwd|pwd|secret)\s*[:=]\s*([^\s,;]+)"
)


def scrub_sensitive_text(text: str) -> str:
    """Best-effort secret redaction for logs and error messages."""
    value = str(text or "")
    if not value:
        return value
    value = _URL_CREDENTIAL_RE.sub(r"\1\2:***@", value)
    value = _GITLAB_TOKEN_RE.sub("***", value)
    value = _GENERIC_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", value)
    return value

