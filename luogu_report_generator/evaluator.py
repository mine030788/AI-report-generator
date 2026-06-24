"""evaluator.py - 报告生成核心 (独立版)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

try:
    from reportlab.pdfbase import pdfmetrics  # noqa: E402
    from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False

import markdown as md  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402
from openai import OpenAI  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from .behavior_analyzer import (  # noqa: E402
    analyze_submission_behavior,
    compute_personality_scores,
    compute_six_dimension_scores,
    format_behavior_summary,
)
from .code_analyzer import analyze_code_style, format_code_analysis  # noqa: E402
from .submission_evolution import evolution_to_prompt_block  # noqa: E402
from .syllabus_matcher import (  # noqa: E402
    format_syllabus_report,
    load_syllabus_context,
)

_ROOT = Path(__file__).resolve().parent
_ASSETS_FONT_DIR = _ROOT.parent / "assets" / "fonts"

DEFAULT_REPORT_MD = "luogu_coach_report.md"
DEFAULT_REPORT_HTML = "luogu_coach_report.html"
DEFAULT_REPORT_PDF = "luogu_coach_report.pdf"
DEFAULT_ASSETS_DIR = "luogu_report_assets"

AI_GENERATION_MAX_RETRIES = int(os.environ.get("LRG_AI_MAX_RETRIES", "3"))
AI_GENERATION_RETRY_SLEEP_SECONDS = float(os.environ.get("LRG_AI_RETRY_SLEEP", "8"))

DIFFICULTY_NAME_MAP = {
    0: "暂无评定", 1: "入门", 2: "普及-", 3: "普及/提高-",
    4: "普及+/提高", 5: "提高+/省选-", 6: "省选/NOI-", 7: "NOI/NOI+/CTSC",
}

DIFFICULTY_COLOR_MAP = {
    0: "#9CA3AF", 1: "#FE4C61", 2: "#F39C12", 3: "#FFC116",
    4: "#52C41A", 5: "#3498DB", 6: "#9D4EDD", 7: "#0E1D69",
}

DIFFICULTY_TEXT_COLOR_MAP = {
    0: "#111827", 1: "#FFFFFF", 2: "#111827", 3: "#111827",
    4: "#FFFFFF", 5: "#FFFFFF", 6: "#FFFFFF", 7: "#FFFFFF",
}

TAG_CHART_PALETTE = [
    "#52C41A", "#3498DB", "#9D4EDD", "#FE4C61",
    "#F39C12", "#14B8A6", "#FFC116", "#0EA5E9",
]


_DIFF_TIER: dict[int, dict] = {
    0: dict(name="未知", fill="#9CA3AF", fg="#FFFFFF", bd="#6B7280"),
    1: dict(name="入门", fill="#F5222D", fg="#FFFFFF", bd="#A8071A"),
    2: dict(name="普及-", fill="#FA541C", fg="#FFFFFF", bd="#AD3811"),
    3: dict(name="普及/提高-", fill="#FAAD14", fg="#FFFFFF", bd="#AD8B14"),
    4: dict(name="提高+/提高", fill="#52C41A", fg="#FFFFFF", bd="#389E0D"),
    5: dict(name="提高+/省选-", fill="#1890FF", fg="#FFFFFF", bd="#096DD9"),
    6: dict(name="省选/NOI-", fill="#722ED1", fg="#FFFFFF", bd="#531DAB"),
    7: dict(name="NOI/NOI+/CTSC", fill="#2F54EB", fg="#FFFFFF", bd="#1D39C4"),
}

_MASTERY_RULES: list[tuple[str, str]] = [
    ("精通", "AC ≥ 20 道"),
    ("熟练", "10 ≤ AC ≤ 19"),
    ("入门", "3  ≤ AC ≤ 9"),
    ("初窥", "1  ≤ AC ≤ 2"),
    ("空白", "AC = 0（警示色：未接触该知识点）"),
]


def _escape_html(s: str) -> str:
    """极简 HTML 转义，避免 topic 名称里出现 < > & 时把外层表格/div 弄破。"""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

_MASTERY_VIS: dict[str, dict] = {
    "精通": dict(r=18, fs=12, fw=700),
    "熟练": dict(r=15, fs=11, fw=700),
    "入门": dict(r=12, fs=11, fw=600),
    "初窥": dict(r=9,  fs=10, fw=500),
    "空白": dict(r=7,  fs=9,  fw=400),
}

_MASTERY_COLOR: dict[str, dict] = {
    "精通": dict(fill="#14532D", fg="#FFFFFF", bd="#052E16"),  # 深绿近黑
    "熟练": dict(fill="#166534", fg="#FFFFFF", bd="#14532D"),  # 深绿
    "入门": dict(fill="#16A34A", fg="#FFFFFF", bd="#166534"),  # 标准绿
    "初窥": dict(fill="#86EFAC", fg="#064E3B", bd="#4ADE80"),  # 浅绿
    "空白": dict(fill="#FFFFFF", fg="#6B7280", bd="#9CA3AF"),  # 白底+灰边+灰字
}

_CATEGORY_KEYWORDS = (
    ("基础实现", ["模拟", "枚举", "排序", "高精度", "进制", "字符串基础", "递推", "分治", "构造"]),
    ("搜索/DFS", ["搜索", "dfs", "bfs", "回溯", "剪枝", "递归", "双向搜索", "启发式"]),
    ("动态规划", ["dp", "动态规划", "背包", "区间dp", "树形dp", "状压", "数位dp", "记忆化", "概率dp"]),
    ("贪心/二分", ["贪心", "二分", "倍增", "三分", "中位数"]),
    ("图论", ["图", "最短路", "dijkstra", "floyd", "spfa", "tarjan", "lca", "并查集", "网络流", "二分图", "匹配", "拓扑", "差分约束", "最小生成树", "mst", "基环树", "欧拉"]),
    ("数据结构", ["线段树", "树状数组", "堆", "单调栈", "单调队列", "平衡树", "st表", "treap", "splay", "红黑树", "字典树", "trie", "树链剖分", "树剖", "树分治", "cdq", "kdtree", "树套树", "跳表", "左偏树"]),
    ("字符串", ["kmp", "字符串", "hash", "sam", "后缀", "manacher", "ac自动机", "回文", "z函数", "最小表示"]),
    ("数学/数论", ["数学", "数论", "组合", "计数", "概率", "期望", "博弈", "矩阵", "高斯消元", "线性基", "生成函数", "多项式", "fft", "ntt", "中国剩余", "原根"]),
    ("计算几何", ["几何", "凸包", "旋转卡壳", "半平面交", "辛普森", "扫描线", "pick"]),
    ("其他", []),  # 兜底
)

_TRUSTED_BLOCK_RE = re.compile(
    r"(?ms)^##\s*数据校准与真实统计\s*\n.*?(?=^##\s*掌握度判定标准|\Z)"
)


def _try_download_lxgw_wenkai(dest_dir: Path) -> str | None:
    auto = os.environ.get("LRG_AUTO_FONT_DOWNLOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    if not auto:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    version = "1.520"
    zip_name = f"lxgw-wenkai-v{version}.zip"
    url = f"https://github.com/lxgw/LxgwWenKai/releases/download/v{version}/{zip_name}"
    expected_sha256 = "3a763543bec896e3c1badc9808bc804116a5e3d26f9f9592dacc834c9e799d8c"
    zip_path = dest_dir / zip_name
    extracted_font = dest_dir / "LXGWWenKai-Regular.ttf"
    if extracted_font.exists():
        return str(extracted_font)
    if zip_path.exists():
        try:
            h = hashlib.sha256()
            with open(zip_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest().lower() != expected_sha256:
                zip_path.unlink(missing_ok=True)
        except Exception:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
    if not zip_path.exists():
        tmp_path = dest_dir / (zip_name + ".tmp")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "luogu-report-generator/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp, open(tmp_path, "wb") as out:
                while True:
                    buf = resp.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)
            h = hashlib.sha256()
            with open(tmp_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest().lower() != expected_sha256:
                tmp_path.unlink(missing_ok=True)
                return None
            tmp_path.replace(zip_path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = f"lxgw-wenkai-v{version}/LXGWWenKai-Regular.ttf"
            if member not in zf.namelist():
                return None
            tmp_extract = dest_dir / (extracted_font.name + ".tmp")
            with zf.open(member) as src, open(tmp_extract, "wb") as dst:
                dst.write(src.read())
            tmp_extract.replace(extracted_font)
        return str(extracted_font) if extracted_font.exists() else None
    except Exception:
        return None


def find_chinese_font_path() -> str | None:
    env_font = os.environ.get("CHINESE_FONT_PATH") or os.environ.get("LRG_FONT_PATH")
    if env_font and os.path.exists(env_font):
        return env_font
    local_candidates: list[str] = []
    try:
        downloaded = _try_download_lxgw_wenkai(_ASSETS_FONT_DIR)
        if downloaded and os.path.exists(downloaded):
            return downloaded
        local_candidates.extend(
            [
                str(_ASSETS_FONT_DIR / "NotoSansCJKsc-Regular.otf"),
                str(_ASSETS_FONT_DIR / "NotoSansSC-Regular.otf"),
                str(_ASSETS_FONT_DIR / "SourceHanSansCN-Regular.otf"),
                str(_ASSETS_FONT_DIR / "wqy-zenhei.ttc"),
                str(_ASSETS_FONT_DIR / "LXGWWenKai-Regular.ttf"),
            ]
        )
    except Exception:
        pass
    candidates = [
        *local_candidates,
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\msyhbd.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simkai.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:
        preferred = [
            "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans CN",
            "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
            "Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS",
        ]
        for family in preferred:
            try:
                fp = font_manager.FontProperties(family=family)
                font_file = font_manager.findfont(fp, fallback_to_default=False)
                if font_file and os.path.exists(font_file):
                    return font_file
            except Exception:
                continue
    except Exception:
        pass
    try:
        keywords = (
            "notosanscjk", "notosanssc", "sourcehansans", "noto sans cjk",
            "noto sans sc", "wqy", "wenquanyi", "droidsansfallback",
            "arphic", "ukai", "uming", "simhei", "msyh", "yahei", "pingfang",
        )
        for fp in (font_manager.findSystemFonts(fontpaths=None, fontext="ttf")
                   + font_manager.findSystemFonts(fontpaths=None, fontext="ttc")
                   + font_manager.findSystemFonts(fontpaths=None, fontext="otf")):
            lower = fp.lower()
            if any(k in lower for k in keywords) and os.path.exists(fp):
                return fp
    except Exception:
        pass
    return None


def configure_matplotlib_font() -> str | None:
    font_path = find_chinese_font_path()
    family_fallback = [
        "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans CN",
        "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
        "Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS",
        "DejaVu Sans",
    ]
    if font_path:
        try:
            font_manager.fontManager.addfont(font_path)
            font_name = font_manager.FontProperties(fname=font_path).get_name()
            plt.rcParams["font.sans-serif"] = [font_name, *family_fallback]
        except Exception:
            plt.rcParams["font.sans-serif"] = family_fallback
    else:
        plt.rcParams["font.sans-serif"] = family_fallback
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 12
    plt.rcParams["axes.titlesize"] = 14
    plt.rcParams["axes.labelsize"] = 12
    plt.rcParams["xtick.labelsize"] = 11
    plt.rcParams["ytick.labelsize"] = 11
    return font_path


def register_pdf_font() -> str:
    if not _HAS_REPORTLAB:
        return "Helvetica"
    font_path = find_chinese_font_path()
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("CoachChinese", font_path))
            return "CoachChinese"
        except Exception:
            pass
    return "Helvetica"


def ensure_dir(path: str | Path) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _render_progress_bar(percentage: float, color: str, width_px: int = 150) -> str:
    pct = max(0.0, min(100.0, float(percentage)))
    return (
        f'<span style="display:inline-block;width:{width_px}px;height:12px;'
        'background:#E5E7EB;border-radius:9999px;overflow:hidden;vertical-align:middle;">'
        f'<span style="display:block;width:{pct:.1f}%;height:12px;background:{color};"></span>'
        "</span>"
    )


def get_difficulty_style(level: int) -> tuple[str, str, str]:
    return (
        DIFFICULTY_NAME_MAP.get(level, str(level)),
        DIFFICULTY_COLOR_MAP.get(level, "#4B5563"),
        DIFFICULTY_TEXT_COLOR_MAP.get(level, "#FFFFFF"),
    )


def summarize_average_difficulty(difficulty_histogram: dict) -> dict[str, Any]:
    total = 0
    weighted = 0
    for key, value in difficulty_histogram.items():
        if str(key).isdigit():
            level = int(key)
            if level <= 0:
                continue
            total += int(value)
            weighted += level * int(value)
    average_value = weighted / total if total else 0.0
    candidate_levels = [k for k in DIFFICULTY_NAME_MAP.keys() if int(k) > 0]
    nearest_level = min(candidate_levels, key=lambda lv: abs(lv - average_value)) if total and candidate_levels else 0
    label, color, text_color = get_difficulty_style(nearest_level)
    return {
        "average_value": average_value,
        "nearest_level": nearest_level,
        "label": label,
        "color": color,
        "text_color": text_color,
    }


def render_star_rating_html(stars: str) -> str:
    filled = stars.count("⭐")
    empty = stars.count("☆")
    total = filled + empty
    if total == 0 or total > 5:
        return stars
    star_items = []
    for ch in stars:
        if ch == "⭐":
            star_items.append('<span style="color:#F5C542;text-shadow:0 1px 0 rgba(0,0,0,0.18);">★</span>')
        elif ch == "☆":
            star_items.append('<span style="color:#94A3B8;">★</span>')
        else:
            star_items.append(ch)
    return (
        '<span style="display:inline-flex;align-items:center;gap:2px;'
        'padding:2px 8px;border-radius:9999px;background:#111827;'
        'border:1px solid #374151;box-shadow:inset 0 1px 0 rgba(255,255,255,0.06);'
        'font-size:1.02em;line-height:1.1;vertical-align:middle;">'
        + "".join(star_items)
        + f'<span style="margin-left:6px;color:#CBD5E1;font-size:12px;font-weight:700;">{filled}/{total}</span>'
        "</span>"
    )


def summarize_detail_fetch_stats(passed_items, failed_items) -> dict[str, Any]:
    items = list(passed_items or []) + list(failed_items or [])
    stats = {
        "total_items": len(items),
        "source_code_success": 0,
        "summary_only": 0,
        "detail_requested": 0,
        "detail_skipped": 0,
        "detail_errors": 0,
        "pure_error_records": 0,
        "blocker_reason": "",
    }
    for item in items:
        record = item.get("record")
        if not isinstance(record, dict):
            continue
        if record.get("_detail_requested"):
            stats["detail_requested"] += 1
        if record.get("sourceCode"):
            stats["source_code_success"] += 1
            continue
        if record.get("submitTime"):
            stats["summary_only"] += 1
        if record.get("_detail_skipped"):
            stats["detail_skipped"] += 1
            if not stats["blocker_reason"]:
                stats["blocker_reason"] = str(record.get("_detail_skipped") or "")
        if record.get("_detail_error"):
            stats["detail_errors"] += 1
            if not stats["blocker_reason"]:
                stats["blocker_reason"] = str(record.get("_detail_error") or "")
        if record.get("error") and not record.get("submitTime"):
            stats["pure_error_records"] += 1
            if not stats["blocker_reason"]:
                stats["blocker_reason"] = str(record.get("error") or "")
    return stats


def build_detail_fetch_overview(detail_fetch_stats: dict | None) -> dict[str, Any]:
    stats = detail_fetch_stats or {}
    total_items = int(stats.get("total_items", 0))
    source_code_success = int(stats.get("source_code_success", 0))
    summary_only = int(stats.get("summary_only", 0))
    detail_skipped = int(stats.get("detail_skipped", 0))
    pure_error_records = int(stats.get("pure_error_records", 0))
    blocker_reason = str(stats.get("blocker_reason") or "")
    has_partial = source_code_success > 0 and source_code_success < total_items
    has_full = source_code_success == total_items and total_items > 0
    has_zero = total_items == 0
    if has_full:
        status = "complete"
        status_text = "全部已抓取源码"
        message = f"全部 {total_items} 道题的源码均已抓取，可深度分析代码风格。"
    elif has_partial:
        status = "partial"
        status_text = "部分已抓取源码"
        message = f"已抓取 {source_code_success}/{total_items} 道题的源码，其余 {summary_only} 道仅记录概要。"
    elif has_zero:
        status = "empty"
        status_text = "暂无题目"
        message = "本次未抓取到任何题目。"
    else:
        status = "summary_only"
        status_text = "仅记录概要"
        message = f"所有 {total_items} 道题均未抓到源码，仅有概要数据。"
    if blocker_reason and status in ("partial", "summary_only"):
        message += f"（受限原因：{blocker_reason}）"

    # 模板展示用的颜色 / 标签 (cover 页用)
    _status_display = {
        "complete":   ("#16A34A", "#FFFFFF", status_text),  # 绿底白字
        "partial":    ("#F59E0B", "#FFFFFF", status_text),  # 橙底白字
        "summary_only": ("#F59E0B", "#FFFFFF", status_text),
        "empty":      ("#9CA3AF", "#FFFFFF", status_text),  # 灰底白字
    }
    bg, fg, label = _status_display.get(status, ("#9CA3AF", "#FFFFFF", status_text))

    return {
        "status": status, "status_text": status_text, "message": message,
        "total_items": total_items, "source_code_success": source_code_success,
        "summary_only": summary_only, "detail_skipped": detail_skipped,
        "pure_error_records": pure_error_records, "blocker_reason": blocker_reason,
        # 兼容模板里 df.status_bg / df.status_fg / df.status_label
        "status_bg": bg, "status_fg": fg, "status_label": label,
    }



def split_practice_problems(practice) -> tuple[list[pyLuogu.ProblemSummary], list[pyLuogu.ProblemSummary]]:
    practice_problems = list(getattr(practice, "problems", []) or [])
    if practice_problems:
        passed = [p for p in practice_problems if getattr(p, "accepted", False)]
        failed = [p for p in practice_problems if getattr(p, "submitted", False) and not getattr(p, "accepted", False)]
        if passed or failed:
            return passed, failed

    raw = practice.data if isinstance(getattr(practice, "data", None), dict) else None
    passed: list[pyLuogu.ProblemSummary] = []
    failed: list[pyLuogu.ProblemSummary] = []
    passed_ids: set[str] = set()

    for key, target, accepted in (("passed", passed, True), ("submitted", failed, False), ("failed", failed, False)):
        items = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            pid = item.get("pid")
            if not pid:
                continue
            pid = str(pid)
            if accepted:
                passed_ids.add(pid)
            elif pid in passed_ids:
                continue
            target.append(
                pyLuogu.ProblemSummary(
                    {
                        "pid": pid,
                        "title": item.get("title") or item.get("name") or "",
                        "difficulty": item.get("difficulty"),
                        "type": item.get("type"),
                        "submitted": True,
                        "accepted": accepted,
                        "tags": item.get("tags") or [],
                        "totalSubmit": item.get("totalSubmit"),
                        "totalAccepted": item.get("totalAccepted"),
                        "flag": item.get("flag"),
                        "fullScore": item.get("fullScore"),
                    }
                )
            )
    return passed, failed


def collect_record_dicts(items: list[dict]) -> list[dict]:
    records: list[dict] = []
    for item in items:
        record = item.get("record")
        if isinstance(record, dict) and record.get("submitTime"):
            records.append(record)
    return records


def describe_behavior_fetch_error(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "未登录或 Cookies 已失效，无法读取提交记录列表"
    if isinstance(exc, ForbiddenError):
        return f"无权访问提交记录列表：{exc}"
    if isinstance(exc, RequestError):
        if getattr(exc, "status_code", None) == 429:
            return "请求提交记录过于频繁，请稍后重试"
        return f"请求提交记录失败：{exc}"
    message = str(exc).strip()
    if message:
        return message
    return "未获取到有效提交记录"


def enrich_problem_tags(
    luogu: pyLuogu.luoguAPI,
    problems: list[pyLuogu.ProblemSummary],
    *,
    max_fetch: int | None = None,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> int:
    """
    为缺失 tags 的题目按需补全标签。
    优先使用 practice.problems 自带标签；只有为空时才走 problem_detail 兜底。
    返回本次成功补全的题目数量。

    progress_callback(fetched, enriched, total_missing) 在每道题处理完后调用，
    用于向前端实时反馈标签抓取进度；传 None 则不回调。
    """
    enriched = 0
    fetched = 0
    cache: dict[str, list[int]] = {}

    # 先一次性统计需要补全的题目总数，方便前端显示 "X/Y" 进度
    missing_indices = [
        i for i, p in enumerate(problems)
        if not list(getattr(p, "tags", []) or [])
    ]
    total_missing = len(missing_indices)
    if progress_callback is not None:
        try:
            progress_callback(0, 0, total_missing)
        except Exception:
            pass

    for idx, problem in enumerate(problems):
        existing_tags = list(getattr(problem, "tags", []) or [])
        if existing_tags:
            continue
        if max_fetch is not None and fetched >= max_fetch:
            break

        pid = str(getattr(problem, "pid", "") or "")
        if not pid:
            continue

        try:
            if pid not in cache:
                fetched += 1
                detail = luogu.get_problem(pid)
                problem_detail = getattr(detail, "problem", None)
                cache[pid] = list(getattr(problem_detail, "tags", []) or [])
            if cache[pid]:
                problem.tags = list(cache[pid])
                enriched += 1
        except Exception:
            continue

        if progress_callback is not None:
            try:
                progress_callback(fetched, enriched, total_missing)
            except Exception:
                pass

    return enriched


def fetch_behavior_analysis(luogu: pyLuogu.luoguAPI, uid: int, fallback_items: list[dict] | None = None) -> dict:
    from behavior_analyzer import analyze_submission_behavior

    raw_records: list[dict] = []
    last_error = None
    for page in range(1, 26):
        try:
            record_list = luogu.get_record_list(page=page, uid=uid, user=str(uid))
            page_records = getattr(record_list, "records", None) or getattr(record_list, "data", None) or []
            normalized_records = [
                rec.to_json() if hasattr(rec, "to_json") else rec
                for rec in page_records
            ]
        except Exception as e:
            last_error = describe_behavior_fetch_error(e)
            break

        if not normalized_records:
            break
        raw_records.extend(normalized_records)
        if len(normalized_records) < 20 or len(raw_records) >= 1000:
            break

    if raw_records:
        behavior = analyze_submission_behavior(raw_records)
        behavior["_source"] = "record_list"
        if last_error:
            behavior["_warning"] = last_error
        return behavior

    fallback_records = collect_record_dicts(fallback_items or [])
    if fallback_records:
        behavior = analyze_submission_behavior(fallback_records)
        behavior["_source"] = "record_detail_fallback"
        if last_error:
            behavior["_warning"] = last_error
        return behavior

    return {"error": last_error or "未获取到有效提交记录"}


def _collect_records_from_items(passed_items, failed_items) -> list[dict]:
    out: list[dict] = []
    for item in list(passed_items or []) + list(failed_items or []):
        r = item.get("record") if isinstance(item, dict) else None
        if isinstance(r, dict) and r.get("submitTime"):
            out.append(r)
    return out


def repair_behavior_analysis_from_items(export_data: dict) -> dict:
    behavior = export_data.get("behavior_analysis", {}) or {}
    if behavior and "error" not in behavior and behavior.get("personality_scores"):
        return behavior
    fallback_records = _collect_records_from_items(
        export_data.get("passed_items"), export_data.get("failed_items")
    )
    if not fallback_records:
        return behavior or {"error": "未获取到有效提交记录"}
    repaired = analyze_submission_behavior(fallback_records)
    repaired["_source"] = "record_detail_fallback_repaired"
    if behavior.get("_warning"):
        repaired["_warning"] = str(behavior["_warning"])
    elif behavior.get("error"):
        repaired["_warning"] = str(behavior["error"])
    export_data["behavior_analysis"] = repaired
    return repaired


def compute_ability_scores(export_data: dict) -> dict[str, int]:
    summary = export_data.get("summary", {}) or {}
    top_tags = summary.get("top_algorithm_tags", []) or summary.get("top_tags", []) or []
    difficulty_histogram = summary.get("difficulty_histogram", {}) or {}
    solved_count = int(export_data.get("solved_count", 0))
    failed_count = int(export_data.get("failed_count", 0))
    keyword_map = {
        "基础实现": [],
        "搜索 / DFS": ["dfs", "搜索", "回溯", "枚举", "树遍历"],
        "动态规划": ["dp", "背包", "区间", "树形", "状压"],
        "图论": ["图", "tarjan", "lca", "最短路", "并查集", "网络流", "匹配",
                 "图遍历", "树的遍历", "树的直径", "树的重心", "基环树"],
        "数据结构": ["线段树", "树状数组", "bit", "堆", "单调", "平衡树", "st表", "数据结构"],
        "字符串 / 数学": ["字符串", "kmp", "hash", "trie", "sam", "数论", "数学",
                          "组合", "计数", "贪心", "构造", "证明"],
    }
    difficulty_total = 0
    weighted = 0
    for key, value in difficulty_histogram.items():
        if str(key).isdigit():
            difficulty_total += int(value)
            weighted += int(key) * int(value)
    avg_difficulty = weighted / difficulty_total if difficulty_total else 0
    scores: dict[str, int] = {}
    for ability, keywords in keyword_map.items():
        score = 35 + min(20, solved_count * 2) - min(12, failed_count * 2)
        if ability == "基础实现":
            score = 48 + min(28, solved_count * 2) + int(avg_difficulty * 4)
        for item in top_tags:
            tag_name = str(item.get("name") or "").lower()
            if any(kw in tag_name for kw in keywords):
                score += int(item.get("count", 0)) * 2
                if ability == "基础实现":
                    score += int(item.get("count", 0))
        scores[ability] = max(20, min(95, score))
    behavior = export_data.get("behavior_analysis", {}) or {}
    ac_rate = behavior.get("ac_rate")
    first_try_rate = behavior.get("first_try_ac_rate")
    if ac_rate is not None and first_try_rate is not None:
        adj = int((ac_rate + first_try_rate - 1.0) * 10)
        for k in list(scores.keys()):
            scores[k] = max(20, min(95, scores[k] + adj))
    return scores


# ═══════════════════════════════════════════════════════════════════════
#  图表生成
# ═══════════════════════════════════════════════════════════════════════

def generate_chart_images(export_data: dict, output_dir: str) -> dict[str, str]:
    ensure_dir(output_dir)
    plt.style.use("default")
    configure_matplotlib_font()
    repair_behavior_analysis_from_items(export_data)

    chart_paths: dict[str, str] = {}
    summary = export_data.get("summary", {}) or {}
    difficulty_histogram = summary.get("difficulty_histogram", {}) or {}
    top_tags = summary.get("top_algorithm_tags", []) or summary.get("top_tags", []) or []
    solved_count = int(export_data.get("solved_count", 0))
    failed_count = int(export_data.get("failed_count", 0))

    def _get_hist_count(key):
        if key in difficulty_histogram:
            return int(difficulty_histogram[key])
        return int(difficulty_histogram.get(str(key), 0))

    numeric_levels: list[int] = []
    other_keys: list[str] = []
    for k in difficulty_histogram.keys():
        ks = str(k)
        if ks.isdigit():
            lv = int(ks)
            if lv > 0:
                numeric_levels.append(lv)
        else:
            other_keys.append(ks)
    numeric_levels = sorted(set(numeric_levels))
    other_keys = sorted(set(other_keys))

    # 1) 难度直方图
    if numeric_levels or other_keys:
        labels, values, colors = [], [], []
        for lv in numeric_levels:
            name, color, _ = get_difficulty_style(lv)
            labels.append(name)
            values.append(_get_hist_count(lv))
            colors.append(color)
        for k in other_keys:
            labels.append(k)
            values.append(_get_hist_count(k))
            colors.append("#4C78A8")

        fig, ax = plt.subplots(figsize=(8.6, 5.0), facecolor="#FFFFFF")
        x = list(range(len(labels)))
        bars = ax.bar(x, values, color=colors, width=0.68, edgecolor="none")
        ax.set_title("题目难度分布（按洛谷难度等级）")
        ax.set_xlabel("难度")
        ax.set_ylabel("题目数量")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=12)
        ax.yaxis.grid(True, linestyle="--", linewidth=0.8, color="#E5E7EB")
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#CBD5E1")
        ax.spines["bottom"].set_color("#CBD5E1")
        max_value = max(values) if values else 0
        total_count = sum(values)
        for idx, (bar, value) in enumerate(zip(bars, values)):
            pct = (value / total_count * 100) if total_count else 0
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + max(max_value * 0.03, 0.12),
                f"{value} 题\n{pct:.1f}%",
                ha="center", va="bottom", fontsize=11,
                color=colors[idx], fontweight="bold",
            )
        fig.tight_layout()
        difficulty_path = os.path.join(output_dir, "difficulty_histogram.png")
        fig.savefig(difficulty_path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        chart_paths["difficulty"] = difficulty_path

    # 2) 通过 / 未通过 饼图
    fig, ax = plt.subplots(figsize=(6.4, 4.4), facecolor="#FFFFFF")
    counts = [solved_count, failed_count]
    pie_labels = ["已通过", "未通过"]
    pie_colors = ["#52C41A", "#FE4C61"]
    if sum(counts) == 0:
        counts = [1]
        pie_labels = ["暂无数据"]
        pie_colors = ["#BAB0AC"]
    ax.pie(
        counts, labels=pie_labels, autopct="%1.0f%%", startangle=90,
        colors=pie_colors, wedgeprops={"width": 0.45, "edgecolor": "#FFFFFF"},
        textprops={"fontsize": 12},
    )
    ax.set_title("通过 / 未通过占比")
    fig.tight_layout()
    status_path = os.path.join(output_dir, "status_ratio.png")
    fig.savefig(status_path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    chart_paths["status"] = status_path

    # 3) Top 8 标签
    selected_tags = top_tags[:8]
    if selected_tags:
        fig, ax = plt.subplots(figsize=(8.4, 5.0), facecolor="#FFFFFF")
        tag_names = [str(it.get("name") or it.get("id")) for it in selected_tags][::-1]
        tag_counts = [int(it.get("count", 0)) for it in selected_tags][::-1]
        tag_colors = [TAG_CHART_PALETTE[i % len(TAG_CHART_PALETTE)] for i in range(len(tag_names))]
        bars = ax.barh(tag_names, tag_counts, color=tag_colors, edgecolor="none")
        ax.set_title("高频算法标签 Top 8")
        ax.set_xlabel("出现次数")
        ax.xaxis.grid(True, linestyle="--", linewidth=0.8, color="#E5E7EB")
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#CBD5E1")
        ax.spines["bottom"].set_color("#CBD5E1")
        for idx, (bar, value) in enumerate(zip(bars, tag_counts)):
            ax.text(value + 0.1, idx, str(value), va="center", fontsize=11, color=tag_colors[idx], fontweight="bold")
        fig.tight_layout()
        tags_path = os.path.join(output_dir, "top_tags.png")
        fig.savefig(tags_path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        chart_paths["tags"] = tags_path

    # 4) 能力雷达
    ability_scores = compute_ability_scores(export_data)
    radar_labels = list(ability_scores.keys())
    radar_values = [ability_scores[k] for k in radar_labels]
    if radar_labels:
        angles = [n / float(len(radar_labels)) * 2 * math.pi for n in range(len(radar_labels))]
        angles += angles[:1]
        plot_values = radar_values + radar_values[:1]
        fig = plt.figure(figsize=(6.6, 6.2))
        ax = plt.subplot(111, polar=True)
        ax.set_theta_offset(math.pi / 2)
        ax.set_theta_direction(-1)
        ax.set_thetagrids([a * 180 / math.pi for a in angles[:-1]], radar_labels, fontsize=11)
        ax.set_ylim(0, 100)
        zone_colors = [
            (0, 40, "#FDECEC"), (40, 65, "#FFF3E0"),
            (65, 85, "#E8F4FF"), (85, 100, "#E7F6EC"),
        ]
        zone_angles = [n / 180.0 * math.pi for n in range(361)]
        for s, e, c in zone_colors:
            ax.fill_between(zone_angles, s, e, color=c, alpha=0.35)
        ax.plot(angles, plot_values, color="#4C78A8", linewidth=2)
        ax.fill(angles, plot_values, color="#4C78A8", alpha=0.25)
        ax.set_rgrids([20, 40, 60, 80, 100], angle=90, fontsize=10, color="#8A96A3")
        ax.set_title("能力雷达图", pad=18)
        radar_path = os.path.join(output_dir, "ability_radar.png")
        fig.savefig(radar_path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        chart_paths["radar"] = radar_path

    # 5) 性格雷达
    behavior_data = export_data.get("behavior_analysis", {}) or {}
    personality_scores = behavior_data.get("personality_scores", {})
    if personality_scores:
        p_labels = list(personality_scores.keys())
        p_values = [personality_scores[k] for k in p_labels]
        angles = [n / float(len(p_labels)) * 2 * math.pi for n in range(len(p_labels))]
        angles += angles[:1]
        p_plot_values = p_values + p_values[:1]
        fig = plt.figure(figsize=(6.6, 6.2))
        ax = plt.subplot(111, polar=True)
        ax.set_theta_offset(math.pi / 2)
        ax.set_theta_direction(-1)
        ax.set_thetagrids([a * 180 / math.pi for a in angles[:-1]], p_labels, fontsize=12)
        ax.set_ylim(0, 100)
        zone_colors = [
            (0, 40, "#F3F4F6"), (40, 60, "#E5E7EB"),
            (60, 80, "#FEF3C7"), (80, 100, "#FEF08A"),
        ]
        zone_angles = [n / 180.0 * math.pi for n in range(361)]
        for s, e, c in zone_colors:
            ax.fill_between(zone_angles, s, e, color=c, alpha=0.35)
        ax.plot(angles, p_plot_values, color="#D97706", linewidth=2.5)
        ax.fill(angles, p_plot_values, color="#F59E0B", alpha=0.3)
        ax.set_rgrids([20, 40, 60, 80, 100], angle=90, fontsize=10, color="#9CA3AF")
        ax.set_title("性格特质雷达图", pad=18, fontsize=12, fontweight="bold", color="#92400E")
        p_radar_path = os.path.join(output_dir, "personality_radar.png")
        fig.savefig(p_radar_path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        chart_paths["personality_radar"] = p_radar_path

    # 6) 首次 AC 提交次数分布
    ac_submit_distribution = behavior_data.get("ac_submit_distribution", {}) or {}
    if ac_submit_distribution:
        keys_int: list[int] = []
        for k in ac_submit_distribution.keys():
            try:
                keys_int.append(int(k))
            except ValueError:
                pass
        keys_int.sort()

        def _dist_get(mapping, key):
            if key in mapping:
                return int(mapping[key])
            return int(mapping.get(str(key), 0))

        labels, values = [], []
        count_10_plus = 0
        total_ac = sum(int(v) for v in ac_submit_distribution.values())
        for k in keys_int:
            if k >= 10:
                count_10_plus += _dist_get(ac_submit_distribution, k)
            else:
                labels.append(str(k))
                values.append(_dist_get(ac_submit_distribution, k))
        if count_10_plus > 0:
            labels.append("10+")
            values.append(count_10_plus)
        if labels:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            colors = ["#2563EB" if l == "1" else "#93C5FD" for l in labels]
            bars = ax.bar(labels, values, color=colors, edgecolor="none")
            ax.set_title("首次 AC 提交次数分布", fontsize=12, fontweight="bold")
            ax.set_xlabel("AC 所需提交次数")
            ax.set_ylabel("题目数")
            for bar, value in zip(bars, values):
                percentage = (value / total_ac * 100) if total_ac > 0 else 0
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width() / 2., height + 0.5,
                        f"{value}\n({percentage:.0f}%)",
                        ha="center", va="bottom", fontsize=10)
            fig.tight_layout()
            ac_path = os.path.join(output_dir, "ac_submit_distribution.png")
            fig.savefig(ac_path, dpi=180, bbox_inches="tight")
            plt.close(fig)
            chart_paths["ac_distribution"] = ac_path

    return chart_paths


# ═══════════════════════════════════════════════════════════════════════
#  可信数据摘要
# ═══════════════════════════════════════════════════════════════════════

def build_trusted_data_summary_md(export_data: dict) -> str:
    student_info = export_data.get("student_info", {}) or {}
    eval_time = str(student_info.get("eval_time") or "")
    summary = export_data.get("summary", {}) or {}
    difficulty_histogram = summary.get("difficulty_histogram", {}) or {}
    level_experience = summary.get("level_experience", {}) or {}
    detail_fetch_stats = export_data.get("detail_fetch_stats", {}) or {}
    syllabus_eval = export_data.get("syllabus_evaluation", {}) or {}

    total = 0
    for level in range(1, 8):
        total += int(difficulty_histogram.get(str(level), difficulty_histogram.get(level, 0)))
    total = total or 1
    lines = [
        "## 数据校准与真实统计",
        f"- 报告生成时间：{eval_time or '未知'}",
    ]
    lines.extend([
        "",
        "### 难度分布（程序生成）",
        '<table><thead><tr><th>洛谷难度</th><th>题数</th><th>占比</th><th>分布图</th></tr></thead><tbody>',
    ])

    for level in range(1, 8):
        count = int(difficulty_histogram.get(str(level), difficulty_histogram.get(level, 0)))
        name = DIFFICULTY_NAME_MAP[level]
        color = DIFFICULTY_COLOR_MAP[level]
        pct = count * 100 / total
        badge = (
            f'<span style="display:inline-block;padding:2px 10px;border-radius:6px;'
            f'background:{color};color:#fff;font-weight:600;">{name}</span>'
        )
        lines.append(
            "<tr>"
            f"<td>{badge}</td>"
            f"<td>{count}</td>"
            f"<td>{pct:.1f}%</td>"
            f"<td>{_render_progress_bar(pct, color)} <span style=\"margin-left:8px;\">{pct:.1f}%</span></td>"
            "</tr>"
        )
    lines.extend([
        "</tbody></table>",
    ])
    lines.extend(
        [
            "",
            "### 知识点覆盖统计表（按算法标签）",
            '<table><thead><tr><th>级别</th><th>已覆盖/总数</th><th>覆盖率</th><th>掌握度分布</th></tr></thead><tbody>',
        ]
    )

    for key, label in (
        ("csp_j", "入门级（CSP-J）"),
        ("csp_s", "提高级（CSP-S）"),
        ("provincial", "省选级"),
        ("noi", "NOI级"),
    ):
        group = syllabus_eval.get(key, {}) or {}
        stats = group.get("stats", {}) or {}
        detail_list = group.get("details", []) or []
        total_topics = int(stats.get("total", 0))
        covered = total_topics - int(stats.get("空白", 0))
        coverage = group.get("coverage", 0)
        # "掌握度分布"列：本意是展示这一级别分组下，**所有**知识点 topic
        # 按掌握度（精通/熟练/入门/初窥/空白）的分布，颜色用绿色深浅，与知识树果子一致。
        # 注意：与"已覆盖/总数"列的对应关系是——
        #   精通 + 熟练 + 入门 + 初窥 = "已覆盖"（AC ≥ 1）
        #   空白 = "未覆盖"（AC = 0）
        #   总数 = 上述 5 档合计
        m1 = m2 = m3 = m4 = m5 = 0
        # 同时按档位收集 topic 名称（用于在徽章下方展开列表，照搬 oi.aijiangti.cn 原版）
        topics_by_level: dict[str, list[str]] = {
            "精通": [], "熟练": [], "入门": [], "初窥": [], "空白": [],
        }
        for item in detail_list:
            if not isinstance(item, dict):
                continue
            topic_name = (item.get("topic") or "").strip()
            if not topic_name:
                continue
            ac = int(item.get("ac_count", 0) or 0)
            level = _level_for_ac(ac)
            if level == "精通":
                m1 += 1
                topics_by_level["精通"].append(topic_name)
            elif level == "熟练":
                m2 += 1
                topics_by_level["熟练"].append(topic_name)
            elif level == "入门":
                m3 += 1
                topics_by_level["入门"].append(topic_name)
            elif level == "初窥":
                m4 += 1
                topics_by_level["初窥"].append(topic_name)
            else:
                m5 += 1
                topics_by_level["空白"].append(topic_name)

        # 渲染「掌握度分布」单元：5 个并排小列，每列内是
        #   ① 顶部彩色徽章（精通/熟练/入门/初窥/空白 + 数量）
        #   ② 该档位下所有 topic 名称列表（顿号分隔）
        # 整体用 flex 横向铺开；与原版 oi.aijiangti.cn 报告视觉一致
        def _level_col(level_name: str, n: int, topics: list[str]) -> str:
            c = _MASTERY_COLOR[level_name]
            border = f"border:1px solid {c['bd']};" if c.get("bd") else ""
            chip = (
                f'<span style="display:block;text-align:center;padding:2px 4px;'
                f'border-radius:4px;background:{c["fill"]};color:{c["fg"]};'
                f'{border}font-size:11px;font-weight:700;line-height:1.3;">'
                f'{level_name} <span style="opacity:0.85;">{n}项</span></span>'
            )
            if topics:
                items_html = "、".join(_escape_html(t) for t in topics)
                body = (
                    f'<div style="margin-top:3px;padding:2px 3px;'
                    f'font-size:9.5px;line-height:1.35;color:#1F2937;'
                    f'word-break:break-all;">{items_html}</div>'
                )
            else:
                body = (
                    f'<div style="margin-top:3px;padding:2px 3px;'
                    f'font-size:9.5px;line-height:1.35;color:#9CA3AF;'
                    f'font-style:italic;">—</div>'
                )
            return f'<div style="flex:1;min-width:0;">{chip}{body}</div>'

        details_cell = (
            '<div style="display:flex;gap:4px;align-items:flex-start;">'
            + _level_col("精通", m1, topics_by_level["精通"])
            + _level_col("熟练", m2, topics_by_level["熟练"])
            + _level_col("入门", m3, topics_by_level["入门"])
            + _level_col("初窥", m4, topics_by_level["初窥"])
            + _level_col("空白", m5, topics_by_level["空白"])
            + '</div>'
        )
        lines.append(
            f"<tr>"
            f"<td><strong>{label.split('（')[0].replace('级','')}</strong></td>"
            f"<td>{covered}/{total_topics}</td>"
            f"<td>{coverage}%</td>"
            f"<td>{details_cell}</td>"
            f"</tr>"
        )

    lines.extend(
        [
            "</tbody></table>",
            "",
            "- 口径说明：",
            "  - 行 = 级别（入门/提高/省选/NOI），列 = 已覆盖/总数、覆盖率、**掌握度分布**。",
            "  - **掌握度分布**展示该级别下所有知识点 topic 按掌握度 5 档（精通/熟练/入门/初窥/空白）的分布，颜色用绿色深浅：精通近黑→熟练深绿→入门标准绿→初窥浅绿→空白白。",
            "  - 与前一列的对应：精通 + 熟练 + 入门 + 初窥 = “已覆盖”（AC ≥ 1）；空白 = “未覆盖”（AC = 0）；5 档合计 = “总数”。",
            "- 备注：本表只根据题目的算法标签评估知识点覆盖，表示“接触过”，不等于“熟练掌握”。",
        ]
    )

    # ------------------------------------------------------------------
    # 掌握度判定标准小节（独立 H2）
    # 重要：必须用 H2 而非 H3。normalize_report_markdown 会用
    # "^## 知识点覆盖统计表（按算法标签）..." 整块吞掉 AI 重复生成的统计表，
    # "掌握度判定标准"作为同级 H2 不会被吞，会原样保留。
    # ------------------------------------------------------------------
    def _legend_chip(c: dict, name: str) -> str:
        """无数字的纯色块图例（用于判定标准表的"颜色图例"列）。"""
        border = f"border:1px solid {c.get('bd','')};" if c.get('bd') else ""
        return (
            f'<span style="display:inline-block;padding:2px 12px;'
            f'border-radius:6px;background:{c["fill"]};color:{c["fg"]};'
            f'{border}font-size:12px;font-weight:600;">{name}</span>'
        )

    lines.append("")
    lines.append("## 掌握度判定标准（5 档）")
    lines.append(
        '<table><thead><tr>'
        '<th>掌握度</th><th>判定标准（AC 题目数）</th><th>颜色图例</th>'
        '</tr></thead><tbody>'
    )
    for name, rule in _MASTERY_RULES:
        chip = _legend_chip(_MASTERY_COLOR[name], name)
        lines.append(
            f'<tr><td><strong>{name}</strong></td><td>{rule}</td><td>{chip}</td></tr>'
        )
    lines.append("</tbody></table>")
    lines.append(
        "- 口径说明：5 档阈值是『知识点覆盖统计表』中『掌握度分布』列的统一判定标准；"
        "AC = 实际通过的题目数（去重）；『空白』档使用灰色警示色，提示该知识点未接触。"
    )

    # 知识树图谱（SVG：完全照搬 oi.aijiangti.cn 原版 report.html 的展示形式）
    # 关键修复（v3.9.51）：原版用 SVG 树，所有知识点一视同仁平铺展示。
    # v3.9.50 之前用 MAX_FRUITS=6 截断会被 "+N" 截掉分支末端，违反用户"不截断"
    # 要求。改用 _build_one_tree_svg 的 auto-scale 模式：计算每棵最长的分支
    # 果子数 → 反比缩放果子半径、间距、字号、树干粗细，让所有知识点
    # 都能塞进 460 宽的容器，绝不截断，绝不"+N"。
    lines.append("")
    lines.append('<div style="page-break-before:always;margin-top:24px;">')
    lines.append('<h2 style="font-size:1.45rem;font-weight:700;color:#065F46;border-bottom:3px solid #10B981;padding-bottom:8px;margin:18px 0 12px 0;">🌳 知识树图谱（按算法标签 · 掌握度可视化）</h2>')
    lines.append("")
    lines.append('<p style="color:#6B7280;font-size:14px;margin:6px 0 14px 0;">下面按 4 个竞赛级别（CSP-J / CSP-S / 省选 / NOI）分别画 4 棵"知识树"（<b>2×2 网格</b>）。每棵树上 <b>主干</b> = 竞赛级别，<b>分支</b> = 算法分类，<b>果子</b> = 知识点。果子半径 + 颜色（绿色深浅）= 掌握度（精通=大且近黑，空白=小且灰）。<b>所有知识点一视同仁展示，绝不截断</b>——某分支果子过多时，树会自动等比例缩小，确保全部展示。</p>')
    lines.append(build_knowledge_tree_html(syllabus_eval))
    lines.append('</div>')
    return "\n".join(lines)



def _classify_topic(topic: str) -> str:
    """把一个知识点名归类到上面的 9 个分类中。"""
    t = str(topic or "").lower()
    for cat, kws in _CATEGORY_KEYWORDS:
        for kw in kws:
            if kw and kw.lower() in t:
                return cat
    return "其他"


def _level_for_ac(ac_count: int) -> str:
    # ⚠️ 阈值与 _MASTERY_RULES 强绑定，修改时务必同步更新二者
    # （"掌握度判定标准"小节和"知识点覆盖统计表-掌握度分布"列都基于此）。
    if ac_count >= 20:
        return "精通"
    if ac_count >= 10:
        return "熟练"
    if ac_count >= 3:
        return "入门"
    if ac_count >= 1:
        return "初窥"
    return "空白"


def _build_one_tree_svg(
    icon: str,
    title: str,
    cat_topics: list,
    *,
    width: int = 460,
) -> str:
    """把一个竞赛级别画成一棵"真正的树"（SVG：树干 + 树枝 + 果子）。

    Parameters
    ----------
    icon : str
        级别前的 emoji（🌱/🌿/🌳/🏆）
    title : str
        级别名（CSP-J 入门 / CSP-S 提高 / 省选级 / NOI 级）
    cat_topics : list
        已按"掌握度从高到低"排好序的 [(cat, [(topic, ac, level), ...]), ...]
        排在最上面的是掌握度最高的分类。
    width : int
        SVG 宽度（px）。树高根据分类数自适应。v3.6 缩小到 460（之前 680），
        配合 2×2 网格让 4 棵树一页并排展示。

    设计
    ----
    - 中央树干（SVG 正中，棕色三层叠加 + 顶部 5 簇绿叶）
    - 主分支从中央向左右两侧**扇形展开**（奇偶交替），避免全在一侧像耙子
    - 长度因子：上层分支短、下层分支长，整体呈下宽上窄的圆锥轮廓
    - 树根处一条棕色虚线 + 草尖，模拟"地面"
    - 每条主分支 = 一个算法分类，Q 二次贝塞尔曲线，弯曲更明显
    - 分支上点缀几片小绿叶（装饰用）
    - 分支末端挂一排"果子" = 知识点
        - 半径 r 越大 → 掌握越好（6px 空白 → 18px 精通）
        - 颜色越深（灰 → 浅蓝 → 浅绿 → 中绿 → 深绿）→ 掌握越好
        - 果子中心写 AC 数（半径够大才写，避免溢出）
        - 果子下方写知识点名（>4 字拆两行）
    - 分类小帽（深色药丸）挂在分支根处上沿，靠树干一侧
    - 树干不透明度最低；果子最显眼
    """
    if not cat_topics:
        return (
            '<div style="color:#9CA3AF;text-align:center;'
            'padding:30px 0;font-size:12px;">（该级别暂无知识点数据）</div>'
        )

    # === 自动等比例缩放（关键修复 v3.9.51） ===
    # 找到所有分支中果子最多的那一支 → 反比缩放整个树
    # 这样即使某分支有 8 / 10 / 12 个果子，也全部塞得下，绝不截断
    max_fruits = max((len(topics) for _, topics in cat_topics), default=0)
    if max_fruits <= 0:
        return (
            '<div style="color:#9CA3AF;text-align:center;'
            'padding:30px 0;font-size:12px;">（该级别暂无知识点）</div>'
        )

    # 基础参数（果子 ≤ BASE_FRUITS 时用 1.0 缩放）
    BASE_FRUITS = 5          # <=5 果用 1.0 缩放
    MIN_SCALE = 0.40         # 缩放下限（果子小到 r=3 仍可见）
    raw_scale = BASE_FRUITS / max(max_fruits, 1)
    scale = max(MIN_SCALE, raw_scale)

    # 布局常量（按 scale 缩放，让任何分支都塞得下所有果子）
    HEADER_H = 24            # 顶部留白
    # 果子小时 BRANCH_H 要增加，给两行标签（5+ 字知识点）留足垂直空间
    BRANCH_H = int(60 * (0.85 + 0.30 * scale))  # scale=1.0→69, scale=0.4→76
    BOTTOM_PAD = 22
    FRUIT_W = max(16, int(42 * scale))   # 果子间距（最小 16px）
    SIDE_MARGIN = 16
    # 果子半径缩放
    R_SCALE = scale
    # 标签字号缩放
    LABEL_FS_BASE = 8.5
    LABEL_FS = max(6, LABEL_FS_BASE * (0.7 + 0.3 * scale))
    # 树干粗细缩放
    TRUNK_W = max(8, int(18 * scale))

    n_branches = len(cat_topics)
    height = HEADER_H + n_branches * BRANCH_H + BOTTOM_PAD

    # 树干几何：居中
    trunk_x = width // 2
    ground_y = HEADER_H + 6
    trunk_top = ground_y + 4
    trunk_bottom = height - 12
    half_w = width // 2 - SIDE_MARGIN  # 一侧可用的最大水平距离

    svg: list[str] = []
    # 用百分比宽度，避免 PDF 渲染时被裁切
    svg.append(
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="auto" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;max-width:100%;margin:0 auto;" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'PingFang SC\', '
        f'\'Microsoft YaHei\', sans-serif">'
    )

    # 背景渐变（淡绿天 → 白）
    grad_id = f"sky_{title[:3].replace(' ', '')}"
    svg.append(
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#F0FDF4"/>'
        f'<stop offset="1" stop-color="#FFFFFF"/>'
        f'</linearGradient></defs>'
    )
    svg.append(
        f'<rect x="0" y="0" width="{width}" height="{ground_y + 2}" '
        f'fill="url(#{grad_id})"/>'
    )

    # 地面（虚线 + 草尖）
    svg.append(
        f'<line x1="0" y1="{ground_y}" x2="{width}" y2="{ground_y}" '
        f'stroke="#A89878" stroke-width="1.5" stroke-dasharray="2 3"/>'
    )
    for gx in range(8, width, 22):
        svg.append(
            f'<line x1="{gx}" y1="{ground_y}" x2="{gx - 2}" '
            f'y2="{ground_y + 5}" stroke="#86EFAC" stroke-width="1.2"/>'
        )

    # 树干（外深 → 中棕 → 内高光，三层叠加出立体感；按 scale 缩放）
    trunk_path = (
        f'M {trunk_x} {trunk_bottom} '
        f'C {trunk_x + 1.5} {(trunk_top + trunk_bottom) * 0.7} '
        f'{trunk_x - 1.5} {(trunk_top + trunk_bottom) * 0.3} '
        f'{trunk_x} {trunk_top}'
    )
    svg.append(
        f'<path d="{trunk_path}" stroke="#3F2410" stroke-width="{TRUNK_W}" '
        f'fill="none" stroke-linecap="round"/>'
    )
    svg.append(
        f'<path d="{trunk_path}" stroke="#6B4423" stroke-width="{int(TRUNK_W * 0.72)}" '
        f'fill="none" stroke-linecap="round"/>'
    )
    svg.append(
        f'<path d="{trunk_path}" stroke="#A07A50" stroke-width="{int(TRUNK_W * 0.33)}" '
        f'fill="none" stroke-linecap="round" opacity="0.55"/>'
    )

    # 树冠（一簇小绿叶 + 一颗大果子装饰在树顶，强化"树"的形象）
    canopy_y = trunk_top - 4
    for cx, cy, rr in [(trunk_x - 12, canopy_y, 9), (trunk_x + 10, canopy_y - 4, 11),
                       (trunk_x - 2, canopy_y - 12, 10), (trunk_x + 16, canopy_y + 4, 7),
                       (trunk_x - 16, canopy_y + 5, 7)]:
        svg.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rr}" ry="{rr * 0.75:.2f}" '
            f'fill="#4ADE80" opacity="0.85"/>'
        )
        svg.append(
            f'<ellipse cx="{cx - rr * 0.3:.2f}" cy="{cy - rr * 0.3:.2f}" '
            f'rx="{rr * 0.35:.2f}" ry="{rr * 0.2:.2f}" '
            f'fill="#FFFFFF" opacity="0.45"/>'
        )

    # 主分支 = 分类；按 cat_topics 顺序（已排好）从下往上画
    branch_zone = trunk_bottom - trunk_top - 14
    for i, (cat, topics) in enumerate(cat_topics):
        # 分支 y：均匀分布（i=0 在最上，越往下 i 越大）
        by = trunk_top + 7 + (i + 0.5) * (branch_zone / n_branches)
        # 方向：奇偶交替（i=0 → 右，i=1 → 左，i=2 → 右，…）
        going_right = (i % 2 == 0)
        # 长度因子已移除：v3.9.51 让所有分支尽量长，确保不截断

        # 不截断：所有知识点全部展示（v3.9.51 关键修复）
        # 之前是 sorted(topics, key=lambda t: -t[1])[:MAX_FRUITS] + "+N" 截断
        topics_sorted = sorted(topics, key=lambda t: -t[1])
        n_fruits = len(topics_sorted)
        if n_fruits == 0:
            continue

        # 计算本侧最大可用水平距离（不缩：v3.9.51 让分支尽量长以容纳更多果子）
        max_extent = half_w

        # 果子间距（如果太长则继续压缩，最小 14px——只要能塞下就不截断）
        if n_fruits > 1:
            ideal_span = (n_fruits - 1) * FRUIT_W
            if ideal_span > max_extent - 24:
                fw = max(14, (max_extent - 24) / (n_fruits - 1))
            else:
                fw = FRUIT_W
        else:
            fw = 0

        if going_right:
            # 分支起点/终点
            branch_start_x = trunk_x + 7
            first_fruit_x = trunk_x + 22
            last_fruit_x = first_fruit_x + (n_fruits - 1) * fw
            branch_end_x = last_fruit_x + 12
            ctrl_x = (branch_start_x + branch_end_x) / 2
            ctrl_y = by - 22
            branch_path = (
                f'M {branch_start_x} {by} '
                f'Q {ctrl_x} {ctrl_y} {branch_end_x} {by - 1}'
            )
            # 分类 chip 锚点（在分支"内侧"，即靠近树干的左侧）
            chip_x = trunk_x + 14
        else:
            # 镜像：分支从树干左侧出发
            branch_start_x = trunk_x - 7
            first_fruit_x = trunk_x - 22
            last_fruit_x = first_fruit_x - (n_fruits - 1) * fw
            branch_end_x = last_fruit_x - 12
            ctrl_x = (branch_start_x + branch_end_x) / 2
            ctrl_y = by - 22
            branch_path = (
                f'M {branch_start_x} {by} '
                f'Q {ctrl_x} {ctrl_y} {branch_end_x} {by - 1}'
            )
            # 分类 chip 锚点（在分支"内侧"，即靠近树干的右侧）
            chip_x = trunk_x - 14

        # 主分支曲线（阴影 + 主色，双层叠加）
        svg.append(
            f'<path d="{branch_path}" stroke="#5C3A1E" stroke-width="6" '
            f'fill="none" stroke-linecap="round"/>'
        )
        svg.append(
            f'<path d="{branch_path}" stroke="#8B7355" stroke-width="3" '
            f'fill="none" stroke-linecap="round" opacity="0.7"/>'
        )

        # 分支上的几片小叶子（装饰，给点绿意；只画在分支前段，不挤到果子下面）
        for lx_frac, lrot in [(0.32, -28), (0.55, 24)]:
            lx = branch_start_x + (branch_end_x - branch_start_x) * lx_frac
            ly = by - (5 if lrot > 0 else 7)
            if going_right:
                if lx < first_fruit_x - 6 and lx < branch_end_x - 8:
                    svg.append(
                        f'<ellipse cx="{lx}" cy="{ly}" rx="4" ry="2" '
                        f'fill="#4ADE80" opacity="0.75" '
                        f'transform="rotate({lrot} {lx} {ly})"/>'
                    )
            else:
                if lx > first_fruit_x + 6 and lx > branch_end_x + 8:
                    svg.append(
                        f'<ellipse cx="{lx}" cy="{ly}" rx="4" ry="2" '
                        f'fill="#4ADE80" opacity="0.75" '
                        f'transform="rotate({-lrot} {lx} {ly})"/>'
                    )

        # 分类小帽（深色药丸 + 白字，挂在分支根处上沿）
        # 关键修复：之前 y=by-24 会让 chip 底（y=by-8）和最大果子（r=18, 顶 y=by-20）
        # 垂直方向 12px 重叠，导致 chip 文字被果子盖住。把 chip 整体上移到 by-34，
        # 让 chip 底（y=by-18）刚好不超过最大果子顶（y=by-20），不再被遮挡。
        chip_w = max(40, len(cat) * 9 + 16)
        if going_right:
            chip_left = chip_x
        else:
            chip_left = chip_x - chip_w
        svg.append(
            f'<rect x="{chip_left}" y="{by - 34}" width="{chip_w}" '
            f'height="16" rx="8" fill="#1F2937" opacity="0.92"/>'
        )
        svg.append(
            f'<text x="{chip_left + chip_w / 2:.1f}" y="{by - 22}" '
            f'font-size="10" font-weight="700" fill="#FFFFFF" '
            f'text-anchor="middle">{cat}</text>'
        )

        # 果子们
        for j, (topic, ac, level, difficulty) in enumerate(topics_sorted):
            if going_right:
                fx = first_fruit_x + j * fw
            else:
                fx = first_fruit_x - j * fw
            fy = by - 2
            # 颜色规则：果子颜色按"掌握度"用绿色深浅表示
            mt = _MASTERY_VIS.get(level, _MASTERY_VIS["空白"])
            # 关键修复 v3.9.51：果子半径按 scale 缩放
            r = max(3, mt["r"] * R_SCALE)
            mc = _MASTERY_COLOR.get(level, _MASTERY_COLOR["空白"])
            fill = mc["fill"]
            fg = mc["fg"]
            bd = mc["bd"]
            diff_label = _DIFF_TIER.get(difficulty, _DIFF_TIER[0])["name"]
            # 完整信息（hover/assistive 显示）：保留难度
            full_info = (
                f"{topic} · AC {ac} · {level} · 难度[{diff_label}]"
            )
            # 果柄（短竖线，从果子底部到分支）
            svg.append(
                f'<line x1="{fx}" y1="{fy + r}" '
                f'x2="{fx}" y2="{fy + r + 4}" '
                f'stroke="#5C3A1E" stroke-width="1.2"/>'
            )
            # 果子本体（带 <title> 鼠标悬停看完整信息）
            svg.append(
                f'<circle cx="{fx}" cy="{fy}" r="{r}" '
                f'fill="{fill}" stroke="{bd}" '
                f'stroke-width="1.2">'
                f'<title>{full_info}</title>'
                f'</circle>'
            )
            # 高光（左上）
            svg.append(
                f'<ellipse cx="{fx - r * 0.32:.2f}" '
                f'cy="{fy - r * 0.4:.2f}" '
                f'rx="{r * 0.35:.2f}" '
                f'ry="{r * 0.22:.2f}" '
                f'fill="#FFFFFF" opacity="0.5"/>'
            )
            # 果子内写 AC 数（半径 >= 8 才写，按 scale 缩放字号）
            if r >= 8:
                fs_inner = max(6, mt["fs"] * R_SCALE)
                svg.append(
                    f'<text x="{fx}" y="{fy + max(3, fs_inner * 0.35)}" '
                    f'font-size="{fs_inner:.1f}" '
                    f'font-weight="{mt["fw"]}" fill="{fg}" '
                    f'text-anchor="middle">{ac}</text>'
                )
            # 果子下写知识点名（<=4 字直接显示；5+ 字拆两行；字号按 LABEL_FS 缩放）
            topic_chars = list(topic)
            n = len(topic_chars)
            if n <= 4:
                lines_lbl = ["".join(topic_chars)]
            else:
                mid = (n + 1) // 2
                lines_lbl = ["".join(topic_chars[:mid]), "".join(topic_chars[mid:])]
            label_y_start = fy + r + max(8, int(12 * R_SCALE))
            for li, line in enumerate(lines_lbl):
                svg.append(
                    f'<text x="{fx}" y="{label_y_start + li * max(8, int(10 * R_SCALE))}" '
                    f'font-size="{LABEL_FS:.1f}" font-weight="600" fill="#1F2937" '
                    f'text-anchor="middle">{line}</text>'
                )

    svg.append('</svg>')
    return '\n'.join(svg)


def build_knowledge_tree_text(syllabus_eval: dict) -> str:
    """渲染 4 棵独立的"真·知识树"（**纯文本**：每棵一个竞赛级别）。

    完全照搬 oi.aijiangti.cn 原版 report.html 的展示形式：

        🌱 CSP-J 入门 · 知识树已点亮 **22** / 28（78.6%）

        动态规划DP基础 · AC 55 · 精通 · 难度[未知]55DP基础
        背包DP · AC 13 · 熟练 · 难度[未知]13背包DP
        区间DP · AC 1 · 初窥 · 难度[未知]1区间DP
        基础实现模拟法 · AC 48 · 精通 · 难度[未知]48模拟法
        ...

    特点
    ----
    - **不截断**：所有知识点一视同仁平铺展示，4 个等级总共 ~131 个知识点全部展开
    - **不截断**：分类多就多换行，不会出现 SVG 那种"超出容器就被裁"的问题
    - **不截断**：与原版报告 1:1 对齐，AC=0 仍显示"难度[未知]<主题名>"，空白也看得见
    - 排版：按"分类 max AC 降序 → 分类内 AC 降序"排序，强项在上，弱项在下面
    - 每个分类首行前拼分类名（如"动态规划DP基础"），后面所有主题不带分类前缀
    - 末端的"难度[未知]<N><主题名>"是原版"果子标签"的纯文本化：AC>0 时显示 N+主题，AC=0 时只显示主题
    """
    group_keys = (
        ("csp_j", "CSP-J 入门", "🌱"),
        ("csp_s", "CSP-S 提高", "🌿"),
        ("provincial", "省选级", "🌳"),
        ("noi", "NOI 级", "🏆"),
    )

    # 颜色 CSS：每条知识点按"掌握度"用绿色深浅染色，与原版保持一致的视觉感受
    # （原版 5 档：精通近黑/熟练深绿/入门标准绿/初窥浅绿/空白白）
    level_style = {
        "精通": "color:#064E3B;font-weight:700;",
        "熟练": "color:#065F46;font-weight:600;",
        "入门": "color:#047857;font-weight:500;",
        "初窥": "color:#10B981;font-weight:400;",
        "空白": "color:#9CA3AF;font-weight:400;",
    }

    out: list[str] = []

    for key, title, icon in group_keys:
        group = syllabus_eval.get(key, {}) or {}
        details = group.get("details", []) or []
        stats = group.get("stats", {}) or {}
        coverage = group.get("coverage", 0)
        total = int(stats.get("total", 0))
        blank = int(stats.get("空白", 0))
        lit = total - blank

        # 标题行（与原版一致：emoji + 级别名 + "知识树已点亮" + 数字 + 覆盖率）
        out.append(
            f'<h3 style="font-size:1.15rem;font-weight:800;color:#065F46;'
            f'border-left:4px solid #10B981;padding:6px 0 6px 10px;'
            f'margin:18px 0 10px 0;background:#F0FDF4;border-radius:0 4px 4px 0;">'
            f'{icon} {title} · 知识树已点亮 '
            f'<b style="color:#059669;">{lit}</b> / {total}（{coverage}%）'
            f'</h3>'
        )

        if not details:
            out.append(
                '<p style="color:#9CA3AF;font-size:13px;margin:4px 0 14px 0;">'
                '（该级别暂无知识点数据）</p>'
            )
            continue

        # 按分类聚合
        cat_to_topics: dict[str, list[tuple[str, int, str, int]]] = {}
        for item in details:
            topic = str(item.get("topic", "")).strip()
            if not topic:
                continue
            ac = int(item.get("ac_count", 0) or 0)
            level = _level_for_ac(ac)
            difficulty = int(item.get("difficulty", 0) or 0)
            cat = _classify_topic(topic)
            cat_to_topics.setdefault(cat, []).append((topic, ac, level, difficulty))

        # 分类排序：每个分类的"最强 AC 数"降序（强的画在上面）
        def _cat_max(cat: str) -> int:
            return max((t[1] for t in cat_to_topics[cat]), default=0)

        ordered_cats = sorted(cat_to_topics.keys(), key=_cat_max, reverse=True)

        # 逐分类展开：每行一个知识点，**绝不截断**
        for cat in ordered_cats:
            topics_sorted = sorted(
                cat_to_topics[cat], key=lambda t: -t[1]
            )  # 分类内按 AC 降序
            for i, (topic, ac, level, _diff) in enumerate(topics_sorted):
                # 第一个主题前拼分类前缀（与原版一致）
                prefix = cat if i == 0 else ""
                # 末端的"果子标签"：AC>0 显示"N主题"，AC=0 只显示"主题"
                marker = f"{ac}{topic}" if ac > 0 else topic
                # 整行用 <span> 包一层按掌握度染色
                line_style = level_style.get(level, level_style["空白"])
                out.append(
                    f'<div style="font-size:13px;line-height:1.85;'
                    f'padding:1px 0 1px 8px;border-left:2px solid #E5E7EB;'
                    f'margin:0;{line_style}">'
                    f'{prefix}{topic} · AC {ac} · {level} · 难度[未知]{marker}'
                    f'</div>'
                )

        # 每个级别之间加一道空行（视觉分隔，HTML 渲染时也会有一点呼吸感）
        out.append(
            '<div style="height:10px;"></div>'
        )

    return "\n".join(out)


def build_knowledge_tree_html(syllabus_eval: dict) -> str:
    """渲染 4 棵独立的"真·知识树"（SVG：每棵一个竞赛级别）。

    每棵树 = 1 个竞赛级别（CSP-J / CSP-S / 省选 / NOI）：
        - 棕色树干
        - 分类作为主分支（带小绿叶装饰）
        - 知识点作为果子挂枝头
        - 果子大小 + 颜色（绿深浅） = 掌握度
        - 鼠标悬停：显示 AC 题目数 / 掌握等级 / 关联题目难度

    返回完整 HTML（含图例、说明、4 棵树）。"
    """
    group_keys = (
        ("csp_j", "CSP-J 入门", "🌱"),
        ("csp_s", "CSP-S 提高", "🌿"),
        ("provincial", "省选级", "🌳"),
        ("noi", "NOI 级", "🏆"),
    )

    # ---------- 图例 1：大小=掌握度 ----------
    # 关键修复：图例每个点用掌握度自身的颜色（绿深浅），跟真果子一致。
    legend_size: list[str] = []
    for name in ("精通", "熟练", "入门", "初窥", "空白"):
        mt = _MASTERY_VIS[name]
        r = mt["r"]
        col = _MASTERY_COLOR.get(name, _MASTERY_COLOR["空白"])
        dot_fill = col["fill"]
        dot_stroke = col["bd"]
        legend_size.append(
            f'<span style="display:inline-flex;align-items:center;'
            f'gap:5px;margin-right:12px;">'
            f'<svg width="{r * 2 + 4}" height="{r * 2 + 4}" '
            f'viewBox="-{r + 2} -{r + 2} {r * 2 + 4} {r * 2 + 4}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<circle r="{r}" fill="{dot_fill}" stroke="{dot_stroke}" '
            f'stroke-width="1.2"/>'
            f'<ellipse cx="{-r * 0.32:.2f}" cy="{-r * 0.4:.2f}" '
            f'rx="{r * 0.35:.2f}" ry="{r * 0.22:.2f}" '
            f'fill="#FFFFFF" opacity="0.5"/>'
            f'</svg>'
            f'<span style="font-size:11px;color:#1F2937;">{name}</span>'
            f'</span>'
        )

    legend = (
        '<div style="background:#F9FAFB;border:1px solid #E5E7EB;'
        'border-radius:6px;padding:10px 14px;margin:0 0 14px 0;'
        'font-size:11px;color:#374151;">'
        '<div style="display:flex;flex-wrap:wrap;align-items:center;'
        'gap:6px;">'
        '<span style="font-weight:700;color:#1F2937;margin-right:6px;">'
        '� 果子大小 + 颜色 = 掌握度（绿色深浅：精通近黑→熟练深绿→入门标准绿→初窥浅绿→空白白）'
        '</span>'
        + ''.join(legend_size)
        + '</div>'
        '</div>'
    )

    # ---------- 4 棵树（v3.6 改为 2×2 网格 · 一页并排 2 棵）----------
    # 每棵：醒目标题条（级别 + 图标 + 大字号 + 彩色背景 + 边框）
    tree_blocks: list[str] = []
    for idx, (key, title, icon) in enumerate(group_keys):
        group = syllabus_eval.get(key, {}) or {}
        details = group.get("details", []) or []
        stats = group.get("stats", {}) or {}
        coverage = group.get("coverage", 0)
        total = int(stats.get("total", 0))
        blank = int(stats.get("空白", 0))
        lit = total - blank

        # 按分类聚合
        # tuple 顺序: (topic, ac, level, difficulty)
        cat_to_topics: dict[str, list[tuple[str, int, str, int]]] = {}
        cat_order: list[str] = []
        for item in details:
            topic = str(item.get("topic", "")).strip()
            if not topic:
                continue
            ac = int(item.get("ac_count", 0) or 0)
            level = _level_for_ac(ac)
            difficulty = int(item.get("difficulty", 0) or 0)
            cat = _classify_topic(topic)
            if cat not in cat_to_topics:
                cat_to_topics[cat] = []
                cat_order.append(cat)
            cat_to_topics[cat].append((topic, ac, level, difficulty))

        # 排序：分类按"该分类最高 AC 数"降序（强的分类画在树上更高位置）
        def _cat_score(cat: str) -> int:
            return max((t[1] for t in cat_to_topics[cat]), default=0)

        cat_topics = [(c, cat_to_topics[c]) for c in cat_order]
        cat_topics.sort(key=lambda kv: _cat_score(kv[0]), reverse=True)

        svg = _build_one_tree_svg(icon, title, cat_topics)

        # 该棵树的统计条
        meta = (
            f'已点亮 <b style="color:#059669;font-weight:700;">{lit}</b>'
            f' / {total}（{coverage}%）'
        )

        # v3.6 醒目标题：级别色编码（按竞赛级别从浅到深）
        # CSP-J 浅绿 / CSP-S 中绿 / 省选 深绿 / NOI 金色
        level_colors = {
            "CSP-J 入门": ("#10B981", "#D1FAE5", "#065F46"),   # 浅绿
            "CSP-S 提高": ("#059669", "#A7F3D0", "#064E3B"),   # 中绿
            "省选级":     ("#047857", "#6EE7B7", "#022C22"),    # 深绿
            "NOI 级":     ("#D97706", "#FDE68A", "#78350F"),    # 金色
        }
        border_c, bg_c, text_c = level_colors.get(title, ("#10B981", "#F0FDF4", "#065F46"))

        tree_blocks.append(
            f'<div class="kt-tree-block" style="'
            f'background:#FFFFFF;border:2px solid {border_c};'
            f'border-radius:8px;padding:8px 10px;margin:0;">'
            # 醒目标题条：级别 emoji + 名称 + 统计
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:center;background:{bg_c};border-left:5px solid {border_c};'
            f'padding:6px 10px;margin:0 0 6px 0;border-radius:4px;">'
            f'<span style="font-size:16px;font-weight:800;color:{text_c};'
            f'letter-spacing:0.5px;">{icon} {title} · 知识树</span>'
            f'<span style="font-size:11px;color:{text_c};font-weight:600;">{meta}</span>'
            f'</div>'
            f'{svg}'
            f'</div>'
        )

    # v3.6 2×2 网格：前 2 棵一行，后 2 棵一行（每行 2 棵并排）
    # 在小屏自动降为 1 列
    return (
        '<div class="kt-section" style="margin:8px 0 18px 0;">'
        '<h2 style="font-size:18px;font-weight:700;color:#065F46;'
        'border-left:5px solid #10B981;padding:6px 0 6px 10px;'
        'margin:0 0 8px 0;background:#F0FDF4;border-radius:0 6px 6px 0;">'
        '🌳 知识树图谱（按竞赛级别 · 果子大小/颜色 = 掌握度）</h2>'
        '<p style="font-size:12px;color:#4B5563;margin:0 0 10px 0;'
        'line-height:1.6;">下图为按 4 个竞赛级别（CSP-J / CSP-S / 省选 / '
        'NOI）分别画出的 4 棵"知识树"（<b>2×2 并排</b>，每棵带级别色编码）。'
        '每棵树上，<b>主干</b>代表该级别，<b>分支</b>代表算法分类（基础实现 / '
        '搜索 · DFS / 动态规划 / 贪心 · 二分 / 图论 / 数据结构 / 字符串 / '
        '数学 · 数论 / 计算几何 / 其他），<b>果子</b>就是该分类下的具体知识点。'
        '<b>果子越大、颜色越深</b> = 该知识点 AC 数越多 = 掌握越好；'
        '灰色小果子 = 该知识点尚未接触（AC=0）。</p>'
        + legend
        # 2×2 网格：grid-template-columns:repeat(2, 1fr)，gap:8px
        + '<div style="display:grid;grid-template-columns:repeat(2, 1fr);'
        'gap:10px;align-items:start;'
        '@media (max-width:768px){grid-template-columns:1fr;}'
        '">'
        + ''.join(tree_blocks)
        + '</div>'
        + '</div>'
    )


# ═══════════════════════════════════════════════════════════════════════
#  Normalize: 把 AI 写的小节标题统一, 并插入可信数据块
# ═══════════════════════════════════════════════════════════════════════

def remove_injected_trusted_block(report_md: str) -> str:
    pattern = re.compile(
        r"##\s*数据校准与真实统计.*?(?=\n##\s|\Z)",
        re.S,
    )
    return pattern.sub("", report_md)


def normalize_report_markdown(report_md: str, export_data: dict | None = None) -> str:
    """拼接 AI 报告与"程序生成的可信数据块"。

    关键不变量（v3.6 照搬 oi.aijiangti.cn 14:07 原版 report.html）:
    1. 模板 `report_template.html` **已经**渲染了：
       - 封面页（H1 洛谷 AI 学习测评报告 / 测评基础信息 / 数据卡片 / 抓取统计）
       - 目录页（1-7 章节）
       - 第 1 章节 H1 "1. 核心数据概览与图表化分析" + 6 张图表
       → 所以 markdown 区域（`{{ report_html | safe }}`）第一段是
         4 个程序生成的 H2（数据校准与真实统计 + 掌握度判定标准 + 2 棵知识树 + 知识点明细），
         **紧接**AI 报告的 H1 "🏅 OI 竞赛选手深度能力诊断与训练报告" + 7 个 H2 章节。
    2. AI 报告**必须**从 `# 🏅 OI 竞赛选手深度能力诊断与训练报告` (H1) 开始,
       紧接 7 个 H2 章节（1.~7.【...】），**不要**写模板已有的 H1 洛谷 AI 学习测评报告，
       **不要**写程序已生成的可信数据块的任何 H2/H3。
       所以这里先把 AI 报告里**多余的** `## 数据校准与真实统计` 块**删掉**（H1 保留），
       然后把 `build_trusted_data_summary_md()` 输出**插到**最前面（4 个 H2），
       最后接 AI 报告的 H1 + 7 个 H2 章节。
    """
    normalized = report_md or ""
    # 1) 删 AI 报告里**多余的** `## 数据校准与真实统计` 块（程序已生成同名 H2）
    normalized = remove_injected_trusted_block(normalized)
    if export_data is None:
        return normalized.lstrip()
    trusted_block = build_trusted_data_summary_md(export_data)
    # 2) 拼：trusted_block（4 个 H2）在最前，紧接模板的章节 1 H1 + 6 图表，
    #    AI 报告（H1 + 7 个 H2 章节）紧接 trusted_block 之后
    return f"{trusted_block}\n\n{normalized.lstrip()}"


# ═══════════════════════════════════════════════════════════════════════
#  Evolution prompt (代码考古)
# ═══════════════════════════════════════════════════════════════════════

def _build_evolution_prompt(export_data: dict) -> str:
    evolution_data = export_data.get("submission_evolution", {}) or {}
    if evolution_data.get("selected_problems"):
        try:
            return evolution_to_prompt_block(evolution_data)
        except Exception as e:
            return f"（代码考古数据格式化失败：{e}）"
    _total_records = sum(
        len(items) for items in (
            export_data.get("passed_items", []) or [],
            export_data.get("failed_items", []) or [],
        )
    )
    return (
        f"（v3.9.43 · 提示：未抓取到该用户同一道题多次提交的源码记录,"
        f"无法做「逐版 diff」分析；"
        f"但本份报告已抓取 {_total_records} 条提交记录的源码,"
        f"位于「提交行为分析」和「代码风格」章节。）"
    )


DIAGNOSTIC_FRAMEWORK = """
【能力评估参考框架】(请对照此框架对用户进行诊断和分级建议):
1. S级 - 计数与组合推导: 赛时容易先写DFS/枚举, 缺乏"统计对象集合"思维。需强化: 组合数/容斥/DP/生成函数。
2. S级 - 图论建模与最短路变形: 模板能写但建图边含义不稳, 差分约束/分层图易卡。需强化: 图的语义定义、最短路树。
3. A级 - 数据结构维护不变量: 基础线段树能做, 多标记易WA。需强化: 节点信息明确数学定义、merge/pushdown的代数正确性。
4. A级 - DP 状态设计与优化: 常规DP能写, 维度多易爆复杂度。需强化: 树形/区间/状压DP, 单调队列优化。
5. A级 - 部分分升级能力: 赛时能拿部分分, 但不会倒推。需强化: 从小n、小值域、树退化等子任务倒推正解。
6. B级 - 高级字符串结构: KMP/Hash有基础, 自动机/SAM不稳定。需强化: 节点代表的集合、Fail树/link的含义。
7. B级 - 计算几何: 缺模板, 少边界意识。需强化: 向量/叉积、凸包、扫描线基础与eps处理。
8. B级 - 网络流/匹配: 缺乏模式识别。需强化: 建图谱系、最小割模型、费用流。
9. S级 - 复盘与错因沉淀: 盲目改代码AC后就过。需强化: 四段式复盘 (赛时模型、错因、正解性质、代码不变量)。
"""


def _build_prompt(export_data: dict) -> str:
    """构造喂给 LLM 的 prompt。"""
    summary = export_data.get("summary", {}) or {}
    solved = int(export_data.get("solved_count", 0))
    failed = int(export_data.get("failed_count", 0))
    behavior = export_data.get("behavior_analysis", {}) or {}
    six_dim = export_data.get("six_dimension_scores", {}) or {}
    # B 项目暂未集成 self_register / 政策匹配 / 提交行为分析, 默认空字符串/空字典, 模板会兜底为"（无xxx数据）"
    profile_block = export_data.get("profile_block", "") or ""
    policy_block = export_data.get("policy_block", "") or ""
    behavior_data = behavior
    solved_count = solved
    failed_count = failed

    passed_samples: list[str] = []
    for it in export_data.get("passed_items", []):
        r = it.get("record") if isinstance(it, dict) else None
        if r and isinstance(r, dict) and r.get("sourceCode"):
            pid = (it.get("problem") or {}).get("pid", "?")
            title = (it.get("problem") or {}).get("title", "")
            passed_samples.append(
                f"### Problem {pid} - {title} (Passed)\n```cpp\n{r['sourceCode'][:800]}\n```\n"
            )
        if len(passed_samples) >= 3:
            break

    failed_samples: list[str] = []
    for it in export_data.get("failed_items", []):
        r = it.get("record") if isinstance(it, dict) else None
        problem = it.get("problem") or {}
        pid = problem.get("pid", "?")
        title = problem.get("title", "")
        code_str = ""
        if r and isinstance(r, dict) and r.get("sourceCode"):
            code_str = f"User's failed code:\n```cpp\n{r['sourceCode'][:800]}\n```\n"
        failed_samples.append(
            f"### Problem {pid} - {title} (Attempted but NOT passed)\n{code_str}"
        )
        if len(failed_samples) >= 5:
            break

    if behavior and "error" not in behavior:
        behavior_summary = format_behavior_summary(behavior)
    else:
        behavior_summary = f"**提交行为分析**: {behavior.get('error', '未获取到提交记录数据。')}"

    code_records: list[dict] = []
    for it in (export_data.get("passed_items", []) or []) + (export_data.get("failed_items", []) or []):
        if isinstance(it, dict) and isinstance(it.get("record"), dict):
            code_records.append(it["record"])
    code_analysis = analyze_code_style(code_records)
    code_analysis_summary = format_code_analysis(code_analysis)

    evolution_prompt = _build_evolution_prompt(export_data)

    syllabus_eval = export_data.get("syllabus_evaluation", {}) or {}
    if syllabus_eval:
        syllabus_summary = format_syllabus_report(syllabus_eval)
    else:
        syllabus_summary = "**大纲知识点对标**: 未获取到评估数据。"

    six_dim_text = ""
    if six_dim:
        six_dim_text = "| 维度 | 评分 |\n|------|------|\n"
        for dim, score in six_dim.items():
            six_dim_text += f"| {dim} | {score} |\n"

    syllabus_context_info = load_syllabus_context(max_chars=20000)
    syllabus_context = ""
    if syllabus_context_info.get("content"):
        source_path = syllabus_context_info.get("path") or "未知路径"
        syllabus_context = (
            f"【2025 大纲真实来源】{syllabus_context_info.get('source')} | {source_path}\n"
            f"{syllabus_context_info['content']}\n\n"
        )

    difficulty_guide = """
