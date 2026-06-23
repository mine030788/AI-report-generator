"""cli.py - 命令行入口

子命令:
  generate  从 ZIP 生成报告
  load      解析 ZIP 并打印摘要
  web       启动 Web 界面 (默认)
  info      打印环境信息
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def cmd_generate(args) -> int:
    if not args.zip:
        print("❌ 请指定 --zip <path>")
        return 2
    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.exists():
        print(f"❌ ZIP 不存在: {zip_path}")
        return 2

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("❌ 需要 API Key, 通过 --api-key 或环境变量 OPENAI_API_KEY 提供")
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    from .evaluator import generate_report_from_zip
    print(f"🚀 开始生成报告")
    print(f"   ZIP: {zip_path}")
    print(f"   输出: {out_dir}")
    print(f"   模型: {args.model or 'gpt-4o-mini'}")
    print(f"   PDF: {args.pdf}")

    def _on_progress(stage: str, key: str, message: str) -> None:
        prefix = {"done": "✅", "error": "❌", "ai": "🤖", "charts": "📊", "html": "🎨"}.get(stage, "•")
        print(f"  {prefix} [{stage}] {message}", flush=True)

    try:
        result = generate_report_from_zip(
            str(zip_path),
            api_key=api_key,
            base_url=args.base_url or None,
            model_name=args.model or "gpt-4o-mini",
            output_dir=str(out_dir),
            export_pdf=args.pdf,
            on_progress=_on_progress,
        )
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        return 1

    print()
    print("✅ 报告生成完成:")
    for k, v in result.items():
        print(f"  - {k}: {v}")
    return 0


def cmd_load(args) -> int:
    if not args.zip:
        print("❌ 请指定 --zip <path>")
        return 2
    from .bundle_loader import load_zip, BundleLoadError
    try:
        bundle = load_zip(args.zip)
    except BundleLoadError as e:
        print(f"❌ {e}")
        return 1
    print(f"✅ 解析成功:")
    print(f"   {bundle.summary_line()}")
    print(f"   passed_items: {len(bundle.passed_items)}")
    print(f"   failed_items: {len(bundle.failed_items)}")
    student = bundle.export_data.get("student_info") or {}
    if student:
        print(f"   student_info: {student}")
    return 0


def cmd_web(args) -> int:
    from .web import main as web_main
    if args.host:
        os.environ["LRG_HOST"] = args.host
    if args.port:
        os.environ["LRG_PORT"] = str(args.port)
    if args.upload_dir:
        os.environ["LRG_UPLOAD_DIR"] = args.upload_dir
    if args.output_dir:
        os.environ["LRG_OUTPUT_DIR"] = args.output_dir
    if args.debug:
        os.environ["LRG_DEBUG"] = "1"
    web_main()
    return 0


def cmd_info(_args) -> int:
    print("luogu-report-generator  环境信息")
    print(f"  Python: {sys.version.split()[0]}")
    try:
        from . import __version__
        print(f"  版本: {__version__}")
    except Exception:
        pass
    try:
        import openai
        print(f"  openai: {openai.__version__}")
    except Exception:
        print("  openai: NOT INSTALLED")
    try:
        import playwright
        print(f"  playwright: {playwright.__version__}")
    except Exception:
        print("  playwright: NOT INSTALLED")
    try:
        import matplotlib
        print(f"  matplotlib: {matplotlib.__version__}")
    except Exception:
        print("  matplotlib: NOT INSTALLED")
    try:
        import jinja2
        print(f"  jinja2: {jinja2.__version__}")
    except Exception:
        print("  jinja2: NOT INSTALLED")
    try:
        import reportlab
        print(f"  reportlab: {reportlab.Version}")
    except Exception:
        print("  reportlab: NOT INSTALLED")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="luogu-report",
        description="洛谷 AI 报告生成器 (读取 luogu-toolkit 打出的 ZIP, 生成结构化 AI 报告)",
    )
    sub = p.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="从 ZIP 生成报告")
    p_gen.add_argument("--zip", required=True, help="luogu-toolkit 打出的 ZIP 路径")
    p_gen.add_argument("--api-key", default="", help="OpenAI 兼容 API Key (默认读 OPENAI_API_KEY)")
    p_gen.add_argument("--base-url", default="", help="OpenAI 兼容 Base URL (可选)")
    p_gen.add_argument("--model", default="", help="模型名 (默认 gpt-4o-mini)")
    p_gen.add_argument("--out", default="./out", help="输出目录")
    p_gen.add_argument("--pdf", action="store_true", default=True, help="同时导出 PDF (默认开启)")
    p_gen.add_argument("--no-pdf", dest="pdf", action="store_false", help="不导出 PDF")
    p_gen.set_defaults(func=cmd_generate)

    p_load = sub.add_parser("load", help="解析 ZIP 并打印摘要")
    p_load.add_argument("--zip", required=True, help="ZIP 路径")
    p_load.set_defaults(func=cmd_load)

    p_web = sub.add_parser("web", help="启动 Web 界面")
    p_web.add_argument("--host", default="", help="绑定 host (默认 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=0, help="端口 (默认 8765)")
    p_web.add_argument("--upload-dir", default="", help="上传临时目录")
    p_web.add_argument("--output-dir", default="", help="输出目录")
    p_web.add_argument("--debug", action="store_true", help="Flask debug 模式")
    p_web.set_defaults(func=cmd_web)

    p_info = sub.add_parser("info", help="打印环境信息")
    p_info.set_defaults(func=cmd_info)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # 默认: 显示帮助
        parser.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
