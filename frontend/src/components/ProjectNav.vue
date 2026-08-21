<template>
  <div class="project-nav">
    <el-page-header class="page-header" @back="$router.push('/projects')">
      <template #content>
        <span class="proj-name">{{ name || '招标项目' }}</span>
      </template>
    </el-page-header>
    <el-tabs :model-value="active" class="nav-tabs" @tab-click="onTab">
      <el-tab-pane label="项目概览" :name="`/projects/${id}`" />
      <el-tab-pane label="招标文件" :name="`/projects/${id}/tender`" />
      <el-tab-pane label="需求分析" :name="`/projects/${id}/requirements`" />
      <el-tab-pane label="标书生成" :name="`/projects/${id}/generate`" />
      <el-tab-pane label="质量检查" :name="`/projects/${id}/quality`" />
      <el-tab-pane label="最终交付" :name="`/projects/${id}/deliver`" />
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { TabsPaneContext } from 'element-plus'

defineProps<{ id: string; name?: string; active: string }>()
const router = useRouter()

function onTab(pane: TabsPaneContext) {
  if (typeof pane.paneName === 'string') router.push(pane.paneName)
}
</script>

<style scoped>
.project-nav {
  margin-bottom: 16px;
}
.page-header {
  padding-bottom: 8px;
}
.proj-name {
  font-size: 16px;
  font-weight: 600;
}
.nav-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}
</style>