洛谷难度映射请严格使用以下标准名称, 不要写"难度1/难度2":
- 0: 暂无评定 (灰色)
- 1: 入门 (红色)
- 2: 普及- (橙色)
- 3: 普及/提高- (黄色)
- 4: 普及+/提高 (绿色)
- 5: 提高+/省选- (蓝色)
- 6: 省选/NOI- (紫色)
- 7: NOI/NOI+/CTSC (黑色)
"""

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    student = export_data.get("student_info", {}) or {}
    student_line = (
        f"学员: {student.get('name') or student.get('username') or '匿名'}"
        f" (uid={student.get('luogu_uid', '?')})"
    )

    prompt = f"""
你是一位顶级的算法竞赛金牌教练。我导出了一位选手的近期洛谷做题记录（包括已通过和尝试但未通过的题目代码）。
请你根据我提供的【能力评估参考框架】以及【官方考纲】，对他进行深度的诊断，并针对他【未做完/做错的题目】给出极具启发性的题解。

**报告生成时间**：{current_time}

{DIAGNOSTIC_FRAMEWORK}

{difficulty_guide}

{syllabus_context}

### 选手学籍档案（来自 self_register 表单 · v3.8 增强）
{profile_block or "（无档案数据，可能未注册或仅游客模式）"}

