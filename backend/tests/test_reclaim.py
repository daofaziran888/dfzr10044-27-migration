"""回收凭据包：审计 CSV 迁包、凭据核验、回收核销与幂等归账的领域测试。"""
from __future__ import annotations

import pytest

from app.lake_store import LakeStore
from app.services import reclaim_engine as engine_module
from app.services.reclaim_engine import ReclaimEngine
from app.store import Store


AUDIT_CSV = """条目编号,所属湖表,操作类型,操作对象,变更前值,变更后值,操作者标识,操作时间,status
F,ods_reclaim,过期清理,file-F,100字节,0字节,op1,2026-11-10T01:00:00,已核查
G,ods_reclaim,过期清理,file-G,40字节,40字节,op1,2026-11-11T01:00:00,待核查
H,ods_other,过期清理,file-H,70字节,0字节,op2,2026-11-12T01:00:00,已核查
"""


@pytest.fixture
def engine(monkeypatch) -> ReclaimEngine:
    """每个测试独立的内存引擎与空仓库，避免跨用例串数据。"""
    fresh_store = Store()
    for module in ("reclaim_package", "reclaim_credential", "reclaim_order"):
        fresh_store._tables[module] = []
    fresh_lake = LakeStore()
    monkeypatch.setattr(engine_module, "store", fresh_store)
    monkeypatch.setattr(engine_module, "lake_store", fresh_lake)
    instance = ReclaimEngine()
    return instance


def _import(engine: ReclaimEngine, **kwargs):
    payload = {
        "lake_table": "ods_reclaim",
        "window_start": "2026-11-01",
        "window_end": "2026-11-30",
        "csv_text": AUDIT_CSV,
    }
    payload.update(kwargs)
    return engine.import_package(**payload)


class TestImportPackage:
    def test_f_matched_deleted_g_pending_h_excluded(self, engine: ReclaimEngine):
        result = _import(engine)
        assert result["ok"]
        package = result["package"]
        items = {item["条目编号"]: item for item in package["明细"]}
        assert items["F"]["审计处置"] == "已删除"
        assert items["F"]["处理状态"] == "已匹配删除"
        assert items["G"]["审计处置"] == "待核"
        assert items["G"]["处理状态"] == "待核"
        assert "H" not in items
        excluded = package["排除明细"]
        assert any(row["条目编号"] == "H" and row["排除原因"] == "跨湖表" for row in excluded)
        # H 的原文仍然可查
        assert any("file-H" in row["审计原文"] for row in excluded)

    def test_f_not_effective_until_import_confirmed(self, engine: ReclaimEngine):
        result = _import(engine)
        package_id = result["package"]["id"]
        credentials = {row["审计条目"]: row for row in engine_module.store.rows("reclaim_credential")}
        assert credentials["F"]["status"] == "待核"
        assert credentials["F"]["实际释放字节"] == 0
        assert credentials["G"]["status"] == "待核"

        engine.confirm_package(package_id)
        credentials = {row["审计条目"]: row for row in engine_module.store.rows("reclaim_credential")}
        assert credentials["F"]["status"] == "有效"
        assert credentials["F"]["确认来源"] == "包内回执"
        assert credentials["F"]["实际释放字节"] == 100
        assert credentials["G"]["status"] == "待核"

    def test_window_filter_excludes_out_of_range_rows(self, engine: ReclaimEngine):
        csv_text = AUDIT_CSV.replace("2026-11-10T01:00:00", "2026-09-10T01:00:00")
        result = _import(engine, csv_text=csv_text)
        excluded = result["package"]["排除明细"]
        assert any(row["条目编号"] == "F" and row["排除原因"] == "不在时间段内" for row in excluded)

    def test_original_text_and_missing_fields_preserved(self, engine: ReclaimEngine):
        csv_text = AUDIT_CSV.replace("op1,2026-11-11T01:00:00,待核查", ",2026-11-11T01:00:00,待核查")
        result = _import(engine, csv_text=csv_text)
        item = next(row for row in result["package"]["明细"] if row["条目编号"] == "G")
        assert item["审计原文"]
        assert "操作者标识" in item["缺失项"]


