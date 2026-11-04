"""岗位日报生成器。

输入一份结构化岗位清单（JSON），输出两份东西：
  1. Markdown 日报——给人看、可以进 git、可以贴到聊天里；
  2. HTML 邮件——直接粘进邮件正文，表格与链接在邮箱客户端里不会散架。

设计上刻意把「找岗位」和「排版」分开：抓取端（人或 Agent）只负责产出结构化数据，
这里只管校验、去重、过滤过期、分组和渲染。这样换抓取源不用动渲染，改版式也不用动抓取。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from collections import OrderedDict

# 方向分组顺序：表格按这个顺序出现，未列出的方向排在最后
TRACK_ORDER = [
    "国企央企-通信",
    "国企央企-电子信息",
    "通信工程-射频",
    "通信工程-无线/传输",
    "通信工程-测试/网络优化",
    "电子信息-嵌入式",
    "电子信息-硬件",
    "电子信息-电子工艺",
    "互联网-AI 应用",
    "互联网-前端/测试",
]

REQUIRED_FIELDS = ("title", "company", "city", "track", "deadline", "url")
URL_RE = re.compile(r"^https?://[^\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JobError(ValueError):
    """岗位数据不合法。宁可整条丢掉并报错，也不要把脏数据印进日报。"""


def load_jobs(path: str) -> tuple[list[dict], dict]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    meta = {k: v for k, v in payload.items() if k != "jobs"}
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        raise JobError("jobs 必须是数组")
    return jobs, meta


def normalize(job: dict, index: int) -> dict:
    # url 不放进必填：允许「确实搜不到投递页」的岗位进日报，但必须写明原因，
    # 否则等于把一条查不到入口的岗位混进可投列表。
    missing = [
        f for f in REQUIRED_FIELDS if f != "url" and not str(job.get(f, "")).strip()
    ]
    if missing:
        raise JobError(f"第 {index + 1} 条缺字段：{', '.join(missing)}")

    url = str(job.get("url", "")).strip()
    reason = str(job.get("no_url_reason", "")).strip()
    if not URL_RE.match(url):
        if not reason:
            raise JobError(f"第 {index + 1} 条 url 不是 http(s) 链接，且没有写 no_url_reason")
        url = ""

    return {
        "title": str(job["title"]).strip(),
        "company": str(job["company"]).strip(),
        "city": str(job["city"]).strip(),
        "track": str(job["track"]).strip(),
        "deadline": str(job["deadline"]).strip(),
        "url": url,
        "no_url_reason": reason,
    }


def dedupe(jobs: list[dict]) -> tuple[list[dict], int]:
    """同一公司 + 同一岗位名 + 同一城市视为重复，保留先出现的（清单靠前的优先级更高）。"""
    seen = set()
    kept = []
    for job in jobs:
        key = (job["company"], job["title"], job["city"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(job)
    return kept, len(jobs) - len(kept)


def is_expired(job: dict, today: dt.date) -> bool:
    """只过滤写得清截止日期的岗位；「招满即止」这种没法判断，一律保留。"""
    deadline = job["deadline"]
    if not DATE_RE.match(deadline):
        return False
    return dt.date.fromisoformat(deadline) < today


def group_by_track(jobs: list[dict]) -> "OrderedDict[str, list[dict]]":
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        groups.setdefault(job["track"], []).append(job)

    def sort_key(track: str) -> tuple[int, str]:
        return (TRACK_ORDER.index(track), "") if track in TRACK_ORDER else (len(TRACK_ORDER), track)

    return OrderedDict((k, groups[k]) for k in sorted(groups, key=sort_key))


def group_label(track: str) -> str:
    """把「通信工程-射频」这类内部标签换成日报里给人看的大类标题。"""
    families = [
        ("国企央企", "国企 / 央企"),
        ("通信工程", "通信工程"),
        ("电子信息", "电子信息"),
        ("互联网", "互联网"),
    ]
    for prefix, label in families:
        if track.startswith(prefix):
            return label
    return track


def render_markdown(jobs: list[dict], meta: dict, stats: dict) -> str:
    today = meta.get("date") or dt.date.today().isoformat()
    lines = [f"# 岗位日报 {today}（共 {len(jobs)} 条）", ""]

    scope = meta.get("scope") or []
    if scope:
        lines += [f"> 检索范围：{'、'.join(scope)}", ""]
    if meta.get("profile"):
        lines += [f"> 候选人背景：{meta['profile']}", ""]

    if stats["deduped"]:
        lines += [f"> 已自动去重 {stats['deduped']} 条重复岗位；过滤过期 {stats['expired']} 条。", ""]

    grouped = group_by_track(jobs)
    current_family = None
    section_no = 0
    for track, items in grouped.items():
        family = group_label(track)
        if family != current_family:
            section_no += 1
            cn = "一二三四五六七八九十"[section_no - 1] if section_no <= 10 else str(section_no)
            if current_family is not None:
                lines.append("")  # 表格与下一个标题之间留空行，否则部分 Markdown 解析器会吃掉标题
            lines += [f"## {cn}、{family}", "", "| 岗位名称 | 公司 | 城市 | 方向 | 投递截止 | 投递入口 |", "|---|---|---|---|---|---|"]
            current_family = family
        for job in items:
            entry = f"[投递页面]({job['url']})" if job["url"] else job["no_url_reason"] or "需自行搜索"
            lines.append(
                f"| {job['title']} | {job['company']} | {job['city']} | {track} | {job['deadline']} | {entry} |"
            )
    lines.append("")

    missing = meta.get("no_match_notes") or []
    if missing:
        lines += ["## 今日无匹配岗位", ""]
        lines += [f"- **{item['city']}**：{item['note']}" for item in missing]
        lines.append("")

    advice = meta.get("advice") or []
    if advice:
        lines += ["## 今日建议", ""]
        lines += [f"{i}. {text}" for i, text in enumerate(advice, 1)]
        lines.append("")

    return "\n".join(lines)


def render_html(jobs: list[dict], meta: dict, stats: dict) -> str:
    """邮件用 HTML：全用内联样式和 table 布局，Outlook 与手机邮箱都能正常显示。"""
    today = html.escape(meta.get("date") or dt.date.today().isoformat())
    parts = [
        '<div style="font-family:-apple-system,\'Segoe UI\',\'Microsoft YaHei\',sans-serif;'
        'max-width:860px;margin:0 auto;color:#1a1c20;font-size:14px;line-height:1.6">',
        f'<h1 style="font-size:20px;margin:0 0 4px">岗位日报 {today}</h1>',
        f'<p style="color:#6b7280;margin:0 0 16px">共 {len(jobs)} 条'
        + (f'，已去重 {stats["deduped"]} 条' if stats["deduped"] else "")
        + "</p>",
    ]

    if meta.get("scope"):
        parts.append(
            '<p style="color:#6b7280;margin:0 0 20px">检索范围：'
            + html.escape("、".join(meta["scope"]))
            + "</p>"
        )

    for track, items in group_by_track(jobs).items():
        parts.append(
            f'<h2 style="font-size:15px;margin:22px 0 8px;padding-bottom:6px;'
            f'border-bottom:1px solid #e5e7eb">{html.escape(track)}</h2>'
        )
        parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px">')
        parts.append(
            '<tr style="background:#f8f9fb;color:#4b5563;text-align:left">'
            '<th style="padding:8px 10px;border-bottom:1px solid #e5e7eb">岗位</th>'
            '<th style="padding:8px 10px;border-bottom:1px solid #e5e7eb">公司 / 城市</th>'
            '<th style="padding:8px 10px;border-bottom:1px solid #e5e7eb">截止</th>'
            '<th style="padding:8px 10px;border-bottom:1px solid #e5e7eb">入口</th></tr>'
        )
        for job in items:
            link = (
                f'<a href="{html.escape(job["url"])}" style="color:#1d4ed8;text-decoration:none">投递</a>'
                if job["url"]
                else html.escape(job["no_url_reason"] or "需自行搜索")
            )
            parts.append(
                '<tr>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #f1f2f5">{html.escape(job["title"])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #f1f2f5">'
                f'{html.escape(job["company"])}<br><span style="color:#9ca3af">{html.escape(job["city"])}</span></td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #f1f2f5">{html.escape(job["deadline"])}</td>'
                f'<td style="padding:8px 10px;border-bottom:1px solid #f1f2f5">{link}</td>'
                "</tr>"
            )
        parts.append("</table>")

    advice = meta.get("advice") or []
    if advice:
        parts.append('<h2 style="font-size:15px;margin:24px 0 8px">今日建议</h2><ol style="padding-left:20px">')
        parts += [f'<li style="margin-bottom:8px">{html.escape(text)}</li>' for text in advice]
        parts.append("</ol>")

    parts.append("</div>")
    return "\n".join(parts)


def build(input_path: str, out_dir: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    raw_jobs, meta = load_jobs(input_path)

    clean = []
    for i, job in enumerate(raw_jobs):
        clean.append(normalize(job, i))

    alive = [job for job in clean if not is_expired(job, today)]
    expired = len(clean) - len(alive)
    jobs, deduped = dedupe(alive)
    stats = {"deduped": deduped, "expired": expired, "total": len(jobs)}

    os.makedirs(out_dir, exist_ok=True)
    date_tag = meta.get("date") or today.isoformat()
    md_path = os.path.join(out_dir, f"{date_tag}.md")
    html_path = os.path.join(out_dir, f"{date_tag}.html")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(jobs, {**meta, "date": date_tag}, stats))
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(jobs, {**meta, "date": date_tag}, stats))

    return {"markdown": md_path, "html": html_path, **stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岗位日报生成器：JSON → Markdown + HTML")
    parser.add_argument("input", help="岗位清单 JSON")
    parser.add_argument("-o", "--out", default="out", help="输出目录，默认 out/")
    args = parser.parse_args(argv)

    try:
        result = build(args.input, args.out)
    except JobError as exc:
        print(f"数据有误：{exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"找不到文件：{args.input}", file=sys.stderr)
        return 1

    print(
        f"生成 {result['total']} 条岗位（去重 {result['deduped']}，过期 {result['expired']}）\n"
        f"  → {result['markdown']}\n  → {result['html']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
