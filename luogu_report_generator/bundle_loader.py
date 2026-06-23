"""bundle_loader.py - 解析 luogu-toolkit 打出的 ZIP, 还原成 export_data 字典。

设计目标:
  让 luogu-report-generator 完全独立于 luogu-toolkit 的运行期依赖 (pyLuogu / 登录态),
  只通过 ZIP 压缩包接收数据。本模块负责解析 ZIP, 校验 schema, 暴露 export_data。

ZIP schema (来自 luogu-toolkit.bundle):
  manifest.json           元信息 (uid, username, generated_at, 文件清单)
  export_data.json        完整 export_data 字典
  items/passed/P*.json    每道已通过的题
  items/failed/P*.json    每道失败/未通过的题

调用方式 (SDK):
    from luogu_report_generator.bundle_loader import load_zip
    bundle = load_zip("uploaded.zip")
    print(bundle.export_data["student_info"])
    print(bundle.passed_items)

调用方式 (CLI):
    luogu-report load uploaded.zip
"""
from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("luogu_report_generator.bundle_loader")

# 与 luogu-toolkit.bundle.BUNDLE_SCHEMA_VERSION 保持一致
EXPECTED_SCHEMA_VERSION = 1

_BJ_TZ = timezone(timedelta(hours=8))


class BundleLoadError(Exception):
    """ZIP 解析失败 (格式不对 / schema 不匹配 / 文件缺失)"""


@dataclass
class ReportBundle:
    """一个解析好的 ZIP 数据包"""
    export_data: Dict[str, Any]
    manifest: Dict[str, Any]
    passed_items: List[Dict[str, Any]] = field(default_factory=list)
    failed_items: List[Dict[str, Any]] = field(default_factory=list)
    source_path: Optional[Path] = None

    @property
    def schema_version(self) -> int:
        return int(self.manifest.get("schema_version") or 0)

    @property
    def luogu_uid(self) -> str:
        return str(self.manifest.get("luogu_uid") or "")

    @property
    def username(self) -> str:
        return str(self.manifest.get("username") or "")

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.manifest.get("username") or "")

    @property
    def generated_at(self) -> Optional[str]:
        return self.manifest.get("generated_at_iso")

    @property
    def solved_count(self) -> int:
        return int(self.manifest.get("solved_count") or 0)

    @property
    def failed_count(self) -> int:
        return int(self.manifest.get("failed_count") or 0)

    def summary_line(self) -> str:
        return (
            f"uid={self.luogu_uid} username={self.username} "
            f"name={self.name} passed={self.solved_count} failed={self.failed_count} "
            f"generated_at={self.generated_at}"
        )


def _read_json(zf: zipfile.ZipFile, name: str) -> Dict[str, Any]:
    try:
        with zf.open(name) as f:
            raw = f.read().decode("utf-8")
    except KeyError as e:
        raise BundleLoadError(f"ZIP 缺少必需文件: {name}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise BundleLoadError(f"ZIP 中 {name} 不是合法 JSON: {e}") from e


def load_zip(zip_path: str | Path) -> ReportBundle:
    """从磁盘 ZIP 文件解析出 ReportBundle。"""
    p = Path(zip_path)
    if not p.exists():
        raise BundleLoadError(f"ZIP 文件不存在: {p}")
    if not p.is_file():
        raise BundleLoadError(f"不是文件: {p}")
    if p.stat().st_size == 0:
        raise BundleLoadError(f"ZIP 文件是空的: {p}")
    if not zipfile.is_zipfile(str(p)):
        raise BundleLoadError(f"不是合法 ZIP: {p}")

    with zipfile.ZipFile(str(p), "r") as zf:
        names = set(zf.namelist())
        manifest = _read_json(zf, "manifest.json")
        export_data = _read_json(zf, "export_data.json")

        # 校验 schema
        sv = int(manifest.get("schema_version") or 0)
        if sv != EXPECTED_SCHEMA_VERSION:
            raise BundleLoadError(
                f"schema_version 不匹配: 期望 {EXPECTED_SCHEMA_VERSION}, 实际 {sv}。"
                f"请用匹配的 luogu-toolkit 版本重新打 ZIP"
            )
        if not isinstance(export_data, dict):
            raise BundleLoadError("export_data.json 必须是 dict")

        # 解析 items/ 子目录
        passed_items: List[Dict[str, Any]] = []
        failed_items: List[Dict[str, Any]] = []
        for name in names:
            if name.startswith("items/passed/") and name.endswith(".json"):
                with zf.open(name) as f:
                    passed_items.append(json.loads(f.read().decode("utf-8")))
            elif name.startswith("items/failed/") and name.endswith(".json"):
                with zf.open(name) as f:
                    failed_items.append(json.loads(f.read().decode("utf-8")))

    return ReportBundle(
        export_data=export_data,
        manifest=manifest,
        passed_items=passed_items,
        failed_items=failed_items,
        source_path=p.resolve(),
    )


def load_zip_bytes(data: bytes, source_name: str = "<memory>") -> ReportBundle:
    """从内存中的 ZIP 字节流解析 (供 Web 上传场景用)。"""
    import io
    if not data:
        raise BundleLoadError("上传内容为空")
    try:
        bio = io.BytesIO(data)
        with zipfile.ZipFile(bio, "r") as zf:
            names = set(zf.namelist())
            manifest = _read_json(zf, "manifest.json")
            export_data = _read_json(zf, "export_data.json")

            sv = int(manifest.get("schema_version") or 0)
            if sv != EXPECTED_SCHEMA_VERSION:
                raise BundleLoadError(
                    f"schema_version 不匹配: 期望 {EXPECTED_SCHEMA_VERSION}, 实际 {sv}"
                )

            passed_items: List[Dict[str, Any]] = []
            failed_items: List[Dict[str, Any]] = []
            for name in names:
                if name.startswith("items/passed/") and name.endswith(".json"):
                    with zf.open(name) as f:
                        passed_items.append(json.loads(f.read().decode("utf-8")))
                elif name.startswith("items/failed/") and name.endswith(".json"):
                    with zf.open(name) as f:
                        failed_items.append(json.loads(f.read().decode("utf-8")))
    except BundleLoadError:
        raise
    except zipfile.BadZipFile as e:
        raise BundleLoadError(f"上传内容不是合法 ZIP: {e}") from e

    return ReportBundle(
        export_data=export_data,
        manifest=manifest,
        passed_items=passed_items,
        failed_items=failed_items,
        source_path=Path(source_name) if source_name else None,
    )


__all__ = [
    "BundleLoadError",
    "EXPECTED_SCHEMA_VERSION",
    "ReportBundle",
    "load_zip",
    "load_zip_bytes",
]
