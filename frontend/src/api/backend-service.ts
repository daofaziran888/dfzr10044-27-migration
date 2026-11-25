import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ------------------------- 回收凭据包 -------------------------

export type ReclaimPackageSummary = {
  id: number
  status: string
  包编号: string
  所属湖表: string
  时间段起: string
  时间段止: string
  条目数: number
  已确认条目: number
  待核条目: number
  导入进度: string
  失败位置?: string
}

export type ReclaimDetail = {
  package: ReclaimPackageSummary & Record<string, unknown>
  import_state: {
    status: string
    processed: number
    total: number
    checkpoint: number
    failure_position: string
    reimport_count: number
  }
  summary: {
    total: number
    matched_deleted: number
    pending_verify: number
    excluded: number
    confirmed: number
    pending: number
    external_confirmed_pending: number
    released_bytes: number
  }
  items: Record<string, any>[]
  excluded_items: Record<string, any>[]
  credentials: Record<string, any>[]
  external_receipts: Record<string, any>[]
}

export async function importAuditPackage(payload: Record<string, unknown>): Promise<Record<string, any>> {
  const response = await request('/api/reclaim_package/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.message ?? '审计包导入失败')
  return data
}

export async function loadReclaimDetail(packageId: number): Promise<ReclaimDetail> {
  const response = await request(`/api/reclaim_package/${packageId}/detail`)
  if (!response.ok) throw new Error('导入详情读取失败')
  return await response.json()
}

export async function retryReclaimImport(packageId: number): Promise<Record<string, any>> {
  const response = await request(`/api/reclaim_package/${packageId}/retry`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
  if (!response.ok) throw new Error('重试导入失败')
  return await response.json()
}

export async function confirmReclaimImport(packageId: number): Promise<Record<string, any>> {
  const response = await request(`/api/reclaim_package/${packageId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
  if (!response.ok) throw new Error('确认导入失败')
  return await response.json()
}

export async function reportReclaimReceipt(
  packageId: number,
  payload: Record<string, unknown>,
): Promise<Record<string, any>> {
  const response = await request(`/api/reclaim_package/${packageId}/receipts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('补报回执失败')
  return await response.json()
}

export async function setReclaimAmounts(
  packageId: number,
  amounts: Record<string, number>,
): Promise<Record<string, any>> {
  const response = await request(`/api/reclaim_package/${packageId}/amounts`, {
    method: 'POST',
    body: JSON.stringify({ amounts }),
  })
  if (!response.ok) throw new Error('登记实删量失败')
  return await response.json()
}

export async function downloadReclaimPackage(packageId: number): Promise<void> {
  const response = await request(`/api/reclaim_package/${packageId}/export`)
  if (!response.ok) throw new Error('凭据包导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `reclaim_package_${packageId}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export type ReclaimOrderDetail = {
  order: Record<string, any>
  adoptions: Record<string, any>[]
  allocations: Record<string, any>[]
  verified_items: Record<string, any>[]
}

export async function createReclaimOrder(payload: Record<string, unknown>): Promise<Record<string, any>> {
  const response = await request('/api/reclaim_order', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.message ?? '回收单提交失败')
  return data
}

export async function executeReclaimOrder(
  orderId: number,
  payload: Record<string, unknown> = {},
): Promise<Record<string, any>> {
  const response = await request(`/api/reclaim_order/${orderId}/execute`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('回收执行失败')
  return await response.json()
}

export async function loadReclaimOrder(orderId: number): Promise<ReclaimOrderDetail> {
  const response = await request(`/api/reclaim_order/${orderId}/detail`)
  if (!response.ok) throw new Error('回收单详情读取失败')
  return await response.json()
}

export async function registerReclaimFile(payload: Record<string, unknown>): Promise<Record<string, any>> {
  const response = await request('/api/reclaim/files', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.message ?? '可处置文件登记失败')
  return data
}
