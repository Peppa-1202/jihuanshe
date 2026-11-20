# 集换社洛卡纳 → Google Sheets

[运行看板](dashboard/README.md) · [运行工作流](../../actions/workflows/sync.yml)

Phase 1–3 以在线表格为数据源和最终输出，CSV 只作为每次运行的中间断点与恢复附件，不需要手工导入 Excel。

**采集 App 必须使用你电脑上的模拟器。** GitHub 云端 runner 无法访问你的 `emulator-5554`。本项目将采集任务交给 Windows self-hosted runner；`repair`（仅纠正在线表格）直接用 GitHub 云端运行，不需要模拟器。

## 表格对应关系

工作簿地址仅存放在仓库配置中，不公开写进源码或看板。页签按下表的 gid 定位；不会改动其他核对/原始数据页。

| 阶段 | 页签 | gid | 用途 |
| --- | --- | --- | --- |
| Phase 1 | 1.inventory | 1241568147 | 产品清单 |
| Phase 1 | 1.inventory counts | 1829516308 | 系列产品数量 |
| Phase 2 | 2.product details | 2026111902 | 产品详情、页面张数、商品数、入口状态 |
| Phase 2 | 2.multi parents | 939666554 | 多子类父产品 |
| Phase 3 | 3.all cards(Myself) | 900398850 | 卡牌明细 |
| Phase 3 | 3.all non-cards(Myself) | 679165075 | 周边及密封商品 |
| Phase 3 | 3.all products check(Myself) | 1116477578 | 每个产品的数量核对和完成依据 |

Phase 2 原链接的 gid 1829516308 指向 Phase 1 的两列数量页。Phase 2 沿用工作簿中现有的详情页和多子类父产品页。单阶段只写上表中本阶段的页签，`repair` 只写 Phase 3 三张页签。初次迁移只恢复已有清单/核对表能确认的字段，未知首页字段和发售日期留空，状态为“待重新读取详情”。第一次采集请选 `all` 或先选 `phase2`；不要把迁移记录当作新采集完成结果。

## 一次性配置

### 1. Google 凭据

在**运行这个工作流的仓库**进入 Settings → Secrets and variables → Actions：

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Secret | `GOOGLE_SERVICE_ACCOUNT_JSON` | Google 服务账号 JSON 文件的完整内容 |
| Variable（必填） | `SPREADSHEET_ID` | 目标 Google 表格网址 `/d/` 与 `/edit` 之间的字符串 |
| Variable（采集） | `ADB_PATH` | 例如 `C:\platform-tools\adb.exe`，默认同此路径 |
| Variable（采集） | `ADB_SERIAL` | `adb devices` 显示的设备，例如 `emulator-5554` |

在 Google Cloud 项目中启用 Google Sheets API；把在线工作簿共享给 JSON 的 `client_email`，授予编辑权限。可以使用原 Lorcana 同步项目的服务账号，但必须对**这个**工作簿授权。不要上传 JSON 到仓库，也不要放入 Variable。各仓库的 Secret 不会随着代码同步复制。

### 2. 注册 Windows 执行电脑

1. 在安装雷电/其他 Android 模拟器的 Windows 电脑上，进入仓库 Settings → Actions → Runners → New self-hosted runner。
2. 选择 Windows / x64，按 GitHub 页面当时显示的下载、解压和配置命令执行。注册 token 使用页面生成的临时值，不写入代码。
3. 配置时保留默认 `self-hosted`、`Windows`、`X64` 标签，**添加自定义标签 `jihuanshe`**。
4. 用当前 Windows 用户运行 runner 的 `run.cmd`，保持窗口在线。首次建议前台运行，避免 Windows 服务用户看不到你的模拟器。
5. 启动模拟器，关闭此前导致集换社无法打开的 root，进入“迪士尼洛卡纳 → 英文”首页。确认 `adb devices` 和配置的序列号一致。
6. 运行期间不要操作模拟器；Windows 电脑保持开机、不休眠。

若 Actions 一直显示 **Waiting for a runner**，检查电脑是否在线以及是否添加 `jihuanshe` 标签。不要把 ADB 端口开放到公网。工作流仅允许手工在默认分支运行，不会执行外部 PR。

### 3. 看板写入权限

工作流的 dashboard job 已声明 `contents: write`。若组织策略/分支保护禁止机器人提交，按仓库策略允许这个 job 写入；不需要关闭分支保护。即使看板提交失败，Actions 的 Summary 和 run-report 附件仍保留本次结果。

## 日常运行

打开 Actions → **Sync Jihuanshe to Google Sheets** → **Run workflow**，选择默认分支 `main`。日常更新保留默认的 `all`、`apply` 和 `refresh`，即可重新采集并写回。

