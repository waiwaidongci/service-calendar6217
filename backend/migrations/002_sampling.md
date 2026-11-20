# 002 月度抽样

新增月度抽样两张表，由 SQLAlchemy 启动时自动创建（无需手写 DDL）：

- `sample_batches`：抽样批次。`request_key` 全局唯一，作为幂等键——失败后按同键重试直接找回已抽好的名单，不重复占用；`quotas`/`selected` 为 JSON 快照。
- `sample_occupations`：原单占用登记，`UNIQUE(month, order_no)` 保证同一月份内同一原单（含更正单）只被抽中一次；并发批次撞单时由该约束触发整批回滚并用剩余底稿重算。

同时底稿（service_cases.data）新增必填业务字段 `order_no`（原单编号），由 `validate_source` 严格校验；仅影响新底稿与新报告。旧报告冻结快照缺少该字段：照常查看与导出，抽样时返回 409 说明不能抽的原因。
