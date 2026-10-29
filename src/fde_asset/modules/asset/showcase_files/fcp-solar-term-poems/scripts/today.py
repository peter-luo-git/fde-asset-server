#!/usr/bin/env python3
"""给一个日期，说出它在二十四节气里的位置、是不是传统节日，并带出相关古诗原文。

只用标准库，不联网。数据在 ../references/：calendar.json（2024–2035 年）、poems.json。

用法：
    python3 today.py              # 今天（北京时间）
    python3 today.py 2026-10-23   # 指定日期
输出 JSON，字段见 SKILL.md。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "references"
SEASONS = {
    "立春": "春",
    "雨水": "春",
    "惊蛰": "春",
    "春分": "春",
    "清明": "春",
    "谷雨": "春",
    "立夏": "夏",
    "小满": "夏",
    "芒种": "夏",
    "夏至": "夏",
    "小暑": "夏",
    "大暑": "夏",
    "立秋": "秋",
    "处暑": "秋",
    "白露": "秋",
    "秋分": "秋",
    "寒露": "秋",
    "霜降": "秋",
    "立冬": "冬",
    "小雪": "冬",
    "大雪": "冬",
    "冬至": "冬",
    "小寒": "冬",
    "大寒": "冬",
}


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def describe(day: date) -> dict:
    calendar = load("calendar.json")
    library = load("poems.json")["poems"]
    terms = sorted((date.fromisoformat(k), v) for k, v in calendar["terms"].items())
    if not terms[0][0] <= day <= terms[-1][0]:
        return {
            "date": day.isoformat(),
            "error": f"只内置了 {terms[0][0].year}–{terms[-1][0].year} 年的节气表，这个日期超出范围",
        }
    previous = max((t for t in terms if t[0] <= day), key=lambda t: t[0])
    upcoming = min((t for t in terms if t[0] > day), key=lambda t: t[0], default=None)
    festival = calendar["festivals"].get(day.isoformat())
    term = previous[1]
    result = {
        "date": day.isoformat(),
        "weekday": "一二三四五六日"[day.weekday()],
        "season": SEASONS[term],
        "solar_term": term,
        "is_term_day": previous[0] == day,
        "days_into_term": (day - previous[0]).days,
        "next_term": upcoming[1] if upcoming else None,
        "days_to_next_term": (upcoming[0] - day).days if upcoming else None,
        "festival": festival,
        # 节日的诗排在前面；节气当天以外也给出所处节气的诗
        "poems": (library.get(festival, []) if festival else []) + library.get(term, []),
        "source": load("poems.json")["source"],
    }
    soon = [
        (date.fromisoformat(k), v)
        for k, v in calendar["festivals"].items()
        if day < date.fromisoformat(k) <= day + timedelta(days=7)
    ]
    if soon:
        result["festival_soon"] = {"name": soon[0][1], "date": soon[0][0].isoformat()}
    return result


def main() -> int:
    if len(sys.argv) > 1:
        try:
            day = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(json.dumps({"error": "日期格式应为 YYYY-MM-DD"}, ensure_ascii=False))
            return 2
    else:
        day = datetime.now(timezone(timedelta(hours=8))).date()
    print(json.dumps(describe(day), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
