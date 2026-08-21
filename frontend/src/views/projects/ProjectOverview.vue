<template>
  <div v-loading="loading">
    <ProjectNav :id="id" :name="project?.name" :active="`/projects/${id}`" />

    <template v-if="project">
      <StageSteps :stages="project.stages" @select="onStageSelect" />

      <!-- 六阶段状态卡片 -->
      <el-row :gutter="16">
        <el-col v-for="card in stageCards" :key="card.key" :xs="24" :sm="12" :md="8">
          <el-card class="stage-card" shadow="hover">
            <div class="card-head">
              <span class="card-title">{{ card.label }}</span>
              <el-icon :class="`card-ico st-${card.status}`">
                <Check v-if="card.status === 'done'" />
                <Loading v-else-if="card.status === 'in_progress'" />
                <Warning v-else-if="card.status === 'warning'" />
                <Close v-else-if="card.status === 'error'" />
                <More v-else />
              </el-icon>
            </div>
            <div class="card-summary">{{ card.summary }}</div>
            <div class="card-extra">{{ card.extra }}</div>
            <div class="card-actions">
              <el-button v-if="card.action" size="small" type="primary"
                         :loading="card.busy" @click="card.action()">
                {{ card.actionText }}
              </el-button>
              <el-button size="small" text type="primary" @click="$router.push(card.route)">
                进入{{ card.label }} »
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 待处理质量问题提醒 -->
      <el-card v-if="project.pending_issues.length" class="alert-card" shadow="never">
        <template #header>
          <span class="alert-title">
            <el-icon style="color:#e6a23c"><Warning /></el-icon>
            质量检查待处理问题（{{ project.quality.pending_issues }}）
          </span>
        </template>
        <div v-for="i in project.pending_issues" :key="i.id" class="issue-row">
          <el-tag size="small" :type="severityType(i.severity)">{{ i.severity }}</el-tag>
          <span class="issue-msg">{{ i.message }}</span>
          <el-button size="small" text type="primary"
                     @click="$router.push(`/projects/${id}/quality`)">去处理</el-button>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check, Loading, Warning, Close, More } from '@element-plus/icons-vue'
import ProjectNav from '@/components/ProjectNav.vue'
import StageSteps from '@/components/StageSteps.vue'
import {
  workbenchApi, matchingApi, generationApi, qualityApi, type StageState,
} from '@/api/client'
import { useWorkbenchStore } from '@/stores/workbench'

const route = useRoute()
const router = useRouter()
const store = useWorkbenchStore()
const id = route.params.id as string
const project = ref<Awaited<ReturnType<typeof workbenchApi.project>> | null>(null)
const loading = ref(false)
const busy = ref('')

