# 集换社洛卡纳运行看板

[运行工作流](../../actions/workflows/sync.yml) · [配置说明](../README.md)

每次运行后自动更新；保留最近 200 次记录，较早记录可从 Git 历史查看。

## 最近一次

状态：**失败（未写入）**；模式：phase1；时间：2026-11-20T14:34:04+08:00

实际写入：否；已同步阶段：无

| 指标 | 运行前 | 运行后 / 预览结果 | 变化 |
| --- | ---: | ---: | ---: |
| 产品 | 34 | 34 | +0 |
| 卡牌记录 | 6727 | 6727 | +0 |
| 非卡商品 | 214 | 214 | +0 |
| 待复核产品 | 7 | 7 | +0 |
| 失败产品 | 0 | 0 | +0 |

纠正非卡分类：0 条。

卡牌记录按产品保留关系，不跨产品去重。纯表格纠错不代表重新采集 App。

错误：Phase1 扫描未完整完成；不覆盖在线产品清单。已核验部分保存在运行附件。

## 运行历史

| 时间（北京时间） | 阶段 | 状态 | 写入 | 卡牌 | 非卡 | 待复核 | 运行详情 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-11-20T14:34:04+08:00 | phase1 | 失败（未写入） | 否 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/44688661370) |
| 2026-11-20T14:29:25+08:00 | phase3 | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/44688057372) |
| 2026-11-20T14:20:58+08:00 | phase3 | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/44687047888) |
| 2026-11-20T11:31:43+08:00 | repair | 完成（有待复核） | 是 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/44668315311) |
| 2026-11-20T11:21:54+08:00 | repair | 预览：完成（有待复核） | 否 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/44667181842) |
| 2026-11-20T09:41:59+08:00 | phase1 | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/zhucui1202/jihuanshe/actions/runs/44655354256) |
| 2026-11-19T17:48:40+08:00 | phase1 | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/zhucui1202/jihuanshe/actions/runs/44499695267) |
| 2026-11-19T15:46:38+08:00 | all | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/zhucui1202/jihuanshe/actions/runs/44476480377) |
| 2026-11-19T14:49:23+08:00 | all | 未取得运行报告：failure | 否 | — | — | — | [Actions](https://github.com/zhucui1202/jihuanshe/actions/runs/44474819576) |
| 2026-11-19T11:20:08+08:00 | 初始化：在线表格纠正（非 App 重抓） | 完成（保留待复核） | 是 | 6727 | 214 | 7 | 初始化 |
