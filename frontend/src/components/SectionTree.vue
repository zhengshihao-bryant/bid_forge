<template>
  <el-tree :data="treeData" node-key="id" :default-expanded-keys="expandedKeys"
           :expand-on-click-node="false" :indent="14">
    <template #default="{ data }">
      <span class="node-title" :class="`lv-${data.level}`">{{ data.title }}</span>
      <span v-if="data.page_start" class="node-pages">
        （第{{ data.page_start }}{{ data.page_end && data.page_end !== data.page_start ? `-${data.page_end}` : '' }}页）
      </span>
    </template>
  </el-tree>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SectionNode } from '@/api/client'

const props = defineProps<{ nodes: SectionNode[] }>()

interface TreeNode {
  id: string
  title: string
  level: number
  page_start: number | null
  page_end: number | null
  children: TreeNode[]
}

function toTree(nodes: SectionNode[]): TreeNode[] {
  return nodes.map(n => ({
    id: n.id,
    title: n.title,
    level: n.level,
    page_start: n.page_start,
    page_end: n.page_end,
    children: toTree(n.children),
  }))
}

const treeData = computed(() => toTree(props.nodes))

// 默认展开全部节点
const expandedKeys = computed(() => {
  const keys: string[] = []
  const walk = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      keys.push(n.id)
      walk(n.children)
    }
  }
  walk(treeData.value)
  return keys
})
</script>

<style scoped>
.node-title { font-size: 13px; }
.node-title.lv-1 { font-weight: 700; }
.node-title.lv-2 { font-weight: 600; }
.node-pages { color: #909399; font-size: 12px; }
</style>
