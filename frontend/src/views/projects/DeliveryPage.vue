<template>
  <div>
    <ProjectNav :id="id" :active="`/projects/${id}/deliver`" />

    <!-- 交付检查清单 -->
    <el-card class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>交付检查清单</span>
          <el-tag :type="blocked ? 'danger' : 'success'" effect="dark">
            {{ blocked ? '存在未处理问题' : '检查通过' }}
          </el-tag>
        </div>
      </template>
      <div v-loading="loading">
        <div class="check-row">
          <el-icon :class="unresolved.critical ? 'bad' : 'good'">
            <Close v-if="unresolved.critical" /><Check v-else />
          </el-icon>
          <span>CRITICAL 未处理：{{ unresolved.critical }} 个</span>
        </div>
        <div class="check-row">
          <el-icon :class="unresolved.error ? 'bad' : 'good'">
            <Close v-if="unresolved.error" /><Check v-else />
          </el-icon>
          <span>ERROR 未处理：{{ unresolved.error }} 个</span>
        </div>
        <div v-if="blocked" class="blocked-tip">
          <el-alert type="warning" :closable="false" show-icon
                    title="存在未处理的 CRITICAL/ERROR 问题，建议先到质量检查工作台处理后再导出终版。" />
          <el-button size="small" type="primary" style="margin-top: 10px"
                     @click="$router.push(`/projects/${id}/quality`)">去质量工作台处理 »</el-button>
        </div>
      </div>
    </el-card>

    <!-- 导出区 -->
    <el-card class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>导出终版</span>
          <div>
            <el-input v-model="reviewer" size="small" placeholder="审查人姓名"
                      style="width: 140px; margin-right: 8px" />
            <el-button type="primary" :loading="finalizing" @click="onFinalize(false)">导出终版</el-button>
            <el-button v-if="blocked" type="danger" plain :loading="finalizing"
                       @click="onFinalize(true)">强制导出</el-button>
          </div>
        </div>
      </template>
      <div v-if="finalizeResult" class="result">
        <el-result icon="success" title="终版已生成"
                   :sub-title="`审查人：${finalizeResult.reviewer || '—'} · 时间：${finalizeResult.review_time} · 得分：${finalizeResult.score}`">
          <template #extra>
            <div class="artifacts">
              <el-button type="success" @click="downloadDocx">
                <el-icon style="margin-right: 4px"><Download /></el-icon>final.docx
              </el-button>
              <el-button @click="tab = 'md'">查看 final.md</el-button>
              <el-button @click="tab = 'json'">查看 quality-report.json</el-button>
            </div>
          </template>
        </el-result>
      </div>
      <el-empty v-else-if="!loading" description="尚未导出终版（先完成质量检查并处理问题）"
                :image-size="60" />
    </el-card>

    <!-- 产物展示 -->
    <el-card v-if="finalizeResult" class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>交付产物</span>
          <el-radio-group v-model="tab" size="small">
            <el-radio-button value="md">final.md</el-radio-button>
            <el-radio-button value="json">quality-report.json</el-radio-button>
            <el-radio-button value="audit">审计快照</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <div v-if="tab === 'md'" v-loading="artifactLoading" class="md-box">
        <MarkdownView :content="finalMd" />
      </div>
      <div v-else-if="tab === 'json'" v-loading="artifactLoading" class="json-box">
        <pre>{{ finalJson }}</pre>
      </div>
      <div v-else class="audit-box" v-loading="loading">
        <div class="audit-sum">
          <span class="a-item">待处理 <b>{{ audit.pending }}</b></span>
          <span class="a-item">已确认 <b class="good">{{ audit.confirmed }}</b></span>
          <span class="a-item">已忽略 <b>{{ audit.ignored }}</b></span>
          <span class="a-item">已修复 <b class="good">{{ audit.fixed }}</b></span>
        </div>
        <el-table :data="auditRows" border stripe size="small">
          <el-table-column prop="id" label="问题" width="150" />
          <el-table-column prop="severity" label="严重度" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="sevType(row.severity)">{{ row.severity }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="issue_type" label="类型" width="130" show-overflow-tooltip />
          <el-table-column prop="message" label="描述" min-width="240" show-overflow-tooltip />
          <el-table-column prop="status" label="处理状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="issueStatusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check, Close, Download } from '@element-plus/icons-vue'
