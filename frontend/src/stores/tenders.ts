import { defineStore } from 'pinia'
import { ref } from 'vue'
import { tenderApi, type TenderSummary } from '@/api/client'

// 招标项目列表（M1 主数据）；轮询提取状态
export const useTendersStore = defineStore('tenders', () => {
  const tenders = ref<TenderSummary[]>([])
  const loading = ref(false)
  let timer: number | undefined

  async function fetchList() {
    loading.value = true
    try {
      tenders.value = await tenderApi.list()
    } finally {
      loading.value = false
    }
  }

  // 存在提取中的项目时自动轮询刷新
  function startPolling(intervalMs = 3000) {
    stopPolling()
    timer = window.setInterval(async () => {
      if (tenders.value.some(t => t.extraction_status === '提取中')) {
        try {
          tenders.value = await tenderApi.list()
          if (!tenders.value.some(t => t.extraction_status === '提取中')) {
            stopPolling()
          }
        } catch {
          /* 网络抖动忽略，下轮再试 */
        }
      }
    }, intervalMs)
  }

  function stopPolling() {
    if (timer !== undefined) {
      window.clearInterval(timer)
      timer = undefined
    }
  }

  return { tenders, loading, fetchList, startPolling, stopPolling }
})
