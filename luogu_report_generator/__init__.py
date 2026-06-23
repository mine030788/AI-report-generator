"""luogu_report_generator

报告生成器 (独立于 luogu-toolkit 数据抓取)。

核心模块:
  - bundle_loader:   解析 luogu-toolkit 打出的 ZIP 数据包
  - evaluator:       调用 LLM 生成 AI 报告, 渲染 HTML + 导出 PDF
  - behavior_analyzer: 提交行为分析 (性格画像, 六维评分, AC 模式)
  - code_analyzer:   代码风格静态分析
  - syllabus_matcher: 大纲知识点对标
  - submission_evolution: 多版代码 diff (代码考古)
  - gesp_estimator:  GESP 等级预估

典型用法:
    from luogu_report_generator.evaluator import generate_report_from_zip
    generate_report_from_zip(
        "uploaded.zip",
        api_key="sk-...",
        output_dir="./out",
    )
"""
from .bundle_loader import (
    BundleLoadError,
    ReportBundle,
    load_zip,
    load_zip_bytes,
)
from .evaluator import (
    generate_report_from_export_data,
    generate_report_from_zip,
)

__version__ = "1.0.0"

__all__ = [
    "BundleLoadError",
    "ReportBundle",
    "load_zip",
    "load_zip_bytes",
    "generate_report_from_export_data",
    "generate_report_from_zip",
    "__version__",
]
