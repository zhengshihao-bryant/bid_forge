<template>
  <div>
    <ProjectNav :id="id" :name="detail?.name" :active="`/projects/${id}/tender`" />

    <div class="toolbar">
      <div class="stats">
        <el-tag :type="statusType(detail?.extraction_status || '')" effect="dark">
          {{ detail?.extraction_status || '—' }}
        </el-tag>
        <span class="stat-item">需求数量：<b>{{ detail?.requirement_count ?? 0 }}</b> 条</span>
        <span class="stat-item">评分点：<b>{{ detail?.score_point_count ?? 0 }}</b> 个</span>
      </div>
      <div>
        <el-button type="primary" :loading="extracting"
                   :disabled="detail?.extraction_status === '提取中'"
                   @click="onExtract">提取需求</el-button>
        <el-button @click="$router.push(`/projects/${id}/requirements`)">
          进入需求工作台 »
        </el-button>
      </div>
    </div>

    <el-table :data="detail?.documents || []" border stripe
              v-loading="loading" row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-box">
            <template v-if="row.sections?.length">
              <SectionTree :nodes="row.sections" />
            </template>
            <el-empty v-else description="该文件无章节结构（可能解析失败或为图片扫描件）"
                      :image-size="40" />
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="file_name" label="文件名" min-width="220"
                       show-overflow-tooltip />
      <el-table-column label="类型" width="80" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.file_type.toUpperCase() }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="total_pages" label="页数" width="70" align="center" />
      <el-table-column prop="char_count" label="字符数" width="90" align="center" />
      <el-table-column label="OCR 页" width="90" align="center">
        <template #default="{ row }">
          <span v-if="row.ocr_pages?.length">{{ row.ocr_pages.length }} 页</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="解析状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.parse_error ? 'danger' : 'success'">
            {{ row.parse_error ? '失败' : '成功' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="parse_error" label="解析错误" min-width="180"
                       show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.parse_error" class="err-text">{{ row.parse_error }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ProjectNav from '@/components/ProjectNav.vue'
import SectionTree from '@/components/SectionTree.vue'
import { tenderApi, type TenderDetail } from '@/api/client'

const route = useRoute()
const id = route.params.id as string
const detail = ref<TenderDetail | null>(null)
const loading = ref(false)
const extracting = ref(false)
let timer: number | undefined

async function fetchDetail() {
  loading.value = true
  try {
    detail.value = await tenderApi.get(id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    if (detail.value?.extraction_status === '提取中') {
      try {
        await fetchDetail()
        if (detail.value?.extraction_status !== '提取中') stopPolling()
      } catch { /* 网络抖动忽略 */ }
    }
  }, 3000)
}

function stopPolling() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

onMounted(() => {
  fetchDetail()
  startPolling()
})
onUnmounted(stopPolling)

async function onExtract() {
  extracting.value = true
  try {
    await tenderApi.extract(id)
    ElMessage.success('已启动需求提取，完成后自动刷新')
    startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  } finally {
    extracting.value = false
  }
}

function statusType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === '已完成' || s === '已提取') return 'success'
  if (s === '提取中') return 'warning'
  if (s === '失败') return 'danger'
  return 'info'
}
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  background: #fff;
  padding: 12px 16px;
  border-radius: 8px;
}
.stats { display: flex; align-items: center; gap: 14px; }
.stat-item { font-size: 13px; color: #606266; }
.expand-box { padding: 12px 24px; }
.err-text { color: #f56c6c; font-size: 12px; }
.muted { color: #c0c4cc; }
</style>
