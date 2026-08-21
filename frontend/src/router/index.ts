import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/projects',
      children: [
        // M6-01 项目工作台
        {
          path: 'projects',
          name: 'projects',
          component: () => import('@/views/projects/ProjectList.vue'),
          meta: { title: '项目工作台' },
        },
        {
          path: 'projects/:id',
          name: 'project-overview',
          component: () => import('@/views/projects/ProjectOverview.vue'),
          meta: { title: '项目工作台' },
        },
        // M6-02 招标文件管理
        {
          path: 'projects/:id/tender',
          name: 'tender-docs',
          component: () => import('@/views/projects/TenderDocs.vue'),
          meta: { title: '招标文件' },
        },
        // M6-03 需求分析工作台
        {
          path: 'projects/:id/requirements',
          name: 'requirement-workbench',
          component: () => import('@/views/projects/RequirementWorkbench.vue'),
          meta: { title: '需求分析' },
        },
        // M6-04 企业知识库工作台
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('@/views/knowledge/KnowledgeWorkbench.vue'),
          meta: { title: '企业知识库' },
        },
        // M6-05 标书生成工作台
        {
          path: 'projects/:id/generate',
          name: 'generation-workbench',
          component: () => import('@/views/projects/GenerationWorkbench.vue'),
          meta: { title: '标书生成' },
        },
        // M6-06 质量检查工作台
        {
          path: 'projects/:id/quality',
          name: 'quality-workbench',
          component: () => import('@/views/projects/QualityWorkbench.vue'),
          meta: { title: '质量检查' },
        },
        // M6-07 最终交付
        {
          path: 'projects/:id/deliver',
          name: 'delivery',
          component: () => import('@/views/projects/DeliveryPage.vue'),
          meta: { title: '最终交付' },
        },
      ],
    },
    // 兼容旧入口
    { path: '/tenders', redirect: '/projects' },
    { path: '/tenders/:id', redirect: (to) => `/projects/${to.params.id}` },
  ],
})

export default router
