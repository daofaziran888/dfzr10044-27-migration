import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ReclaimPackageDetail from '@/views/ReclaimPackageDetail.vue'
import ReclaimOrderDetail from '@/views/ReclaimOrderDetail.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/reclaim_package/:packageId',
      name: 'reclaim-package-detail',
      component: ReclaimPackageDetail,
      props: true,
    },
    {
      path: '/reclaim_order/:orderId',
      name: 'reclaim-order-detail',
      component: ReclaimOrderDetail,
      props: true,
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
