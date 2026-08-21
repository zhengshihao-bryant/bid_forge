<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">企业标书生成平台</div>
      <div class="version">MVP v0.1 · M1 里程碑</div>
      <el-menu router :default-active="route.path" class="menu">
        <el-menu-item index="/tenders">
          <el-icon><Document /></el-icon>
          <span>招标管理</span>
        </el-menu-item>
        <el-menu-item index="/knowledge" disabled>
          <el-icon><Collection /></el-icon>
          <span>知识库（M2）</span>
        </el-menu-item>
        <el-menu-item index="/generate" disabled>
          <el-icon><EditPen /></el-icon>
          <span>标书生成（M3）</span>
        </el-menu-item>
        <el-menu-item index="/export" disabled>
          <el-icon><Download /></el-icon>
          <span>导出（M4）</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Document, Collection, EditPen, Download } from '@element-plus/icons-vue'

const route = useRoute()
</script>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  background: #1f2d3d;
  color: #fff;
  display: flex;
  flex-direction: column;
}
.logo {
  padding: 20px 16px 4px;
  font-size: 17px;
  font-weight: 600;
  color: #fff;
}
.version {
  padding: 0 16px 16px;
  font-size: 12px;
  color: #8c9bab;
}
.menu {
  border-right: none;
  background: transparent;
}
.menu :deep(.el-menu-item) {
  color: #c0ccda;
}
.menu :deep(.el-menu-item.is-active) {
  color: #409eff;
  background: #18222e;
}
.main {
  background: #f5f7fa;
  padding: 20px;
}
</style>