### 当地升学政策 + 目标学校政策（v3.8 增强）
{policy_block or "（无政策匹配数据）"}

### 选手的全局数据统计
- 本次导出中已通过题数: {solved_count}
- 本次导出中未通过/卡住题数: {failed_count}
- 卡题数（定义：同一道题提交>=3次且最终未AC）: {len((behavior_data or {}).get('stuck_problems', [])) if isinstance(behavior_data, dict) else 0}
- 难度分布直方图: {json.dumps(summary.get('difficulty_histogram'))}
- 偏好的算法标签: {json.dumps(summary.get('top_algorithm_tags') or summary.get('top_tags'))}

### 六维能力评分
{six_dim_text if six_dim_text else '未计算'}

### 提交行为深度分析
{behavior_summary}

### 大纲知识点对标
{syllabus_summary}

{code_analysis_summary}

### 选手最近通过的代码样本（用于评估代码习惯）
{''.join(passed_samples) if passed_samples else '暂无代码'}

### 选手未做完/尝试失败的题目（重点出题解部分）
{''.join(failed_samples) if failed_samples else '暂无未通过的题目'}

请你输出一份结构化的 Markdown 辅导报告，必须包含以下部分。在生成 Markdown 时，请务必使用以下视觉元素增强表现力：
 - 评分请使用黄色星级，如 ⭐⭐⭐⭐☆ (使用 ⭐ 和 ☆)
 - 难度名称必须使用洛谷官方口径，如“入门 / 普及- / 普及+/提高 / 提高+/省选- / 省选/NOI-”，严禁写“难度1/难度2”
 - 不要生成黑白字符图表或黑白直方图；如果需要表达占比或难度，请优先使用 HTML 彩色徽章、彩色表格，或直接引用上方图表结论
 - 等级前缀符号请使用 🟢精通 | 🟡熟练 | 🟠入门 | 🔵初窥 | 🔴空白
 - 各处点评或结论段落，请使用 `<p class="text-blue-700 font-semibold">解读：...</p>` 样式包装。
 - 整个报告尽可能以 Markdown 表格、区块等图表化、直观的形式呈现，少用长篇大论的文字。

 1. **【选手概览与性格画像】**：
    基于提交行为数据，提炼选手的性格画像。**必须**用 Markdown 表格输出，表格列固定为：`| 性格维度 | 星级评分 | 拟人化评价 | 数据证据 |`。
    **必须包含 6 行**（顺序固定，不允许合并或省略任意一行）：
    1) 坚韧度  2) 完美主义  3) 冒险精神  4) 自律性  5) 调试耐心  6) 作息规律
    严禁把多行合并成一格（例如把"自律性"和"作息规律"合并为"自律性与规律性"），也严禁用列表/段落代替表格。
    星级使用 ⭐⭐⭐⭐⭐/⭐⭐⭐⭐☆/⭐⭐⭐☆☆/⭐⭐☆☆☆/⭐☆☆☆☆☆ 五档（与雷达图六个维度的口径一一对应）。
    每行数据证据栏必须引用具体数字（如提交时段、卡题次数、AC率、重交间隔等），不要写"数据不足"。

 2. **【提交行为深度分析】**：
    基于提供的提交行为数据，以表格和重点解读的形式，深入分析用户的提交习惯。必须包含以下子模块：
    - **死磕题目 TOP (提交次数最多)**：列出提交次数最多的几道题，分析原因。
     - **首次 AC 情况**：分析首次通过和多次尝试后通过的比例。
    - **其他显著行为特征**：如单日高强度刷题记录、长耗时题目等。
    (注意：此部分请用表格展示数据，并在表下附上 `<p class="text-blue-700 font-semibold">特征：...</p>`)

 3. **【难度分布与水平研判】**：
    分析选手的难度分布特征，判断其处于哪个阶段（入门/普及/提高/省选）。必须使用洛谷官方难度名称：暂无评定、入门、普及-、普及/提高-、普及+/提高、提高+/省选-、省选/NOI-、NOI/NOI+/CTSC。严禁输出“难度1/难度2/难度3”。

 4. **【六维能力雷达表与诊断】（评分参考：85-100 优秀 | 65-84 良好 | 40-64 基础 | <40 薄弱）**：
      输出 Markdown 表格，评估选手在六大维度的状态：`| 能力块 | 评分 | 当前等级 | 数据证据 | 已经具备 |`
      六大维度：基础算法、数据结构、图论、动态规划、字符串、数学。当前等级请使用前缀符号（如 🟢精通）。

  5. **【考纲精准定级与知识点盲区】**（根据提供的 NOI大纲 2025版）：
     - **当前对应等级水平**：明确指出该选手目前处于 CSP-J / CSP-S / 省选 / NOI 哪个阶段。
     - **知识点强弱项**：严格对照考纲中的知识点名词，列出其掌握得最好的 3 个考点，以及最薄弱的 3 个考点（使用 🟢🟡🔴 标注）。
     - **训练盲区**：指出他在当前等级中"完全没有涉及/刷题数据中缺失"的必考知识点。
     - **知识点覆盖与树状图**：不要再写知识点覆盖统计表或知识树（这些由程序自动生成，放在"数据校准与真实统计"小节）。你只需要在本节用 1-2 段话点评"哪些大分支（4 大等级）覆盖得好、哪些几乎为零，并给 1-2 条具体训练建议"即可。
     - **题目级别经历表**：单独说明做过多少道 CSP-S / 省选 / NOI 级别题，按来源标签与难度双证据解释，不要与知识点覆盖混为一谈。

  6. **【风险诊断与训练闭环表】**：
     输出 Markdown 表格：`| 优先级 | 风险项 | 触发场景 | 比赛症状 | 根因判断 | 训练专题 | 验收标准 |`
     - 行数至少 5 行，优先级使用 `S/A/B`。
     - 这个表必须是高度可执行的训练方案。

  7. **【代码质量与工程习惯深度分析】**：基于《源码静态风格分析》及代码样本，提供一份来自资深架构师视角的 Review。分析代码长度、宏定义习惯（如 `#define int long long`）、IO 优化、命名、STL 容器使用情况等。指出 2 个优点和 3 个必须改掉的坏习惯。

  8. **【定制训练题单（6个月路线图）】**：
     根据上述大纲盲区和薄弱项，定制一份分阶段的训练计划：
     - 第一阶段（Month 1-2）：巩固基础，补齐短板
     - 第二阶段（Month 3-4）：数据结构/算法突破
     - 第三阶段（Month 5-6）：提速与稳定
     每个阶段包含具体知识点 + 推荐题目（带洛谷题号）。

  9. **【核心建议（优先级排序）】**：
     列出 5-8 条核心建议，按优先级排序（🔴紧急 / 🟡重要 / 🟢建议）。例如：`🔴 紧急: 补加 ios::sync_with_stdio(false) 防止大数据 TLE`。

  10. **【未通过题目专属题解（从暴力到正解）】**：针对上面列出的"未做完/尝试失败的题目"，逐一出题解。
    - 绝不能直接给出最优解！
    - 必须严格遵循**"从暴力到正解的思考过程"**：
      a) **AI 题解摘要**：一句话点出这道题的核心思路或坑点。
      b) 暴力思路怎么想？（复杂度是多少，能拿多少部分分？）
      c) 瓶颈在哪里？（时间卡在哪，空间卡在哪？）
      d) 关键性质/不变量观察（Key Observation）。
      e) 最终正解的推导与核心代码结构。
      f) **推荐同类题**：推荐 1-2 道涉及相同考点或技巧的洛谷题目（标明题号和简要推荐理由）。
    """
    return prompt



def _trim_to_safe_boundary(text: str | None) -> str:
    """把已生成的 partial 文本修剪到最后一个完整行，避免把半句话喂给模型续写。"""
    if not text:
        return ""
    text = text.rstrip()
    if not text:
        return ""
    # 优先尝试切到最后一个 "## " / "### " 之类的二级标题处，作为天然分段点
    boundary_candidates: list[int] = []
    for marker in ("\n## ", "\n### ", "\n#### "):
        idx = text.rfind(marker)
        if idx > 0:
            boundary_candidates.append(idx + 1)  # +1 保留换行符
    # 退化到最后一个换行
    last_newline = text.rfind("\n")
    if last_newline > 0:
        boundary_candidates.append(last_newline + 1)
    if not boundary_candidates:
        return text
    cut = max(boundary_candidates)
    # 至少要保留 80% 内容，否则保守地只切到最后一个换行
    if cut < int(len(text) * 0.2):
        return text
    return text[:cut].rstrip() + "\n"


def _is_retryable_ai_error(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    if name in ("APITimeoutError", "APIConnectionError"):
        return True
    if "timeout" in msg or "connection" in msg or "429" in msg or "rate" in msg or "503" in msg or "502" in msg:
        return True
    return False


def generate_ai_report(
    export_data: dict,
    api_key: str,
    base_url: Optional[str] = None,
    model_name: str = "gpt-4o-mini",
    *,
    output_path: Optional[str] = None,
    resume_prefix: Optional[str] = None,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> str:
    """调用 LLM 生成 AI 报告 (markdown)。"""
    if not str(api_key or "").strip():
        raise ValueError("未提供 API Key (--api-key 或环境变量 OPENAI_API_KEY)")
    repair_behavior_analysis_from_items(export_data)
    client_kwargs = {"api_key": api_key, "timeout": 1800.0}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    prompt = _build_prompt(export_data)
    if resume_prefix:
        prompt = (
            f"{prompt}\n\n"
            f"# 续写说明\n"
            f"下面「已生成内容」是上一次生成的开头部分, 请直接续写, 不要重复。"
            f"输出时, 用「已生成内容」最后一段的逻辑继续, 保持语言风格一致。\n\n"
            f"## 已生成内容\n{resume_prefix}"
        )

    if on_progress:
        on_progress("ai", f"调用 {model_name} 生成 AI 报告...")

    attempt = 1
    last_exc: Optional[Exception] = None
    while attempt <= AI_GENERATION_MAX_RETRIES:
        if on_progress:
            on_progress("ai", f"第 {attempt}/{AI_GENERATION_MAX_RETRIES} 次")
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是一位资深信息学奥赛教练, 严谨、专业、有教练味, 严格基于客观数据写报告。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                stream=True,
            )
            full_text_parts: list[str] = []
            for chunk in response:
                try:
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None) if delta else None
                except (AttributeError, IndexError):
                    piece = None
                if piece:
                    full_text_parts.append(piece)
                    if output_path:
                        with open(output_path, "a", encoding="utf-8") as f:
                            f.write(piece)
            full_text = "".join(full_text_parts)
            if not full_text.strip():
                raise RuntimeError("AI 返回内容为空")
            return full_text
        except Exception as e:
            last_exc = e
            if (attempt >= AI_GENERATION_MAX_RETRIES) or (not _is_retryable_ai_error(e)):
                raise
            if on_progress:
                on_progress("ai", f"失败, 等待 {AI_GENERATION_RETRY_SLEEP_SECONDS * attempt}s 后重试: {e}")
            time.sleep(AI_GENERATION_RETRY_SLEEP_SECONDS * attempt)
            attempt += 1
    if last_exc:
        raise last_exc
    raise RuntimeError("AI 生成失败 (未知原因)")


# ═══════════════════════════════════════════════════════════════════════
#  HTML + PDF 拼装
# ═══════════════════════════════════════════════════════════════════════

def build_html_and_pdf(
    report_md: str,
    export_data: dict,
    html_path: str,
    pdf_path: str,
    chart_paths: dict[str, str],
    export_pdf: bool = True,
) -> None:
    """把 markdown 报告渲染成 HTML, 然后用 Playwright 导成 PDF。"""
    register_pdf_font()
    report_html = md.markdown(report_md, extensions=["tables", "fenced_code"])
    report_html = re.sub(
        r"((?:⭐|☆){1,5})",
        lambda m: render_star_rating_html(m.group(1)),
        report_html,
    )
    report_html = re.sub(r'(<h3>Problem)', r'<div class="page-break"></div>\1', report_html)

    badge_style_base = (
        "display:inline-block;padding:2px 8px;border-radius:9999px;border:1px solid;"
        "font-size:12px;font-weight:700;line-height:1.2;white-space:nowrap;"
    )
    badge_styles = {
        "green": badge_style_base + "background:#DCFCE7;color:#166534;border-color:#86EFAC;",
        "orange": badge_style_base + "background:#FFEDD5;color:#9A3412;border-color:#FDBA74;",
        "red": badge_style_base + "background:#FEE2E2;color:#991B1B;border-color:#FCA5A5;",
        "gray": badge_style_base + "background:#F3F4F6;color:#374151;border-color:#D1D5DB;",
    }
    risk_legend_html = '<p style="margin:0 0 12px 0;color:#6b7280;font-size:13px;">优先级说明: S (高/立即处理) · A (中/近期处理) · B (低/可后置)。</p>'
    risk_legend_inserted = False

    level_rules = [
        (re.compile(r"(短板|明显短板|偏弱|弱|无涉及|未涉及|缺失|不会|没涉及|没有涉及|基础弱)", re.I), "red"),
        (re.compile(r"(中等偏稳|有基础|基础稳|待强化|会但赛时成本高|需要加强|高级弱|易错|不熟)", re.I), "orange"),
        (re.compile(r"(稳|强项|覆盖充分|中上|优秀|熟练|稳定)", re.I), "green"),
    ]

    def _clean_cell_inner(inner: str) -> str:
        inner = re.sub(r"</?p[^>]*>", "", inner, flags=re.I)
        inner = re.sub(r"<[^>]+>", "", inner)
        return inner.strip()

    def _wrap_td_inner(td_html: str, display_text: str, style_key: str) -> str:
        m = re.match(r"<td(?P<attrs>[^>]*)>(?P<inner>.*)</td>", td_html, flags=re.S | re.I)
        if not m:
            return td_html
        attrs = m.group("attrs") or ""
        return f'<td{attrs}><span style="{badge_styles[style_key]}">{display_text}</span></td>'

    def _process_table(table_html: str) -> str:
        nonlocal risk_legend_inserted
        is_ability = bool(
            re.search(r"<th[^>]*>\s*能力块\s*</th>", table_html, flags=re.I)
            and re.search(r"<th[^>]*>\s*当前等级\s*</th>", table_html, flags=re.I)
        )
        is_risk = bool(
            re.search(r"<th[^>]*>\s*优先级\s*</th>", table_html, flags=re.I)
            and re.search(r"<th[^>]*>\s*风险项\s*</th>", table_html, flags=re.I)
        )
        if not (is_ability or is_risk):
            return table_html

        def _row_repl(m: re.Match) -> str:
            row = m.group(0)
            if "<th" in row:
                return row
            tds = re.findall(r"<td[^>]*>.*?</td>", row, flags=re.S | re.I)
            if not tds:
                return row
            if is_ability:
                col_idx = 1
                if len(tds) <= col_idx:
                    return row
                target_td = tds[col_idx]
                inner = re.sub(r"^<td[^>]*>|</td>$", "", target_td, flags=re.S | re.I)
                text = _clean_cell_inner(inner)
                if not text:
                    return row
                style_key = None
                for rule, key in level_rules:
                    if rule.search(text):
                        style_key = key
                        break
                if not style_key:
                    return row
                new_td = _wrap_td_inner(target_td, text, style_key)
                return row.replace(target_td, new_td, 1)

            col_idx = 0
            if len(tds) <= col_idx:
                return row
            target_td = tds[col_idx]
            inner = re.sub(r"^<td[^>]*>|</td>$", "", target_td, flags=re.S | re.I)
            text = _clean_cell_inner(inner)
            normalized = (text or "").strip().upper()
            mapping = {
                "S": ("S (高/立即处理)", "red"),
                "A": ("A (中/近期处理)", "orange"),
                "B": ("B (低/可后置)", "green"),
            }
            if normalized not in mapping:
                return row
            label, style_key = mapping[normalized]
            new_td = _wrap_td_inner(target_td, label, style_key)
            return row.replace(target_td, new_td, 1)

        processed = re.sub(r"<tr>.*?</tr>", _row_repl, table_html, flags=re.S | re.I)
        if is_risk and not risk_legend_inserted:
            risk_legend_inserted = True
            return processed + risk_legend_html
        return processed

    report_html = re.sub(
        r"<table[^>]*>.*?</table>",
        lambda m: _process_table(m.group(0)),
        report_html,
        flags=re.S | re.I,
    )

    summary = export_data.get("summary", {}) or {}
    avg_diff_info = summarize_average_difficulty(summary.get("difficulty_histogram", {}) or {})
    avg_difficulty = f"{float(avg_diff_info['average_value']):.1f}"
    detail_fetch_overview = build_detail_fetch_overview(export_data.get("detail_fetch_stats", {}) or {})
    top_tag = "暂无"
    top_tags = summary.get("top_algorithm_tags", []) or summary.get("top_tags", []) or []
    if top_tags:
        top_tag = str(top_tags[0].get("name") or top_tags[0].get("id"))

    html_dir = Path(html_path).resolve().parent

    def _chart_src(value: str) -> str:
        if not value:
            return ""
        if value.startswith("data:") or value.startswith("file:///") or value.startswith("http://") or value.startswith("https://"):
            return value
        p = Path(value)
        if not p.exists():
            return value
        resolved = p.resolve()
        try:
            return resolved.relative_to(html_dir).as_posix()
        except ValueError:
            try:
                return resolved.relative_to(html_dir.parent).as_posix()
            except ValueError:
                return resolved.as_uri()

    chart_srcs = {k: _chart_src(v) for k, v in chart_paths.items()}
    # 模板用 ac_submit_distribution 别名
    if "ac_distribution" in chart_srcs and "ac_submit_distribution" not in chart_srcs:
        chart_srcs["ac_submit_distribution"] = chart_srcs["ac_distribution"]

    env = Environment(loader=FileSystemLoader(str(_ROOT.parent)))
    template = env.get_template("report_template.html")
    rendered = template.render(
        export_data=export_data,
        report_html=report_html,
        chart_paths=chart_srcs,
        avg_difficulty=avg_difficulty,
        avg_difficulty_label=str(avg_diff_info["label"]),
        avg_difficulty_color=str(avg_diff_info["color"]),
        avg_difficulty_text_color=str(avg_diff_info["text_color"]),
        detail_fetch_overview=detail_fetch_overview,
        top_tag=top_tag,
    )

    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    if not export_pdf:
        return

    temp_pdf = f"{pdf_path}.tmp"
    try:
        if os.path.exists(temp_pdf):
            os.remove(temp_pdf)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            file_url = f"file:///{os.path.abspath(html_path).replace(os.sep, '/')}"
            page.goto(file_url)
            page.wait_for_load_state("networkidle")
            page.pdf(
                path=temp_pdf,
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            browser.close()
        os.replace(temp_pdf, pdf_path)
    except Exception as e:
        if os.path.exists(temp_pdf):
            try:
                os.remove(temp_pdf)
            except OSError:
                pass
        raise RuntimeError(
            f"PDF 导出失败 (Playwright 错误), 请确保已运行 `playwright install chromium`。\n错误详情: {e}"
        ) from e


# ═══════════════════════════════════════════════════════════════════════
#  顶层入口
# ═══════════════════════════════════════════════════════════════════════

def generate_report_from_export_data(
    export_data: dict,
    api_key: str,
    *,
    base_url: Optional[str] = None,
    model_name: str = "gpt-4o-mini",
    output_dir: str = ".",
    md_filename: str = DEFAULT_REPORT_MD,
    html_filename: str = DEFAULT_REPORT_HTML,
    pdf_filename: str = DEFAULT_REPORT_PDF,
    assets_dirname: str = DEFAULT_ASSETS_DIR,
    export_pdf: bool = True,
    on_progress: Optional[Callable[[str, str, str], None]] = None,
) -> dict[str, str]:
    """从 export_data 字典生成完整报告 (md + html + pdf + charts)。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    assets_dir = out / assets_dirname
    md_path = out / md_filename
    html_path = out / html_filename
    pdf_path = out / pdf_filename

    def _p(stage: str, key: str = "", message: str = "") -> None:
        if on_progress:
            try:
                on_progress(stage, key, message)
            except Exception:
                pass

    _p("charts", "", "生成图表")
    chart_paths = generate_chart_images(export_data, str(assets_dir))

    _p("ai", "", "调用 LLM 生成 AI 报告")
    if md_path.exists():
        md_path.unlink()
    report_md = generate_ai_report(
        export_data,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        output_path=str(md_path),
        on_progress=lambda stage, msg: _p(stage, "ai", msg),
    )

    _p("normalize", "", "归一化 markdown (注入可信数据块)")
    report_md = normalize_report_markdown(report_md, export_data)
    md_path.write_text(report_md, encoding="utf-8")

    _p("html", "", "渲染 HTML")
    build_html_and_pdf(
        report_md,
        export_data,
        str(html_path),
        str(pdf_path),
        chart_paths,
        export_pdf=export_pdf,
    )

    result = {
        "md": str(md_path),
        "html": str(html_path),
        "assets_dir": str(assets_dir),
    }
    if export_pdf:
        result["pdf"] = str(pdf_path)
    return result


