<template>
  <section class="page" data-testid="reclaim-package-detail">
    <header class="page-head">
      <div>
        <h2>回收凭据包导入详情</h2>
        <p class="page-desc">
          审计 CSV 原文、缺项、被排除条目与凭据核验结论都在这一页；待核条目必须由包外实际回收回执确认。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="detail-back" type="button" @click="goBack">返回列表</button>
        <button class="btn" data-testid="detail-retry" type="button" :disabled="busy" @click="retryImport">
          重试导入
        </button>
        <button class="btn primary" data-testid="detail-confirm" type="button" :disabled="busy" @click="confirmImport">
          确认导入
        </button>
        <button class="btn" data-testid="detail-export" type="button" @click="exportPackage">
          导出凭据包
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="detail-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="detail-notice">{{ notice }}</p>

    <div v-if="detail" class="stat-row">
      <article class="stat-card">
        <span class="stat-label">导入状态</span>
        <strong class="stat-value" data-testid="detail-status">{{ detail.import_state.status }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">导入进度</span>
        <strong class="stat-value" data-testid="detail-progress">
          {{ detail.import_state.processed }}/{{ detail.import_state.total }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已匹配删除</span>
        <strong class="stat-value">{{ detail.summary.matched_deleted }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核条目</span>
        <strong class="stat-value" data-testid="detail-pending">{{ detail.summary.pending_verify }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已计释放字节</span>
        <strong class="stat-value" data-testid="detail-released">{{ detail.summary.released_bytes }}</strong>
      </article>
    </div>

    <p v-if="detail && detail.import_state.failure_position" class="error-text" data-testid="detail-failure">
      上次中断位置：{{ detail.import_state.failure_position }}
    </p>

    <template v-if="detail">
      <h3>包内审计条目</h3>
      <table class="data-table" data-testid="detail-items">
        <thead>
          <tr>
            <th>条目编号</th>
            <th>所属湖表</th>
            <th>操作对象</th>
            <th>审计处置</th>
            <th>处理状态</th>
            <th>凭据</th>
            <th>审计原文</th>
            <th>缺项</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.items" :key="`${item.条目编号}-${item.处理状态}`">
            <td>{{ item.条目编号 }}</td>
            <td>{{ item.所属湖表 }}</td>
            <td>{{ item.操作对象 }}</td>
            <td>{{ item.审计处置 }}</td>
            <td>{{ item.处理状态 }}</td>
            <td>{{ item.凭据编号 || '—' }}</td>
            <td class="raw-text">{{ item.审计原文 }}</td>
            <td>{{ (item.缺失项 || []).join('、') || '—' }}</td>
          </tr>
          <tr v-if="!detail.items.length">
            <td colspan="8" class="empty-state">该包还没有导入条目</td>
          </tr>
        </tbody>
      </table>

      <h3>被排除条目（跨湖表 / 不在时间段 / 缺编号）</h3>
      <table class="data-table" data-testid="detail-excluded">
        <thead>
          <tr><th>条目编号</th><th>所属湖表</th><th>排除原因</th><th>审计原文</th></tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in detail.excluded_items" :key="`ex-${index}`">
            <td>{{ item.条目编号 || '—' }}</td>
            <td>{{ item.所属湖表 || '—' }}</td>
            <td>{{ item.排除原因 }}</td>
            <td class="raw-text">{{ item.审计原文 }}</td>
          </tr>
          <tr v-if="!detail.excluded_items.length">
            <td colspan="4" class="empty-state">没有条目被排除</td>
          </tr>
        </tbody>
      </table>

      <h3>回收凭据</h3>
      <table class="data-table" data-testid="detail-credentials">
        <thead>
          <tr>
            <th>凭据编号</th>
            <th>审计条目</th>
            <th>状态</th>
            <th>实际释放字节</th>
            <th>确认来源</th>
            <th>外部回执</th>
            <th>核销回收单</th>
            <th>审计原文</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="credential in detail.credentials" :key="credential.凭据编号">
            <td>{{ credential.凭据编号 }}</td>
            <td>{{ credential.审计条目 }}</td>
            <td>{{ credential.status }}</td>
            <td>{{ credential.实际释放字节 }}</td>
            <td>{{ credential.确认来源 }}</td>
            <td>{{ credential.外部回执 ? '是' : '否' }}</td>
            <td>{{ credential.核销回收单 || '—' }}</td>
            <td class="raw-text">{{ credential.审计原文 }}</td>
          </tr>
          <tr v-if="!detail.credentials.length">
            <td colspan="8" class="empty-state">尚无凭据</td>
          </tr>
        </tbody>
      </table>

      <h3>包外实际回收回执</h3>
      <form class="analysis-form" data-testid="receipt-form" @submit.prevent="submitReceipt">
        <label>
          <span>审计条目</span>
          <input v-model="receipt.item_no" data-testid="receipt-item" required placeholder="如 G" />
        </label>
        <label>
          <span>实际释放字节</span>
          <input v-model.number="receipt.actual_bytes" data-testid="receipt-bytes" type="number" min="0" required />
        </label>
        <label>
          <span>回执编号</span>
          <input v-model="receipt.receipt_no" data-testid="receipt-no" placeholder="EXT-..." />
        </label>
        <button class="btn" data-testid="receipt-submit" type="submit" :disabled="busy">补报回执</button>
      </form>

      <table class="data-table" data-testid="detail-receipts">
        <thead>
          <tr><th>回执编号</th><th>审计条目</th><th>实际释放字节</th><th>来源</th><th>登记时间</th><th>首次计入</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.external_receipts" :key="item.回执编号">
            <td>{{ item.回执编号 }}</td>
            <td>{{ item.审计条目 }}</td>
            <td>{{ item.实际释放字节 }}</td>
            <td>{{ item.来源 }}</td>
            <td>{{ item.登记时间 }}</td>
            <td>{{ item.首次计入 ? '是' : '否（已计过，不重复累加）' }}</td>
          </tr>
          <tr v-if="!detail.external_receipts.length">
            <td colspan="6" class="empty-state">暂无包外回执</td>
          </tr>
        </tbody>
      </table>
    </template>

    <p v-else-if="!errorMessage" class="empty-state">正在读取导入详情…</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  confirmReclaimImport,
  downloadReclaimPackage,
  loadReclaimDetail,
  reportReclaimReceipt,
  retryReclaimImport,
  type ReclaimDetail,
} from '@/api/backend-service'

const props = defineProps<{ packageId: string }>()
const router = useRouter()
const detail = ref<ReclaimDetail | null>(null)
const errorMessage = ref('')
const notice = ref('')
const busy = ref(false)
const receipt = ref<{ item_no: string; actual_bytes: number; receipt_no: string }>({
  item_no: '',
  actual_bytes: 0,
  receipt_no: '',
})

function packageId(): number {
  return Number(props.packageId)
}

async function reload() {
  errorMessage.value = ''
  try {
    detail.value = await loadReclaimDetail(packageId())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入详情读取失败'
  }
}

async function retryImport() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await retryReclaimImport(packageId())
    notice.value = String(result.message ?? '已重试导入')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试导入失败'
  } finally {
    busy.value = false
  }
}

async function confirmImport() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await confirmReclaimImport(packageId())
    notice.value = String(result.message ?? '已确认导入')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认导入失败'
  } finally {
    busy.value = false
  }
}

async function submitReceipt() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await reportReclaimReceipt(packageId(), { ...receipt.value })
    notice.value = String(result.message ?? '回执已登记')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补报回执失败'
  } finally {
    busy.value = false
  }
}

async function exportPackage() {
  errorMessage.value = ''
  try {
    await downloadReclaimPackage(packageId())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function goBack() {
  router.push('/reclaim_package')
}

onMounted(reload)
</script>