class TestExternalReceipt:
    def test_external_receipt_confirms_g_only_once(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        first = engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G")
        assert first["ok"]
        assert first["external_receipt"]["首次计入"] is True
        second = engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G-dup")
        assert second["external_receipt"]["首次计入"] is False

        summary = engine.release_summary("ods_reclaim")
        by_item = {row["审计条目"]: row for row in summary["credentials"]}
        assert by_item["G"]["实际释放字节"] == 40
        assert by_item["G"]["确认来源"] == "包外回执"
        assert summary["released_bytes_total"] == 40

    def test_external_receipt_marks_pending_confirmed_by_outside_receipt(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G")
        detail = engine.package_detail(package_id)
        pending = next(item for item in detail["items"] if item["条目编号"] == "G")
        assert pending["处理状态"] == "待核"
        g_credential = next(row for row in detail["credentials"] if row["审计条目"] == "G")
        assert g_credential["外部回执"] is True
        assert g_credential["确认来源"] == "包外回执"
        assert detail["summary"]["external_confirmed_pending"] == 1


class TestRetryFromFailure:
    def test_retry_recovers_from_failure_and_keeps_g_receipt(self, engine: ReclaimEngine):
        result = _import(engine, fail_at=2)
        assert result["ok"] is False
        assert "第2条" in result["message"]
        package_id = result["package"]["id"]
        assert result["checkpoint"] == 1

        receipt = engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G")
        assert receipt["ok"]

        retried = engine.retry_package(package_id)
        assert retried["ok"]
        assert retried["package"]["失败位置"] == ""
        detail = engine.package_detail(package_id)
        g = next(row for row in detail["credentials"] if row["审计条目"] == "G")
        assert g["实际释放字节"] == 40
        assert g["外部回执"] is True
        assert g["status"] == "有效"

    def test_retry_is_idempotent(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.confirm_package(package_id)
        before = len(engine_module.store.rows("reclaim_credential"))
        again = _import(engine, confirm=True)
        assert again["ok"]
        assert len(engine_module.store.rows("reclaim_credential")) == before
        assert again["detail"]["summary"]["released_bytes"] == 100


class TestOrderWriteOff:
    def _prepare(self, engine: ReclaimEngine, *, keep_evidence: bool):
        package_id = _import(engine)["package"]["id"]
        engine.set_historical_amounts(package_id, {"F": 100, "G": 40})
        engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G")
        lake = engine_module.lake_store
        file_j = lake.register_file("ods_reclaim", "reclaim", rows=8, bytes_=80)
        order = engine.create_order(
            lake_table="ods_reclaim",
            required_bytes=100,
            keep_evidence=keep_evidence,
            candidates=[
                {"标识": "F", "类型": "历史凭据", "审计条目": "F", "字节": 100},
                {"标识": "G", "类型": "历史凭据", "审计条目": "G", "字节": 40},
                {"标识": "J", "类型": "新文件", "文件标识": file_j.file_id, "字节": 80},
            ],
        )["order"]
        return package_id, order, file_j

    def test_keep_evidence_reports_partial_write_off(self, engine: ReclaimEngine):
        _, order, file_j = self._prepare(engine, keep_evidence=True)
        result = engine.execute_order(order["id"])
        executed = result["order"]
        assert executed["已核销字节"] == 40
        assert executed["待补字节"] == 60
        assert executed["status"] == "部分核销"
        assert engine_module.lake_store.get_file("ods_reclaim", file_j.file_id) is not None
        j_adoption = next(row for row in executed["本轮采用明细"] if row["标识"] == "J")
        assert j_adoption["已采用"] is False
        assert "保留证据" in j_adoption["原因"]
        f_adoption = next(row for row in executed["本轮采用明细"] if row["标识"] == "F")
        assert f_adoption["已采用"] is False
        assert f_adoption["可校验"] is False

    def test_deleted_j_write_off_uses_actual_delete_receipt(self, engine: ReclaimEngine):
        _, order, file_j = self._prepare(engine, keep_evidence=False)
        result = engine.execute_order(order["id"])
        executed = result["order"]
        assert executed["已核销字节"] == 120
        assert executed["待补字节"] == 0
        assert executed["status"] == "已核销"
        assert engine_module.lake_store.get_file("ods_reclaim", file_j.file_id) is None
        j_credential = next(
            row for row in engine_module.store.rows("reclaim_credential") if row["审计条目"] == "J"
        )
        assert j_credential["确认来源"] == "实际删除确认"
        assert j_credential["实际释放字节"] == 80

    def test_f_arrival_after_j_deleted_keeps_original_accounting(self, engine: ReclaimEngine):
        package_id, order, file_j = self._prepare(engine, keep_evidence=False)
        engine.execute_order(order["id"])
        engine.report_receipt(package_id, item_no="F", actual_bytes=100, receipt_no="EXT-F")
        again = engine.execute_order(order["id"])
        assert again["order"]["已核销字节"] == 120
        assert again["order"]["status"] == "已核销"
        allocations = again["order"]["核销项"]
        assert {row["标识"] for row in allocations} == {"G", "J"}
        # 最终 F、G 各有一份有效凭证，释放量只计一次，原文/缺项仍可查。
        summary = engine.release_summary("ods_reclaim")
        by_item = {row["审计条目"]: row for row in summary["credentials"]}
        assert by_item["F"]["实际释放字节"] == 100
        assert by_item["G"]["实际释放字节"] == 40
        assert by_item["F"]["实际释放字节"] + by_item["G"]["实际释放字节"] == 140
        # J 是独立的新文件，按实际删除确认单列一份凭据，不与 F/G 混在一起。
        assert summary["released_bytes_total"] == 220
        assert "file-F" in by_item["F"]["审计原文"]
        # F 已生效但本轮没有重复分摊，下一张回收单可以继续用 F。
        next_order = engine.create_order(lake_table="ods_reclaim", required_bytes=50)["order"]
        assert any(candidate["标识"] == "F" for candidate in next_order["候选明细"])

    def test_pending_f_is_never_sent_for_storage_delete(self, engine: ReclaimEngine):
        package_id, order, _ = self._prepare(engine, keep_evidence=False)
        # 模拟 F 指向一个仍存在的文件，但凭据仍在待核。
        file_f = engine_module.lake_store.register_file("ods_reclaim", "reclaim", rows=1, bytes_=100)
        order["候选明细"].append({
            "标识": "F-file", "类型": "新文件", "审计条目": "F",
            "凭据编号": "", "文件标识": file_f.file_id, "字节": 100, "可安全处置": True,
        })
        engine.execute_order(order["id"])
        assert engine_module.lake_store.get_file("ods_reclaim", file_f.file_id) is not None
        assert engine.package_detail(package_id) is not None

    def test_same_item_never_double_allocated_across_orders(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.confirm_package(package_id)  # F 有效 100 字节
        first = engine.create_order(lake_table="ods_reclaim", required_bytes=100)["order"]
        result = engine.execute_order(first["id"])
        assert result["order"]["已核销字节"] == 100

        second = engine.create_order(lake_table="ods_reclaim", required_bytes=100)
        assert second["ok"]
        # 第一张单已核销 F，第二张单不应再把 F 列为可用候选。
        assert not any(c["标识"] == "F" for c in second["order"]["候选明细"])


class TestReleaseSummary:
    def test_f_and_g_each_one_valid_credential(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.confirm_package(package_id)
        engine.report_receipt(package_id, item_no="G", actual_bytes=40, receipt_no="EXT-G")
        summary = engine.release_summary("ods_reclaim")
        by_item = {row["审计条目"]: row for row in summary["credentials"]}
        assert set(by_item) == {"F", "G"}
        assert by_item["F"]["实际释放字节"] == 100
        assert by_item["G"]["实际释放字节"] == 40
        assert summary["released_bytes_total"] == 140

    def test_check_released_recognizes_released_file(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.confirm_package(package_id)
        result = engine.check_released("ods_reclaim", item_no="F")
        assert result["released"] is True
        pending = engine.check_released("ods_reclaim", item_no="G")
        assert pending["released"] is False

    def test_export_package_csv_is_reimportable(self, engine: ReclaimEngine):
        package_id = _import(engine)["package"]["id"]
        engine.confirm_package(package_id)
        exported = engine.export_package_csv(package_id)
        assert "凭据状态" in exported
        assert "file-F" in exported
        parsed = engine.parse_audit_csv(exported)
        assert len(parsed) == 2
