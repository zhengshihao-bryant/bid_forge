<template>
  <div>
    <!-- 语义检索 -->
    <div class="search-bar">
      <el-input v-model="query" placeholder="语义检索企业资料，如：等保三级资质、7x24 小时售后响应……"
                clearable class="search-input" @keyup.enter="onSearch">
        <template #prefix><el-icon><Search /></el-icon></template>
        <template #append>
          <el-button :loading="searching" @click="onSearch">检索</el-button>
        </template>
      </el-input>
    </div>

    <!-- 检索结果 -->
    <el-card v-if="searchDone" class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>语义检索结果（{{ hits.length }} 条）</span>
          <el-tag size="small" type="info">engine: {{ engine }}</el-tag>
        </div>
      </template>
      <el-empty v-if="!hits.length" description="未检索到相关内容" :image-size="60" />
      <div v-for="h in hits" :key="h.chunk_id" class="hit">
        <div class="hit-head">
          <el-tag size="small" type="success">{{ h.category }}</el-tag>
          <span class="hit-score">score {{ h.score.toFixed(3) }}</span>
        </div>
        <div class="hit-content">{{ h.content }}</div>
        <div class="hit-src">
          溯源：文档 <b>{{ h.file_name }}</b> · 章节 {{ h.section_path || '—' }}
          · 第{{ h.page ?? '?' }}页
        </div>
      </div>
    </el-card>

    <!-- 分类统计卡 + 上传 -->
    <el-row :gutter="12" class="cat-row">
      <el-col v-for="c in catStats" :key="c.name" :xs="12" :sm="6" :md="3">
        <el-card class="cat-card" shadow="hover">
          <div class="cat-num">{{ c.count }}</div>
          <div class="cat-name">{{ c.name }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 资料列表 -->
    <el-card class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>企业资料（{{ materials.length }} 份）</span>
          <el-button type="primary" size="small" @click="dialogVisible = true">
            <el-icon style="margin-right: 4px"><Upload /></el-icon>上传资料
          </el-button>
        </div>
      </template>
      <el-table :data="materials" border stripe v-loading="loading" row-key="id">
        <el-table-column prop="file_name" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="category" label="类别" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="success">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="file_type" label="类型" width="80" align="center">
          <template #default="{ row }">{{ row.file_type.toUpperCase() }}</template>
        </el-table-column>
        <el-table-column prop="total_pages" label="页数" width="70" align="center" />
        <el-table-column label="处理状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="procType(row.process_status)">
              {{ row.process_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="产出" width="130" align="center">
          <template #default="{ row }">
            <span class="prod">块 {{ row.chunk_count }} · 能力卡 {{ row.capability_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button v-if="row.process_status !== '处理中' && row.parse_error"
                       size="small" type="danger" text @click="onProcess(row)">重新处理</el-button>
            <el-button v-else-if="row.process_status !== '处理中'"
                       size="small" type="primary" text @click="onProcess(row)">处理</el-button>
            <span v-else class="muted">处理中…</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 能力卡 -->
    <el-card class="panel" shadow="never">
      <template #header>
        <div class="panel-head">
          <span>企业能力卡（{{ capabilities.length }} 张）</span>
        </div>
      </template>
      <el-table :data="capabilities" border stripe v-loading="loading" row-key="id"
                @row-click="onCapClick" row-class-name="clickable-row">
        <el-table-column prop="id" label="编号" width="110">
          <template #default="{ row }"><span class="mono">{{ row.id }}</span></template>
        </el-table-column>
        <el-table-column prop="category" label="类别" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="warning">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="description" label="描述" min-width="260" show-overflow-tooltip />
        <el-table-column label="来源" min-width="180">
          <template #default="{ row }">
            <span class="src">{{ row.source_doc }}</span>
            <span v-if="row.source_page" class="page"> · 第{{ row.source_page }}页</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog v-model="dialogVisible" title="上传企业资料" width="560px">
      <el-form label-width="90px">
        <el-form-item label="资料类别">
          <el-select v-model="uploadCategory" style="width: 100%">
            <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="资料文件">
          <el-upload v-model:file-list="fileList" drag multiple :auto-upload="false"
                     accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.tif,.tiff">
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!fileList.length"
                   @click="onUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 能力卡抽屉 -->
    <el-drawer v-model="capDrawer" size="480px" :title="`能力卡 ${curCap?.id}`">
      <template v-if="curCap">
        <div class="cap-head">
          <el-tag size="small" type="warning">{{ curCap.category }}</el-tag>
          <h3 class="cap-name">{{ curCap.name }}</h3>
          <p class="cap-desc">{{ curCap.description }}</p>
          <div class="cap-src">
            来源：<b>{{ curCap.source_doc }}</b>
            <span v-if="curCap.source_page"> · 第{{ curCap.source_page }}页</span>
          </div>
        </div>
        <div class="attr-title">结构化属性</div>
        <el-table :data="attrRows" border size="small">
          <el-table-column prop="key" label="属性" width="140" />
          <el-table-column label="值">
            <template #default="{ row }">
              <span class="mono">{{ row.value }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, Upload, UploadFilled } from '@element-plus/icons-vue'
import { knowledgeApi, type Capability, type KnowledgeMaterial, type SearchHit } from '@/api/client'
import type { UploadUserFile } from 'element-plus'

const categories = ['产品', '案例', '资质', '人员', '方案', '售后', '介绍', '历史标书']

const materials = ref<KnowledgeMaterial[]>([])
const capabilities = ref<Capability[]>([])
const loading = ref(false)

const query = ref('')
const searching = ref(false)
const searchDone = ref(false)
const hits = ref<SearchHit[]>([])
const engine = ref('')

const dialogVisible = ref(false)
const uploadCategory = ref('产品')
const uploading = ref(false)
const fileList = ref<UploadUserFile[]>([])

const capDrawer = ref(false)
const curCap = ref<Capability | null>(null)

let timer: number | undefined

async function fetchData() {
  loading.value = true
  try {
    const [ms, cs] = await Promise.all([
      knowledgeApi.materials(),
      knowledgeApi.capabilities(),
    ])
    materials.value = ms
    capabilities.value = cs
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

const catStats = computed(() =>
  categories.map(name => ({
    name,
    count: materials.value.filter(m => m.category === name).length,
  })),
)

const attrRows = computed(() => {
  if (!curCap.value) return []
  const attrs = curCap.value.attributes || {}
  return Object.entries(attrs).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }))
})

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    if (materials.value.some(m => m.process_status === '处理中')) {
      try {
        await fetchData()
        if (!materials.value.some(m => m.process_status === '处理中')) stopPolling()
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
  fetchData()
  startPolling()
})
onUnmounted(stopPolling)

async function onSearch() {
  if (!query.value.trim()) {
    ElMessage.warning('请输入检索内容')
    return
  }
  searching.value = true
  try {
    const res = await knowledgeApi.search(query.value.trim())
    hits.value = res.hits
    engine.value = res.engine
    searchDone.value = true
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '检索失败')
  } finally {
    searching.value = false
  }
}

async function onProcess(row: KnowledgeMaterial) {
  try {
    await knowledgeApi.process(row.id)
    ElMessage.success('已启动处理，完成后自动刷新')
    startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

async function onUpload() {
  uploading.value = true
  try {
    const files = fileList.value.map(f => f.raw).filter(Boolean) as File[]
    await knowledgeApi.upload(files, uploadCategory.value)
    ElMessage.success(`上传成功：${files.length} 个文件`)
    dialogVisible.value = false
    fileList.value = []
    await fetchData()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

function onCapClick(row: Capability) {
  curCap.value = row
  capDrawer.value = true
}

function procType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === '已完成') return 'success'
  if (s === '处理中') return 'warning'
  if (s === '失败') return 'danger'
  return 'info'
}
</script>

<style scoped>
.search-bar { margin-bottom: 14px; }
.search-input { max-width: 640px; }
.panel { margin-bottom: 14px; }
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}
.cat-row { margin-bottom: 14px; }
.cat-card { text-align: center; }
.cat-num { font-size: 22px; font-weight: 700; color: #409eff; }
.cat-name { font-size: 12px; color: #909399; margin-top: 2px; }
.prod { font-size: 12px; color: #606266; }
.muted { color: #c0c4cc; font-size: 12px; }
.mono { font-family: Consolas, monospace; font-size: 12px; }
.src { font-size: 12px; color: #606266; }
.page { font-size: 12px; color: #909399; }
.hit {
  border-bottom: 1px solid #ebeef5;
  padding: 10px 0;
}
.hit:last-child { border-bottom: none; }
.hit-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.hit-score { font-size: 12px; color: #909399; }
.hit-content { font-size: 13px; color: #303133; line-height: 1.7; margin-bottom: 4px; }
.hit-src { font-size: 12px; color: #909399; }
.cap-head { margin-bottom: 16px; }
.cap-name { margin: 8px 0 6px; }
.cap-desc { font-size: 13px; color: #606266; line-height: 1.7; margin: 0 0 8px; }
.cap-src { font-size: 12px; color: #909399; }
.attr-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
:deep(.clickable-row) { cursor: pointer; }
</style>
