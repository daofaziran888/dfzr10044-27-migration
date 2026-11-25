"""回收凭据包接口：迁包、导入详情、断点重试、包外回执、回收单核销。

固定路径集中在本路由，必须在通用 ``/api/{module}`` 动态路由之前注册，
否则 ``/api/reclaim_package/import`` 会被当成条目编号处理。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse, Response

from app.lake_store import lake_store
from app.services.reclaim_engine import reclaim_engine

router = APIRouter(prefix="/api", tags=["回收凭据包"])


def _require_package(package_id: int) -> dict[str, Any]:
    detail = reclaim_engine.package_detail(package_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"回收凭据包 {package_id} 不存在")
    return detail


@router.post("/reclaim_package/import")
def import_audit_csv(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """按湖表与时间段回导审计 CSV，生成可核销的回收凭据包。"""
    csv_text = payload.get("csv_text")
    if not csv_text and payload.get("csv_base64"):
        import base64
        csv_text = base64.b64decode(str(payload["csv_base64"])).decode("utf-8")
    result = reclaim_engine.import_package(
        lake_table=str(payload.get("lake_table") or "").strip(),
        window_start=str(payload.get("window_start") or "").strip(),
        window_end=str(payload.get("window_end") or "").strip(),
        csv_text=str(csv_text or ""),
        source_file=str(payload.get("source_file") or ""),
        package_no=str(payload.get("package_no") or ""),
        confirm=bool(payload.get("confirm")),
        fail_at=(int(payload["fail_at"]) if payload.get("fail_at") not in (None, "") else None),
    )
    if not result.get("ok") and result.get("package") is None:
        return JSONResponse(status_code=400, content=result)
    return result


@router.get("/reclaim_package/{package_id}/detail")
def package_detail(package_id: int) -> dict[str, Any]:
    """导入详情页：原文、缺项、排除项、凭据与包外回执。"""
    return _require_package(package_id)


@router.post("/reclaim_package/{package_id}/retry")
def retry_import(package_id: int, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """从失败位置恢复导入，重试回收接口。"""
    _require_package(package_id)
    return reclaim_engine.retry_package(package_id, confirm=bool(payload.get("confirm")))


@router.post("/reclaim_package/{package_id}/confirm")
def confirm_import(package_id: int) -> dict[str, Any]:
    """确认导入：包内明确删除回执的凭据正式生效。"""
    _require_package(package_id)
    return reclaim_engine.confirm_package(package_id)


@router.post("/reclaim_package/{package_id}/receipts")
def report_external_receipt(package_id: int,
                            payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """补报存量包之外的实际回收回执（例如待核条目的实删确认）。"""
    _require_package(package_id)
    return reclaim_engine.report_receipt(
        package_id,
        item_no=str(payload.get("item_no") or payload.get("审计条目") or ""),
        actual_bytes=int(payload.get("actual_bytes") or payload.get("实际释放字节") or 0),
        receipt_no=str(payload.get("receipt_no") or payload.get("回执编号") or ""),
        source=str(payload.get("source") or "库内实际回收回执"),
        operator=str(payload.get("operator") or ""),
    )


@router.post("/reclaim_package/{package_id}/amounts")
def set_historical_amounts(package_id: int,
                           payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """登记历史实删量，例如 {"amounts": {"F": 100, "G": 40}}。"""
    _require_package(package_id)
    amounts = payload.get("amounts")
    if not isinstance(amounts, dict):
        amounts = {key: value for key, value in payload.items() if key not in ("amounts", "package_id")}
    return reclaim_engine.set_historical_amounts(package_id, amounts)


@router.get("/reclaim_package/{package_id}/export")
def export_package(package_id: int) -> Response:
    """导出可回导的凭据包 CSV：原始审计字段 + 核验结论。"""
    _require_package(package_id)
    content = reclaim_engine.export_package_csv(package_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"回收凭据包 {package_id} 不存在")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="reclaim_package_{package_id}.csv"'},
    )


@router.get("/reclaim_credential/{credential_id}/detail")
def credential_detail(credential_id: int) -> dict[str, Any]:
    for row in _credential_rows():
        if int(row.get("id", 0)) == credential_id:
            return {"credential": row, "released": row.get("status") in ("有效", "已核销")}
    raise HTTPException(status_code=404, detail=f"回收凭据 {credential_id} 不存在")


def _credential_rows() -> list[dict[str, Any]]:
    from app.store import store
    return store.rows("reclaim_credential")


@router.get("/reclaim/{lake_table}/released")
def check_released(lake_table: str, item_no: str = "", file_id: str = "") -> dict[str, Any]:
    """新回收流程判断同一文件/条目是否已经释放。"""
    return reclaim_engine.check_released(lake_table, item_no=item_no, file_id=file_id)


@router.get("/reclaim/{lake_table}/summary")
def release_summary(lake_table: str) -> dict[str, Any]:
    """列出该湖表全部有效凭据与释放量（同一条目只返回一份）。"""
    return reclaim_engine.release_summary(lake_table)


@router.get("/reclaim/files")
def list_reclaim_files(lake_table: str = "") -> dict[str, Any]:
    """列出湖表当前可安全处置的文件，供回收单选择候选（例如本轮的新文件 J）。"""
    tables = [lake_table] if lake_table else list(lake_store._files)  # noqa: SLF001
    files = []
    for table in tables:
        files.extend(
            {
                "file_id": item.file_id,
                "lake_table": item.lake_table,
                "partition_key": item.partition_key,
                "file_rows": item.file_rows,
                "file_bytes": item.file_bytes,
            }
            for item in lake_store.list_files(table)
        )
    return {"items": files, "total": len(files)}


@router.post("/reclaim/files")
def register_disposable_file(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """登记可安全处置的新文件（例如本轮的 J），供回收单作为候选。"""
    lake_table = str(payload.get("lake_table") or "").strip()
    if not lake_table:
        return JSONResponse(status_code=400, content={"ok": False, "message": "缺少所属湖表"})
    pf = lake_store.register_file(
        lake_table,
        str(payload.get("partition_key") or "reclaim"),
        rows=int(payload.get("rows") or 0),
        bytes_=int(payload.get("bytes") or payload.get("file_bytes") or 0),
        manifest=str(payload.get("file_id") or "") or None,
    )
    return {
        "ok": True,
        "message": "可处置文件已登记",
        "file": {
            "file_id": pf.file_id,
            "lake_table": pf.lake_table,
            "file_bytes": pf.file_bytes,
            "file_rows": pf.file_rows,
        },
    }


@router.post("/reclaim_order")
def create_order(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """提交回收单：所需核销字节 + 候选凭据/文件。"""
    result = reclaim_engine.create_order(
        lake_table=str(payload.get("lake_table") or "").strip(),
        required_bytes=int(payload.get("required_bytes") or payload.get("所需核销字节") or 0),
        candidates=payload.get("candidates"),
        keep_evidence=bool(payload.get("keep_evidence") or payload.get("保留证据")),
        order_no=str(payload.get("order_no") or ""),
    )
    if not result.get("ok"):
        return JSONResponse(status_code=400, content=result)
    return result


@router.post("/reclaim_order/{order_id}/execute")
def execute_order(order_id: int, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """执行回收：F 待核时不得采用；已分摊项不重复核销。"""
    keep = payload.get("keep_evidence")
    if keep is None:
        keep = payload.get("保留证据")
    result = reclaim_engine.execute_order(
        order_id,
        keep_evidence=(bool(keep) if keep is not None else None),
        operator=str(payload.get("operator") or ""),
    )
    if result.get("order") is None:
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/reclaim_order/{order_id}/detail")
def order_detail(order_id: int) -> dict[str, Any]:
    detail = reclaim_engine.order_detail(order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"回收单 {order_id} 不存在")
    return detail


@router.post("/reclaim_order/{order_id}/close")
def close_order(order_id: int) -> dict[str, Any]:
    return reclaim_engine.close_order(order_id)
