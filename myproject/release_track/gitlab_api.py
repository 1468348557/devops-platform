import urllib.parse
import urllib.error
import urllib.request
import json
import base64
from dataclasses import dataclass
from typing import Optional


@dataclass
class GitLabConfig:
    base_url: str
    group: str
    token: str = ""
    username: str = ""
    password: str = ""
    api_version: str = "v4"

    @property
    def api_base(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/{self.api_version}"


class GitLabAPI:
    """GitLab API 客户端"""

    def __init__(self, config: GitLabConfig):
        self.config = config

    def _url(self, path: str) -> str:
        return f"{self.config.api_base}{path}"

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        form: bool = False,
    ) -> dict:
        url = self._url(path)
        headers = {}
        if self.config.token:
            headers["PRIVATE-TOKEN"] = self.config.token
        elif self.config.username and self.config.password:
            raw = f"{self.config.username}:{self.config.password}".encode("utf-8")
            encoded = base64.b64encode(raw).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
        body = None

        if data:
            if form:
                encoded = urllib.parse.urlencode(data).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                body = encoded
            else:
                headers["Content-Type"] = "application/json"
                body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result or {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            raise Exception(f"GitLab API 错误 {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"连接 GitLab 失败: {e.reason}")

    def _project_path(self, repo: str) -> str:
        return urllib.parse.quote(f"{self.config.group}/{repo}", safe="")

    # ---- 分支 ----

    def branch_exists(self, repo: str, branch: str) -> bool:
        try:
            self._request(
                "GET",
                f"/projects/{self._project_path(repo)}/repository/branches/{branch}",
            )
            return True
        except Exception:
            return False

    def list_branches(self, repo: str) -> list:
        return self._request(
            "GET", f"/projects/{self._project_path(repo)}/repository/branches"
        )

    # ---- Merge Request ----

    def create_mr(
        self,
        repo: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> dict:
        data = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
        }
        return self._request(
            "POST",
            f"/projects/{self._project_path(repo)}/merge_requests",
            data,
            form=True,
        )

    def merge_mr(self, repo: str, mr_iid: int, merge_commit_message: str) -> dict:
        data = {"merge_commit_message": merge_commit_message}
        return self._request(
            "PUT",
            f"/projects/{self._project_path(repo)}/merge_requests/{mr_iid}/merge",
            data,
            form=True,
        )

    def get_mr(self, repo: str, mr_iid: int) -> dict:
        return self._request(
            "GET",
            f"/projects/{self._project_path(repo)}/merge_requests/{mr_iid}",
        )

    # ---- Tag ----

    def tag_exists(self, repo: str, tag_name: str) -> bool:
        try:
            self._request(
                "GET",
                f"/projects/{self._project_path(repo)}/repository/tags/{tag_name}",
            )
            return True
        except Exception:
            return False

    def create_tag(
        self, repo: str, tag_name: str, ref: str, message: str = ""
    ) -> dict:
        data = {"tag_name": tag_name, "ref": ref, "message": message}
        return self._request(
            "POST",
            f"/projects/{self._project_path(repo)}/repository/tags",
            data,
            form=True,
        )

    def delete_tag(self, repo: str, tag_name: str) -> dict:
        return self._request(
            "DELETE",
            f"/projects/{self._project_path(repo)}/repository/tags/{tag_name}",
        )

    def force_push_tag(
        self, repo: str, tag_name: str, ref: str, message: str
    ) -> dict:
        """强制覆盖 Tag（先删后建）"""
        if self.tag_exists(repo, tag_name):
            self.delete_tag(repo, tag_name)
        return self.create_tag(repo, tag_name, ref, message)
