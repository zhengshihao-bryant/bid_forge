<template>
  <el-empty :description="route.meta.title + '——将在对应里程碑交付'">
    <template #default>
      <p class="hint">
        当前里程碑 M1（招标文件解析 + 需求提取）已交付：<br />
        上传招标文件 → 结构化需求清单（四元溯源）→ 评分点规则解析 → 人工修订
      </p>
    </template>
  </el-empty>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<style scoped>
.hint {
  color: #909399;
  line-height: 1.8;
  text-align: center;
}
</style>
