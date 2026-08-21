<template>
  <div>
    <ProjectNav :id="id" :active="`/projects/${id}/generate`" />

    <!-- 流程按钮区 -->
    <div class="flow-bar">
      <div class="left">
        <el-tag :type="jobStatusType(job?.status || '')" effect="dark">
          {{ job?.status || '未生成' }}
        </el-tag>
        <span v-if="job" class="stat">
          章节 {{ job.done_sections }}/{{ job.total_sections }}
          <template v-if="job.failed_sections"> · 失败 {{ job.failed_sections }}</template>
        </span>
        <el-button v-if="!outlineLoaded && !planning" type="primary" :loading="planning"
                   @click="onPlanOutline">规划大纲</el-button>
        <el-button v-else-if="!isGenerating" type="primary" :loading="starting"
                   @click="onStartJob">
          {{ job?.status === '部分失败' ? '断点继续' : '开始生成' }}
        </el-button>
        <el-button v-else disabled><el-icon class="is-loading"><Loading /></el-icon>生成中…</el-button>
      </div>
      <div class="right">
        <el-button @click="openResponseTable">需求响应表</el-button>
        <el-button type="success" plain @click="downloadDocx">下载文档 (docx)</el-button>
      </div>
    </div>

    <div class="split">
      <!-- 左：章节树 -->
      <div class="tree-pane">
        <div class="pane-title">章节大纲</div>
        <el-tree :data="treeData" node-key="id" :expand-on-click-node="false"
                 highlight-current :current-node-key="secId" :indent="14"
                 @node-click="onNodeClick">
          <template #default="{ data }">
            <span class="node">
              <span class="node-ico" :class="`st-${data.status}`">{{ statusIcon(data.status) }}</span>
              <span class="node-title">{{ data.title }}</span>
            </span>
          </template>
        </el-tree>
      </div>

      <!-- 右：当前章节 + 日志 -->
      <div class="main-pane">
        <template v-if="draft">
          <div class="sec-head">
            <div class="sec-title">
              {{ draft.title }}
              <el-tag v-if="draft.status === '已编辑'" size="small" type="warning">已编辑</el-tag>
              <el-tag size="small" type="info">v{{ draft.version }}</el-tag>
            </div>
            <div class="sec-actions">
              <el-button size="small" @click="onRegenerate">重新生成</el-button>
              <el-button v-if="!editing" size="small" type="primary" plain @click="startEdit">
                编辑
              </el-button>
              <template v-else>
                <el-button size="small" type="primary" :loading="saving" @click="saveEdit">保存</el-button>
                <el-button size="small" @click="cancelEdit">取消</el-button>
              </template>
            </div>
          </div>
          <div v-if="draft.warnings?.length" class="warns">
            <el-alert v-for="(w, i) in draft.warnings" :key="i" :title="w"
                      type="warning" :closable="false" class="warn-item" />
          </div>
          <el-input v-if="editing" v-model="editText" type="textarea" :rows="24"
                    class="editor" />
          <div v-else class="content" v-loading="secLoading">
            <MarkdownView :content="draft.content_md" />
          </div>
        </template>
        <el-empty v-else description="在左侧选择章节查看内容" />

        <!-- SSE 实时日志 -->
        <div class="log-panel">
          <div class="pane-title">
            生成日志
            <el-tag v-if="sseAlive" size="small" type="success">SSE 实时</el-tag>
            <el-tag v-else-if="isGenerating" size="small" type="warning">轮询中</el-tag>
          </div>
          <div ref="logBox" class="log-box">
            <div v-for="(l, i) in logs" :key="i" class="log-line" :class="`log-${l.level}`">
              [{{ l.time }}] {{ l.message }}
            </div>
            <el-empty v-if="!logs.length" description="暂无日志" :image-size="40" />
          </div>
        </div>
      </div>
    </div>

    <!-- 需求响应表弹窗 -->
    <el-dialog v-model="tableVisible" title="需求响应表" width="760px" top="5vh">
      <div v-loading="tableLoading" class="rtable">
        <MarkdownView v-if="rtableMd" :content="rtableMd" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import ProjectNav from '@/components/ProjectNav.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import {
  generationApi, streamJobEvents,
  type GenerationJob, type GenerationSectionNode, type SectionDraft,
} from '@/api/client'

