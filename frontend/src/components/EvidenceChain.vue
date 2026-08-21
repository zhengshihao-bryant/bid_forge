<template>
  <div class="chain" v-loading="loading">
    <template v-if="!loading">
      <!-- 需求原文 -->
      <div class="block">
        <div class="block-title">
          <el-icon><Document /></el-icon>需求原文
          <el-tag v-if="requirement?.is_star" size="small" type="danger">★ 星标条款</el-tag>
        </div>
        <div class="req-text">{{ requirement?.text || '—' }}</div>
        <div v-if="requirement?.constraints?.length" class="constraints">
          <span class="c-label">量化约束：</span>
          <el-tag v-for="(c, i) in requirement.constraints" :key="i"
                  size="small" type="warning" class="c-tag">
            {{ c.metric }} {{ c.op }}{{ c.value }}{{ c.unit }}
          </el-tag>
        </div>
      </div>

      <!-- 匹配判定 -->
      <div class="block">
        <div class="block-title">
          <el-icon><Connection /></el-icon>匹配判定
          <el-tag size="small" :type="statusTagType(match?.status || '')">
            {{ match?.status || '—' }}
          </el-tag>
          <span class="conf">置信度 {{ (match?.confidence ?? 0).toFixed(2) }}</span>
        </div>
        <div class="reason">{{ match?.reason || '—' }}</div>
        <div v-if="match?.conflicts?.length" class="conflicts">
          <div class="c-label conflict-label">冲突提示：</div>
          <div v-for="(c, i) in match.conflicts" :key="i" class="conflict-item">
            {{ typeof c === 'string' ? c : JSON.stringify(c) }}
          </div>
        </div>
      </div>

      <!-- 证据链 -->
      <div class="block">
        <div class="block-title">
          <el-icon><Link /></el-icon>证据链（{{ evidences.length }} 条）
        </div>
        <el-empty v-if="!evidences.length" description="无证据记录" :image-size="50" />
        <div v-for="(e, i) in evidences" :key="e.evidence_id" class="evd">
          <!-- 链路：REQ-C → MATCH → EVD -->
          <div class="trace-line">
            <span class="trace-node req-node">{{ requirement?.id || 'REQ-C' }}</span>
            <span class="trace-arrow">→</span>
            <span class="trace-node">{{ match?.id || 'MATCH' }}</span>
            <span class="trace-arrow">→</span>
            <span class="trace-node evd-node">{{ e.evidence_id }}</span>
            <el-tag v-if="e.validation === 'VALID'" size="small" type="success" class="valid-tag">
              VALID
            </el-tag>
            <el-tag v-else-if="e.validation" size="small" type="warning" class="valid-tag">
              {{ e.validation }}
            </el-tag>
          </div>
          <!-- 来源：能力卡/知识块 → 文档 → 章节 → 页码 -->
          <div class="src-line">
            <span class="src-chip">
              {{ e.source_type === 'capability' ? '能力卡' : '知识块' }}
              {{ e.source_id }}
            </span>
            <span class="trace-arrow dim">→</span>
            <span class="src-chip">文档 {{ e.document }}</span>
            <span class="trace-arrow dim">→</span>
            <span class="src-chip">章节 {{ e.section_path || '—' }}</span>
            <span class="trace-arrow dim">→</span>
            <span class="src-chip">第{{ e.page ?? '?' }}页</span>
            <span class="conf">{{ (e.confidence ?? 0).toFixed(2) }}</span>
          </div>
          <!-- 原文高亮 -->
          <div class="evd-content">
            <span class="evd-idx">{{ i + 1 }}.</span>
            <span v-if="e.matched_text" class="hl">{{ e.matched_text }}</span>
            <div v-if="e.content" class="evd-full">{{ e.content }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { Document, Connection, Link } from '@element-plus/icons-vue'
import type { CanonicalRequirement, EvidenceItem, MatchRecord } from '@/api/client'

defineProps<{
  match: MatchRecord | null
  requirement: CanonicalRequirement | null
  evidences: EvidenceItem[]
  loading: boolean
}>()

function statusTagType(s: string): 'success' | 'warning' | 'danger' | 'info' {
  if (s === 'FULL') return 'success'
  if (s === 'PARTIAL') return 'warning'
  if (s === 'MISSING') return 'danger'
  return 'info'
}
</script>

<style scoped>
.chain { min-height: 120px; }
.block { margin-bottom: 18px; }
.block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}
.req-text {
  font-size: 13px;
  color: #303133;
  line-height: 1.7;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px 12px;
}
.constraints { margin-top: 8px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.c-label { font-size: 12px; color: #909399; }
.c-tag { margin: 0; }
.reason { font-size: 13px; color: #606266; line-height: 1.7; }
.conf { font-size: 12px; color: #909399; margin-left: auto; }
.conflicts { margin-top: 8px; }
.conflict-label { margin-bottom: 4px; }
.conflict-item {
  font-size: 12px;
  color: #e6a23c;
  background: #fdf6ec;
  border-radius: 4px;
  padding: 4px 8px;
  margin-bottom: 4px;
}
.evd {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.trace-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.trace-node {
  font-family: Consolas, monospace;
  font-size: 12px;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 4px;
  padding: 2px 6px;
}
.trace-node.req-node { background: #f0f9eb; color: #67c23a; }
.trace-node.evd-node { background: #fdf6ec; color: #e6a23c; }
.trace-arrow { color: #c0c4cc; font-size: 12px; }
.trace-arrow.dim { color: #dcdfe6; }
.valid-tag { margin-left: 2px; }
.src-line {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  padding-left: 2px;
}
.src-chip {
  font-size: 12px;
  color: #606266;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 2px 6px;
}
.evd-content { font-size: 13px; line-height: 1.7; color: #303133; }
.evd-idx { color: #c0c4cc; font-size: 12px; margin-right: 4px; }
.hl {
  background: #fff3cd;
  padding: 1px 3px;
  border-radius: 3px;
}
.evd-full { margin-top: 4px; color: #606266; }
</style>
