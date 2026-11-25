<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button
          v-if="meta.key === 'reclaim_package'"
          class="btn"
          data-testid="open-import"
          type="button"
          @click="openImport"
        >
          导入审计 CSV
        </button>
        <button
          v-if="meta.key === 'reclaim_order'"
          class="btn"
          data-testid="open-order"
          type="button"
          @click="openOrder"
        >
          提交回收单
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <RouterLink
              v-if="detailPath(row)"
              class="link"
              :data-testid="`detail-${row.id}`"
              :to="detailPath(row)!"
            >
              查看详情
            </RouterLink>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showImport" class="modal-backdrop" data-testid="import-modal">
      <form class="modal" @submit.prevent="submitImport">
        <h3>导入审计 CSV 为回收凭据包</h3>
        <label class="create-field">
          <span>所属湖表</span>
          <input v-model="importValues.lake_table" data-testid="import-lake-table" required />
        </label>
        <label class="create-field">
          <span>时间段起</span>
          <input v-model="importValues.window_start" data-testid="import-window-start" placeholder="2026-11-01" required />
        </label>
        <label class="create-field">
          <span>时间段止</span>
          <input v-model="importValues.window_end" data-testid="import-window-end" placeholder="2026-11-30" required />
        </label>
        <label class="create-field">
          <span>审计 CSV 文件</span>
          <input data-testid="import-file" type="file" accept=".csv,text/csv" @change="pickCsv" />
        </label>
        <label class="create-field">
          <span>或直接粘贴 CSV 内容</span>
          <textarea
            v-model="importValues.csv_text"
            data-testid="import-csv-text"
            rows="6"
            placeholder="条目编号,所属湖表,操作类型,操作对象,变更前值,变更后值,操作者标识,操作时间,status"
          />
        </label>
        <label class="create-field">
          <span>模拟中断位置（可选，用于演练从失败位置恢复）</span>
          <input v-model="importValues.fail_at" data-testid="import-fail-at" type="number" min="1" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showImport = false">取消</button>
          <button class="btn primary" data-testid="submit-import" type="submit" :disabled="importing">
            {{ importing ? '导入中…' : '开始导入' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showOrder" class="modal-backdrop" data-testid="order-modal">
      <form class="modal" @submit.prevent="submitOrder">
        <h3>提交回收单</h3>
        <label class="create-field">
          <span>所属湖表</span>
          <input v-model="orderValues.lake_table" data-testid="order-lake-table" required />
        </label>
        <label class="create-field">
          <span>所需核销字节</span>
          <input
            v-model.number="orderValues.required_bytes"
            data-testid="order-required-bytes"
            type="number"
            min="1"
            required
          />
        </label>
        <label class="create-field">
          <span>候选审计条目（逗号分隔，留空则自动取本表有效凭据）</span>
          <input v-model="orderValues.items" data-testid="order-items" placeholder="AUD-002,AUD-004" />
        </label>
        <label class="create-field">
          <span>可安全处置的新文件标识（可选，如 J）</span>
          <input v-model="orderValues.new_file_id" data-testid="order-new-file" />
        </label>
        <label class="create-field">
          <span>可安全处置的新文件字节数（可选）</span>
          <input v-model.number="orderValues.new_file_bytes" data-testid="order-new-file-bytes" type="number" min="0" />
        </label>
        <label class="create-field">
          <span>保留新文件作为证据（本轮不删除）</span>
          <input v-model="orderValues.keep_evidence" data-testid="order-keep-evidence" type="checkbox" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showOrder = false">取消</button>
          <button class="btn primary" data-testid="submit-order" type="submit" :disabled="submittingOrder">
            {{ submittingOrder ? '提交中…' : '提交回收单' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  createEntry,
  createReclaimOrder,
  downloadEntries,
  importAuditPackage,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const router = useRouter()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const showImport = ref(false)
const importing = ref(false)
const importValues = ref({
  lake_table: '',
  window_start: '',
  window_end: '',
  csv_text: '',
  fail_at: '',
})
const showOrder = ref(false)
const submittingOrder = ref(false)
const orderValues = ref({
  lake_table: '',
  required_bytes: 0,
  items: '',
  new_file_id: '',
  new_file_bytes: 0,
  keep_evidence: true,
})
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

function openImport() {
  errorMessage.value = ''
  importValues.value = {
    lake_table: filters.value['所属湖表'] ?? '',
    window_start: '',
    window_end: '',
    csv_text: '',
    fail_at: '',
  }
  showImport.value = true
}

function pickCsv(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    importValues.value.csv_text = String(reader.result ?? '')
  }
  reader.readAsText(file)
}

async function submitImport() {
  importing.value = true
  errorMessage.value = ''
  try {
    const payload: Record<string, unknown> = {
      lake_table: importValues.value.lake_table,
      window_start: importValues.value.window_start,
      window_end: importValues.value.window_end,
      csv_text: importValues.value.csv_text,
      source_file: 'lake_audit.csv',
    }
    if (importValues.value.fail_at) payload.fail_at = Number(importValues.value.fail_at)
    const result = await importAuditPackage(payload)
    showImport.value = false
    await reload()
    const packageId = result.package?.id
    if (packageId) {
      // 导入完成（含中断）都进入详情页，值班员能直接看失败位置与待核条目。
      router.push(`/reclaim_package/${packageId}`)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入失败'
  } finally {
    importing.value = false
  }
}

function detailPath(row: EntryRow): string | null {
  if (meta.key === 'reclaim_package') return `/reclaim_package/${row.id}`
  if (meta.key === 'reclaim_order') return `/reclaim_order/${row.id}`
  return null
}

function openOrder() {
  errorMessage.value = ''
  orderValues.value = {
    lake_table: filters.value['所属湖表'] ?? '',
    required_bytes: 0,
    items: '',
    new_file_id: '',
    new_file_bytes: 0,
    keep_evidence: true,
  }
  showOrder.value = true
}

async function submitOrder() {
  submittingOrder.value = true
  errorMessage.value = ''
  try {
    const lakeTable = orderValues.value.lake_table.trim()
    const candidates: Record<string, unknown>[] = []
    const itemNos = orderValues.value.items
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    for (const itemNo of itemNos) {
      candidates.push({ 标识: itemNo, 类型: '历史凭据', 审计条目: itemNo, 字节: 0 })
    }
    if (orderValues.value.new_file_id) {
      candidates.push({
        标识: 'J',
        类型: '新文件',
        文件标识: orderValues.value.new_file_id,
        字节: orderValues.value.new_file_bytes,
      })
    }
    const payload: Record<string, unknown> = {
      lake_table: lakeTable,
      required_bytes: orderValues.value.required_bytes,
      keep_evidence: orderValues.value.keep_evidence,
    }
    if (candidates.length) payload.candidates = candidates
    const result = await createReclaimOrder(payload)
    showOrder.value = false
    await reload()
    const orderId = result.order?.id
    if (orderId) router.push(`/reclaim_order/${orderId}`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回收单提交失败'
  } finally {
    submittingOrder.value = false
  }
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