const route = useRoute()
const id = route.params.id as string

const outline = ref<GenerationSectionNode[]>([])
const outlineLoaded = ref(false)
const job = ref<GenerationJob | null>(null)
const planning = ref(false)
const starting = ref(false)

const secId = ref('')
const draft = ref<SectionDraft | null>(null)
const secLoading = ref(false)
const editing = ref(false)
const editText = ref('')
const saving = ref(false)

const logs = ref<{ time: string; level: string; message: string }[]>([])
const logBox = ref<HTMLElement | null>(null)
const sseAlive = ref(false)
const tableVisible = ref(false)
const tableLoading = ref(false)
const rtableMd = ref('')

let abort: AbortController | null = null
let fallbackTimer: number | undefined
let statusTimer: number | undefined

const isGenerating = computed(() => job.value?.status === '生成中')

// ── 数据加载 ──────────────────────────────────────────────
async function fetchOutline() {
  try {
    const res = await generationApi.outline(id)
    outline.value = res.sections
    outlineLoaded.value = true
  } catch (e: any) {
    if (e?.response?.status === 404) outlineLoaded.value = false
    else ElMessage.error(e?.response?.data?.detail || '大纲加载失败')
  }
}

async function fetchJob() {
  try {
    const res = await generationApi.listJobs(id)
    job.value = res.jobs[0] || null
  } catch { /* 404 等忽略 */ }
}

async function refreshAll() {
  await Promise.all([fetchOutline(), fetchJob()])
}

// ── 章节树 ──────────────────────────────────────────────
interface TreeNode { id: string; title: string; status: string; children: TreeNode[] }
function toTree(nodes: GenerationSectionNode[]): TreeNode[] {
  return nodes.map(n => ({
    id: n.id, title: n.title, status: n.status,
    children: toTree(n.children || []),
  }))
}
const treeData = computed(() => toTree(outline.value))

function statusIcon(s: string): string {
  switch (s) {
    case '已完成': return '✓'
    case '生成中': return '⭕'
    case '失败': return '⚠'
    case '跳过': return '⏭'
    default: return '○'
  }
}

async function onNodeClick(node: TreeNode) {
  secId.value = node.id
  await loadSection(node.id)
}

async function loadSection(sectionId: string) {
  secLoading.value = true
  editing.value = false
  try {
    draft.value = await generationApi.section(id, sectionId)
  } catch (e: any) {
    if (e?.response?.status === 409) {
      draft.value = null
      ElMessage.info(e.response.data.detail)
    } else {
      ElMessage.error(e?.response?.data?.detail || '章节加载失败')
    }
  } finally {
    secLoading.value = false
  }
}

// ── 流程操作 ─────────────────────────────────────────────
async function onPlanOutline() {
  planning.value = true
  try {
    const res = await generationApi.createOutline(id)
    ElMessage.success(`大纲规划完成：${res.total_sections} 个章节，需求映射 ${res.mapped_requirements} 条`)
    await fetchOutline()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '规划失败')
  } finally {
    planning.value = false
  }
}

