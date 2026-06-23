"""web.py - 洛谷报告生成器 Web 界面 (Flask)

功能:
  - GET  /             上传页面 (拖拽 ZIP)
  - POST /api/upload   接收上传 ZIP, 异步生成报告
  - GET  /api/status/<job_id>  查询生成进度
  - GET  /api/download/<job_id>/<filename>  下载生成的文件
  - GET  /health       健康检查

启动:
  python -m luogu_report_generator.web
  # 或
  flask --app luogu_report_generator.web run
"""
from __future__ import annotations

import os
import sys
import json
import time
import uuid
import shutil
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import (
    Flask, request, jsonify, send_file, send_from_directory,
    render_template_string, abort,
)

# 让 bundle_loader 路径可解析
_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE.parent / "templates"
_STATIC = _HERE.parent / "static"

app = Flask(
    __name__,
    template_folder=str(_TEMPLATES) if _TEMPLATES.exists() else None,
    static_folder=str(_STATIC) if _STATIC.exists() else None,
)

# 任务状态 (内存, 单实例)
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

UPLOAD_DIR = Path(os.environ.get("LRG_UPLOAD_DIR", "./lrg_uploads"))
OUTPUT_DIR = Path(os.environ.get("LRG_OUTPUT_DIR", "./lrg_outputs"))
ALLOWED_API_KEY = os.environ.get("LRG_API_KEY")  # 可选, 限制可用的 API Key (空则不限制)
DEFAULT_MODEL = os.environ.get("LRG_DEFAULT_MODEL", "gpt-4o-mini")
DEFAULT_BASE_URL = os.environ.get("LRG_DEFAULT_BASE_URL", "")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("lrg.web")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ═══════════════════════════════════════════════════════════════════════
#  进度回调
# ═══════════════════════════════════════════════════════════════════════

def _make_progress_fn(job_id: str):
    def _progress(stage: str, key: str, message: str) -> None:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return
            entry = {"stage": stage, "key": key, "message": message, "ts": time.time()}
            job.setdefault("progress", []).append(entry)
            if job["progress"][-100:] != job["progress"]:
                job["progress"] = job["progress"][-100:]
            job["last_update"] = time.time()
            if stage == "done":
                job["status"] = "success"
            elif stage == "error":
                job["status"] = "error"
            else:
                job["status"] = "running"
            logger.info("[%s] %s/%s: %s", job_id, stage, key, message)
    return _progress


# ═══════════════════════════════════════════════════════════════════════
#  报告生成 (后台线程)
# ═══════════════════════════════════════════════════════════════════════

def _run_generation(
    job_id: str,
    bundle,
    api_key: str,
    base_url: str | None,
    model_name: str,
    output_dir: Path,
    export_pdf: bool,
):
    """后台线程: 调用 evaluator 生成报告。"""
    progress = _make_progress_fn(job_id)
    try:
        progress("load", "", f"已加载 ZIP: {bundle.summary_line()}")
        progress("start", "", "开始生成报告...")
        from .evaluator import generate_report_from_export_data
        result = generate_report_from_export_data(
            bundle.export_data,
            api_key=api_key,
            base_url=base_url or None,
            model_name=model_name,
            output_dir=str(output_dir),
            md_filename="report.md",
            html_filename="report.html",
            pdf_filename="report.pdf",
            assets_dirname="assets",
            export_pdf=export_pdf,
            on_progress=lambda s, k, m: progress(s, k, m),
        )
        with _JOBS_LOCK:
            _JOBS[job_id]["result"] = result
            _JOBS[job_id]["result_short"] = {
                k: os.path.basename(v) if isinstance(v, str) else v
                for k, v in result.items()
            }
        progress("done", "", "报告生成完成")
    except Exception as e:
        logger.exception("[%s] 生成失败", job_id)
        progress("error", "", f"生成失败: {e}")


# ═══════════════════════════════════════════════════════════════════════
#  路由
# ═══════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return jsonify({"ok": True, "ts": time.time()})


@app.get("/")
def index():
    """上传页 (内联 HTML, 无需模板文件)。"""
    return render_template_string(INDEX_HTML, default_model=DEFAULT_MODEL, default_base_url=DEFAULT_BASE_URL)


