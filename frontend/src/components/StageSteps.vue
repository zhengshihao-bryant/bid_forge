<template>
  <div class="stage-steps">
    <div v-for="(s, i) in stages" :key="s.key" class="stage-item">
      <div
        class="stage-dot"
        :class="`st-${s.status}`"
        :title="s.summary"
        @click="$emit('select', s)"
      >
        <el-icon v-if="s.status === 'done'"><Check /></el-icon>
        <el-icon v-else-if="s.status === 'in_progress'" class="spin"><Loading /></el-icon>
        <el-icon v-else-if="s.status === 'error'"><CircleClose /></el-icon>
        <el-icon v-else-if="s.status === 'warning'"><Warning /></el-icon>
        <span v-else class="dot-empty" />
      </div>
      <div class="stage-label" :class="{ active: s.status === 'in_progress' }">
        {{ s.label }}
      </div>
      <div class="stage-summary">{{ s.summary }}</div>
      <div v-if="i < stages.length - 1" class="stage-line" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { Check, Loading, CircleClose, Warning } from '@element-plus/icons-vue'
import type { StageState } from '@/api/client'

defineProps<{ stages: StageState[] }>()
defineEmits<{ (e: 'select', stage: StageState): void }>()
</script>

<style scoped>
.stage-steps {
  display: flex;
  align-items: flex-start;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 16px;
  overflow-x: auto;
}
.stage-item {
  flex: 1;
  min-width: 110px;
  position: relative;
  text-align: center;
}
.stage-dot {
  width: 28px;
  height: 28px;
  margin: 0 auto 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: #e4e7ed;
  color: #909399;
  transition: all 0.2s;
}
.stage-dot.st-done { background: #67c23a; color: #fff; }
.stage-dot.st-in_progress { background: #409eff; color: #fff; }
.stage-dot.st-warning { background: #e6a23c; color: #fff; }
.stage-dot.st-error { background: #f56c6c; color: #fff; }
.dot-empty { width: 8px; height: 8px; border-radius: 50%; background: #c0c4cc; }
.stage-label { font-size: 12px; color: #606266; }
.stage-label.active { color: #409eff; font-weight: 600; }
.stage-summary {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 4px;
}
.stage-line {
  position: absolute;
  top: 13px;
  left: calc(50% + 18px);
  right: calc(-50% + 18px);
  height: 2px;
  background: #e4e7ed;
}
.spin { animation: rotate 1.2s linear infinite; }
@keyframes rotate { to { transform: rotate(360deg); } }
</style>
