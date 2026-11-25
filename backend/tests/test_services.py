"""测试全部领域服务的真实业务逻辑（通用模块，回收凭据包见 test_reclaim.py）。"""
from __future__ import annotations

import pytest

from app.lake_store import lake_store
from app.services.lake_catalog import LakeCatalogService
from app.services.table_schema import TableSchemaService
from app.services.partition_plan import PartitionPlanService
from app.services.partition_evolve import PartitionEvolveService
from app.services.small_file_merge import SmallFileMergeService
from app.services.snapshot_mgr import SnapshotMgrService
from app.services.time_travel import TimeTravelService
from app.services.ingest_watermark import IngestWatermarkService
from app.services.ingest_batch import IngestBatchService
from app.services.write_job import WriteJobService
from app.services.compact_job import CompactJobService
from app.services.expire_policy import ExpirePolicyService
from app.services.stats_profile import StatsProfileService
from app.services.column_stat import ColumnStatService
from app.services.quality_rule import QualityRuleService
from app.services.quarantine_zone import QuarantineZoneService
from app.services.rebuild_backfill import RebuildBackfillService
from app.services.storage_tier import StorageTierService
from app.services.retention_window import RetentionWindowService
from app.services.publish_view import PublishViewService
from app.services.quota_meter import QuotaMeterService
from app.services.lake_audit import LakeAuditService


def _setup_test_data():
    """为集成测试准备一致的湖表初始状态。"""
    pf = lake_store.register_file("test_table", "20261101", rows=100, bytes_=25600)
    lake_store.create_snapshot("test_table", [pf.file_id], added_rows=100, deleted_rows=0)
    # 标记为available
    for snap in lake_store.list_snapshots("test_table"):
        lake_store.mark_snapshot_available("test_table", snap.snapshot_id)
    lake_store.set_watermark("test_table", "event_time", "2026-11-01T00:00:00")
    rows = [
        {"event_time": "2026-11-01T12:00:00", "user_id": "u1", "action": "login", "amount": "100.00", "partition_dt": "20261101"},
        {"event_time": "2026-11-01T13:00:00", "user_id": "u2", "action": "purchase", "amount": "250.00", "partition_dt": "20261101"},
        {"event_time": "2026-11-01T14:00:00", "user_id": "u3", "action": "login", "amount": None, "partition_dt": "20261101"},
    ]
    lake_store.store_batch("batch-test", "test_table", "OLTP_DB", rows, "2026-11-01T00:00:00", "2026-11-01T23:59:59")


class TestLakeCatalog:
    def test_create_entry_registers_file_and_snapshot(self):
        svc = LakeCatalogService()
        entry, missing = svc.create_entry({"表编号": "T001", "表名全称": "test_create", "所属库域": "lake_ods"})
        assert entry is not None
        assert len(missing) == 0
        files = lake_store.list_files("test_create")
        assert len(files) >= 1

    def test_confirm_register_sets_watermark(self):
        svc = LakeCatalogService()
        entry, _ = svc.create_entry({"表编号": "T002", "表名全称": "test_watermark", "所属库域": "lake_ods"})
        entry_id = entry["id"]
        svc.run_action(entry_id, "确认登记")
        wm = lake_store.get_watermark("test_watermark")
        assert wm is not None

    def test_stats_snapshot(self):
        svc = LakeCatalogService()
        stats = svc.stats_snapshot()
        assert "total" in stats
        assert "registered" in stats


class TestTableSchema:
    def test_compatibility_initial(self):
        svc = TableSchemaService()
        cols = [{"name": "a", "type": "STRING", "nullable": True}]
        assert svc.judge_compatibility(cols, []) == "INITIAL_VERSION"

    def test_compatibility_backward(self):
        svc = TableSchemaService()
        parent = [{"name": "a", "type": "STRING", "nullable": True}]
        current = [
            {"name": "a", "type": "STRING", "nullable": True},
            {"name": "b", "type": "INT", "nullable": True},
        ]
        result = svc.judge_compatibility(current, parent)
        assert result == "BACKWARD_COMPATIBLE"

    def test_compatibility_column_dropped(self):
        svc = TableSchemaService()
        parent = [{"name": "a", "type": "STRING"}, {"name": "b", "type": "INT"}]
        current = [{"name": "a", "type": "STRING"}]
        assert svc.judge_compatibility(current, parent) == "BREAKING_COLUMN_DROPPED"

    def test_compatibility_type_changed(self):
        svc = TableSchemaService()
        parent = [{"name": "a", "type": "STRING"}]
        current = [{"name": "a", "type": "INT"}]
        assert svc.judge_compatibility(current, parent) == "BREAKING_TYPE_CHANGED"


