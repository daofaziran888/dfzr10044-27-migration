"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
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
from app.services.reclaim_package import ReclaimPackageService
from app.services.reclaim_credential import ReclaimCredentialService, ReclaimOrderService

SERVICES: dict[str, BaseModuleService] = {
    "lake_catalog": LakeCatalogService(),
    "table_schema": TableSchemaService(),
    "partition_plan": PartitionPlanService(),
    "partition_evolve": PartitionEvolveService(),
    "small_file_merge": SmallFileMergeService(),
    "snapshot_mgr": SnapshotMgrService(),
    "time_travel": TimeTravelService(),
    "ingest_watermark": IngestWatermarkService(),
    "ingest_batch": IngestBatchService(),
    "write_job": WriteJobService(),
    "compact_job": CompactJobService(),
    "expire_policy": ExpirePolicyService(),
    "stats_profile": StatsProfileService(),
    "column_stat": ColumnStatService(),
    "quality_rule": QualityRuleService(),
    "quarantine_zone": QuarantineZoneService(),
    "rebuild_backfill": RebuildBackfillService(),
    "storage_tier": StorageTierService(),
    "retention_window": RetentionWindowService(),
    "publish_view": PublishViewService(),
    "quota_meter": QuotaMeterService(),
    "lake_audit": LakeAuditService(),
    "reclaim_package": ReclaimPackageService(),
    "reclaim_credential": ReclaimCredentialService(),
    "reclaim_order": ReclaimOrderService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
