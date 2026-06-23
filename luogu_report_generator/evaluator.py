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
    detail_fetch_stats = export_data.get("detail_fetch_stats", {}) or {}
    total = 0
    for level in range(1, 8):
        total += int(difficulty_histogram.get(str(level), difficulty_histogram.get(level, 0)))
    total = total or 1
    lines = [
        "## 数据校准与真实统计",
        f"- 报告生成时间：{eval_time or '未知'}",
        "",
        "### 难度分布（程序生成）",
        '<table><thead><tr><th>洛谷难度</th><th>题数</th><th>占比</th><th>分布图</th></tr></thead><tbody>',
    ]
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
            f"<tr><td>{badge}</td><td>{count}</td>"
            f"<td>{pct:.1f}%</td>"
            f"<td>{_render_progress_bar(pct, color)} <span style=\"margin-left:8px;\">{pct:.1f}%</span></td></tr>"
        )
    lines.append("</tbody></table>")
    lines.append("")
    lines.append("### 源码抓取概况（程序生成）")
    if detail_fetch_stats:
        lines.append(
            f"- 共 {detail_fetch_stats.get('total_items', 0)} 道题,"
            f" 已抓到源码 {detail_fetch_stats.get('source_code_success', 0)} 道,"
            f" 仅概要 {detail_fetch_stats.get('summary_only', 0)} 道,"
            f" 异常 {detail_fetch_stats.get('detail_errors', 0)} 道"
        )
        if detail_fetch_stats.get("blocker_reason"):
            lines.append(f"- 受限原因: {detail_fetch_stats['blocker_reason']}")
    else:
        lines.append("- 暂无数据")
    return "\n".join(lines)


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
    normalized = report_md
    normalized = remove_injected_trusted_block(normalized)
    if export_data is None:
        return normalized
    trusted_block = build_trusted_data_summary_md(export_data)
    heading_match = re.match(r"^(# .+\n+)", normalized)
    if heading_match:
        head = heading_match.group(1)
        tail = normalized[len(head):]
        return f"{head}{trusted_block}\n\n{tail}"
    return f"{trusted_block}\n\n{normalized}"


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

    prompt = f"""你是一位资深的信息学奥赛 (OI/ACM) 教练与代码评审, 擅长对 CSP-J/S / NOIP / NOI 阶段学员的洛谷刷题数据进行深度诊断。
请基于下方的"客观数据"生成一份结构化的 Markdown 报告, 严格按照"输出框架"中的 7 个章节顺序输出, 不要新增/删除/重排章节。

# 基本信息
{student_line}
报告生成时间: {current_time}
通过题数: {solved}, 未通过题数: {failed}

# 客观数据

## 难度分布
{json.dumps(summary.get("difficulty_histogram", {}), ensure_ascii=False)}

## Top 算法标签
{json.dumps(summary.get("top_algorithm_tags", []) or summary.get("top_tags", []), ensure_ascii=False, indent=2)}

## 提交行为分析摘要
{behavior_summary}

## 代码风格静态分析
{code_analysis_summary}

## 通过题源码样本
{''.join(passed_samples) if passed_samples else "(无源码样本)"}

## 未通过题源码样本
{''.join(failed_samples) if failed_samples else "(无失败样本)"}

## 大纲知识点对标摘要
{syllabus_summary}

## 六维评分
{six_dim_text or "(无六维数据)"}

## 代码考古 (多版 diff)
{evolution_prompt}

{DIAGNOSTIC_FRAMEWORK}

# 难度映射
{difficulty_guide}

# 2025 大纲原文 (供参考)
{syllabus_context}

# 输出框架 (严格按此 7 个章节, 不要增删)
# 洛谷 AI 测评报告

## 1. 总体诊断
(2-3 段, 先给一句话总评, 再展开: 当前能力阶段 / 优势 / 风险)

## 2. 能力块评估表
(用 markdown 表格, 列: 能力块 / 当前等级 / 证据 / 提升建议, 至少 6 行, 引用上面客观数据)

## 3. 知识点覆盖与缺口
(对比 2025 大纲, 用表格列出 已掌握 / 部分掌握 / 缺失 的知识点)

## 4. 代码风格与习惯观察
(基于源码样本, 引用具体代码片段, 谈命名 / 缩进 / 模块化 / 调试输出 / 模板代码 等)

## 5. 错题与未通过题分析
(逐题或归类, 给出"题面理解 → 赛时模型 → 错因 → 正解性质 → 改进路径"四段式)

## 6. 提交行为画像
(基于行为分析: 坚韧度 / 完美主义 / 冒险精神 / 自律性 / 调试耐心 / 作息规律 6 维)

## 7. 30 天提升路线图
(分 3 周 + 1 周, 给出具体可执行的学习 / 刷题 / 复盘任务)

### 7.5 提交代码考古
{evolution_prompt}

# 写作要求
1. 严格基于上面"客观数据"中的事实, 严禁编造题号、得分、源码片段。
2. 引用具体题目请用题号 (如 P1001) + 标题。
3. 表格用 markdown 表格语法, 报告会在 HTML 渲染阶段再加工。
4. 难度名称严格使用上面"难度映射"中的标准名称。
5. 不要输出 H1 标题 (大标题"洛谷 AI 测评报告"由模板生成); 直接从 H2 开始。
6. 整体语言简洁、专业、有教练味, 避免空话和套话。
"""
    return prompt


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

