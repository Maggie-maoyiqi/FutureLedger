"""Published semantic contract for the fixed personal ledger dataset."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATASET_ID = "personal_ledger"
VERSION = 1
CATEGORIES = ("餐饮", "交通", "购物", "娱乐", "订阅", "房租", "医疗", "其他", "工资", "转账")
METRICS = {
    "gross_expense": "总支出",
    "refund": "退款",
    "net_expense": "净支出",
    "income": "收入",
    "transaction_count": "流水笔数",
    "avg_expense": "平均每笔支出",
    "transfer_amount": "转账金额",
    "transfer_count": "转账笔数",
}
METRIC_DEFINITIONS = {
    "gross_expense": "已入账 expense/OUT 正整数分之和，不扣退款；转账单独统计",
    "refund": "已入账 refund/IN 金额之和，按退款入账日期计入",
    "net_expense": "已入账 expense/OUT 金额之和，减去同一日期范围内入账的退款",
    "income": "已入账 income/IN 金额之和；不含退款和账户互转",
    "transaction_count": "已入账消费和退款流水的条数，不把账户互转算作消费",
    "avg_expense": "已入账 expense/OUT 金额之和除以对应消费出账笔数；无出账时为空值",
    "transfer_amount": "已入账 transfer/OUT 金额之和；可按选定用途类别统计",
    "transfer_count": "已入账转账流水的条数",
}
GROUPS = {"category": "类别", "merchant": "商户", "month": "月份", "account": "账户"}


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str | None = None
    merchant: str | None = None
    account_id: str | None = None


class IntentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["aggregate", "detail"]
    dataset_id: Literal["personal_ledger"]
    semantic_version: Literal[1]
    metric: Literal["gross_expense", "refund", "net_expense", "income", "transaction_count", "avg_expense", "transfer_amount", "transfer_count"]
    start: date
    end_exclusive: date
    filters: Filters = Field(default_factory=Filters)
    group_by: Literal["category", "merchant", "month", "account"] | None = None
    series_by: Literal["category", "merchant", "account"] | None = None
    sort: Literal["metric_desc", "metric_asc", "date_desc", "date_asc"] = "metric_desc"
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def valid_combination(self):
        if self.start >= self.end_exclusive:
            raise ValueError("结束日期必须晚于开始日期")
        if (self.end_exclusive - self.start).days > 1096:
            raise ValueError("单次查询最多三年")
        if self.kind == "detail" and self.group_by is not None:
            raise ValueError("明细不能同时分组")
        if self.kind == "detail" and self.series_by is not None:
            raise ValueError("明细不能同时设置折线系列")
        if self.series_by is not None and self.group_by != "month":
            raise ValueError("只有按月份分组时才能生成多系列趋势图")
        if self.kind == "aggregate" and self.group_by is None and self.sort.startswith("date_"):
            raise ValueError("单值聚合没有日期排序")
        if self.kind == "aggregate" and self.group_by != "month" and self.sort.startswith("date_"):
            raise ValueError("日期排序只适用于月度分组")
        return self


def public_catalog():
    return {
        "dataset_id": DATASET_ID,
        "semantic_version": VERSION,
        "timezone": "Asia/Shanghai",
        "money_unit": "cent",
        "metrics": METRICS,
        "metric_definitions": METRIC_DEFINITIONS,
        "dimensions": GROUPS,
        "categories": CATEGORIES,
        "query_policy": "Only POSTED facts; refund by posting date; outgoing transfers count as outflows and incoming transfers count as inflows.",
    }
