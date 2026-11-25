"""回收凭据包领域引擎：审计 CSV 迁包、凭据核验、回收核销与幂等归账。

核心约束（与回收核销业务约定一致）：

* 一个回收凭据包按「所属湖表 + 时间段」回导审计 CSV，跨湖表的同编号条目一律排除。
* 包内带明确删除回执的条目在导入时匹配为「已删除」，但凭据要等导入确认后才生效；
  只有待处理记录的条目保持「待核」，必须由包外实际回收回执确认。
* 同一文件/条目只登记一份有效凭据，实际释放字节只计一次。
* 未完成核验的条目不得发往存储删除；已释放的文件不会二次删除。
* 回收单的核销分摊全局幂等：同一项不会重复分摊到下一张回收单。

数据仍落在 ``app.store`` 的内存表里，便于通用资源接口直接列出、导出。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from datetime import datetime
from typing import Any

from app.lake_store import lake_store
from app.store import store

PACKAGE_MODULE = "reclaim_package"
CREDENTIAL_MODULE = "reclaim_credential"
ORDER_MODULE = "reclaim_order"

# 审计 CSV 的规范字段；导入时按这些字段检查缺项，原文始终保留。
AUDIT_FIELDS = [
    "条目编号", "所属湖表", "操作类型", "操作对象",
    "变更前值", "变更后值", "操作者标识", "操作时间", "status",
]

# 明确删除回执的判定：显式回执列，或变更后值里的完成态关键词。
RECEIPT_COLUMNS = [
    "删除回执", "回收回执", "删除确认", "处置回执", "回执编号", "receipt", "receipt_no",
]
RECEIPT_KEYWORDS = [
    "已删除", "已释放", "删除回执", "已回收", "已清理", "已过期", "已物理删除",
]
BYTES_COLUMNS = [
    "实际释放字节", "释放字节", "释放字节数", "回收字节数", "核销字节", "bytes", "释放量",
]

CREDENTIAL_PENDING = "待核"
CREDENTIAL_VALID = "有效"
CREDENTIAL_WRITTEN_OFF = "已核销"
CREDENTIAL_INVALID = "已失效"

SOURCE_IN_PACKAGE = "包内回执"
SOURCE_EXTERNAL = "包外回执"
SOURCE_ACTUAL_DELETE = "实际删除确认"
SOURCE_NONE = "无"

PACKAGE_PENDING = "待确认"
PACKAGE_FAILED = "导入失败"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _next_id(module: str) -> int:
    rows = store.rows(module)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _to_int(value: Any) -> int:
    """从「100」「100字节」「100B」「0.1KB」之类文本里取整数字节数。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|B|字节)?", text, re.IGNORECASE)
    if not match:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "").upper()
    factor = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}.get(unit, 1)
    return int(number * factor)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00").replace("/", "-")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def _window_contains(row_time: Any, window_start: Any, window_end: Any) -> bool:
    start = _parse_time(window_start)
    end = _parse_time(window_end)
    if start is None and end is None:
        return True
    moment = _parse_time(row_time)
    if moment is None:
        # 时间缺失时不武断排除，交由缺项清单提示值班员复核。
        return True
    if start is not None and moment < start:
        return False
    if end is not None and moment > end:
        return False
    return True