def generate_report_from_zip(
    zip_path: str,
    api_key: str,
    *,
    base_url: Optional[str] = None,
    model_name: str = "gpt-4o-mini",
    output_dir: str = ".",
    **kwargs,
) -> dict[str, str]:
    """从 ZIP 直接生成报告 (便捷入口)。"""
    from .bundle_loader import load_zip
    bundle = load_zip(zip_path)
    out = Path(output_dir)
    uid = bundle.luogu_uid or "user"
    safe_uid = re.sub(r"[^0-9A-Za-z_-]", "_", uid) or "user"
    default_name = f"luogu_coach_report_{safe_uid}"
    kwargs.setdefault("md_filename", f"{default_name}.md")
    kwargs.setdefault("html_filename", f"{default_name}.html")
    kwargs.setdefault("pdf_filename", f"{default_name}.pdf")
    return generate_report_from_export_data(
        bundle.export_data,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        output_dir=output_dir,
        **kwargs,
    )


__all__ = [
    "DIFFICULTY_NAME_MAP", "DIFFICULTY_COLOR_MAP", "DIFFICULTY_TEXT_COLOR_MAP",
    "TAG_CHART_PALETTE", "DIAGNOSTIC_FRAMEWORK",
    "DEFAULT_REPORT_MD", "DEFAULT_REPORT_HTML", "DEFAULT_REPORT_PDF", "DEFAULT_ASSETS_DIR",
    "AI_GENERATION_MAX_RETRIES", "AI_GENERATION_RETRY_SLEEP_SECONDS",
    "find_chinese_font_path", "configure_matplotlib_font", "register_pdf_font",
    "ensure_dir",
    "summarize_average_difficulty", "render_star_rating_html",
    "build_detail_fetch_overview", "summarize_detail_fetch_stats",
    "compute_ability_scores", "repair_behavior_analysis_from_items",
    "build_trusted_data_summary_md", "normalize_report_markdown",
    "generate_chart_images", "build_html_and_pdf",
    "generate_ai_report",
    "generate_report_from_export_data", "generate_report_from_zip",
]

