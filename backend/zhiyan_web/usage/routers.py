"""Token 用量统计 API。"""
from fastapi import APIRouter

from zhiyan_web.core.llm.usage_meter import summary, price_for

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/summary")
async def usage_summary(days: int = 30):
    """最近 N 天的 token / 费用汇总（按日、按模型）。"""
    return summary(days)


@router.get("/price")
async def usage_price(model: str = ""):
    """查询单个模型的估算单价（USD / 1M tokens）。"""
    return {"model": model, "price": price_for(model)}
