<template>
  <section class="page" data-testid="reclaim-order-detail">
    <header class="page-head">
      <div>
        <h2>回收核销单详情</h2>
        <p class="page-desc">
          列出本轮是否采用了可校验的历史凭据、哪些待核、哪些已被别的回收单分摊，以及实际释放字节的归账结果。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="order-back" type="button" @click="goBack">返回列表</button>
        <button class="btn" data-testid="order-execute" type="button" :disabled="busy" @click="execute">
          执行回收
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="order-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="order-notice">{{ notice }}</p>

    <div v-if="detail" class="stat-row">
      <article class="stat-card">
        <span class="stat-label">所需核销字节</span>
        <strong class="stat-value">{{ detail.order.所需核销字节 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已核销字节</span>
        <strong class="stat-value" data-testid="order-written">{{ detail.order.已核销字节 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待补字节</span>
        <strong class="stat-value" data-testid="order-remaining">{{ detail.order.待补字节 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">状态</span>
        <strong class="stat-value" data-testid="order-status">{{ detail.order.status }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">保留证据</span>
        <strong class="stat-value">{{ detail.order.保留证据 ? '是' : '否' }}</strong>
      </article>
    </div>

    <template v-if="detail">
      <h3>本轮采用明细</h3>
      <table class="data-table" data-testid="order-adoptions">
        <thead>
          <tr>
            <th>标识</th><th>类型</th><th>字节</th><th>已采用</th>
            <th>可校验</th><th>凭据状态</th><th>原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in detail.adoptions" :key="`adopt-${index}`">
            <td>{{ item.标识 }}</td>
            <td>{{ item.类型 }}</td>
            <td>{{ item.字节 }}</td>
            <td>{{ item.已采用 ? '是' : '否' }}</td>
            <td>{{ item.可校验 ? '是' : '否' }}</td>
            <td>{{ item.凭据状态 || '—' }}</td>
            <td>{{ item.原因 }}</td>
          </tr>
          <tr v-if="!detail.adoptions.length">
            <td colspan="7" class="empty-state">本轮还没有执行记录</td>
          </tr>
        </tbody>
      </table>

      <h3>已归账核销项</h3>
      <table class="data-table" data-testid="order-allocations">
        <thead>
          <tr><th>标识</th><th>审计条目</th><th>核销字节</th><th>确认来源</th><th>核销单</th></tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in detail.allocations" :key="`alloc-${index}`">
            <td>{{ item.标识 }}</td>
            <td>{{ item.审计条目 }}</td>
            <td>{{ item.核销字节 }}</td>
            <td>{{ item.确认来源 }}</td>
            <td>{{ item.核销单 }}</td>
          </tr>
          <tr v-if="!detail.allocations.length">
            <td colspan="5" class="empty-state">还没有归账项</td>
          </tr>
        </tbody>
      </table>

      <h3>核销明细</h3>
      <p class="page-desc" data-testid="order-detail-text">{{ detail.order.核销明细 || '本轮无核销' }}</p>

      <h3>可用凭据核验状态</h3>
      <table class="data-table" data-testid="order-credentials">
        <thead>
          <tr>
            <th>审计条目</th><th>凭据编号</th><th>核验状态</th>
            <th>实际释放字节</th><th>确认来源</th><th>外部回执</th><th>已分摊回收单</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.verified_items" :key="item.凭据编号">
            <td>{{ item.审计条目 }}</td>
            <td>{{ item.凭据编号 }}</td>
            <td>{{ item.核验状态 }}</td>
            <td>{{ item.实际释放字节 }}</td>
            <td>{{ item.确认来源 }}</td>
            <td>{{ item.外部回执 ? '是' : '否' }}</td>
            <td>{{ item.已分摊回收单 || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <p v-else-if="!errorMessage" class="empty-state">正在读取回收单详情…</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { executeReclaimOrder, loadReclaimOrder, type ReclaimOrderDetail } from '@/api/backend-service'

const props = defineProps<{ orderId: string }>()
const router = useRouter()
const detail = ref<ReclaimOrderDetail | null>(null)
const errorMessage = ref('')
const notice = ref('')
const busy = ref(false)

function orderId(): number {
  return Number(props.orderId)
}

async function reload() {
  errorMessage.value = ''
  try {
    detail.value = await loadReclaimOrder(orderId())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回收单详情读取失败'
  }
}

async function execute() {
  busy.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await executeReclaimOrder(orderId())
    notice.value = String(result.message ?? '回收已执行')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回收执行失败'
  } finally {
    busy.value = false
  }
}

function goBack() {
  router.push('/reclaim_order')
}

onMounted(reload)
</script>