import ProjectNav from '@/components/ProjectNav.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { qualityApi, type QualityIssue } from '@/api/client'

const route = useRoute()
const id = route.params.id as string

const loading = ref(false)
const issues = ref<QualityIssue[]>([])
const reviewer = ref('')
const finalizing = ref(false)
const finalizeResult = ref<Awaited<ReturnType<typeof qualityApi.finalize>> | null>(null)

const tab = ref('md')
const artifactLoading = ref(false)
const finalMd = ref('')
const finalJson = ref('')

// 未处理（非已确认/已忽略/已修复）的 CRITICAL/ERROR
const unresolved = computed(() => {
  const open = issues.value.filter(i =>
    !['已确认', '已忽略', '已修复'].includes(i.status))
  return {
    critical: open.filter(i => i.severity === 'CRITICAL').length,
    error: open.filter(i => i.severity === 'ERROR').length,
  }
})
const blocked = computed(() =>
  unresolved.value.critical > 0 || unresolved.value.error > 0)

// 审计快照：各 issue 的处理动作聚合
const audit = computed(() => ({
  pending: issues.value.filter(i => i.status === '待处理').length,
  confirmed: issues.value.filter(i => i.status === '已确认').length,
  ignored: issues.value.filter(i => i.status === '已忽略').length,
  fixed: issues.value.filter(i => i.status === '已修复').length,
}))
const auditRows = computed(() =>
  [...issues.value].sort((a, b) => {
    const order = { CRITICAL: 0, ERROR: 1, WARNING: 2, INFO: 3 }
    return (order[a.severity as keyof typeof order] ?? 9)
         - (order[b.severity as keyof typeof order] ?? 9)
  }))

async function fetchIssues() {
  loading.value = true
  try {
    const res = await qualityApi.issues(id)
    issues.value = res.issues
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '问题加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(fetchIssues)

async function onFinalize(force: boolean) {
  if (!reviewer.value.trim()) {
    ElMessage.warning('请填写审查人姓名')
    return
  }
  finalizing.value = true
  try {
    finalizeResult.value = await qualityApi.finalize(id, reviewer.value.trim(), force)
    ElMessage.success('终版导出成功')
    await loadArtifact('md')
  } catch (e: any) {
    if (e?.response?.status === 409) {
      ElMessage.warning(e.response.data.detail)
    } else {
      ElMessage.error(e?.response?.data?.detail || '导出失败')
    }
  } finally {
    finalizing.value = false
  }
}

async function loadArtifact(which: 'md' | 'json') {
  artifactLoading.value = true
  try {
    if (which === 'md') {
      const res = await qualityApi.finalContent(id, 'markdown')
      finalMd.value = (res.content as string) || ''
    } else {
      const res = await qualityApi.finalContent(id, 'json')
      finalJson.value = JSON.stringify(res, null, 2)
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '产物加载失败')
  } finally {
    artifactLoading.value = false
  }
}

function downloadDocx() {
  window.open(qualityApi.finalUrl(id, 'docx'), '_blank')
}

function sevType(s: string): 'danger' | 'warning' | 'info' {
  if (s === 'CRITICAL' || s === 'ERROR') return 'danger'
  if (s === 'WARNING') return 'warning'
  return 'info'
}
function issueStatusType(s: string): 'warning' | 'success' | 'info' | 'danger' {
  if (s === '待处理') return 'warning'
  if (s === '已确认') return 'success'
  if (s === '已忽略') return 'info'
  return 'danger'
}
</script>

<style scoped>
.panel { margin-bottom: 14px; }
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  padding: 6px 0;
}
.good { color: #67c23a; }
.bad { color: #f56c6c; }
.blocked-tip { margin-top: 10px; }
.result { padding: 10px 0; }
.artifacts { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.md-box { max-height: 560px; overflow: auto; padding: 4px 8px; }
.json-box {
  max-height: 560px;
  overflow: auto;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 12px;
}
.json-box pre { margin: 0; font-size: 12px; line-height: 1.6; white-space: pre-wrap; }
.audit-box { padding: 4px 0; }
.audit-sum { display: flex; gap: 20px; margin-bottom: 12px; }
.a-item { font-size: 13px; color: #606266; }
</style>