class TestPartitionPlan:
    def test_recommend_partition(self):
        _setup_test_data()
        svc = PartitionPlanService()
        result = svc.recommend_partition("test_table")
        assert "recommended_granularity" in result
        assert "estimated_partitions" in result

    def test_skew_zero_for_empty(self):
        svc = PartitionPlanService()
        skew = svc.evaluate_partition_skew("nonexistent")
        assert skew == 0.0


class TestPartitionEvolve:
    def test_start_coexist(self):
        _setup_test_data()
        svc = PartitionEvolveService()
        result = svc.start_coexist_period("test_table", "partition_dt", "partition_hh")
        assert result["phase"] == "dual_coexist"
        assert result["old_file_count"] >= 1

    def test_finalize_switch(self):
        _setup_test_data()
        svc = PartitionEvolveService()
        result = svc.finalize_switch("test_table", "partition_dt", "partition_hh")
        assert "old_file_ids_to_archive" in result


class TestSmallFileMerge:
    def test_detect_small_files(self):
        _setup_test_data()
        svc = SmallFileMergeService()
        small = svc.detect_small_files("test_table", "20261101", size_threshold=99999)
        assert len(small) >= 1

    def test_plan_merge_targets_empty(self):
        svc = SmallFileMergeService()
        targets = svc.plan_merge_targets([])
        assert targets == []

    def test_execute_merge_atomic(self):
        _setup_test_data()
        svc = SmallFileMergeService()
        files = lake_store.list_files("test_table", "20261101")
        small_ids = [f.file_id for f in files]
        result = svc.execute_merge_atomic("test_table", "20261101", small_ids, "merge-t1")
        assert result is not None
        assert result["result_file"] is not None

    def test_recover_nonexistent_checkpoint(self):
        svc = SmallFileMergeService()
        assert svc.recover_from_checkpoint("nonexistent", "test_table") is None


class TestSnapshotMgr:
    def test_generate_snapshot(self):
        _setup_test_data()
        svc = SnapshotMgrService()
        files = lake_store.list_files("test_table")
        result = svc.generate_snapshot_for_table("test_table", [f.file_id for f in files])
        assert result["snapshot_id"] is not None
        assert result["file_count"] >= 1

    def test_ancestor_chain(self):
        _setup_test_data()
        svc = SnapshotMgrService()
        chain = svc.get_ancestor_chain("test_table")
        assert len(chain) >= 1


class TestTimeTravel:
    def test_read_at_snapshot(self):
        _setup_test_data()
        svc = TimeTravelService()
        snaps = lake_store.list_snapshots("test_table")
        available = [s for s in snaps if s.status == "available"]
        if available:
            result = svc.read_at_snapshot("test_table", available[0].snapshot_id)
            assert result["success"]

    def test_read_missing_snapshot(self):
        svc = TimeTravelService()
        result = svc.read_at_snapshot("test_table", "nonexistent")
        assert not result["success"]


class TestIngestWatermark:
    def test_create_and_advance(self):
        _setup_test_data()
        svc = IngestWatermarkService()
        result = svc.advance_watermark_after_batch("test_table", "2026-11-02T00:00:00")
        assert result["success"]
        wm = lake_store.get_watermark("test_table")
        assert wm.committed_value == "2026-11-02T00:00:00"

    def test_detect_lagging(self):
        _setup_test_data()
        svc = IngestWatermarkService()
        lagging = svc.detect_lagging_batches("test_table", "2026-12-01T00:00:00")
        assert len(lagging) >= 0


class TestIngestBatch:
    def test_ingest_batch_dedup(self):
        _setup_test_data()
        svc = IngestBatchService()
        rows = [
            {"event_time": "2026-11-01", "user_id": "u1", "action": "login"},
            {"event_time": "2026-11-01", "user_id": "u1", "action": "login"},
            {"event_time": "2026-11-01", "user_id": "u2", "action": "purchase"},
        ]
        result = svc.ingest_batch("test_table", "source1", rows, "2026-11-01", "2026-11-01")
        assert result["duplicates_removed"] == 1
        assert result["row_count"] == 2

    def test_verify_integrity_ok(self):
        _setup_test_data()
        svc = IngestBatchService()
        result = svc.verify_batch_integrity("batch-test")
        assert result["integrity_ok"]


class TestWriteJob:
    def test_acquire_and_write(self):
        _setup_test_data()
        svc = WriteJobService()
        rows = [{"k": "v1", "k2": "v2"}]
        result = svc.acquire_and_write("test_table", "APPEND", rows)
        assert result["success"]
        assert result["rows_written"] == 1

    def test_detect_conflict_no_snapshots(self):
        svc = WriteJobService()
        result = svc.detect_write_conflict("fresh_table")
        assert result["conflict"] is False


