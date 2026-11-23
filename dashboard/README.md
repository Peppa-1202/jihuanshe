# 集换社洛卡纳运行看板

[运行工作流](../../actions/workflows/sync.yml) · [配置说明](../README.md)

每次运行后自动更新；保留最近 200 次记录，较早记录可从 Git 历史查看。

## 最近一次

状态：**完成（有待复核）**；模式：phase3；时间：2026-11-23T10:19:22+08:00

实际写入：是；已同步阶段：phase3

| 指标 | 运行前 | 运行后 / 预览结果 | 变化 |
| --- | ---: | ---: | ---: |
| 产品 | 34 | 34 | +0 |
| 卡牌记录 | 6727 | 6727 | +0 |
| 非卡商品 | 214 | 214 | +0 |
| 待复核产品 | 7 | 7 | +0 |
| 失败产品 | 0 | 0 | +0 |

纠正非卡分类：0 条。

卡牌记录按产品保留关系，不跨产品去重。纯表格纠错不代表重新采集 App。

- Phase 3 已完成可处理的产品。数量或完成依据待确认的产品已列入人工复核清单。

- 现有核对表仍有 7 个产品待复核；未修改其采集完成依据。

### 人工复核：产品数量与采集状态

| 系列 | 编号 | 产品 | 状态 | 完成依据 | 页面张数 | 页面收录商品 | 实抓卡牌 | 实抓非卡 | 实抓合计 | 卡牌核对 | 总项目核对 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 简体中文 | PR | [简中]活动PR | 待复核（未见END） | 稳定无END（待复核） | 3 | 13 | 13 | 0 | 13 | 不一致 | 一致 |
| 推广卡 | P3 | Year 3 推广卡 | 抓取完成（END，数量待复核） | END | 55 | 1 | 55 | 1 | 56 | 一致 | 不一致 |
| 推广卡 | P2 | Year 2 推广卡 | 抓取完成（END，数量待复核） | END | 37 | 2 | 37 | 2 | 39 | 一致 | 不一致 |
| 推广卡 | P1 | Year 1 推广卡 | 抓取完成（END，数量待复核） | END | 39 | 2 | 39 | 2 | 41 | 一致 | 不一致 |
| 推广卡 | C2 | 洛卡纳挑战赛 Year 2 | 待复核（未见END） | 稳定无END（待复核） | 3 | 13 | 13 | 0 | 13 | 不一致 | 一致 |
| 推广卡 | C1 | 洛卡纳挑战赛 Year 1 | 待复核（未见END） | 稳定无END（待复核） | 5 | 9 | 9 | 0 | 9 | 不一致 | 一致 |
| 推广卡 | D23·EN | D23 2024 活动特典 | 待复核（未见END） | 稳定无END（待复核） | 1 | 2 | 2 | 0 | 2 | 不一致 | 一致 |

## 运行历史

| 时间（北京时间） | 阶段 | 状态 | 写入 | 卡牌 | 非卡 | 待复核 | 运行详情 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-11-23T10:19:22+08:00 | phase3 | 完成（有待复核） | 是 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/45099497517) |
| 2026-11-23T10:13:21+08:00 | phase2 | 完成（有待复核） | 是 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/45098915802) |
| 2026-11-23T10:00:06+08:00 | all | 完成（有待复核） | 是 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/45097634108) |
| 2026-11-23T09:43:09+08:00 | phase3 | 完成（有待复核） | 是 | 6727 | 214 | 7 | [Actions](https://github.com/Peppa-1202/jihuanshe/actions/runs/45095413359) |
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
