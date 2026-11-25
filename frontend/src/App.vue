<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">湖表工坊 · 湖表分区演化与小文件归并平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向湖表目录登记、表结构演进、分区规划与演化、小文件归并、快照与时间旅行、入湖水位推进、压实调度、统计画像、隔离区处置、消费视图发布与存储分层的湖表治理平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "湖表目录", path: "/lake_catalog" }, { label: "结构演进", path: "/table_schema" }, { label: "分区规划", path: "/partition_plan" }, { label: "分区演化", path: "/partition_evolve" }, { label: "小文件归并", path: "/small_file_merge" }, { label: "快照管理", path: "/snapshot_mgr" }, { label: "时间旅行", path: "/time_travel" }, { label: "入湖水位", path: "/ingest_watermark" }, { label: "入湖批次", path: "/ingest_batch" }, { label: "写入作业", path: "/write_job" }, { label: "压实作业", path: "/compact_job" }, { label: "过期策略", path: "/expire_policy" }, { label: "统计画像", path: "/stats_profile" }, { label: "列统计", path: "/column_stat" }, { label: "入湖质量规则", path: "/quality_rule" }, { label: "隔离区", path: "/quarantine_zone" }, { label: "重建补数", path: "/rebuild_backfill" }, { label: "存储分层", path: "/storage_tier" }, { label: "保留窗口", path: "/retention_window" }, { label: "消费视图", path: "/publish_view" }, { label: "资源额度计量", path: "/quota_meter" }, { label: "湖表审计", path: "/lake_audit" }, { label: "回收凭据包", path: "/reclaim_package" }, { label: "回收凭据", path: "/reclaim_credential" }, { label: "回收核销单", path: "/reclaim_order" }]
</script>
