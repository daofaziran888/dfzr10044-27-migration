"""回收凭据包领域服务：把审计 CSV 按湖表与时间段迁成可回导的回收凭据包。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.reclaim_engine import (
    PACKAGE_MODULE,
    reclaim_engine,
)
from app.store import store


class ReclaimPackageService(BaseModuleService):
    MODULE = PACKAGE_MODULE
    ENTITY = "回收凭据包"
    OPERATION_TERM = "迁包"
    LIST_FIELDS = [
        "包编号", "所属湖表", "时间段起", "时间段止", "来源文件",
        "条目数", "已确认条目", "待核条目", "导入进度",
    ]
    REQUIRED_FIELDS = ["所属湖表", "时间段起", "时间段止"]
    STATUS_ORDER = ["导入中", "待确认", "已确认", "导入失败"]
    ACTION_RULES = {"重试导入": "待确认", "确认导入": "已确认", "封存凭据包": "已确认"}
    NEGATIVE_ACTIONS = []

    def import_audit_csv(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按湖表与时间段导入审计 CSV，可选立即确认。"""
        return reclaim_engine.import_package(
            lake_table=str(payload.get("lake_table") or payload.get("所属湖表") or "").strip(),
            window_start=str(payload.get("window_start") or payload.get("时间段起") or "").strip(),
            window_end=str(payload.get("window_end") or payload.get("时间段止") or "").strip(),
            csv_text=str(payload.get("csv_text") or payload.get("审计CSV") or ""),
            source_file=str(payload.get("source_file") or payload.get("来源文件") or ""),
            package_no=str(payload.get("package_no") or payload.get("包编号") or ""),
            confirm=bool(payload.get("confirm")),
            fail_at=(int(payload["fail_at"]) if payload.get("fail_at") not in (None, "") else None),
        )

    def package_detail(self, package_id: int) -> dict[str, Any] | None:
        return reclaim_engine.package_detail(package_id)

    def retry_import(self, package_id: int, *, confirm: bool = False) -> dict[str, Any]:
        return reclaim_engine.retry_package(package_id, confirm=confirm)

    def confirm_import(self, package_id: int) -> dict[str, Any]:
        return reclaim_engine.confirm_package(package_id)

    def report_receipt(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return reclaim_engine.report_receipt(
            package_id,
            item_no=str(payload.get("item_no") or payload.get("审计条目") or ""),
            actual_bytes=int(payload.get("actual_bytes") or payload.get("实际释放字节") or 0),
            receipt_no=str(payload.get("receipt_no") or payload.get("回执编号") or ""),
            source=str(payload.get("source") or "库内实际回收回执"),
            operator=str(payload.get("operator") or payload.get("操作者") or ""),
        )

    def set_amounts(self, package_id: int, amounts: dict[str, Any]) -> dict[str, Any]:
        return reclaim_engine.set_historical_amounts(package_id, amounts)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action in ("重试导入", "从失败位置恢复"):
            result = self.retry_import(entry_id)
            return entry, str(result.get("message", "已重试导入"))
        if action in ("确认导入", "封存凭据包"):
            result = self.confirm_import(entry_id)
            return entry, str(result.get("message", "已确认导入"))
        return super().run_action(entry_id, action)
