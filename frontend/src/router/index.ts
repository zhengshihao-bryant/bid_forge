import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/tenders',
      children: [
        {
          path: 'tenders',
          name: 'tenders',
          component: () => import('@/views/TenderList.vue'),
          meta: { title: '招标管理' },
        },
        {
          path: 'tenders/:id',
          name: 'tender-detail',
          component: () => import('@/views/TenderDetail.vue'),
          meta: { title: '招标详情' },
        },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('@/views/Placeholder.vue'),
          meta: { title: '知识库（M2）' },
        },
        {
          path: 'generate',
          name: 'generate',
          component: () => import('@/views/Placeholder.vue'),
          meta: { title: '标书生成（M3）' },
        },
        {
          path: 'export',
          name: 'export',
          component: () => import('@/views/Placeholder.vue'),
          meta: { title: '导出（M4）' },
        },
      ],
    },
  ],
})

export default router
