import { defineStore } from 'pinia'
import { ref } from 'vue'
import { workbenchApi, type KbStats, type WorkbenchProject } from '@/api/client'

// 工作台聚合数据（M6）；存在进行中任务时自动轮询刷新
export const useWorkbenchStore = defineStore('workbench', () => {
  const projects = ref<WorkbenchProject[]>([])
  const kb = ref<KbStats>({ materials: 0, capabilities: 0, ready_materials: 0 })
  const loading = ref(false)
  let timer: number | undefined

  async function fetchList() {
    loading.value = true
    try {
      const res = await workbenchApi.projects()
      projects.value = res.projects
      kb.value = res.kb
    } finally {
      loading.value = false
    }
  }

  function busy() {
    return projects.value.some(p =>
      p.extraction_status === '提取中' ||
      p.matching.status === '匹配中' ||
      p.generation.status === '生成中')
  }

  // 存在进行中的任务时自动轮询刷新
  function startPolling(intervalMs = 3000) {
    stopPolling()
    timer = window.setInterval(async () => {
      if (busy()) {
        try {
          await fetchList()
          if (!busy()) stopPolling()
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

  return { projects, kb, loading, fetchList, startPolling, stopPolling, busy }
})
