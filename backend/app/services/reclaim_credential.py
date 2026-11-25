"""回收凭据领域服务：每条审计条目只保留一份可校验凭证。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.reclaim_engine import CREDENTIAL_MODULE, CREDENTIAL_PENDING, reclaim_engine
from app.store import store


class ReclaimCredentialService(BaseModuleService):
    MODULE = CREDENTIAL_MODULE
    ENTITY = "回收凭据"
    OPERATION_TERM = "核验"
    LIST_FIELDS = [
        "凭据编号", "所属湖表", "来源包", "审计条目", "处置对象",
        "实际释放字节", "确认来源", "核销回收单",
    ]
    REQUIRED_FIELDS = ["所属湖表", "审计条目"]
    STATUS_ORDER = ["待核", "有效", "已核销", "已失效"]
    ACTION_RULES = {"确认凭据": "有效", "作废凭据": "已失效"}
    NEGATIVE_ACTIONS = ["作废凭据"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "确认凭据":
            if entry.get("status") != CREDENTIAL_PENDING:
                return entry, "凭据已生效，不重复确认"
            # 手工确认也必须拿得出回执或实删量，不能凭空生出一份释放凭证。
            has_receipt = bool(
                entry.get("包内回执编号") or entry.get("外部回执编号") or entry.get("外部回执"))
            if not entry.get("审计原文") or not entry.get("来源包"):
                return entry, "缺少审计原文或来源包，不能确认凭据"
            if not has_receipt and int(entry.get("实际释放字节", 0) or 0) <= 0:
                return entry, "缺少删除回执与实际释放量，不能确认凭据"
            reclaim_engine._activate_credential(
                entry,
                actual_bytes=int(entry.get("实际释放字节", 0) or entry.get("声明释放字节", 0)),
                source=str(entry.get("候选确认来源") or "包内回执"),
            )
            return entry, "凭据已确认有效"
        if action == "作废凭据":
            entry["status"] = "已失效"
            entry["pending"] = False
            entry["abnormal"] = True
            return entry, "凭据已作废"
        return super().run_action(entry_id, action)


class ReclaimOrderService(BaseModuleService):
    MODULE = "reclaim_order"
    ENTITY = "回收单"
    OPERATION_TERM = "核销"
    LIST_FIELDS = [
        "回收单编号", "所属湖表", "所需核销字节", "已核销字节",
        "待补字节", "候选文件", "核销明细", "最近执行",
    ]
    REQUIRED_FIELDS = ["所属湖表", "所需核销字节"]
    STATUS_ORDER = ["待执行", "部分核销", "已核销", "已关闭"]
    ACTION_RULES = {"执行回收": "已核销", "关闭回收单": "已关闭"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记回收单：候选与字节明细可随 values 一起提交。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        entry["归账锁定"] = False
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "执行回收":
            result = reclaim_engine.execute_order(entry_id)
            return entry, str(result.get("message", "回收已执行"))
        if action in ("关闭回收单", "关闭"):
            result = reclaim_engine.close_order(entry_id)
            return entry, str(result.get("message", "回收单已关闭"))
        return super().run_action(entry_id, action)