@app.post("/api/upload")
def api_upload():
    if "zip" not in request.files:
        return jsonify({"ok": False, "error": "未收到 ZIP 文件 (字段名需为 'zip')"}), 400
    f = request.files["zip"]
    if not f.filename:
        return jsonify({"ok": False, "error": "文件名为空"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "文件后缀必须为 .zip"}), 400

    api_key = (request.form.get("api_key") or request.headers.get("X-API-Key") or "").strip()
    if ALLOWED_API_KEY and api_key and api_key != ALLOWED_API_KEY:
        return jsonify({"ok": False, "error": "API Key 不正确"}), 403
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "未提供 API Key (表单 api_key 或环境 OPENAI_API_KEY)"}), 400

    base_url = (request.form.get("base_url") or "").strip() or DEFAULT_BASE_URL or None
    model_name = (request.form.get("model") or DEFAULT_MODEL).strip() or "gpt-4o-mini"
    export_pdf = (request.form.get("pdf", "1") not in ("0", "false", "no"))

    # 保存上传
    job_id = uuid.uuid4().hex[:16]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    zip_path = job_dir / "upload.zip"
    f.save(str(zip_path))
    size = zip_path.stat().st_size
    if size == 0:
        return jsonify({"ok": False, "error": "上传文件为空"}), 400

    # 解析 ZIP (失败立即返回)
    from .bundle_loader import load_zip, BundleLoadError
    try:
        bundle = load_zip(zip_path)
    except BundleLoadError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"ok": False, "error": f"ZIP 解析失败: {e}"}), 400
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("[%s] ZIP 解析失败", job_id)
        return jsonify({"ok": False, "error": f"ZIP 解析异常: {e}"}), 400

    # 准备输出目录
    out_dir = OUTPUT_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 创建任务
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "last_update": time.time(),
            "bundle_summary": bundle.summary_line(),
            "solved_count": bundle.solved_count,
            "failed_count": bundle.failed_count,
            "luogu_uid": bundle.luogu_uid,
            "username": bundle.username,
            "progress": [],
            "result": None,
            "result_short": None,
            "error": None,
            "output_dir": str(out_dir),
        }

    # 启动后台线程
    th = threading.Thread(
        target=_run_generation,
        args=(job_id, bundle, api_key, base_url, model_name, out_dir, export_pdf),
        daemon=True,
    )
    th.start()

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": "queued",
        "bundle": {
            "luogu_uid": bundle.luogu_uid,
            "username": bundle.username,
            "name": bundle.name,
            "solved_count": bundle.solved_count,
            "failed_count": bundle.failed_count,
            "schema_version": bundle.schema_version,
            "generated_at": bundle.generated_at,
        },
        "status_url": f"/api/status/{job_id}",
    })


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    progress = job.get("progress", [])
    last = progress[-10:] if progress else []
    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": job["status"],
        "created_at": job["created_at"],
        "last_update": job["last_update"],
        "last_progress": last,
        "result": job.get("result_short"),
        "error": job.get("error"),
    })


