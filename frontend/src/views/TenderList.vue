<template>
  <div>
    <div class="toolbar">
      <h2 class="title">招标项目</h2>
      <el-button type="primary" @click="dialogVisible = true">
        <el-icon style="margin-right: 4px"><Upload /></el-icon>上传招标文件
      </el-button>
    </div>

    <el-table :data="store.tenders" v-loading="store.loading" border stripe>
      <el-table-column prop="name" label="项目名称" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.extraction_status)" size="small">
            {{ row.extraction_status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="extraction_progress" label="进度" min-width="200" show-overflow-tooltip />
      <el-table-column prop="requirement_count" label="需求数" width="90" align="center" />
      <el-table-column prop="score_point_count" label="评分点" width="90" align="center" />
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary"
                     @click="$router.push(`/tenders/${row.id}`)">详情</el-button>
          <el-button size="small" text type="primary"
                     :disabled="row.extraction_status === '提取中'"
                     @click="onExtract(row)">提取需求</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!store.loading && !store.tenders.length"
              description="还没有招标项目，点击右上角上传招标文件（PDF/Word/Excel/图片）" />

    <!-- 上传对话框 -->
    <el-dialog v-model="dialogVisible" title="上传招标文件" width="560px">
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
import { Upload, UploadFilled } from '@element-plus/icons-vue'
import { useTendersStore } from '@/stores/tenders'
import { tenderApi } from '@/api/client'
import type { UploadUserFile } from 'element-plus'

const store = useTendersStore()
const dialogVisible = ref(false)
const uploading = ref(false)
const name = ref('')
const fileList = ref<UploadUserFile[]>([])

onMounted(() => {
  store.fetchList()
  store.startPolling()
})

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case '已完成': return 'success'
    case '提取中': return 'warning'
    case '失败': return 'danger'
    default: return 'info'
  }
}

async function onUpload() {
  uploading.value = true
  try {
    // UploadRawFile extends File；raw 为 undefined 的条目过滤掉
    const files = fileList.value.map(f => f.raw).filter(Boolean) as File[]
    const res = await tenderApi.create(files, name.value.trim())
    const okCount = res.results.filter((r: { ok: boolean }) => r.ok).length
    ElMessage.success(`上传成功：${okCount}/${res.results.length} 个文件解析通过`)
    dialogVisible.value = false
    name.value = ''
    fileList.value = []
    await store.fetchList()
    store.startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function onExtract(row: { id: string }) {
  try {
    await tenderApi.extract(row.id)
    ElMessage.success('已启动需求提取，完成后自动刷新')
    store.startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
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
.title {
  margin: 0;
}
</style>
