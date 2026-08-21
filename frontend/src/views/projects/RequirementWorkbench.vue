<template>
  <div>
    <ProjectNav :id="id" :active="`/projects/${id}/requirements`" />

    <!-- 匹配状态栏 -->
    <div class="status-bar">
      <div class="left">
        <el-tag :type="runStatusType(status?.status || '')" effect="dark">
          {{ status?.status || '未执行匹配' }}
        </el-tag>
        <span v-if="status?.progress" class="prog">{{ status.progress }}</span>
        <span class="stat">规范需求 <b>{{ status?.canonical_count ?? 0 }}</b> 条</span>
        <span class="stat">匹配记录 <b>{{ status?.match_count ?? 0 }}</b> 条</span>
      </div>
      <div class="right">
        <div class="dist">
          <span v-for="d in distItems" :key="d.label" class="dist-item">
            <i class="dot" :class="`dot-${d.cls}`"></i>{{ d.label }} {{ d.value }}
          </span>
        </div>
        <el-button type="primary" :loading="matching"
                   :disabled="status?.status === '匹配中'"
                   @click="onMatch">启动匹配</el-button>
      </div>
    </div>

    <!-- 状态过滤 tabs -->
    <el-radio-group v-model="filter" size="small" class="filter-tabs">
      <el-radio-button value="">全部</el-radio-button>
      <el-radio-button value="FULL">FULL</el-radio-button>
      <el-radio-button value="PARTIAL">PARTIAL</el-radio-button>
      <el-radio-button value="MISSING">MISSING</el-radio-button>
      <el-radio-button value="UNKNOWN">UNKNOWN</el-radio-button>
    </el-radio-group>

    <!-- 主表：规范需求 + 匹配结果联合 -->
    <el-table :data="rows" border stripe v-loading="loading" @row-click="onRowClick"
              row-class-name="clickable-row">
      <el-table-column prop="req.id" label="需求编号" width="130">
        <template #default="{ row }">
          <span class="mono">{{ row.req.id }}</span>
          <span v-if="row.req.is_star" class="star">★</span>
        </template>
      </el-table-column>
      <el-table-column label="招标要求" min-width="280" show-overflow-tooltip>
        <template #default="{ row }">
          <div class="req-title">{{ row.req.title }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="req.req_type" label="类型" width="110">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.req.req_type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="企业响应" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.match" class="resp">{{ row.match.reason }}</span>
          <span v-else class="muted">未匹配</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.match" size="small" :type="statusTagType(row.match.status)">
            {{ row.match.status }}
          </el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="90" align="center">
        <template #default="{ row }">
          <span v-if="row.match" class="mono">{{ row.match.confidence.toFixed(2) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <!-- 证据链抽屉 -->
    <el-drawer v-model="drawerVisible" size="620px" :title="drawerTitle">
      <EvidenceChain :match="currentMatch" :requirement="currentReq"
                     :evidences="evidences" :loading="detailLoading" />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import ProjectNav from '@/components/ProjectNav.vue'
import EvidenceChain from '@/components/EvidenceChain.vue'
import {
  matchingApi, type CanonicalRequirement, type EvidenceItem, type MatchRecord,
} from '@/api/client'

const route = useRoute()
const id = route.params.id as string

const status = ref<Awaited<ReturnType<typeof matchingApi.status>> | null>(null)
const reqList = ref<CanonicalRequirement[]>([])
const matchList = ref<MatchRecord[]>([])
const filter = ref('')
const loading = ref(false)
const matching = ref(false)

const drawerVisible = ref(false)
const detailLoading = ref(false)
const currentMatch = ref<MatchRecord | null>(null)
const currentReq = ref<CanonicalRequirement | null>(null)
const evidences = ref<EvidenceItem[]>([])
const drawerTitle = computed(() =>
  currentMatch.value ? `证据链 · ${currentMatch.value.id}` : '证据链')

let timer: number | undefined

async function fetchAll() {
  loading.value = true
  try {
    const [s, r, m] = await Promise.all([
      matchingApi.status(id),
      matchingApi.canonicalRequirements(id),
      matchingApi.matches(id),
    ])
    status.value = s
    reqList.value = r.requirements
    matchList.value = m.matches
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

// 联合查询：matches 按 requirement_id 关联 canonical requirements
const rows = computed(() => {
  const matchMap = new Map(matchList.value.map(m => [m.requirement_id, m]))
  const merged = reqList.value.map(req => ({ req, match: matchMap.get(req.id) || null }))
  if (!filter.value) return merged
  return merged.filter(r => r.match?.status === filter.value)
})

const distItems = computed(() => {
  const counts: Record<string, number> = { FULL: 0, PARTIAL: 0, MISSING: 0, UNKNOWN: 0 }
  for (const m of matchList.value) {
    if (m.status in counts) counts[m.status]++
  }
  return [
    { label: 'FULL', value: counts.FULL, cls: 'full' },
    { label: 'PARTIAL', value: counts.PARTIAL, cls: 'partial' },
    { label: 'MISSING', value: counts.MISSING, cls: 'missing' },
    { label: 'UNKNOWN', value: counts.UNKNOWN, cls: 'unknown' },
  ]
})

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    if (status.value?.status === '匹配中') {
      try {
        await fetchAll()
        if (status.value?.status !== '匹配中') stopPolling()
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
  fetchAll()
  startPolling()
})
onUnmounted(stopPolling)

async function onMatch() {
  matching.value = true
  try {
    await matchingApi.start(id)
    ElMessage.success('已启动需求匹配，完成后自动刷新')
    startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败（需先完成需求提取）')
  } finally {
    matching.value = false
  }
}

// 行点击 → 按需加载 matchDetail（证据链）
async function onRowClick(row: { req: CanonicalRequirement; match: MatchRecord | null }) {
  if (!row.match) {
    ElMessage.info('该需求尚未执行匹配')
    return
  }
  currentMatch.value = row.match
  currentReq.value = row.req
  evidences.value = []
  drawerVisible.value = true
  detailLoading.value = true
  try {
    const d = await matchingApi.matchDetail(id, row.match.id)
    evidences.value = d.evidences
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '证据链加载失败')
  } finally {
    detailLoading.value = false
  }
}

function statusTagType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'FULL') return 'success'
  if (s === 'PARTIAL') return 'warning'
  if (s === 'MISSING') return 'danger'
  return 'info'
}
function runStatusType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === '已完成') return 'success'
  if (s === '匹配中') return 'warning'
  if (s === '失败') return 'danger'
  return 'info'
}
</script>

<style scoped>
.status-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  background: #fff;
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 12px;
}
.left { display: flex; align-items: center; gap: 12px; }
.prog { font-size: 12px; color: #909399; }
.stat { font-size: 13px; color: #606266; }
.right { display: flex; align-items: center; gap: 14px; }
.dist { display: flex; gap: 10px; }
.dist-item { font-size: 12px; color: #606266; display: flex; align-items: center; gap: 3px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-full { background: #67c23a; }
.dot-partial { background: #e6a23c; }
.dot-missing { background: #f56c6c; }
.dot-unknown { background: #c0c4cc; }
.filter-tabs { margin-bottom: 12px; }
.mono { font-family: Consolas, monospace; font-size: 12px; }
.star { color: #f56c6c; font-weight: 700; margin-left: 3px; }
.req-title { font-size: 13px; }
.resp { font-size: 12px; color: #606266; }
.muted { color: #c0c4cc; }
:deep(.clickable-row) { cursor: pointer; }
</style>
