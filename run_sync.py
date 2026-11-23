"""GitHub/local entry point. Sheets are authoritative; CSVs are run-local checkpoints."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from google_sync import GoogleSheets
from jhs_tables import SPECS, SPREADSHEET_ID, bootstrap_details, key, repair, read_csv, typed, write_csv
import jhs_lorcana_en_phase2 as p2
import jhs_lorcana_en_phase3_all_fix7 as p3

ROOT = Path(__file__).resolve().parent


def timestamp():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')


def stats(tables):
    checks = tables['checks']
    return {'products': len(tables['inventory']), 'cards': len(tables['cards']), 'noncards': len(tables['noncards']),
            'review': sum(not p3.finished_and_matched({k: str(v) for k,v in r.items()}) for r in checks),
            'failed': sum(str(r.get('统计状态', '')).startswith('失败') for r in checks)}


def validate_phase3(tables):
    c = Counter(map(key, tables['cards']))
    n = Counter(map(key, tables['noncards']))
    seen = set()
    for r in tables['checks']:
        k = key(r)
        if k in seen:
            raise ValueError('产品核对表出现重复身份')
        seen.add(k)
        if str(r.get('统计状态', '')).startswith('失败'):
            continue  # The old detail rows remain as the last known snapshot.
        expected = [p3.parse_int(r.get(h)) for h in ('抓取卡牌数', '抓取非卡数', '抓取总项目数')]
        if expected != [c[k], n[k], c[k]+n[k]]:
            raise ValueError('CSV 保存不完整或核对数不符，停止同步：' + str(k))
    if (c.keys() | n.keys()) - seen:
        raise ValueError('明细缺少产品核对记录')


def merge_details(before, new):
    # A failed --retry-all must not erase unprocessed products from Sheets.
    identity = lambda r: tuple(str(r.get(h, '')) for h in ('系列', '列表编号', '列表名称'))
    values = {identity(r): deepcopy(r) for r in before}
    for r in new:
        values[identity(r)] = r
    return list(values.values())


def reconcile_phase2(before, new, inventory):
    identity = lambda r: tuple(str(r.get(h, '')) for h in ('系列', '列表编号', '列表名称'))
    old = {identity(r): r for r in before}
    current = {identity(r): r for r in new}
    result = []
    for item in inventory:
        k = identity(item)
        if k in current:
            result.append(current[k])
        else:
            r = deepcopy(old.get(k, {h: '' for h in p2.DETAIL_COLUMNS}))
            r.update({h: item.get(h, '') for h in ('系列', '列表编号', '列表名称')})
            r.update({'统计状态': '未完成（本次未定位）', '统计时间': timestamp(),
                      '备注': '本次未找到产品详情；其余旧字段仅供参考，不能作为本次完成依据。'})
            result.append(r)
    return result


def execute_phase(phase, work, args):
    paths = {k: str((work / s[3]).resolve()) for k,s in SPECS.items()}
    base = ['--adb', args.adb, '--serial', args.serial]
    if phase == 'phase1':
        script = 'jhs_lorcana_en_phase1.py'
        flags = base + ['--output', paths['inventory'], '--counts-output', paths['counts']]
    elif phase == 'phase2':
        script = 'jhs_lorcana_en_phase2.py'
        flags = base + ['--input', paths['inventory'], '--output', paths['details'], '--multi-output', paths['multi']]
        if args.refresh:
            flags.append('--retry-all')
    else:
        script = 'jhs_lorcana_en_phase3_all_fix7.py'
        flags = base + ['--details', paths['details'], '--output', paths['cards'],
                        '--noncard-output', paths['noncards'], '--check-output', paths['checks'],
                        '--report-review']
        if args.refresh:
            flags.append('--redo')
    print('运行阶段：', phase, flush=True)
    return subprocess.run([sys.executable, '-u', str(ROOT/script), *flags], cwd=ROOT).returncode


def review_products(checks):
    fields = {
        'series': '系列', 'code': '详情编号', 'name': '详情名称', 'status': '统计状态',
        'evidence': '完成依据', 'page_cards': '页面张数', 'page_products': '收录商品数量',
        'cards': '抓取卡牌数', 'noncards': '抓取非卡数', 'total': '抓取总项目数',
        'card_check': '卡牌核对', 'total_check': '总项目核对',
    }
    return [{label: row.get(column, '') for label, column in fields.items()} for row in checks
            if not p3.finished_and_matched({k: str(v) for k, v in row.items()})]


def phase3_pending(details):
    pending = []
    for row in details:
        reason = p3.target_skip_reason({k: str(v) for k, v in row.items()})
        if reason:
            pending.append({'series': row.get('系列', ''), 'code': row.get('列表编号', ''),
                            'name': row.get('列表名称', ''), 'reason': reason})
    return pending


def review_table(title, records, columns):
    if not records:
        return []
    def escape(value):
        return str(value if value is not None and value != '' else '—').replace(
            '|', '&#124;').replace('\n', ' ').replace('\r', ' ').replace('<', '&lt;').replace('>', '&gt;')
    return ['', f'### {title}', '', '| ' + ' | '.join(label for _, label in columns) + ' |',
            '| ' + ' | '.join('---' for _ in columns) + ' |',
            *['| ' + ' | '.join(escape(record.get(k)) for k, _ in columns) + ' |' for record in records]]


def summary(report):
    a,b = report.get('before', {}),report.get('after', {})
    lines = ['# 集换社洛卡纳运行总结', '', f"状态：**{report['status']}**；模式：{report['phase']}；时间：{report['started_at']}", '',
             f"实际写入：{'是' if report.get('applied') else '否'}；已同步阶段：{', '.join(report.get('applied_phases', [])) or '无'}", '',
             '| 指标 | 运行前 | 运行后 / 预览结果 | 变化 |','| --- | ---: | ---: | ---: |']
    for k,label in [('products','产品'),('cards','卡牌记录'),('noncards','非卡商品'),('review','待复核产品'),('failed','失败产品')]:
        old,new = a.get(k,0),b.get(k,a.get(k,0))
        lines.append(f'| {label} | {old} | {new} | {new-old:+d} |')
    lines += ['', f"纠正非卡分类：{report.get('repair', {}).get('moved',0)} 条。", '',
              '卡牌记录按产品保留关系，不跨产品去重。纯表格纠错不代表重新采集 App。']
    for warning in report.get('warnings', []):
        lines += ['', '- ' + warning]
    if report.get('error'):
        lines += ['', '错误：' + report['error']]
    identity_columns = [('series', '系列'), ('code', '编号'), ('name', '产品')]
    lines += review_table('人工复核：产品数量与采集状态', report.get('review_products', []), [
        *identity_columns, ('status', '状态'), ('evidence', '完成依据'),
        ('page_cards', '页面张数'), ('page_products', '页面收录商品'),
        ('cards', '实抓卡牌'), ('noncards', '实抓非卡'), ('total', '实抓合计'),
        ('card_check', '卡牌核对'), ('total_check', '总项目核对'),
    ])
    lines += review_table('待补跑：Phase 2 未就绪的产品', report.get('phase3_pending', []), [
        *identity_columns, ('reason', '未处理原因'),
    ])
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['repair','phase1','phase2','phase3','all'], default='repair')
    parser.add_argument('--apply', action='store_true', help='Write Google Sheets; otherwise preview only')
    parser.add_argument('--refresh', action='store_true', help='Re-read completed Phase2/3 products to detect updates')
    parser.add_argument('--adb', default=os.getenv('ADB_PATH') or r'C:\platform-tools\adb.exe')
    parser.add_argument('--serial', default=os.getenv('ADB_SERIAL') or 'emulator-5554')
    parser.add_argument('--spreadsheet-id', default=os.getenv('SPREADSHEET_ID') or SPREADSHEET_ID)
    parser.add_argument('--output', default='output')
    args=parser.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True, exist_ok=True)
    # A unique directory avoids accidentally consuming stale CSVs on self-hosted runners.
    work=out/('checkpoints-'+str(time.time_ns()));work.mkdir()
    report={'started_at':timestamp(),'phase':args.phase,'status':'运行中','applied':False,'applied_phases':[],
            'warnings':[], 'run_id':os.getenv('GITHUB_RUN_ID','local-'+str(time.time_ns())),
            'attempt':os.getenv('GITHUB_RUN_ATTEMPT','1'), 'run_url':os.getenv('GITHUB_SERVER_URL','https://github.com')+'/'+os.getenv('GITHUB_REPOSITORY','zhucui1202/jihuanshe')+'/actions/runs/'+os.getenv('GITHUB_RUN_ID','')}
    exit_code=0
    try:
        gs=GoogleSheets(args.spreadsheet_id)
        baseline=gs.snapshot()
        tables={k:deepcopy(v['records']) for k,v in baseline.items()}
        report['before']=stats(tables)
        if args.phase == 'repair':
            tables['cards'],tables['noncards'],tables['checks'],report['repair']=repair(tables['cards'],tables['noncards'],tables['checks'])
        if not tables['details']:
            tables['details']=bootstrap_details(tables['inventory'],tables['checks'],tables['multi'])
        for k,spec in SPECS.items():
            write_csv(work/spec[3],spec[2],tables[k])
        if args.phase == 'repair':
            desired={k:tables[k] for k in ['cards','noncards','checks']}
            validate_phase3(tables)
            if args.apply:
                gs.apply(baseline,desired)
                report['applied']=True;report['applied_phases'].append('repair')
        else:
            # Check before navigating the App; never fall back to another serial.
            if not Path(args.adb).is_file():
                raise ValueError('找不到 ADB_PATH 指定的 adb.exe')
            state=subprocess.run([args.adb,'-s',args.serial,'get-state'],capture_output=True,text=True,timeout=20)
            if state.returncode or state.stdout.strip() != 'device':
                raise ValueError('指定的 ADB_SERIAL 未连接；请启动模拟器')
            phases=['phase1','phase2','phase3'] if args.phase=='all' else [args.phase]
            for phase in phases:
                rc=execute_phase(phase,work,args)
                if phase=='phase1' and rc:
                    raise RuntimeError('Phase1 扫描未完整完成；不覆盖在线产品清单。已核验部分保存在运行附件。')
                if phase == 'phase3' and rc not in (0, p3.REVIEW_EXIT_CODE, p3.PRODUCT_FAILURE_EXIT_CODE):
                    raise RuntimeError(f'Phase 3 异常退出（退出码 {rc}）；本阶段未写入 Google 表格。')
                names={'phase1':['inventory','counts'],'phase2':['details','multi'],'phase3':['cards','noncards','checks']}[phase]
                for k in names:
                    records=typed(read_csv(work/SPECS[k][3]))
                    if k=='details':
                        records=reconcile_phase2(tables[k],records,tables['inventory'])
                    elif k=='multi' and rc:
                        known={(r['系列'],r['父列表编号'],r['父列表名称']):r for r in tables[k]}
                        known.update({(r['系列'],r['父列表编号'],r['父列表名称']):r for r in records})
                        records=list(known.values())
                    tables[k]=records
                    write_csv(work/SPECS[k][3],SPECS[k][2],records)
                if phase=='phase2' and any(not p2.result_finished(r) for r in tables['details']):
                    rc=rc or 1
                if phase=='phase3':
                    tables['cards'],tables['noncards'],tables['checks'],extra=repair(tables['cards'],tables['noncards'],tables['checks'])
                    report['repair']=extra
                    validate_phase3(tables)
                    report['phase3_pending'] = phase3_pending(tables['details'])
                    pending_count = len(report['phase3_pending'])
                    if pending_count:
                        report['warnings'].append(f'Phase 2 有 {pending_count} 个产品未就绪，已列入待补跑清单。')
                if phase == 'phase3' and rc == p3.REVIEW_EXIT_CODE:
                    report['warnings'].append('Phase 3 已完成可处理的产品。数量或完成依据待确认的产品已列入人工复核清单。')
                elif rc:
                    report['warnings'].append(f'{phase} 返回 {rc}：保留已采集结果和失败/待复核状态，不宣称完整。')
                    exit_code=1
                    if phase == 'phase3':
                        report['error'] = 'Phase 3 有产品采集报错，详情见产品复核清单及采集日志。'
                desired={k:tables[k] for k in names}
                if args.apply:
                    gs.apply(baseline,desired)
                    report['applied']=True;report['applied_phases'].append(phase)
                    baseline=gs.snapshot()
                if rc and phase=='phase2':
                    break
        report['after']=stats(tables)
        report['review_products'] = review_products(tables['checks'])
        if report['after']['review']:
            report['warnings'].append(f"现有核对表仍有 {report['after']['review']} 个产品待复核；未修改其采集完成依据。")
        report['status']='完成（有待复核）' if report['warnings'] else '完成'
        if report.get('error'):
            report['status'] = '失败（已有结果保留）' if report['applied'] else '失败（未写入）'
        elif not args.apply:
            report['status']='预览：'+report['status']
    except Exception as exc:
        report['status']='失败（此前已同步阶段保留）' if report['applied'] else '失败（未写入）'
        # Avoid putting arbitrary API responses/credential contents in public dashboards.
        report['error']=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__+'；请检查 Actions 日志及配置'
        report['after']=report.get('before',{})
        if report['applied']:
            try:
                report['after']=stats({k:v['records'] for k,v in gs.snapshot().items()})
            except Exception:
                report['warnings'].append('错误后无法再次读取表格；看板计数为运行前快照，请检查已同步阶段。')
        print(report['error'],file=sys.stderr)
        exit_code=1
    finally:
        report['finished_at']=timestamp()
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        text=summary(report)
        (out/'summary.md').write_text(text,encoding='utf-8')
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(text)
        print(text)
    return exit_code

if __name__=='__main__':
    raise SystemExit(main())
