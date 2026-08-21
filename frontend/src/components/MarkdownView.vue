<template>
  <div class="markdown-view" v-html="html" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'

const props = defineProps<{ content: string }>()

// 轻量 markdown 渲染（内容来自本系统生成引擎，非用户输入）
const html = computed(() => marked.parse(props.content || '') as string)
</script>

<style scoped>
.markdown-view :deep(h1) { font-size: 22px; margin: 16px 0 8px; }
.markdown-view :deep(h2) { font-size: 19px; margin: 14px 0 8px; }
.markdown-view :deep(h3) { font-size: 16px; margin: 12px 0 6px; }
.markdown-view :deep(p) { margin: 8px 0; line-height: 1.8; }
.markdown-view :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
}
.markdown-view :deep(th), .markdown-view :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
  font-size: 13px;
  text-align: left;
}
.markdown-view :deep(th) { background: #f5f7fa; }
.markdown-view :deep(blockquote) {
  border-left: 3px solid #409eff;
  margin: 8px 0;
  padding: 4px 12px;
  color: #606266;
  background: #f0f7ff;
}
.markdown-view :deep(ul), .markdown-view :deep(ol) { padding-left: 22px; }
.markdown-view :deep(li) { margin: 4px 0; line-height: 1.7; }
.markdown-view :deep(strong) { color: #303133; }
</style>
