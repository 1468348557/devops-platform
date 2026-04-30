"""
新建分支 - 配置解析
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class BranchTask:
    line_no: int
    new_branch: str
    raw_project: str
    mapped_project: str


STANDARD_PROJECTS = [
    "hobo-customer-front",
    "hobo-element-front",
    "hobo-credit-front",
    "hobo-asset-front",
    "hobo-payment-front",
    "hobo-deposit-front",
    "hobo-pub-front",
    "hobo-work-front",
    "hobo-image-component",
    "hobo-factory-front",
    "hobo-flow-orch",
    "hobo-flow-config",
    "hobo-pub-flow",
    "hobo-deposit-flow",
    "hobo-customer-flow",
    "hobo-credit-flow",
    "hobo-element-flow",
    "hobo-asset-flow",
    "hobo-payment-flow",
]


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("_", "-")


def _map_project(raw: str) -> Optional[str]:
    n = _normalize(raw)
    if n in STANDARD_PROJECTS:
        return n
    if not n.startswith("hobo-"):
        candidate = f"hobo-{n}"
        if candidate in STANDARD_PROJECTS:
            return candidate
    suffix = n.replace("hobo-", "")
    for p in STANDARD_PROJECTS:
        if p.replace("hobo-", "") == suffix:
            return p
    return None


def parse_branch_config(config_text: str, base_branch: str = "master") -> list:
    tasks = []
    for line_no, raw_line in enumerate(config_text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        new_branch = parts[0].strip()
        raw_project = parts[1].strip()
        if not new_branch or not raw_project:
            continue
        mapped = _map_project(raw_project)
        if mapped:
            tasks.append(BranchTask(
                line_no=line_no,
                new_branch=new_branch,
                raw_project=raw_project,
                mapped_project=mapped,
            ))
    return tasks