| phase | 执行位置 | 行为 |
| --- | --- | --- |
| `repair` | GitHub 云端 | 读取在线表格，纠正非卡误分类并更新核对表，不采集 App |
| `all` | 你的 Windows runner | 依次重新扫描 Phase 1、Phase 2、Phase 3 并分阶段写回 |
| `phase1` | Windows runner | 更新产品清单和系列产品数量 |
| `phase2` | Windows runner | 更新产品详情与多子类父产品状态 |
| `phase3` | Windows runner | 基于已完成的 Phase 2 详情，采集卡牌、非卡及产品核对 |

- `apply` 默认开启：写回 Google 表格；关闭时仅预览，不修改在线数据。
- `refresh` 默认开启：Phase 2/3 重读已完成产品，才能发现旧产品里的新增卡牌。关闭时仅续跑未完成/不一致的产品，**不能保证发现已完成产品的新数据**。Phase 1 始终扫描当前清单。
- 首次可运行 `repair` 验证 Google 凭据；它不需要 Windows runner。
- Phase 1 不完整时不覆盖在线清单。后续阶段失败保留旧结果，并记录失败/待复核；已成功同步的前一阶段不回滚。采集期间有人修改目标在线页签时停止写入，请重新运行。
- 工作流同一时刻只执行一轮，避免两个采集任务争抢模拟器。
- 超时或中止后的 CSV 断点在 `checkpoints` 附件中保留 14 天；报告保留 90 天。在线表格仍是下次运行的基线。

## 非卡识别和本次纠正

类型字段加入徽章、收纳盒、玩偶及对应繁体/明确周边类型；不会按商品名包含某个词就删除记录。卡名含“徽章”但稀有度为 R/Promo 的卡仍属于卡牌。

2026-11-19 已对在线表格完成历史纠正：20 条徽章、1 条收纳盒、5 条玩偶从卡牌页移入非卡页。卡牌记录 6,753 → **6,727**，非卡 188 → **214**。“[简中]官方周边”改为 **0 卡 / 85 非卡 / 85 总项目**，来源页面张数 1 原样保留，其卡牌核对标记“不适用（纯周边产品）”。

保留中文名、英文名、编号、语言、全部可见文字与所属产品。不跨产品去重，不删除同文字但独立出现的项目。未见 END 的产品继续保留待复核状态，不会靠分类修正伪造采集完成。

## 运行看板

[dashboard/README.md](dashboard/README.md) 显示最新运行结果、运行前后数量、变化量、分类修正数、待复核产品、历史记录及 Actions 链接。

`dashboard/history.json` 保留最近 200 次运行；更早版本在 Git 历史中。初始记录是本次在线表格纠正，明确标注为初始化，并不是模拟器重抓。每次 workflow 的 Actions Summary 也会显示当次总结。缺少报告或任务失败会单独标注，不能把它理解成数据已完整更新。

## 本地运行（不经过 GitHub runner）

在本仓库目录用 PowerShell 执行，Python 3.10+：

```powershell
python -m pip install -r requirements.txt
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content -Raw 'C:\secure\service-account.json'
$env:SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID'
python run_sync.py --phase repair                 # 只预览
python run_sync.py --phase repair --apply         # 修正在线表格
python run_sync.py --phase all --refresh --apply  # 采集并同步
```

原三个脚本仍可直接运行并输出 CSV；需要写在线表格时使用统一入口 `run_sync.py`。保留所有 `.py` 文件在同一个目录。

## 同步到 Peppa-1202

代码主仓库：[zhucui1202/jihuanshe](https://github.com/zhucui1202/jihuanshe)。目标 `Peppa-1202/jihuanshe` 若仍为空仓库，在有两个仓库访问权限的电脑上执行：

```powershell
git clone https://github.com/zhucui1202/jihuanshe.git
cd jihuanshe
git remote add peppa https://github.com/Peppa-1202/jihuanshe.git
git push peppa main
```

以后从同一目录同步：

```powershell
git pull --ff-only origin main
git fetch peppa
git merge peppa/main
git push peppa main
```

目标仓库已经产生独立提交时合并保留它们；不要强制覆盖。若提示两个仓库历史不相关，先核对目标已有文件，再人工合并。由 `Peppa-1202` 运行时重新配置它的 Google Secret、Variables 和 self-hosted runner。建议只选一个仓库负责写这份 Google 表格，避免两个仓库同时采集写入。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试覆盖商品类型解析、真实卡名不误伤、旧 CSV 断点纠正、重复运行不重复移动、多产品关系保留、纯周边数量核对、表格并发修改保护、合并单元格写入范围、预览不写入以及看板历史。模拟器导航沿用用户提供的 Phase1/2 和 Phase3 fix7 逻辑；本次没有连接用户电脑做完整 App 采集实测。