@app.get("/api/download/<job_id>/<path:filename>")
def api_download(job_id: str, filename: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        abort(404)
    if job["status"] not in ("success",):
        return jsonify({"ok": False, "error": f"任务未完成: {job['status']}"}), 400
    out_dir = Path(job["output_dir"])
    fp = (out_dir / filename).resolve()
    if not str(fp).startswith(str(out_dir.resolve())):
        abort(403)
    if not fp.exists():
        abort(404)
    return send_file(str(fp), as_attachment=True, download_name=filename)


@app.get("/api/result/<job_id>")
def api_result(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    if job["status"] != "success":
        return jsonify({"ok": False, "error": f"任务未完成: {job['status']}"}), 400
    return jsonify({
        "ok": True,
        "result": job.get("result_short"),
        "downloads": [
            {"name": "report.md", "url": f"/api/download/{job_id}/report.md"},
            {"name": "report.html", "url": f"/api/download/{job_id}/report.html"},
            {"name": "assets/", "url": f"/api/download/{job_id}/assets/difficulty_histogram.png"},
        ],
    })


# ═══════════════════════════════════════════════════════════════════════
#  内联 HTML (上传页)
# ═══════════════════════════════════════════════════════════════════════

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>洛谷 AI 报告生成器</title>
<style>
:root { --primary: #2563EB; --primary-dark: #1E40AF; --bg: #F9FAFB; --card: #FFFFFF; --border: #E5E7EB; --text: #111827; --text-light: #6B7280; --success: #16A34A; --error: #DC2626; --warning: #F59E0B; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }
.container { max-width: 880px; margin: 0 auto; padding: 32px 24px; }
header { text-align: center; margin-bottom: 32px; }
h1 { margin: 0 0 8px; font-size: 28px; color: var(--primary-dark); }
.subtitle { color: var(--text-light); font-size: 14px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.card h2 { margin: 0 0 16px; font-size: 18px; color: var(--primary-dark); }
.dropzone { border: 2px dashed var(--border); border-radius: 8px; padding: 48px 24px; text-align: center; cursor: pointer; transition: all 0.2s; background: #FAFBFC; }
.dropzone:hover, .dropzone.dragover { border-color: var(--primary); background: #EFF6FF; }
.dropzone p { margin: 8px 0; }
.dropzone .hint { color: var(--text-light); font-size: 13px; }
.row { display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.field { flex: 1; min-width: 200px; }
label { display: block; font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
input[type=text], input[type=password] { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; font-family: inherit; }
input[type=text]:focus, input[type=password]:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
button { background: var(--primary); color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 15px; font-weight: 600; cursor: pointer; font-family: inherit; }
button:hover:not(:disabled) { background: var(--primary-dark); }
button:disabled { background: #94A3B8; cursor: not-allowed; }
.checkbox { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.checkbox input { width: 18px; height: 18px; }
.progress { background: #F3F4F6; border-radius: 6px; overflow: hidden; height: 8px; margin: 16px 0; }
.progress-bar { background: var(--primary); height: 100%; transition: width 0.3s; width: 0; }
.log { background: #1F2937; color: #E5E7EB; padding: 16px; border-radius: 6px; max-height: 280px; overflow-y: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; line-height: 1.5; }
.log .err { color: #FCA5A5; }
.log .ok { color: #86EFAC; }
.log .warn { color: #FCD34D; }
.log .info { color: #93C5FD; }
.downloads { display: grid; gap: 8px; margin-top: 16px; }
.downloads a { display: block; padding: 10px 14px; background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 6px; color: var(--primary-dark); text-decoration: none; font-weight: 600; }
.downloads a:hover { background: #DBEAFE; }
.bundle-info { background: #F0F9FF; border: 1px solid #BAE6FD; border-radius: 8px; padding: 12px 16px; margin: 16px 0; font-size: 14px; }
footer { text-align: center; color: var(--text-light); font-size: 12px; margin-top: 32px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>洛谷 AI 报告生成器</h1>
    <p class="subtitle">上传 luogu-toolkit 打出的 ZIP 数据包 → AI 生成结构化测评报告 (HTML + PDF)</p>
  </header>

  <div class="card">
    <h2>1. 选择 ZIP 文件</h2>
    <div class="dropzone" id="dropzone">
      <p><strong>点击选择 / 拖拽 ZIP 文件</strong></p>
      <p class="hint">从 luogu-toolkit 的 bundle 功能下载的数据包</p>
      <p class="hint" id="file-info"></p>
    </div>
    <input type="file" id="fileInput" accept=".zip" style="display:none">
  </div>

  <div class="card">
    <h2>2. 配置 LLM</h2>
    <div class="row">
      <div class="field">
        <label>API Key *</label>
        <input type="password" id="apiKey" placeholder="sk-... (或留空使用环境变量 OPENAI_API_KEY)">
      </div>
    </div>
    <div class="row">
      <div class="field">
        <label>Base URL (可选)</label>
        <input type="text" id="baseUrl" placeholder="https://api.openai.com/v1" value="{{ default_base_url }}">
      </div>
      <div class="field">
        <label>模型</label>
        <input type="text" id="model" value="{{ default_model }}">
      </div>
    </div>
    <div class="row">
      <label class="checkbox">
        <input type="checkbox" id="exportPdf" checked> 同时导出 PDF (需要 playwright)
      </label>
    </div>
  </div>

  <div class="card">
    <button id="submitBtn" disabled>开始生成报告</button>
    <div id="bundleInfo" class="bundle-info" style="display:none"></div>
    <div class="progress" id="progressBox" style="display:none"><div class="progress-bar" id="progressBar"></div></div>
    <div class="log" id="log" style="display:none"></div>
    <div class="downloads" id="downloads" style="display:none"></div>
  </div>

  <footer>
    Powered by luogu-report-generator · 数据源: luogu-toolkit (GitHub)
  </footer>
</div>

<script>
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const fileInfo = document.getElementById('file-info');
const submitBtn = document.getElementById('submitBtn');
const log = document.getElementById('log');
const progressBox = document.getElementById('progressBox');
const progressBar = document.getElementById('progressBar');
const bundleInfo = document.getElementById('bundleInfo');
const downloadsEl = document.getElementById('downloads');

let selectedFile = null;

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) {
    handleFile(e.dataTransfer.files[0]);
  }
});
fileInput.addEventListener('change', (e) => {
  if (e.target.files.length) handleFile(e.target.files[0]);
});

function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    fileInfo.innerHTML = '<span style="color:var(--error)">❌ 文件必须是 .zip</span>';
    selectedFile = null;
    submitBtn.disabled = true;
    return;
  }
  selectedFile = file;
  const sizeKB = (file.size / 1024).toFixed(1);
  fileInfo.innerHTML = `✅ ${file.name} (${sizeKB} KB)`;
  submitBtn.disabled = false;
}

function appendLog(msg, cls='info') {
  log.style.display = 'block';
  const line = document.createElement('div');
  line.className = cls;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function setProgress(pct) {
  progressBox.style.display = 'block';
  progressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
}

submitBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  const apiKey = document.getElementById('apiKey').value.trim();
  if (!apiKey && !confirm('未填写 API Key, 是否继续? (将使用环境变量 OPENAI_API_KEY)')) {
    return;
  }
  submitBtn.disabled = true;
  submitBtn.textContent = '上传中...';
  bundleInfo.style.display = 'none';
  downloadsEl.style.display = 'none';
  log.innerHTML = '';
  setProgress(0);

  const form = new FormData();
  form.append('zip', selectedFile);
  form.append('api_key', apiKey);
  form.append('base_url', document.getElementById('baseUrl').value.trim());
  form.append('model', document.getElementById('model').value.trim() || 'gpt-4o-mini');
  form.append('pdf', document.getElementById('exportPdf').checked ? '1' : '0');

  try {
    appendLog('上传 ZIP...', 'info');
    const res = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      appendLog('上传失败: ' + (data.error || res.statusText), 'err');
      submitBtn.disabled = false;
      submitBtn.textContent = '开始生成报告';
      return;
    }
    appendLog('已创建任务: ' + data.job_id, 'ok');
    if (data.bundle) {
      bundleInfo.style.display = 'block';
      bundleInfo.innerHTML = `📦 <strong>${data.bundle.name || data.bundle.username}</strong> (uid=${data.bundle.luogu_uid}) · 通过 ${data.bundle.solved_count} / 未通过 ${data.bundle.failed_count} · 生成于 ${data.bundle.generated_at}`;
    }
    submitBtn.textContent = '生成中...';
    pollStatus(data.job_id);
  } catch (e) {
    appendLog('网络错误: ' + e, 'err');
    submitBtn.disabled = false;
    submitBtn.textContent = '开始生成报告';
  }
});

let pollTimer = null;
async function pollStatus(jobId) {
  let stage = 0;
  const stages = ['load', 'start', 'charts', 'ai', 'normalize', 'html', 'done'];
  try {
    const res = await fetch(`/api/status/${jobId}`);
    const data = await res.json();
    if (!data.ok) { appendLog('查询失败: ' + data.error, 'err'); return; }
    const status = data.status;
    setProgress(Math.min(95, (stages.indexOf(data.last_progress[data.last_progress.length-1]?.stage) + 1) / stages.length * 100));
    data.last_progress.forEach(p => {
      const tag = p.stage === 'error' ? 'err' : (p.stage === 'done' ? 'ok' : 'info');
      appendLog(`[${p.stage}] ${p.message}`, tag);
    });
    if (status === 'success') {
      appendLog('生成完成 ✅', 'ok');
      setProgress(100);
      submitBtn.disabled = false;
      submitBtn.textContent = '重新生成';
      const r = await fetch(`/api/result/${jobId}`);
      const rd = await r.json();
      downloadsEl.style.display = 'block';
      downloadsEl.innerHTML = '';
      rd.downloads.forEach(d => {
        const a = document.createElement('a');
        a.href = d.url;
        a.target = '_blank';
        a.textContent = '📥 ' + d.name;
        downloadsEl.appendChild(a);
      });
      return;
    }
    if (status === 'error') {
      appendLog('任务失败 ❌', 'err');
      submitBtn.disabled = false;
      submitBtn.textContent = '重试';
      return;
    }
    pollTimer = setTimeout(() => pollStatus(jobId), 1500);
  } catch (e) {
    appendLog('轮询错误: ' + e, 'err');
  }
}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════════════════════════════════

def main():
    host = os.environ.get("LRG_HOST", "127.0.0.1")
    port = int(os.environ.get("LRG_PORT", "8765"))
    debug = os.environ.get("LRG_DEBUG", "0").lower() in ("1", "true", "yes")
    print(f"🚀 洛谷报告生成器 Web 启动: http://{host}:{port}")
    print(f"   上传目录: {UPLOAD_DIR.resolve()}")
    print(f"   输出目录: {OUTPUT_DIR.resolve()}")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
