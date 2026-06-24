# luogu-report-generator

洛谷刷题数据 **AI 报告生成器** (独立项目 B)。

读取 [luogu-toolkit](https://github.com/mine030788/luogu-SRC-tool) 打出的 ZIP 数据包, 调用 LLM 生成结构化 AI 测评报告 (Markdown + HTML + PDF)。

> 项目 A (luogu-toolkit) 负责: 抓取数据 → 打 ZIP  
> 项目 B (luogu-report-generator, 本仓库) 负责: 解 ZIP → AI 报告 → 可视化 → 导出

两个项目完全解耦, 数据交换靠标准化 ZIP schema (`schema_version=1`)。

---

## 快速开始

### 1. 安装

```bash
git clone <your-repo> luogu-report-generator
cd luogu-report-generator
pip install -r requirements.txt
# 如果要导出 PDF, 还需要安装 Playwright 浏览器
playwright install chromium
```

### 2. 命令行: 从 ZIP 生成报告

```bash
# 通过 luogu-toolkit 先打个 ZIP
# (在 luogu-toolkit 目录下)
python -m luogu_toolkit.bundle --cookie cookies.json --output ./bundles
# 假设产出 bundles/luogu-report-12345-20250101-120000.zip

# 然后在本项目 (luogu-report-generator) 下
export OPENAI_API_KEY=sk-...
python -m luogu_report_generator generate \
  --zip ../luogu-toolkit/bundles/luogu-report-12345-20250101-120000.zip \
  --out ./out \
  --model gpt-4o-mini
```

产物:
- `out/luogu_coach_report_<uid>.md`   - AI 生成的 Markdown
- `out/luogu_coach_report_<uid>.html` - 渲染好的 HTML (含图表)
- `out/luogu_coach_report_<uid>.pdf`  - 导出 PDF (需要 playwright)
- `out/assets/*.png`                   - 雷达图, 分布图等

### 3. Web 界面: 拖拽上传

```bash
python -m luogu_report_generator web --port 8765
```

打开浏览器访问 <http://127.0.0.1:8765/>, 拖拽 ZIP → 配置 API Key → 提交 → 等几十秒即可下载报告。

也可通过 `LRG_*` 环境变量配置:

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `OPENAI_API_KEY` | - | 兜底 API Key (前端没填时使用) |
| `LRG_HOST` | `127.0.0.1` | 绑定 host |
| `LRG_PORT` | `8765` | 端口 |
| `LRG_UPLOAD_DIR` | `./lrg_uploads` | 上传临时目录 |
| `LRG_OUTPUT_DIR` | `./lrg_outputs` | 报告输出根目录 |
| `LRG_DEFAULT_MODEL` | `gpt-4o-mini` | Web 默认模型 |
| `LRG_DEFAULT_BASE_URL` | `""` | Web 默认 Base URL |
| `LRG_DEBUG` | `0` | Flask debug |

---

## ZIP 数据格式 (`schema_version=1`)

ZIP 由 luogu-toolkit 的 `bundle.build_report_zip()` 产生, 报告端会校验 schema, 不匹配会报错。

```
<uid>-<ts>.zip
├─ manifest.json        # 元信息: schema_version, luogu_uid, username, solved_count, ...
├─ export_data.json     # 全部抓取的数据 + 行为/六维/性格/大纲对标
├─ items/passed/P1000.json   # 每道已通过题目的完整记录
├─ items/passed/P1001.json
├─ items/failed/P2000.json
└─ ...
```

`export_data.json` 主要字段:
- `student_info` (name, school, grade, eval_time, luogu_uid)
- `solved_count`, `failed_count`
- `summary` (avg_difficulty, top_tag, difficulty_histogram, top_algorithm_tags)
- `passed_items`, `failed_items` (题目列表)
- `records` (原始提交记录)
- `detail_fetch_stats` (源码抓取情况)
- `behavior_analysis` (行为分析, 项目 A 已算好, 报告端可复算)
- `syllabus_evaluation` (大纲知识点对标)
- `six_dimension_scores` (六维能力评分)
- `submission_evolution` (代码考古)
- `tags.by_id` (标签字典)

---

## 作为库使用

```python
from luogu_report_generator.bundle_loader import load_zip
from luogu_report_generator.evaluator import generate_report_from_export_data

bundle = load_zip("uploaded.zip")
result = generate_report_from_export_data(
    bundle.export_data,
    api_key="sk-...",
    model_name="gpt-4o-mini",
    output_dir="./out",
    export_pdf=True,
    on_progress=lambda stage, key, msg: print(f"[{stage}] {msg}"),
)
print(result)
# {"md": "out/luogu_coach_report.md", "html": "out/luogu_coach_report.html",
#  "pdf": "out/luogu_coach_report.pdf", "assets_dir": "out/assets"}
```

## API 速查

| 模块 | 主要函数 | 说明 |
| --- | --- | --- |
| `bundle_loader` | `load_zip`, `load_zip_bytes` | 解析 ZIP, 返回 `ReportBundle` |
| `behavior_analyzer` | `analyze_submission_behavior`, `compute_personality_scores`, `compute_six_dimension_scores` | 行为/性格/六维 |
| `code_analyzer` | `analyze_code_style` | 代码风格静态分析 |
| `syllabus_matcher` | `evaluate_all_topics`, `format_syllabus_report` | 知识点对标 |
| `submission_evolution` | `analyze_submission_evolution`, `evolution_to_prompt_block` | 代码考古 |
| `evaluator` | `generate_report_from_zip`, `generate_report_from_export_data` | 报告生成主入口 |
| `web` | `app` (Flask) | Web 上传 + 异步生成 |

## CLI 子命令

```bash
python -m luogu_report_generator generate --zip <p> --api-key sk-... --out ./out
python -m luogu_report_generator load     --zip <p>     # 解析并打印摘要
python -m luogu_report_generator web      --port 8765   # 启动 Web
python -m luogu_report_generator info                  # 打印环境信息
```

## 与 luogu-toolkit 协作

```
┌─────────────────────┐                  ┌──────────────────────────┐
│  luogu-toolkit (A)  │    ZIP schema v1  │ luogu-report-generator(B)│
│                     │ ───────────────►  │                          │
│  bundle.build_zip() │  manifest.json +  │  bundle_loader.load_zip()│
│                     │  export_data.json │  evaluator.generate()    │
│  Web / CLI / API    │  + items/*.json   │  Web / CLI / API         │
└─────────────────────┘                  └──────────────────────────┘
```

## 常见问题

**Q: ZIP 提示 `schema_version 不匹配`?**  
A: 升级项目 B 到最新版, 或在项目 A 用匹配的 toolkit 版本重新打 ZIP。

**Q: 怎么切换 LLM (OpenAI / DeepSeek / 智谱 / Ollama)?**  
A: 用 `--base-url` + `--model` 参数, 任何 OpenAI 兼容 API 都可:
```bash
python -m luogu_report_generator generate \
  --zip foo.zip \
  --base-url https://api.deepseek.com/v1 \
  --model deepseek-chat
```

**Q: 怎么只生成 HTML 不要 PDF?**  
A: `--no-pdf` 或 Web 页面取消勾选 "同时导出 PDF"。

**Q: 字体 / 中文乱码?**  
A: `assets/fonts/LXGWWenKai-Regular.ttf` 已内置, `evaluator` 会自动加载; 也可放到 `~/.fonts/` 由 matplotlib 自行发现。

## 目录结构

```
luogu-report-generator/
├─ luogu_report_generator/
│   ├─ __init__.py
│   ├─ __main__.py
│   ├─ cli.py                  # argparse 子命令
│   ├─ web.py                  # Flask Web 应用
│   ├─ bundle_loader.py        # ZIP 解析
│   ├─ evaluator.py            # 报告生成核心 (AI + 图表 + HTML + PDF)
│   ├─ behavior_analyzer.py    # 行为/性格/六维
│   ├─ code_analyzer.py        # 代码风格
│   ├─ syllabus_matcher.py     # 大纲对标
│   └─ submission_evolution.py # 代码考古
├─ report_template.html        # Jinja2 HTML 模板
├─ assets/fonts/               # 中文字体 (LXGW WenKai)
├─ pyproject.toml
├─ requirements.txt
└─ README.md
```

## 社区 & 交流

- Telegram 群: <https://t.me/+Q4h6R9iM5F80NDEy>
- 项目 A: [luogu-toolkit](https://github.com/mine030788/luogu-SRC-tool)

## 许可

MIT
