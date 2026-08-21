<template>
  <div v-if="tender">
    <div class="header">
      <el-button text @click="$router.back()">← 返回列表</el-button>
      <h2 class="title">{{ tender.name }}</h2>
      <el-tag :type="statusType(tender.extraction_status)">{{ tender.extraction_status }}</el-tag>
      <el-button v-if="tender.extraction_status !== '提取中'" type="primary" size="small"
                 style="margin-left: auto" @click="onExtract">提取需求</el-button>
    </div>

    <el-tabs v-model="tab">
      <!-- 需求清单 -->
      <el-tab-pane label="需求清单" name="requirements">
        <div class="filters">
          <el-select v-model="filter.type" placeholder="类型" clearable size="small" style="width: 130px">
            <el-option v-for="t in types" :key="t" :label="t" :value="t" />
          </el-select>
          <el-select v-model="filter.importance" placeholder="重要度" clearable size="small" style="width: 110px">
            <el-option label="高" value="高" />
            <el-option label="中" value="中" />
            <el-option label="低" value="低" />
          </el-select>
          <el-checkbox v-model="filter.is_star" label="仅看★条款" />
          <span class="count">共 {{ requirements.length }} 条</span>
        </div>
        <el-table :data="requirements" v-loading="loading" border size="small">
          <el-table-column prop="id" label="编号" width="100" />
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ row.type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="标题" min-width="220">
            <template #default="{ row }">
              <span v-if="row.is_star" class="star">★</span>
              <span v-if="row.human_confirmed" class="confirmed">已人工确认</span>
              {{ row.title }}
            </template>
          </el-table-column>
          <el-table-column label="原文摘录" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">{{ row.original_text }}</template>
          </el-table-column>
          <el-table-column label="量化指标" width="140">
            <template #default="{ row }">
              <span v-for="(q, i) in row.quantitative" :key="i" class="quant">
                {{ q.op }}{{ q.value }}{{ q.unit }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="重要度" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.importance === '高' ? 'danger' : 'info'">
                {{ row.importance }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="出处" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.source">
                {{ row.source.document }}<template v-if="row.source.page"> 第{{ row.source.page }}页</template>
              </span>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 评分点 -->
      <el-tab-pane :label="`评分点（${scorePoints.length}）`" name="scores">
        <el-table :data="scorePoints" border size="small">
          <el-table-column prop="category" label="类别" width="90" />
          <el-table-column prop="item" label="评价项" min-width="180" />
          <el-table-column prop="max_score" label="分值" width="80" align="center" />
          <el-table-column prop="criteria" label="评分细则" min-width="300" show-overflow-tooltip />
          <el-table-column prop="source_ref" label="出处" width="220" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>

      <!-- 文件与章节树 -->
      <el-tab-pane label="文件与章节" name="docs">
        <el-collapse>
          <el-collapse-item v-for="d in tender.documents" :key="d.id"
                            :title="`${d.file_name}（${d.file_type} · ${d.total_pages || '—'} 页 · ${d.char_count} 字${d.parse_error ? ' · 解析失败' : ''}）`">
            <el-tree :data="toTree(d.sections)" node-key="id" default-expand-all :expand-on-click-node="false">
              <template #default="{ data }">
                <span>{{ data.title }}
                  <span v-if="data.page_start" class="pages">（第{{ data.page_start }}-{{ data.page_end }}页）</span>
                </span>
              </template>
            </el-tree>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { tenderApi, type RequirementItem, type ScorePoint, type SectionNode, type TenderDetail } from '@/api/client'

const route = useRoute()
const tenderId = route.params.id as string
const tender = ref<TenderDetail | null>(null)
const requirements = ref<RequirementItem[]>([])
const scorePoints = ref<ScorePoint[]>([])
const loading = ref(false)
const tab = ref('requirements')

const types = [
  '项目背景', '建设目标', '技术要求', '功能要求', '实施要求',
  '人员要求', '资质要求', '售后服务', '评分标准', '投标文件格式', '商务要求', '报价要求',
]
const filter = reactive({ type: '', importance: '', is_star: false })

onMounted(async () => {
  tender.value = await tenderApi.get(tenderId)
  await loadRequirements()
  scorePoints.value = await tenderApi.scorePoints(tenderId)
})

async function loadRequirements() {
  loading.value = true
  try {
    const params: Record<string, string> = {}
    if (filter.type) params.type = filter.type
    if (filter.importance) params.importance = filter.importance
    if (filter.is_star) params.is_star = 'true'
    requirements.value = await tenderApi.requirements(tenderId, params)
  } finally {
    loading.value = false
  }
}

// 过滤器联动
watch(
  () => [filter.type, filter.importance, filter.is_star].join('|'),
  () => { void loadRequirements() },
)

async function onExtract() {
  try {
    await tenderApi.extract(tenderId)
    ElMessage.success('已启动需求提取，稍后刷新查看')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

function statusType(status: string) {
  switch (status) {
    case '已完成': return 'success'
    case '提取中': return 'warning'
    case '失败': return 'danger'
    default: return 'info'
  }
}

function toTree(sections: SectionNode[]): any[] {
  return sections.map(s => ({
    id: s.id,
    title: s.title,
    page_start: s.page_start,
    page_end: s.page_end,
    children: toTree(s.children),
  }))
}
</script>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.title {
  margin: 0;
}
.star {
  color: #f56c6c;
  font-weight: 700;
  margin-right: 2px;
}
.confirmed {
  background: #67c23a;
  color: #fff;
  font-size: 11px;
  border-radius: 3px;
  padding: 1px 4px;
  margin-right: 4px;
}
.quant {
  display: inline-block;
  background: #f0f2f5;
  border-radius: 3px;
  padding: 1px 5px;
  margin-right: 4px;
  font-size: 12px;
}
.count {
  color: #909399;
  font-size: 13px;
  margin-left: auto;
}
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.pages {
  color: #909399;
  font-size: 12px;
}
</style>