async function onStartJob() {
  starting.value = true
  try {
    const res = await generationApi.startJob(id)
    ElMessage.success('已启动标书生成')
    await fetchJob()
    startSSE(res.job_id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败（需先规划大纲并完成需求匹配）')
  } finally {
    starting.value = false
  }
}

// ── SSE 实时日志 + 断连回退轮询 ──────────────────────────
function startSSE(jobId: string) {
  stopSSE()
  logs.value = []
  sseAlive.value = true
  abort = streamJobEvents(generationApi.eventsUrl(id, jobId), (e) => {
    if (e.event === 'log' || e.event === 'snapshot') {
      const d = e.data as Record<string, string>
      if (e.event === 'snapshot') {
        logs.value.push({ time: '—', level: 'info', message: `任务快照：${d.status} ${d.progress || ''}` })
      } else {
        logs.value.push({
          time: (d.created_at || '').slice(11, 19),
          level: d.level || 'info',
          message: d.message || '',
        })
      }
      scrollLogs()
    } else if (e.event === 'done') {
      sseAlive.value = false
      refreshAll()
      startFallbackPolling()
    }
  })
}

function stopSSE() {
  abort?.abort()
  abort = null
  sseAlive.value = false
}

// SSE 关闭后若 job 仍在生成中 → 2s 轮询兜底
function startFallbackPolling() {
  window.clearInterval(fallbackTimer)
  fallbackTimer = window.setInterval(async () => {
    await fetchJob()
    if (!isGenerating.value) {
      window.clearInterval(fallbackTimer)
      refreshAll()
      stopStatusPolling()
    }
  }, 2000)
}

// 页面级状态轮询（无 job 或 job 未在生成时轻量刷新）
function startStatusPolling() {
  stopStatusPolling()
  statusTimer = window.setInterval(async () => {
    await fetchJob()
    if (isGenerating.value && !abort) {
      // SSE 断连且仍在生成 → 回退轮询
      window.clearInterval(statusTimer)
      startFallbackPolling()
    }
    if (job.value?.status && ['已完成', '部分失败', '失败'].includes(job.value.status)) {
      stopStatusPolling()
      refreshAll()
    }
  }, 3000)
}
function stopStatusPolling() {
  window.clearInterval(statusTimer)
  statusTimer = undefined
}

function scrollLogs() {
  nextTick(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

// ── 编辑 / 重新生成 ──────────────────────────────────────
function startEdit() {
  editText.value = draft.value?.content_md || ''
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}
async function saveEdit() {
  if (!draft.value) return
  saving.value = true
  try {
    await generationApi.editSection(id, draft.value.section_id, editText.value)
    ElMessage.success('已保存（draft_status=已编辑）')
    editing.value = false
    await loadSection(draft.value.section_id)
    await fetchOutline()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}
async function onRegenerate() {
  if (!draft.value) return
  try {
    await ElMessageBox.confirm(
      `重新生成「${draft.value.title}」将覆盖人工编辑（版本 +1），确认？`,
      '重新生成章节', { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' })
  } catch { return }
  try {
    const res = await generationApi.regenerate(id, draft.value.section_id)
    ElMessage.success('已启动单章节重新生成')
    await fetchJob()
    startSSE(res.job_id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '重新生成失败')
  }
}

// ── 响应表 / 下载 ────────────────────────────────────────
async function openResponseTable() {
  tableVisible.value = true
  tableLoading.value = true
  try {
    const res = await generationApi.responseTable(id, 'markdown')
    rtableMd.value = res.content || ''
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    tableLoading.value = false
  }
}
function downloadDocx() {
  window.open(generationApi.documentUrl(id, 'docx'), '_blank')
}

function jobStatusType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === '已完成') return 'success'
  if (s === '生成中') return 'warning'
  if (s === '部分失败') return 'warning'
  if (s === '失败') return 'danger'
  return 'info'
}

onMounted(async () => {
  await refreshAll()
  if (isGenerating.value && job.value) startSSE(job.value.id)
  startStatusPolling()
})
onUnmounted(() => {
  stopSSE()
  window.clearInterval(fallbackTimer)
  stopStatusPolling()
})
</script>

<style scoped>
.flow-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  background: #fff;
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 14px;
}
.left { display: flex; align-items: center; gap: 12px; }
.right { display: flex; gap: 8px; }
.stat { font-size: 13px; color: #606266; }
.split {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.tree-pane {
  width: 300px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  padding: 12px;
  max-height: calc(100vh - 220px);
  overflow: auto;
}
.main-pane { flex: 1; min-width: 0; }
.pane-title {
  font-weight: 600;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.node { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.node-ico { font-size: 12px; color: #909399; width: 16px; text-align: center; }
.node-ico.st-已完成 { color: #67c23a; }
.node-ico.st-生成中 { color: #409eff; }
.node-ico.st-失败 { color: #f56c6c; }
.sec-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 10px;
}
.sec-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.sec-actions { display: flex; gap: 8px; }
.warns { margin-bottom: 10px; }
.warn-item { margin-bottom: 6px; }
.editor { background: #fff; }
.content {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  min-height: 300px;
  max-height: calc(100vh - 420px);
  overflow: auto;
}
.log-panel {
  background: #fff;
  border-radius: 8px;
  padding: 12px;
  margin-top: 14px;
}
.log-box {
  font-family: Consolas, monospace;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 6px;
  padding: 10px;
}
.log-line { line-height: 1.8; }
.log-error { color: #f48771; }
.log-warning { color: #e2c08d; }
.log-success { color: #89d185; }
.rtable { min-height: 200px; }
</style>
