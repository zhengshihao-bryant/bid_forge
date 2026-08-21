<template>
  <div>
    <div class="toolbar">
      <h2 class="title">我的投标项目</h2>
      <el-button type="primary" @click="dialogVisible = true">
        <el-icon style="margin-right: 4px"><Upload /></el-icon>新建项目（上传招标文件）
      </el-button>
    </div>

    <div v-loading="store.loading">
      <el-row :gutter="16">
        <el-col v-for="p in store.projects" :key="p.id" :xs="24" :sm="12" :md="8">
          <el-card class="proj-card" shadow="hover">
            <div class="proj-head">
              <div class="proj-name" :title="p.name">{{ p.name }}</div>
              <el-tag size="small" :type="qualityTagType(p)">{{ qualityTagText(p) }}</el-tag>
            </div>
            <div class="proj-meta">创建于 {{ p.created_at }}</div>
            <div class="stage-row">
              <div v-for="s in p.stages" :key="s.key" class="stage-cell">
                <div class="stage-ico" :class="`st-${s.status}`">
                  <el-icon v-if="s.status === 'done'"><Check /></el-icon>
                  <el-icon v-else-if="s.status === 'in_progress'"><Loading /></el-icon>
                  <el-icon v-else-if="s.status === 'error'"><Close /></el-icon>
                  <el-icon v-else-if="s.status === 'warning'"><Warning /></el-icon>
                  <span v-else>○</span>
                </div>
                <div class="stage-l">{{ s.label }}</div>
              </div>
            </div>
            <div class="proj-foot">
              <div class="foot-stats">
                <span v-if="p.quality.report_id">质量 {{ p.quality.score }} 分</span>
                <span v-if="p.quality.pending_issues" class="warn">
                  待处理 {{ p.quality.pending_issues }}
                </span>
                <span v-if="!p.quality.report_id">尚未质量检查</span>
              </div>
              <el-button size="small" type="primary" @click="$router.push(`/projects/${p.id}`)">
                进入工作台
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-empty v-if="!store.loading && !store.projects.length"
                description="还没有投标项目。点击右上角「新建项目」上传招标文件（PDF/Word/Excel/图片）" />
    </div>

    <!-- 新建项目（上传招标文件）对话框 -->
    <el-dialog v-model="dialogVisible" title="新建投标项目" width="560px">
      <el-form label-width="90px">
        <el-form-item label="项目名称">
          <el-input v-model="name" placeholder="如：XX市智慧园区建设项目" />
        </el-form-item>
        <el-form-item label="招标文件">
          <el-upload v-model:file-list="fileList" drag multiple :auto-upload="false"
                     accept=".pdf,.docx,.xlsx,.png,.jpg,.jpeg,.tif,.tiff">
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">
                支持 PDF / Word / Excel / 图片（扫描件），单文件 ≤ 50MB，可一次多传
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading"
                   :disabled="!fileList.length" @click="onUpload">上传并解析</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload, UploadFilled, Check, Loading, Close, Warning } from '@element-plus/icons-vue'
import { useWorkbenchStore } from '@/stores/workbench'
import { tenderApi, type WorkbenchProject } from '@/api/client'
import type { UploadUserFile } from 'element-plus'

const store = useWorkbenchStore()
const dialogVisible = ref(false)
const uploading = ref(false)
const name = ref('')
const fileList = ref<UploadUserFile[]>([])

onMounted(() => {
  store.fetchList()
  store.startPolling()
})

function qualityTagText(p: WorkbenchProject): string {
  if (p.delivery.finalized) return '已交付'
  if (p.quality.report_id) return '质量检查中'
  if (p.generation.status === '生成中') return '标书生成中'
  if (p.generation.status === '已完成') return '生成完成'
  if (p.matching.status === '匹配中') return '需求匹配中'
  if (p.matching.status === '已完成') return '匹配完成'
  if (p.extraction_status === '提取中') return '需求提取中'
  if (p.extraction_status === '已完成') return '需求已提取'
  return '待处理'
}

function qualityTagType(p: WorkbenchProject): 'success' | 'warning' | 'danger' | 'info' {
  if (p.delivery.finalized) return 'success'
  if (p.quality.pending_issues) return 'warning'
  if (p.quality.report_id) return 'success'
  if (p.generation.status === '生成中' || p.matching.status === '匹配中'
      || p.extraction_status === '提取中') return 'warning'
  return 'info'
}

async function onUpload() {
  uploading.value = true
  try {
    const files = fileList.value.map(f => f.raw).filter(Boolean) as File[]
    const res = await tenderApi.create(files, name.value.trim())
    const okCount = res.results.filter((r: { ok: boolean }) => r.ok).length
    ElMessage.success(`上传成功：${okCount}/${res.results.length} 个文件解析通过`)
    dialogVisible.value = false
    name.value = ''
    fileList.value = []
    await store.fetchList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title { margin: 0; }
.proj-card { margin-bottom: 16px; }
.proj-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.proj-name {
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proj-meta { font-size: 12px; color: #909399; margin: 6px 0 12px; }
.stage-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0 12px;
}
.stage-cell { flex: 1; text-align: center; }
.stage-ico {
  width: 22px;
  height: 22px;
  margin: 0 auto 2px;
  border-radius: 50%;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
  color: #909399;
}
.stage-ico.st-done { background: #67c23a; color: #fff; }
.stage-ico.st-in_progress { background: #409eff; color: #fff; }
.stage-ico.st-warning { background: #e6a23c; color: #fff; }
.stage-ico.st-error { background: #f56c6c; color: #fff; }
.stage-l { font-size: 11px; color: #606266; }
.proj-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px solid #ebeef5;
  padding-top: 10px;
}
.foot-stats { font-size: 12px; color: #606266; }
.foot-stats .warn { color: #e6a23c; margin-left: 8px; }
</style>
