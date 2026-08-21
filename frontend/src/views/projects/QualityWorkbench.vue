<template>
  <div>
    <ProjectNav :id="id" :active="`/projects/${id}/quality`" />

    <!-- 评分卡区 -->
    <div class="score-bar" v-if="report">
      <div class="total">
        <div class="total-num">{{ report.score }}</div>
        <div class="total-label">综合得分</div>
        <el-tag size="small" :type="scoreType(report.score)">{{ report.status }}</el-tag>
      </div>
      <div class="dims">
        <div v-for="d in report.dimensions" :key="d.name" class="dim">
          <div class="dim-head">
            <span class="dim-name">{{ d.name }}</span>
            <span class="dim-score">{{ d.score }}</span>
          </div>
          <el-progress :percentage="Math.max(0, Math.min(100, d.score))"
                       :stroke-width="8" :show-text="false"
                       :color="dimColor(d.score)" />
        </div>
      </div>
      <div class="counts">
        <div v-for="(v, k) in report.issue_counts" :key="k" class="count-item">
          <span class="c-num" :class="`c-${k}`">{{ v }}</span>
          <span class="c-label">{{ k }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else-if="!loading" description="尚未运行质量检查" :image-size="60" />

    <!-- 操作栏 -->
    <div class="toolbar">
      <div class="left">
        <el-select v-model="reportFilter" size="small" style="width: 260px"
                   placeholder="报告历史" :disabled="!reports.length"
                   @change="onReportChange">
          <el-option v-for="r in reports" :key="r.id" :value="r.id"
                     :label="`${r.created_at} · 得分 ${r.score}`" />
        </el-select>
        <el-checkbox v-model="includeLlm" size="small">启用 LLM 深度检查</el-checkbox>
      </div>
      <el-button type="primary" :loading="checking" @click="onCheck">运行检查</el-button>
    </div>

    <!-- 状态过滤 tabs -->
    <el-radio-group v-model="filter" size="small" class="filter-tabs">
      <el-radio-button value="待处理">待处理</el-radio-button>
      <el-radio-button value="已确认">已确认</el-radio-button>
      <el-radio-button value="已忽略">已忽略</el-radio-button>
      <el-radio-button value="已修复">已修复</el-radio-button>
      <el-radio-button value="">全部</el-radio-button>
    </el-radio-group>

    <!-- 问题列表 -->
    <el-table :data="issues" border stripe v-loading="loading" row-key="id">
      <el-table-column label="严重度" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="sevType(row.severity)" effect="dark">
            {{ row.severity }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="issue_type" label="类型" width="130" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="type-text">{{ row.issue_type }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="message" label="问题描述" min-width="260" show-overflow-tooltip />
      <el-table-column prop="suggestion" label="修复建议" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="muted">{{ row.suggestion || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="issueStatusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="240" align="center" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="onViewEvidence(row)">查看证据</el-button>
          <template v-if="row.status === '待处理'">
            <el-button size="small" text type="success" @click="onSetStatus(row, '已确认')">确认</el-button>
            <el-button size="small" text type="info" @click="onSetStatus(row, '已忽略')">忽略</el-button>
            <el-button v-if="row.autofixable" size="small" text type="warning"
                       :loading="fixingId === row.id" @click="onAutofix(row)">修复</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <!-- 证据抽屉 -->
    <el-drawer v-model="evdDrawer" size="480px" :title="`问题证据 · ${curIssue?.id || ''}`">
      <template v-if="curIssue">
        <el-alert :title="curIssue.message" :type="sevAlertType(curIssue.severity)"
                  :closable="false" class="evd-alert" />
        <div class="evd-title">来源定位（source_refs）</div>
        <div v-for="(ref, i) in curIssue.source_refs" :key="i" class="src-ref">
          <el-tag size="small" type="info">REF {{ i + 1 }}</el-tag>
          <span class="src-text">{{ ref }}</span>
          <el-button size="small" text type="primary" @click="jumpToSection(ref)">跳转</el-button>
        </div>
        <el-empty v-if="!curIssue.source_refs?.length" description="无来源定位信息"
                  :image-size="50" />
      </template>
    </el-drawer>

    <!-- 确认/忽略 备注对话框 -->
    <el-dialog v-model="noteDialog" :title="noteAction === '已确认' ? '确认问题' : '忽略问题'"
               width="420px">
      <el-input v-model="note" type="textarea" :rows="3"
                placeholder="处理备注（将写入审查记录）" />
      <template #footer>
        <el-button @click="noteDialog = false">取消</el-button>
        <el-button type="primary" :loading="patching" @click="submitNote">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ProjectNav from '@/components/ProjectNav.vue'
import { qualityApi, type QualityIssue, type QualityReport } from '@/api/client'

const route = useRoute()
const router = useRouter()
const id = route.params.id as string

const reports = ref<QualityReport[]>([])
const report = ref<QualityReport | null>(null)
const reportFilter = ref('')
const issues = ref<QualityIssue[]>([])
const filter = ref('待处理')
const loading = ref(false)
const checking = ref(false)
const includeLlm = ref(false)
const fixingId = ref('')

const evdDrawer = ref(false)
const curIssue = ref<QualityIssue | null>(null)

const noteDialog = ref(false)
const note = ref('')
const noteAction = ref('')
const noteIssue = ref<QualityIssue | null>(null)
const patching = ref(false)

async function fetchReports() {
  try {
    const res = await qualityApi.reports(id)
    reports.value = res.reports
    if (res.reports.length) {
      const latest = res.reports[0]
      reportFilter.value = latest.id
      report.value = latest
    } else {
      report.value = null
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '报告加载失败')
  }
}

async function fetchIssues() {
  loading.value = true
  try {
    const res = await qualityApi.issues(id, filter.value)
    issues.value = res.issues
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '问题加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchReports()
  fetchIssues()
})

// 报告历史切换
async function onReportChange() {
  report.value = reports.value.find(r => r.id === reportFilter.value) || null
  await fetchIssues()
}

async function onCheck() {
  checking.value = true
  try {
    await qualityApi.check(id, includeLlm.value)
    ElMessage.success('质量检查完成')
    await fetchReports()
    filter.value = '待处理'
    await fetchIssues()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '检查失败（需先生成标书）')
  } finally {
    checking.value = false
  }
}

function onViewEvidence(row: QualityIssue) {
  curIssue.value = row
  evdDrawer.value = true
}

// source_ref 形如 "章节 CH-XX-X" → 尝试跳转生成工作台对应章节
function jumpToSection(_refStr: string) {
  router.push(`/projects/${id}/generate`)
  ElMessage.info('已跳转标书生成工作台，请在左侧选择对应章节核对')
}

function onSetStatus(row: QualityIssue, status: string) {
  noteIssue.value = row
  noteAction.value = status
  note.value = ''
  noteDialog.value = true
}

async function submitNote() {
  if (!noteIssue.value) return
  patching.value = true
  try {
    await qualityApi.patchIssue(noteIssue.value.id, {
      status: noteAction.value,
      reviewer: '人工审查',
      note: note.value,
    })
    ElMessage.success(`已${noteAction.value === '已确认' ? '确认' : '忽略'}该问题`)
    noteDialog.value = false
    await fetchIssues()
    await fetchReports()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally {
    patching.value = false
  }
}

async function onAutofix(row: QualityIssue) {
  fixingId.value = row.id
  try {
    await qualityApi.autofix(row.id)
    ElMessage.success('自动修复完成')
    await fetchIssues()
    await fetchReports()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '修复失败')
  } finally {
    fixingId.value = ''
  }
}

function sevType(s: string): 'danger' | 'warning' | 'info' | 'success' {
  if (s === 'CRITICAL') return 'danger'
  if (s === 'ERROR') return 'danger'
  if (s === 'WARNING') return 'warning'
  return 'info'
}
function sevAlertType(s: string): 'error' | 'warning' | 'info' {
  if (s === 'CRITICAL' || s === 'ERROR') return 'error'
  if (s === 'WARNING') return 'warning'
  return 'info'
}
function issueStatusType(s: string): 'warning' | 'success' | 'info' | 'danger' {
  if (s === '待处理') return 'warning'
  if (s === '已确认') return 'success'
  if (s === '已忽略') return 'info'
  return 'danger'
}
function scoreType(score: number): 'success' | 'warning' | 'danger' {
  if (score >= 80) return 'success'
  if (score >= 60) return 'warning'
  return 'danger'
}
function dimColor(score: number): string {
  if (score >= 80) return '#67c23a'
  if (score >= 60) return '#e6a23c'
  return '#f56c6c'
}
</script>

<style scoped>
.score-bar {
  display: flex;
  align-items: center;
  gap: 24px;
  background: #fff;
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.total { text-align: center; min-width: 110px; }
.total-num { font-size: 40px; font-weight: 700; color: #303133; line-height: 1.1; }
.total-label { font-size: 12px; color: #909399; margin: 4px 0 6px; }
.dims { flex: 1; min-width: 320px; display: grid; grid-template-columns: 1fr; gap: 10px; }
.dim-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 3px;
}
.dim-name { color: #606266; }
.dim-score { color: #303133; font-weight: 600; }
.counts { display: flex; gap: 14px; flex-wrap: wrap; }
.count-item { text-align: center; min-width: 52px; }
.c-num { display: block; font-size: 18px; font-weight: 700; }
.c-CRITICAL { color: #f56c6c; }
.c-ERROR { color: #e6a23c; }
.c-WARNING { color: #e6a23c; }
.c-INFO { color: #909399; }
.c-label { font-size: 11px; color: #909399; }
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.left { display: flex; align-items: center; gap: 12px; }
.filter-tabs { margin-bottom: 12px; }
.type-text { font-size: 12px; color: #606266; }
.muted { color: #c0c4cc; font-size: 12px; }
.evd-alert { margin-bottom: 14px; }
.evd-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
.src-ref {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  margin-bottom: 8px;
}
.src-text { font-size: 12px; color: #606266; flex: 1; word-break: break-all; }
</style>