async function fetchProject() {
  loading.value = true
  try {
    project.value = await workbenchApi.project(id)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(fetchProject)

function onStageSelect(s: StageState) {
  const map: Record<string, string> = {
    docs: `/projects/${id}/tender`,
    extract: `/projects/${id}/tender`,
    kb: '/knowledge',
    match: `/projects/${id}/requirements`,
    generate: `/projects/${id}/generate`,
    quality: `/projects/${id}/quality`,
  }
  router.push(map[s.key] || `/projects/${id}`)
}

interface StageCard {
  key: string
  label: string
  status: string
  summary: string
  extra: string
  route: string
  actionText: string
  action?: () => Promise<void>
  busy: boolean
}

const stageCards = computed<StageCard[]>(() => {
  const p = project.value
  if (!p) return []
  const dist = p.matching.distribution
  return [
    {
      key: 'docs', label: '招标文件',
      status: p.stages.find(s => s.key === 'docs')?.status || 'pending',
      summary: `已上传 ${p.documents.total} 个文件，${p.documents.ok} 个解析成功`,
      extra: p.documents.ocr ? `${p.documents.ocr} 个文件含 OCR 扫描页` : '无 OCR 扫描件',
      route: `/projects/${id}/tender`, actionText: '',
      busy: false,
    },
    {
      key: 'extract', label: '需求解析',
      status: p.stages.find(s => s.key === 'extract')?.status || 'pending',
      summary: `已提取 ${p.requirement_count} 条需求`,
      extra: p.extraction_status,
      route: `/projects/${id}/tender`,
      actionText: p.extraction_status === '提取中' ? '' : '提取需求',
      action: p.extraction_status === '提取中' ? undefined : async () => {
        busy.value = 'extract'
        try {
          await (await import('@/api/client')).tenderApi.extract(id)
          ElMessage.success('已启动需求提取，完成后自动刷新')
          pollRefresh()
        } catch (e: any) {
          ElMessage.error(e?.response?.data?.detail || '启动失败')
        } finally {
          busy.value = ''
        }
      },
      busy: busy.value === 'extract',
    },
    {
      key: 'kb', label: '企业资料',
      status: p.stages.find(s => s.key === 'kb')?.status || 'pending',
      summary: `企业能力卡 ${p.kb?.capabilities ?? 0} 张`,
      extra: '全局共享，所有项目通用',
      route: '/knowledge', actionText: '',
      busy: false,
    },
    {
      key: 'match', label: '需求匹配',
      status: p.stages.find(s => s.key === 'match')?.status || 'pending',
      summary: p.matching.match_count
        ? `${p.matching.canonical_count} 条规范需求`
        : '尚未执行匹配',
      extra: p.matching.match_count
        ? `FULL ${dist.FULL} / PARTIAL ${dist.PARTIAL} / MISSING ${dist.MISSING} / UNKNOWN ${dist.UNKNOWN}`
        : '',
      route: `/projects/${id}/requirements`,
      actionText: p.matching.status === '匹配中' ? '' : '启动匹配',
      action: p.matching.status === '匹配中' ? undefined : async () => {
        busy.value = 'match'
        try {
          await matchingApi.start(id)
          ElMessage.success('已启动需求匹配，完成后自动刷新')
          pollRefresh()
        } catch (e: any) {
          ElMessage.error(e?.response?.data?.detail || '启动失败')
        } finally {
          busy.value = ''
        }
      },
      busy: busy.value === 'match',
    },
    {
      key: 'generate', label: '标书生成',
      status: p.stages.find(s => s.key === 'generate')?.status || 'pending',
      summary: p.generation.total_sections
        ? `${p.generation.done_sections}/${p.generation.total_sections} 章节已生成`
        : '尚未规划大纲',
      extra: p.generation.status,
      route: `/projects/${id}/generate`,
      actionText: !p.generation.total_sections
        ? '规划大纲'
        : (p.generation.status === '已完成' || p.generation.status === '部分失败'
           ? '继续生成'
           : ''),
      action: (!p.generation.total_sections
        ? async () => {
            busy.value = 'generate'
            try {
              await generationApi.createOutline(id)
              ElMessage.success('大纲规划完成')
              await fetchProject()
            } catch (e: any) {
              ElMessage.error(e?.response?.data?.detail || '规划失败')
            } finally {
              busy.value = ''
            }
          }
        : (p.generation.status === '已完成' || p.generation.status === '部分失败'
           ? async () => {
               busy.value = 'generate'
               try {
                 await generationApi.startJob(id)
                 ElMessage.success('已启动标书生成，完成后自动刷新')
                 pollRefresh()
               } catch (e: any) {
                 ElMessage.error(e?.response?.data?.detail || '启动失败')
               } finally {
                 busy.value = ''
               }
             }
           : undefined)),
      busy: busy.value === 'generate',
    },
    {
      key: 'quality', label: '质量检查',
      status: p.stages.find(s => s.key === 'quality')?.status || 'pending',
      summary: p.quality.report_id ? `质量得分 ${p.quality.score}` : '尚未执行质量检查',
      extra: p.quality.report_id
        ? (p.quality.pending_issues
           ? `${p.quality.pending_issues} 个问题待处理`
           : '全部问题已处理')
        : '',
      route: `/projects/${id}/quality`,
      actionText: p.quality.report_id ? '重新检查' : '运行检查',
      action: async () => {
        busy.value = 'quality'
        try {
          await qualityApi.check(id)
          ElMessage.success('质量检查完成')
          await fetchProject()
        } catch (e: any) {
          ElMessage.error(e?.response?.data?.detail || '检查失败（需先生成标书）')
        } finally {
          busy.value = ''
        }
      },
      busy: busy.value === 'quality',
    },
  ]
})

// 轮询刷新（任务完成后自动停止）
let pollTimer: number | undefined
function pollRefresh() {
  window.clearInterval(pollTimer)
  pollTimer = window.setInterval(async () => {
    await fetchProject()
    const busyStages = project.value?.stages.some(s => s.status === 'in_progress')
    if (!busyStages) {
      window.clearInterval(pollTimer)
      store.fetchList()
    }
  }, 3000)
}

function severityType(s: string): 'danger' | 'warning' | 'info' {
  if (s === 'CRITICAL' || s === 'ERROR') return 'danger'
  if (s === 'WARNING') return 'warning'
  return 'info'
}
</script>

<style scoped>
.stage-card { margin-bottom: 16px; }
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title { font-size: 15px; font-weight: 600; }
.card-ico { font-size: 20px; color: #909399; }
.card-ico.st-done { color: #67c23a; }
.card-ico.st-in_progress { color: #409eff; }
.card-ico.st-warning { color: #e6a23c; }
.card-ico.st-error { color: #f56c6c; }
.card-summary { font-size: 13px; margin: 10px 0 4px; }
.card-extra { font-size: 12px; color: #909399; min-height: 18px; }
.card-actions { margin-top: 10px; display: flex; gap: 8px; }
.alert-card { margin-top: 4px; }
.alert-title { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.issue-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
}
.issue-row:last-child { border-bottom: none; }
.issue-msg { flex: 1; font-size: 13px; }
</style>