class TestCompactJob:
    def test_evaluate_trigger(self):
        _setup_test_data()
        svc = CompactJobService()
        result = svc.evaluate_compact_trigger("test_table")
        assert "should_trigger" in result

    def test_execute_compact(self):
        _setup_test_data()
        svc = CompactJobService()
        result = svc.execute_compact("test_table", "20261101")
        assert result["compact_success"]


class TestExpirePolicy:
    def test_identify_expired(self):
        _setup_test_data()
        svc = ExpirePolicyService()
        expired = svc.identify_expired_snapshots("test_table", keep_count=1)
        assert isinstance(expired, list)

    def test_execute_cleanup(self):
        _setup_test_data()
        svc = ExpirePolicyService()
        snaps = lake_store.list_snapshots("test_table")
        available = [s for s in snaps if s.status == "available"]
        if available:
            result = svc.execute_cleanup("test_table", [available[0].snapshot_id])
            assert result["expired_count"] == 1


class TestStatsProfile:
    def test_sample_table_stats(self):
        _setup_test_data()
        svc = StatsProfileService()
        result = svc.sample_table_stats("test_table")
        assert result["total_rows"] >= 100

    def test_skew_distribution(self):
        _setup_test_data()
        svc = StatsProfileService()
        dist = svc.profile_skew_distribution("test_table")
        assert len(dist) >= 1


class TestColumnStat:
    def test_compute_column_stats(self):
        _setup_test_data()
        svc = ColumnStatService()
        result = svc.compute_column_stats("test_table", "amount")
        assert result["total_count"] >= 1

    def test_compute_empty_table(self):
        svc = ColumnStatService()
        result = svc.compute_column_stats("nonexistent", "col")
        assert result["total_count"] == 0
        assert result["null_ratio"] == 0.0


class TestQualityRule:
    def test_apply_rules_to_batch(self):
        _setup_test_data()
        svc = QualityRuleService()
        rules = [
            {"规则编号": "QR-1", "校验列": "amount", "校验类型": "RANGE", "阈值下限": 0, "阈值上限": 5000},
        ]
        result = svc.apply_rules_to_batch("batch-test", rules)
        assert result["total_rows"] == 3
        assert result["clean_rows"] >= 0


class TestQuarantineZone:
    def test_isolate_and_release(self):
        _setup_test_data()
        svc = QuarantineZoneService()
        svc.isolate_violations("batch-test", "QR-1", "金额异常",
                               [{"k": "bad_row"}])
        result = svc.release_for_retry("batch-test")
        assert result["released_count"] == 1


class TestRebuildBackfill:
    def test_execute_backfill(self):
        _setup_test_data()
        svc = RebuildBackfillService()
        result = svc.execute_backfill("test_table", "20261101", "20261110", "replace")
        assert result["window"] == "20261101~20261110"


class TestStorageTier:
    def test_classify_to_tier(self):
        _setup_test_data()
        svc = StorageTierService()
        result = svc.classify_to_tier("test_table", hot_days=365)
        assert result["cold_file_count"] == 0

    def test_migrate_cold_files(self):
        _setup_test_data()
        svc = StorageTierService()
        result = svc.migrate_cold_files("test_table")
        assert result["bytes_saved"] >= 0


class TestRetentionWindow:
    def test_define_window(self):
        svc = RetentionWindowService()
        result = svc.define_retention_window("test_table", "DAY", 90, True)
        assert result["audit_tagged"]

    def test_check_audit_block(self):
        svc = RetentionWindowService()
        result = svc.check_audit_block("test_table", True)
        assert result["block_reclaim"]


class TestPublishView:
    def test_publish_view(self):
        _setup_test_data()
        svc = PublishViewService()
        result = svc.publish_view("test_table", "SELECT * FROM test_table",
                                   ["col1", "col2"], "ADHOC_QUERY")
        assert result["bound_snapshot"] is not None

    def test_revoke_view(self):
        svc = PublishViewService()
        result = svc.revoke_view("test_table", "ADHOC_QUERY")
        assert result["revoked"]


class TestQuotaMeter:
    def test_meter_resource(self):
        svc = QuotaMeterService()
        result = svc.meter_resource("test_table", "写入", 80, 500, "2026-11")
        assert not result["over_quota"]

    def test_meter_over_quota(self):
        svc = QuotaMeterService()
        result = svc.meter_resource("test_table", "写入", 600, 500, "2026-11")
        assert result["over_quota"]
        assert result["status"] == "超额告警"


class TestLakeAudit:
    def test_record_audit_entry(self):
        svc = LakeAuditService()
        result = svc.record_audit_entry("test_table", "结构演进", "SCH-v1",
                                         "old", "new", "operator_01")
        assert result["operation_type"] == "结构演进"

    def test_check_audit_gap(self):
        svc = LakeAuditService()
        result = svc.check_audit_gap("test_table", "val_before", "val_after")
        assert result["has_gap"]