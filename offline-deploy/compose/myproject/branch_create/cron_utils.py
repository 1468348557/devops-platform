from __future__ import annotations

from datetime import datetime


def cron_matches(expr: str, now: datetime) -> bool:
    """检查标准 5 字段 cron 表达式是否匹配给定时间。"""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return False

    minute, hour, dom, month, dow = parts

    def match(token: str, value: int) -> bool:
        if token == "*":
            return True
        if token.startswith("*/"):
            try:
                step = int(token[2:])
                return step > 0 and value % step == 0
            except ValueError:
                return False
        if "," in token:
            return any(match(t.strip(), value) for t in token.split(","))
        try:
            return int(token) == value
        except ValueError:
            return False

    return (
        match(minute, now.minute)
        and match(hour, now.hour)
        and match(dom, now.day)
        and match(month, now.month)
        and match(dow, now.weekday())
    )