def _render_raw_line(fields: list[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(fields)
    return buffer.getvalue()


def _digest_rows(text: str, lake_table: str, window_start: str, window_end: str) -> str:
    payload = f"{lake_table}|{window_start}|{window_end}|{text}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def _row_has_receipt(row: dict[str, Any]) -> bool:
    for column in RECEIPT_COLUMNS:
        if str(row.get(column) or "").strip():
            return True
    joined = " ".join(
        str(row.get(column) or "")
        for column in ("变更后值", "变更前值", "操作类型", "处置结论", "status")
    )
    if any(keyword in joined for keyword in RECEIPT_KEYWORDS):
        return True
    # 「明确删除回执」也常见于：删除类操作 + 变更后值落到 0 字节。
    operation = str(row.get("操作类型") or "")
    after = str(row.get("变更后值") or "")
    deletion_like = any(
        word in operation for word in ("删除", "清理", "回收", "释放", "过期", "物理")
    )
    after_zero = bool(re.search(r"(^|[^\d])0\s*(字节|B)\b", after, re.IGNORECASE))
    return deletion_like and after_zero and _row_declared_bytes(row) > 0


def _row_declared_bytes(row: dict[str, Any]) -> int:
    for column in BYTES_COLUMNS:
        if column in row and str(row.get(column) or "").strip():
            return _to_int(row.get(column))
    # 兜底：从「100字节 → 0字节」一类变更前后值里取较大的释放量。
    for column in ("变更前值", "变更后值"):
        text = str(row.get(column) or "")
        match = re.search(r"(\d+)\s*(?:字节|B)\b", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _row_receipt_no(row: dict[str, Any]) -> str:
    for column in RECEIPT_COLUMNS:
        value = str(row.get(column) or "").strip()
        if value:
            return value
    return ""


class ReclaimEngine:
    """回收凭据包/凭据/回收单的统一领域引擎。"""

    def __init__(self) -> None:
        # 全局分摊登记：candidate_key -> 回收单编号，保证同一项只归账一次。
        self._allocations: dict[str, str] = {}
        # 包外回执流水，便于导入详情页追溯「存量包之外的回执」。
        self._receipts: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 通用内部工具
    # ------------------------------------------------------------------
    def _find_package(self, package_id: int) -> dict[str, Any] | None:
        return store.find(PACKAGE_MODULE, package_id)

    def _find_credential(self, lake_table: str, item_no: str) -> dict[str, Any] | None:
        for row in store.rows(CREDENTIAL_MODULE):
            if str(row.get("所属湖表", "")) == lake_table and str(row.get("审计条目", "")) == item_no:
                return row
        return None

    def _find_order(self, order_id: int) -> dict[str, Any] | None:
        return store.find(ORDER_MODULE, order_id)

    def _candidate_key(self, lake_table: str, candidate: dict[str, Any]) -> str:
        """候选自身的分摊键：同一文件优先按文件标识去重，同一审计条目按条目编号去重。"""
        file_id = str(candidate.get("文件标识") or "")
        if file_id:
            return f"file:{lake_table}:{file_id}"
        item_no = str(candidate.get("审计条目") or candidate.get("标识") or "")
        if item_no:
            return f"cred:{lake_table}:{item_no}"
        return f"cred:{lake_table}:{candidate.get('凭据编号', '')}"

    def _allocation_key(
        self, lake_table: str, candidate: dict[str, Any],
        credential: dict[str, Any] | None,
    ) -> str:
        """解析凭据后的归账键：有审计凭据就归到条目，独立新文件才归到文件。"""
        if credential is not None:
            return f"cred:{lake_table}:{credential.get('审计条目', '')}"
        return self._candidate_key(lake_table, candidate)

    def _recount_package(self, package: dict[str, Any]) -> None:
        credentials = [
            row for row in store.rows(CREDENTIAL_MODULE)
            if str(row.get("来源包", "")) == str(package.get("包编号", ""))
        ]
        confirmed = sum(1 for row in credentials if row.get("status") in (CREDENTIAL_VALID, CREDENTIAL_WRITTEN_OFF))
        package["已确认条目"] = confirmed
        package["待核条目"] = sum(
            1 for row in credentials
            if str(row.get("审计条目", "")) in {
                str(item.get("条目编号")) for item in package.get("明细", [])
                if item.get("处理状态") != "重复行跳过"
            }
            and row.get("status") == CREDENTIAL_PENDING
        )
        package["凭据数"] = len(credentials)
        total = int(package.get("已处理行数", 0))
        package["导入进度"] = f"{total}/{int(package.get('条目数', 0))}"
        package["最近导入"] = _now_iso()

    # ------------------------------------------------------------------
    # CSV 解析与迁包
    # ------------------------------------------------------------------
    def parse_audit_csv(self, csv_text: str) -> list[dict[str, Any]]:
        """把审计 CSV 文本解析成带原文的行记录，容忍 BOM 与空行。"""
        text = (csv_text or "").lstrip("﻿")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return []
        header = [str(cell).strip() for cell in rows[0]]
        parsed: list[dict[str, Any]] = []
        for raw in rows[1:]:
            if not any(str(cell).strip() for cell in raw):
                continue
            values = list(raw) + [""] * max(0, len(header) - len(raw))
            fields = {header[index]: values[index] for index in range(len(header))}
            parsed.append({
                "原始字段": fields,
                "审计原文": _render_raw_line(raw),
                "缺失项": [
                    field for field in AUDIT_FIELDS
                    if field in header and not str(fields.get(field) or "").strip()
                ],
            })
        return parsed

    def import_package(
        self,
        *,
        lake_table: str,
        window_start: str,
        window_end: str,
        csv_text: str,
        source_file: str = "",
        package_no: str = "",
        confirm: bool = False,
        fail_at: int | None = None,
    ) -> dict[str, Any]:
        """按湖表与时间段把审计 CSV 迁成回收凭据包，支持断点重试。

        ``fail_at`` 仅用于演练/测试断点，表示处理到第 N 条时中断，
        重试会从检查点继续，已落库的凭据不会被重复创建。
        """
        if not lake_table.strip():
            return {"ok": False, "message": "缺少所属湖表", "package": None}
        parsed = self.parse_audit_csv(csv_text)
        if not parsed:
            return {"ok": False, "message": "审计 CSV 没有可迁入的数据行", "package": None}

        digest = _digest_rows(csv_text, lake_table, window_start, window_end)
        package = None
        for row in store.rows(PACKAGE_MODULE):
            if row.get("内容摘要") == digest and str(row.get("所属湖表", "")) == lake_table:
                package = row
                break

        included: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        seen_items: set[str] = set()

        for entry in parsed:
            fields = entry["原始字段"]
            owner = str(fields.get("所属湖表") or "").strip()
            item_no = str(fields.get("条目编号") or "").strip()
            if owner != lake_table:
                # 另一湖表的同编号条目（H）绝不混入本表凭据包。
                excluded.append({
                    "条目编号": item_no,
                    "所属湖表": owner,
                    "审计原文": entry["审计原文"],
                    "排除原因": "跨湖表",
                })
                continue
            if not _window_contains(fields.get("操作时间"), window_start, window_end):
                excluded.append({
                    "条目编号": item_no,
                    "所属湖表": owner,
                    "审计原文": entry["审计原文"],
                    "排除原因": "不在时间段内",
                })
                continue
            if not item_no:
                excluded.append({
                    "条目编号": "",
                    "所属湖表": owner,
                    "审计原文": entry["审计原文"],
                    "排除原因": "缺少条目编号",
                })
                continue
            duplicate = item_no in seen_items
            seen_items.add(item_no)

            has_receipt = _row_has_receipt(fields)
            declared = _row_declared_bytes(fields)
            if has_receipt:
                disposition = "已删除"
            else:
                # 只有待处理/待核查记录且没有回执的条目：保持待核，必须由实际回收回执确认。
                disposition = "待核"
            included.append({
                "条目编号": item_no,
                "所属湖表": owner,
                "操作类型": str(fields.get("操作类型") or ""),
                "操作对象": str(fields.get("操作对象") or ""),
                "操作时间": str(fields.get("操作时间") or ""),
                "审计处置": disposition,
                "有删除回执": has_receipt,
                "回执编号": _row_receipt_no(fields),
                "声明释放字节": declared,
                "审计原文": entry["审计原文"],
                "原始字段": fields,
                "缺失项": entry["缺失项"],
                "处理状态": "待处理",
                "凭据编号": "",
                "重复行": duplicate,
            })

        if package is None:
            package = {
                "id": _next_id(PACKAGE_MODULE),
                "包编号": package_no or f"PKG-{int(time.time())}-{digest[:6]}",
                "所属湖表": lake_table,
                "时间段起": window_start,
                "时间段止": window_end,
                "来源文件": source_file or "lake_audit.csv",
                "条目数": len(included),
                "已确认条目": 0,
                "待核条目": len(included),
                "凭据数": 0,
                "内容摘要": digest,
                "重复导入": 0,
                "导入进度": f"0/{len(included)}",
                "已处理行数": 0,
                "失败位置": "",
                "外部回执数": 0,
                "明细": included,
                "排除明细": excluded,
                "status": "导入中",
                "pending": True,
                "abnormal": False,
                "最近导入": _now_iso(),
            }
            store.rows(PACKAGE_MODULE).append(package)
        else:
            package["重复导入"] = int(package.get("重复导入", 0)) + 1
            if source_file:
                package["来源文件"] = source_file
            # 已存在包只补新增条目，保留原有处理状态与凭据关联。
            known = {str(item.get("条目编号")) for item in package.get("明细", [])}
            for item in included:
                if str(item.get("条目编号")) not in known:
                    package["明细"].append(item)
                    package["条目数"] = int(package.get("条目数", 0)) + 1
            package["排除明细"] = excluded
            if package.get("status") in (PACKAGE_FAILED, None):
                package["status"] = "导入中"

        package["pending"] = package.get("status") not in ("已确认",)
        package["abnormal"] = package.get("status") == PACKAGE_FAILED

        if package.get("status") == "已确认":
            # 已确认的包再次导入只做幂等识别，不重新核验、不重复计释放量。
            self._recount_package(package)
            return {
                "ok": True,
                "message": "同一审计包已确认，按幂等处理",
                "package": package,
                "checkpoint": int(package.get("已处理行数", 0)),
                "detail": self.package_detail(int(package["id"]), note="同一审计包已确认，按幂等处理"),
            }

        package["status"] = "导入中"
        items: list[dict[str, Any]] = package.get("明细", [])
        processed = int(package.get("已处理行数", 0))
        interrupted = False
        for index in range(processed, len(items)):
            if fail_at is not None and index + 1 == int(fail_at):
                package["status"] = PACKAGE_FAILED
                package["失败位置"] = f"第{index + 1}条（{items[index].get('条目编号', '')}）"
                package["abnormal"] = True
                package["pending"] = True
                interrupted = True
                break

            item = items[index]
            if item.get("重复行"):
                item["处理状态"] = "重复行跳过"
                package["已处理行数"] = index + 1
                continue

            credential = self._find_credential(lake_table, str(item["条目编号"]))
            if credential is None:
                credential = self._create_credential(
                    lake_table=lake_table,
                    item=item,
                    package_no=str(package["包编号"]),
                )
            else:
                # 已有凭据（可能已被包外回执确认）只补原文，绝不降级或重复计数。
                if not credential.get("审计原文"):
                    credential["审计原文"] = item.get("审计原文", "")
                if not credential.get("缺失项"):
                    credential["缺失项"] = item.get("缺失项", [])
                if not credential.get("来源包"):
                    credential["来源包"] = str(package["包编号"])

            item["凭据编号"] = str(credential.get("凭据编号", ""))
            if item.get("有删除回执"):
                item["处理状态"] = "已匹配删除"
                if credential.get("status") == CREDENTIAL_PENDING:
                    credential["审计处置"] = "已删除"
            else:
                item["处理状态"] = "待核"
                credential["审计处置"] = credential.get("审计处置") or "待处理"

            package["已处理行数"] = index + 1

        if interrupted:
            self._recount_package(package)
            return {
                "ok": False,
                "message": f"导入在{package['失败位置']}中断，可从失败位置重试",
                "package": package,
                "checkpoint": package["已处理行数"],
                "detail": self.package_detail(int(package["id"])),
            }

        if package.get("status") == PACKAGE_FAILED:
            package["abnormal"] = False
        package["status"] = PACKAGE_PENDING
        package["pending"] = True
        self._recount_package(package)

        if confirm:
            return self.confirm_package(int(package["id"]), note="导入并确认完成")
        return {
            "ok": True,
            "message": f"已迁入{len(included)}条审计记录，{len(excluded)}条被排除，待确认",
            "package": package,
            "checkpoint": package["已处理行数"],
            "detail": self.package_detail(int(package["id"])),
        }

    def _create_credential(self, *, lake_table: str, item: dict[str, Any], package_no: str) -> dict[str, Any]:
        item_no = str(item["条目编号"])
        has_receipt = bool(item.get("有删除回执"))
        credential = {
            "id": _next_id(CREDENTIAL_MODULE),
            "凭据编号": f"RC-{int(time.time())}-{len(store.rows(CREDENTIAL_MODULE)) + 1:03d}",
            "所属湖表": lake_table,
            "来源包": package_no,
            "审计条目": item_no,
            "处置对象": str(item.get("操作对象") or ""),
            "文件标识": str(item.get("操作对象") or ""),
            "实际释放字节": 0,
            "声明释放字节": int(item.get("声明释放字节", 0)),
            "确认来源": SOURCE_IN_PACKAGE if has_receipt else SOURCE_NONE,
            "候选确认来源": SOURCE_IN_PACKAGE if has_receipt else SOURCE_NONE,
            "包内回执编号": item.get("回执编号", ""),
            "外部回执": False,
            "外部回执编号": "",
            "审计处置": "已删除" if has_receipt else "待处理",
            "审计原文": item.get("审计原文", ""),
            "原始字段": item.get("原始字段", {}),
            "缺失项": item.get("缺失项", []),
            "已计释放量": False,
            "核销回收单": "",
            "首次确认时间": "",
            "最近确认时间": "",
            "status": CREDENTIAL_PENDING,
            "pending": True,
            "abnormal": False,
        }
        # 同一湖表内按条目编号去重的前提由调用方保证；此处再兜底一次。
        if self._find_credential(lake_table, item_no) is not None:
            return self._find_credential(lake_table, item_no)  # type: ignore[return-value]
        store.rows(CREDENTIAL_MODULE).append(credential)
        return credential

    def confirm_package(self, package_id: int, *, note: str = "") -> dict[str, Any]:
        package = self._find_package(package_id)
        if package is None:
            return {"ok": False, "message": f"回收凭据包 {package_id} 不存在", "package": None}
        package["status"] = "已确认"
        package["pending"] = False
        package["abnormal"] = False
        confirmed = 0
        for credential in store.rows(CREDENTIAL_MODULE):
            if str(credential.get("来源包", "")) != str(package.get("包编号", "")):
                continue
            if credential.get("status") != CREDENTIAL_PENDING:
                continue
            if credential.get("候选确认来源") == SOURCE_IN_PACKAGE and credential.get("审计处置") == "已删除":
                self._activate_credential(
                    credential,
                    actual_bytes=int(credential.get("声明释放字节", 0)),
                    source=SOURCE_IN_PACKAGE,
                    receipt_no=str(credential.get("包内回执编号", "")),
                )
                confirmed += 1
        self._recount_package(package)
        return {
            "ok": True,
            "message": note or f"导入已确认，{confirmed}条包内回执生效",
            "package": package,
            "detail": self.package_detail(package_id),
        }

    def retry_package(self, package_id: int, *, confirm: bool = False) -> dict[str, Any]:
        """从失败位置恢复导入；已落库凭据与包外回执保持最新状态。"""
        package = self._find_package(package_id)
        if package is None:
            return {"ok": False, "message": f"回收凭据包 {package_id} 不存在", "package": None}
        if package.get("status") == "已确认":
            return {
                "ok": True,
                "message": "该包已确认，无需重试，按幂等返回详情",
                "package": package,
                "detail": self.package_detail(package_id),
            }
        # 复用原包明细继续处理：不重建凭据，也不覆盖包外回执确认的 G 凭据。
        items: list[dict[str, Any]] = package.get("明细", [])
        processed = int(package.get("已处理行数", 0))
        already_confirmed = package.get("status") == "已确认"
        package["abnormal"] = False
        for index in range(processed, len(items)):
            item = items[index]
            if item.get("重复行"):
                item["处理状态"] = "重复行跳过"
                package["已处理行数"] = index + 1
                continue
            credential = self._find_credential(str(package["所属湖表"]), str(item["条目编号"]))
            if credential is None:
                credential = self._create_credential(
                    lake_table=str(package["所属湖表"]), item=item, package_no=str(package["包编号"]))
            item["凭据编号"] = str(credential.get("凭据编号", ""))
            item["处理状态"] = "已匹配删除" if item.get("有删除回执") else "待核"
            package["已处理行数"] = index + 1
        package["失败位置"] = ""
        # 恢复成功后回到待确认；若原本已确认则保持已确认，避免重试把已生效凭据打回待核。
        package["status"] = "已确认" if already_confirmed else PACKAGE_PENDING
        package["pending"] = not already_confirmed
        self._recount_package(package)
        if confirm:
            return self.confirm_package(package_id, note="从失败位置恢复并确认完成")
        return {
            "ok": True,
            "message": f"已从检查点{package.get('已处理行数', 0)}恢复，包外回执保持有效",
            "package": package,
            "checkpoint": package.get("已处理行数", 0),
            "detail": self.package_detail(package_id),
        }

    # ------------------------------------------------------------------
    # 包外实际回收回执与凭据生效
    # ------------------------------------------------------------------
    def _activate_credential(
        self, credential: dict[str, Any], *, actual_bytes: int,
        source: str, receipt_no: str = "", external: bool = False,
    ) -> bool:
        """把凭据置为有效；实际释放字节只累加一次。返回是否首次计入。"""
        first = not bool(credential.get("已计释放量"))
        credential["实际释放字节"] = max(int(credential.get("实际释放字节", 0)), int(actual_bytes))
        credential["确认来源"] = source
        credential["候选确认来源"] = source
        credential["外部回执"] = external or credential.get("外部回执", False)
        if receipt_no:
            if external:
                credential["外部回执编号"] = receipt_no
            else:
                credential["包内回执编号"] = receipt_no
        if credential.get("status") in (CREDENTIAL_PENDING, CREDENTIAL_INVALID):
            credential["status"] = CREDENTIAL_VALID
        credential["pending"] = credential.get("status") == CREDENTIAL_PENDING
        credential["abnormal"] = False
        credential["已计释放量"] = True
        if not credential.get("首次确认时间"):
            credential["首次确认时间"] = _now_iso()
        credential["最近确认时间"] = _now_iso()
        return first

    def report_receipt(
        self, package_id: int, *, item_no: str, actual_bytes: int,
        receipt_no: str = "", source: str = "库内实际回收回执", operator: str = "",
    ) -> dict[str, Any]:
        """补报包外实际回收回执：让待核条目凭实际释放确认生效。"""
        package = self._find_package(package_id)
        if package is None:
            return {"ok": False, "message": f"回收凭据包 {package_id} 不存在", "package": None}
        if not item_no.strip():
            return {"ok": False, "message": "缺少待补回执的审计条目", "package": package}
        lake_table = str(package.get("所属湖表", ""))
        credential = self._find_credential(lake_table, item_no.strip())
        if credential is None:
            # 条目可能尚未处理，先在包明细里找到原文再建凭据，保证原文可查。
            item = next(
                (row for row in package.get("明细", []) if str(row.get("条目编号")) == item_no.strip()),
                None,
            )
            if item is None:
                return {"ok": False, "message": f"包内没有审计条目 {item_no}", "package": package}
            credential = self._create_credential(lake_table=lake_table, item=item,
                                                  package_no=str(package.get("包编号", "")))
        first = self._activate_credential(
            credential, actual_bytes=int(actual_bytes), source=SOURCE_EXTERNAL,
            receipt_no=receipt_no, external=True,
        )
        receipt = {
            "回执编号": receipt_no or f"EXT-{int(time.time())}-{len(self._receipts) + 1:03d}",
            "包编号": str(package.get("包编号", "")),
            "所属湖表": lake_table,
            "审计条目": item_no.strip(),
            "实际释放字节": int(actual_bytes),
            "来源": source,
            "操作者": operator,
            "登记时间": _now_iso(),
            "首次计入": first,
        }
        self._receipts.append(receipt)
        package["外部回执数"] = int(package.get("外部回执数", 0)) + 1
        package["最近导入"] = _now_iso()
        self._recount_package(package)
        return {
            "ok": True,
            "message": f"已登记{ item_no }的包外回执，释放{int(actual_bytes)}字节"
                       + ("（仅计一次）" if first else "（已计过释放量，不重复累加）"),
            "package": package,
            "credential": credential,
            "external_receipt": receipt,
            "detail": self.package_detail(package_id),
        }

    def set_historical_amounts(self, package_id: int, amounts: dict[str, Any]) -> dict[str, Any]:
        """登记历史实删量：按审计条目写入实际释放字节，只改额度不重复计释放量。

        若凭据此前已因包外回执计过释放量，这里只对齐数值，仍保持「只计一次」。
        """
        package = self._find_package(package_id)
        if package is None:
            return {"ok": False, "message": f"回收凭据包 {package_id} 不存在", "package": None}
        lake_table = str(package.get("所属湖表", ""))
        updated: list[dict[str, Any]] = []
        for item_no, raw in amounts.items():
            credential = self._find_credential(lake_table, str(item_no))
            if credential is None:
                return {
                    "ok": False,
                    "message": f"包内没有审计条目 {item_no} 的凭据，无法登记历史实删量",
                    "package": package,
                }
            value = int(raw)
            credential["实际释放字节"] = value
            credential["声明释放字节"] = value
            credential["历史实删量"] = value
            updated.append({"审计条目": str(item_no), "实际释放字节": value,
                            "状态": credential.get("status")})
        package["最近导入"] = _now_iso()
        self._recount_package(package)
        return {
            "ok": True,
            "message": f"已登记{len(updated)}条历史实删量",
            "package": package,
            "updated": updated,
            "detail": self.package_detail(package_id),
        }

    # ------------------------------------------------------------------
    # 导入详情
    # ------------------------------------------------------------------
    def export_package_csv(self, package_id: int) -> str | None:
        """把凭据包导出成可再次回导的 CSV，包含原始审计字段与核验结论。"""
        package = self._find_package(package_id)
        if package is None:
            return None
        credentials = {
            str(row.get("审计条目", "")): row
            for row in store.rows(CREDENTIAL_MODULE)
            if str(row.get("来源包", "")) == str(package.get("包编号", ""))
        }
        header = [
            *AUDIT_FIELDS,
            "有删除回执", "回执编号", "凭据编号", "凭据状态", "确认来源",
            "实际释放字节", "核销回收单", "缺失项", "处理状态",
        ]
        buffer = io.StringIO()
        buffer.write("﻿")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(header)
        for item in package.get("明细", []):
            credential = credentials.get(str(item.get("条目编号")), {})
            fields = item.get("原始字段") or {}
            if not fields and item.get("审计原文"):
                # 种子/历史包可能只存了原文：回退到解析原文，导出仍保留全部审计字段。
                rebuilt = self.parse_audit_csv(
                    ",".join(AUDIT_FIELDS) + "\n" + str(item["审计原文"]))
                if rebuilt:
                    fields = rebuilt[0]["原始字段"]
            writer.writerow([
                fields.get("条目编号", ""), fields.get("所属湖表", ""),
                fields.get("操作类型", ""), fields.get("操作对象", ""),
                fields.get("变更前值", ""), fields.get("变更后值", ""),
                fields.get("操作者标识", ""), fields.get("操作时间", ""),
                fields.get("status", ""),
                "是" if item.get("有删除回执") else "否",
                item.get("回执编号", ""),
                credential.get("凭据编号", ""),
                credential.get("status", ""),
                credential.get("确认来源", ""),
                credential.get("实际释放字节", 0),
                credential.get("核销回收单", ""),
                "、".join(item.get("缺失项", [])),
                item.get("处理状态", ""),
            ])
        return buffer.getvalue()

    def package_detail(self, package_id: int, *, note: str = "") -> dict[str, Any] | None:
        package = self._find_package(package_id)
        if package is None:
            return None
        items = package.get("明细", [])
        credentials = [
            row for row in store.rows(CREDENTIAL_MODULE)
            if str(row.get("来源包", "")) == str(package.get("包编号", ""))
        ]
        matched = [item for item in items if item.get("有删除回执")]
        pending = [item for item in items if not item.get("有删除回执")]
        external_confirmed = [
            row for row in credentials
            if row.get("外部回执") and str(row.get("审计条目", "")) in {i.get("条目编号") for i in pending}
        ]
        return {
            "package": package,
            "note": note,
            "import_state": {
                "status": package.get("status"),
                "processed": int(package.get("已处理行数", 0)),
                "total": int(package.get("条目数", 0)),
                "checkpoint": int(package.get("已处理行数", 0)),
                "failure_position": package.get("失败位置", ""),
                "reimport_count": int(package.get("重复导入", 0)),
            },
            "summary": {
                "total": int(package.get("条目数", 0)),
                "matched_deleted": len(matched),
                "pending_verify": len(pending),
                "excluded": len(package.get("排除明细", [])),
                "confirmed": int(package.get("已确认条目", 0)),
                "pending": int(package.get("待核条目", 0)),
                "external_confirmed_pending": len(external_confirmed),
                "released_bytes": sum(int(row.get("实际释放字节", 0)) for row in credentials if row.get("已计释放量")),
            },
            "items": items,
            "excluded_items": package.get("排除明细", []),
            "credentials": credentials,
            "external_receipts": [
                receipt for receipt in self._receipts
                if str(receipt.get("包编号", "")) == str(package.get("包编号", ""))
            ],
        }

    # ------------------------------------------------------------------
    # 回收单
    # ------------------------------------------------------------------
    def create_order(
        self, *, lake_table: str, required_bytes: int,
        candidates: list[dict[str, Any]] | None = None,
        keep_evidence: bool = False, order_no: str = "",
    ) -> dict[str, Any]:
        if not lake_table.strip():
            return {"ok": False, "message": "缺少所属湖表"}
        required_bytes = int(required_bytes)
        if required_bytes <= 0:
            return {"ok": False, "message": "所需核销字节必须大于 0"}

        normalized: list[dict[str, Any]] = []
        if candidates is None:
            candidates = [
                {
                    "标识": row.get("审计条目"),
                    "类型": "历史凭据",
                    "审计条目": row.get("审计条目"),
                    "凭据编号": row.get("凭据编号"),
                    "字节": int(row.get("实际释放字节", 0) or row.get("声明释放字节", 0)),
                }
                for row in store.rows(CREDENTIAL_MODULE)
                if str(row.get("所属湖表", "")) == lake_table
                and row.get("status") == CREDENTIAL_VALID
                and not self._allocations.get(f"cred:{lake_table}:{row.get('凭据编号')}")
            ]
        for candidate in candidates:
            normalized.append({
                "标识": str(candidate.get("标识") or candidate.get("审计条目") or candidate.get("文件标识") or ""),
                "类型": str(candidate.get("类型") or ("新文件" if candidate.get("文件标识") else "历史凭据")),
                "审计条目": str(candidate.get("审计条目") or ""),
                "凭据编号": str(candidate.get("凭据编号") or ""),
                "文件标识": str(candidate.get("文件标识") or ""),
                "字节": int(candidate.get("字节", 0) or 0),
                "可安全处置": bool(candidate.get("可安全处置", True)),
            })

        order = {
            "id": _next_id(ORDER_MODULE),
            "回收单编号": order_no or f"RCO-{int(time.time())}-{len(store.rows(ORDER_MODULE)) + 1:03d}",
            "所属湖表": lake_table,
            "所需核销字节": required_bytes,
            "已核销字节": 0,
            "待补字节": required_bytes,
            "候选文件": "、".join(
                f"{item['标识']}({item['字节']}B)" for item in normalized if item["标识"]
            ) or "无",
            "核销明细": "",
            "保留证据": bool(keep_evidence),
            "候选明细": normalized,
            "核销项": [],
            "本轮采用明细": [],
            "提交时间": _now_iso(),
            "最近执行": "",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        }
        store.rows(ORDER_MODULE).append(order)
        return {"ok": True, "message": f"回收单已提交，需核销{required_bytes}字节", "order": order,
                "detail": self.order_detail(int(order["id"]))}

    def execute_order(
        self, order_id: int, *, keep_evidence: bool | None = None,
        operator: str = "",
    ) -> dict[str, Any]:
        order = self._find_order(order_id)
        if order is None:
            return {"ok": False, "message": f"回收单 {order_id} 不存在", "order": None}
        if order.get("status") in (CREDENTIAL_WRITTEN_OFF, "已关闭") or order.get("归账锁定"):
            # 归账锁定后不再改动原单结果，避免把已分摊项重复归到下一轮。
            return {
                "ok": True,
                "message": f"回收单当前为{order.get('status')}，保持原归账结果",
                "order": order,
                "detail": self.order_detail(order_id),
            }
        if keep_evidence is not None:
            order["保留证据"] = bool(keep_evidence)

        lake_table = str(order.get("所属湖表", ""))
        required = int(order.get("所需核销字节", 0))
        written = int(order.get("已核销字节", 0))
        adoptions: list[dict[str, Any]] = list(order.get("本轮采用明细", []))
        adopted_keys = {str(row.get("分摊键", "")) for row in order.get("核销项", [])}

        for candidate in order.get("候选明细", []):
            if written >= required:
                adoptions.append(self._adoption(candidate, False, "本轮已满足核销目标，未采用"))
                continue

            credential = None
            if candidate.get("凭据编号"):
                for row in store.rows(CREDENTIAL_MODULE):
                    if str(row.get("凭据编号", "")) == candidate["凭据编号"]:
                        credential = row
                        break
            if credential is None and candidate.get("审计条目"):
                credential = self._find_credential(lake_table, candidate["审计条目"])

            # 统一分摊键：解析到审计凭据就按条目编号，独立新文件按文件标识，保证只归账一次。
            key = self._allocation_key(lake_table, candidate, credential)
            if key in adopted_keys:
                continue
            owner = self._allocations.get(key)
            if owner and owner != str(order.get("回收单编号", "")):
                adoptions.append(self._adoption(
                    candidate, False, f"已分摊至回收单{owner}，不重复核销", credential=credential))
                continue

            if credential is not None and credential.get("status") == CREDENTIAL_PENDING:
                # 未完成核验的条目（如导入中的 F）不能发往存储删除，也不能核销。
                adoptions.append(self._adoption(candidate, False, "凭据待核，未采用", credential=credential))
                continue
            if credential is not None and credential.get("status") == CREDENTIAL_INVALID:
                adoptions.append(self._adoption(candidate, False, "凭据已失效", credential=credential))
                continue
            if candidate.get("类型") == "新文件" or (candidate.get("文件标识") and credential is None):
                file_id = str(candidate.get("文件标识") or "")
                if order.get("保留证据"):
                    adoptions.append(self._adoption(candidate, False, "保留证据，不删除"))
                    continue
                current = lake_store.get_file(lake_table, file_id) if file_id else None
                if current is None:
                    # 文件已不在存储里：只有存在实际删除确认凭据才允许核销，绝不臆造。
                    if credential is not None and credential.get("确认来源") == SOURCE_ACTUAL_DELETE:
                        written = self._adopt(order, candidate, credential, key, written, adoptions)
                    else:
                        adoptions.append(self._adoption(candidate, False, "文件不存在且无实际删除确认"))
                    continue
                # 真正删除成功后才生成「实际删除确认」凭据并核销。
                receipt = lake_store.delete_file_with_receipt(lake_table, file_id, operator=operator)
                if receipt is None:
                    adoptions.append(self._adoption(candidate, False, "文件不存在且无实际删除确认"))
                    continue
                credential = self._create_actual_delete_credential(lake_table, candidate, receipt=receipt)
                written = self._adopt(order, candidate, credential, key, written, adoptions)
                continue

            if credential is None:
                adoptions.append(self._adoption(candidate, False, "无可用回收凭据"))
                continue
            if credential.get("status") not in (CREDENTIAL_VALID, CREDENTIAL_WRITTEN_OFF):
                adoptions.append(self._adoption(candidate, False, "凭据未生效，未采用", credential=credential))
                continue
            written = self._adopt(order, candidate, credential, key, written, adoptions)

        order["已核销字节"] = written
        order["待补字节"] = max(0, required - written)
        if written >= required:
            order["status"] = CREDENTIAL_WRITTEN_OFF
            order["pending"] = False
            order["归账锁定"] = True
        elif written > 0:
            order["status"] = "部分核销"
            order["pending"] = True
        else:
            order["status"] = "待执行"
            order["pending"] = True
        order["abnormal"] = False
        order["最近执行"] = _now_iso()
        order["本轮采用明细"] = adoptions
        order["核销明细"] = "；".join(
            f"{row.get('标识')}核销{row.get('核销字节')}字节（{row.get('确认来源')}）"
            for row in order.get("核销项", [])
        ) or "本轮无核销"
        return {
            "ok": True,
            "message": f"回收执行完成：已核销{written}字节，待补{order['待补字节']}字节",
            "order": order,
            "detail": self.order_detail(order_id),
        }

    def _adopt(
        self, order: dict[str, Any], candidate: dict[str, Any], credential: dict[str, Any],
        key: str, written: int, adoptions: list[dict[str, Any]],
    ) -> int:
        # 以凭据上的实际释放量为准，候选里的字节只是提交时的预估。
        actual = int(credential.get("实际释放字节", 0) or 0)
        bytes_ = actual if actual > 0 else int(candidate.get("字节", 0) or 0)
        order.setdefault("核销项", []).append({
            "标识": candidate.get("标识"),
            "分摊键": key,
            "凭据编号": credential.get("凭据编号", ""),
            "审计条目": credential.get("审计条目", candidate.get("审计条目", "")),
            "文件标识": candidate.get("文件标识", ""),
            "核销字节": bytes_,
            "确认来源": credential.get("确认来源", ""),
            "外部回执": bool(credential.get("外部回执")),
            "核销单": order.get("回收单编号", ""),
        })
        self._allocations[key] = str(order.get("回收单编号", ""))
        if credential.get("status") == CREDENTIAL_VALID:
            credential["status"] = CREDENTIAL_WRITTEN_OFF
        credential["pending"] = False
        credential["核销回收单"] = str(order.get("回收单编号", ""))
        credential["已计释放量"] = True
        adoptions.append(self._adoption(
            candidate, True, f"凭据{credential.get('确认来源', '')}，核销{bytes_}字节",
            credential=credential))
        return written + bytes_

    def _adoption(
        self, candidate: dict[str, Any], adopted: bool, reason: str,
        credential: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "标识": candidate.get("标识"),
            "类型": candidate.get("类型"),
            "审计条目": candidate.get("审计条目"),
            "文件标识": candidate.get("文件标识"),
            "字节": candidate.get("字节"),
            "已采用": adopted,
            # 可校验 = 已解析到带编号和原文的凭据，且凭据不是待核状态。
            "可校验": bool(credential) and bool(credential.get("凭据编号")) and bool(
                credential.get("审计原文")) and credential.get("status") != CREDENTIAL_PENDING,
            "凭据编号": (credential or {}).get("凭据编号", ""),
            "凭据状态": (credential or {}).get("status", ""),
            "原因": reason,
        }

    def _create_actual_delete_credential(
        self, lake_table: str, candidate: dict[str, Any],
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item_no = str(candidate.get("审计条目") or candidate.get("标识") or candidate.get("文件标识"))
        credential = self._find_credential(lake_table, item_no)
        receipt_id = str((receipt or {}).get("receipt_id") or f"DEL-{candidate.get('文件标识', '')}")
        actual_bytes = int((receipt or {}).get("file_bytes") or candidate.get("字节", 0) or 0)
        if credential is None:
            credential = {
                "id": _next_id(CREDENTIAL_MODULE),
                "凭据编号": f"RC-DEL-{int(time.time())}-{len(store.rows(CREDENTIAL_MODULE)) + 1:03d}",
                "所属湖表": lake_table,
                "来源包": "",
                "审计条目": item_no,
                "处置对象": str(candidate.get("文件标识", "")),
                "文件标识": str(candidate.get("文件标识", "")),
                "实际释放字节": 0,
                "声明释放字节": int(candidate.get("字节", 0)),
                "确认来源": SOURCE_ACTUAL_DELETE,
                "候选确认来源": SOURCE_ACTUAL_DELETE,
                "包内回执编号": "",
                "外部回执": False,
                "外部回执编号": "",
                "审计处置": "已删除",
                "审计原文": f"存储实际删除确认：{candidate.get('文件标识', '')}",
                "原始字段": {},
                "缺失项": [],
                "已计释放量": False,
                "核销回收单": "",
                "首次确认时间": "",
                "最近确认时间": "",
                "status": CREDENTIAL_PENDING,
                "pending": True,
                "abnormal": False,
            }
            store.rows(CREDENTIAL_MODULE).append(credential)
        if credential.get("status") in (CREDENTIAL_VALID, CREDENTIAL_WRITTEN_OFF):
            # 已被同一次删除或包外回执确认过：不重复计释放量。
            return credential
        self._activate_credential(
            credential, actual_bytes=actual_bytes, source=SOURCE_ACTUAL_DELETE,
            receipt_no=receipt_id,
        )
        return credential

    def close_order(self, order_id: int) -> dict[str, Any]:
        order = self._find_order(order_id)
        if order is None:
            return {"ok": False, "message": f"回收单 {order_id} 不存在"}
        order["status"] = "已关闭"
        order["pending"] = False
        order["归账锁定"] = True
        return {"ok": True, "message": "回收单已关闭，已分摊结果保留", "order": order,
                "detail": self.order_detail(order_id)}

    def order_detail(self, order_id: int) -> dict[str, Any] | None:
        order = self._find_order(order_id)
        if order is None:
            return None
        lake_table = str(order.get("所属湖表", ""))
        credentials = [
            row for row in store.rows(CREDENTIAL_MODULE)
            if str(row.get("所属湖表", "")) == lake_table
        ]
        return {
            "order": order,
            "adoptions": order.get("本轮采用明细", []),
            "allocations": order.get("核销项", []),
            "credentials": credentials,
            "verified_items": [
                {
                    "审计条目": row.get("审计条目"),
                    "凭据编号": row.get("凭据编号"),
                    "核验状态": row.get("status"),
                    "实际释放字节": row.get("实际释放字节"),
                    "确认来源": row.get("确认来源"),
                    "外部回执": row.get("外部回执"),
                    "已分摊回收单": row.get("核销回收单", ""),
                }
                for row in credentials
            ],
        }

    def release_summary(self, lake_table: str) -> dict[str, Any]:
        """列出本湖表每一条目的有效凭据与释放量（同一文件只计一次）。"""
        credentials = [
            row for row in store.rows(CREDENTIAL_MODULE)
            if str(row.get("所属湖表", "")) == lake_table
            and row.get("status") in (CREDENTIAL_VALID, CREDENTIAL_WRITTEN_OFF)
        ]
        by_item: dict[str, dict[str, Any]] = {}
        for row in credentials:
            key = str(row.get("审计条目", ""))
            if key in by_item:
                continue  # 同一文件/条目只返回一份有效凭证
            by_item[key] = {
                "审计条目": key,
                "凭据编号": row.get("凭据编号"),
                "状态": row.get("status"),
                "实际释放字节": int(row.get("实际释放字节", 0)),
                "确认来源": row.get("确认来源"),
                "外部回执": bool(row.get("外部回执")),
                "审计原文": row.get("审计原文"),
                "缺失项": row.get("缺失项", []),
                "核销回收单": row.get("核销回收单", ""),
            }
        return {
            "lake_table": lake_table,
            "credentials": list(by_item.values()),
            "released_bytes_total": sum(item["实际释放字节"] for item in by_item.values()),
            "released_file_ids": [
                row.get("文件标识") for row in credentials if row.get("文件标识")
            ],
        }

    def check_released(self, lake_table: str, *, item_no: str = "", file_id: str = "") -> dict[str, Any]:
        """新回收流程识别同一文件是否已经释放。"""
        credential = None
        for row in store.rows(CREDENTIAL_MODULE):
            if str(row.get("所属湖表", "")) != lake_table:
                continue
            if item_no and str(row.get("审计条目", "")) == item_no:
                credential = row
                break
            if file_id and str(row.get("文件标识", "")) == file_id:
                credential = row
                break
        if credential is None:
            return {"lake_table": lake_table, "released": False, "credential": None}
        return {
            "lake_table": lake_table,
            "released": credential.get("status") in (CREDENTIAL_VALID, CREDENTIAL_WRITTEN_OFF),
            "credential": credential,
        }


reclaim_engine = ReclaimEngine()
