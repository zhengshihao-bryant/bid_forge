<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">企业标书生成平台</div>
      <div class="version">BidForge · M6 标书工作台</div>
      <el-menu router :default-active="activeMenu" class="menu">
        <el-menu-item index="/projects">
          <el-icon><Monitor /></el-icon>
          <span>项目工作台</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Collection /></el-icon>
          <span>企业知识库</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Monitor, Collection } from '@element-plus/icons-vue'

const route = useRoute()
// 项目内子页面（/projects/:id/xxx）高亮"项目工作台"
const activeMenu = computed(() =>
  route.path.startsWith('/knowledge') ? '/knowledge' : '/projects')
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
