"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import reclaim, resource

# 固定路径的回收路由必须排在通用 /api/{module} 之前注册。
ROUTERS = [reclaim, resource]
