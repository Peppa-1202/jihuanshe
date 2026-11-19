"""Shared schemas, conservative merchandise correction, and CSV checkpoints."""
import csv
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import jhs_lorcana_en_phase1 as p1
import jhs_lorcana_en_phase2 as p2
import jhs_lorcana_en_phase3_all_fix7 as p3

SPREADSHEET_ID = ''  # Required repository Variable; never publish the private workbook ID.
# The supplied Phase2 gid is the two-column Phase1 count sheet: keep it intact.
SPECS = {
    'inventory': (1241568147, '1.inventory', p1.INVENTORY_COLUMNS, 'jhs_lorcana_en_product_inventory.csv'),
    'counts': (1829516308, '1.inventory counts', p1.COUNT_COLUMNS, 'jhs_lorcana_en_inventory_counts.csv'),
    'details': (2026111902, '2.product details', p2.DETAIL_COLUMNS, 'jhs_lorcana_en_phase2_product_details.csv'),
    'multi': (939666554, '2.multi parents', p2.MULTI_COLUMNS, 'jhs_lorcana_en_phase2_multi_parents.csv'),
    'cards': (900398850, '3.all cards(Myself)', p3.CARD_COLUMNS, 'jhs_lorcana_en_phase3_all_cards.csv'),
    'noncards': (679165075, '3.all non-cards(Myself)', p3.NONCARD_COLUMNS, 'jhs_lorcana_en_phase3_all_noncard.csv'),
    'checks': (1116477578, '3.all products check(Myself)', p3.CHECK_COLUMNS, 'jhs_lorcana_en_phase3_all_product_check.csv'),
}
NUMBERS = {'产品数量', '发现顺序', '页面张数', '收录商品数量', '子类数量', '抓取序号', '项目序号',
           '抓取卡牌数', '抓取非卡数', '抓取总项目数'}


def key(row):
    return tuple(str(row.get(c, '') or '') for c in ('系列', '详情编号', '详情名称'))


def decode(rows, headers):
    if not rows:
        return []
    if rows[0][:len(headers)] != headers or any(v not in ('', None) for v in rows[0][len(headers):]):
        raise ValueError('Google 表格表头不匹配：' + ','.join(headers))
    result = []
    for values in rows[1:]:
        if not any(v not in ('', None) for v in values):
            continue
        if any(v not in ('', None) for v in values[len(headers):]):
            raise ValueError('管理范围外有额外数据，停止覆盖')
        result.append({h: (values[i] if i < len(values) and values[i] is not None else '') for i, h in enumerate(headers)})
    return result


def encode(records, headers):
    return [headers] + [[r.get(h, '') for h in headers] for r in records]


def read_csv(path):
    if not Path(path).exists():
        return []
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, headers, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    def value(h, v):
        if ('时间' in h or h == '发售日期') and isinstance(v, (int, float)):
            return (datetime(1899, 12, 30) + timedelta(days=v)).strftime('%Y-%m-%d %H:%M:%S')
        return '' if v is None else v
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        w.writeheader()
        w.writerows({h: value(h, r.get(h, '')) for h in headers} for r in records)


def typed(records):
    result = deepcopy(records)
    for r in result:
        for h in NUMBERS & r.keys():
            if str(r[h]).isdigit():
                r[h] = int(r[h])
    return result


def repair(cards, noncards, checks):
    """Move by explicit type, never by substrings in a character/card name.

    No cross-product deduplication, and no deletion of identical visible items.
    Idempotent because only records still in the card table are moved.
    """
    kept, moved = [], []
    noncards, checks = deepcopy(noncards), deepcopy(checks)
    seq = {}
    for r in noncards:
        seq[key(r)] = max(seq.get(key(r), 0), p3.parse_int(r.get('项目序号')) or 0)
    for r in cards:
        if not p3.classify_noncard(str(r.get('稀有度', '') or ''), str(r.get('全部可见文字', '') or '').split(' | ')):
            kept.append(deepcopy(r))
            continue
        k = key(r)
        seq[k] = seq.get(k, 0) + 1
        out = {h: r.get(h, '') for h in p3.NONCARD_COLUMNS}
        out.update({'项目序号': seq[k], '项目类型': r.get('稀有度', '')})
        noncards.append(out)
        moved.append(deepcopy(r))
    affected = {key(r) for r in moved}
    ccount, ncount = Counter(map(key, kept)), Counter(map(key, noncards))
    seen = set()
    for r in checks:
        k = key(r)
        if k not in affected:
            continue
        if k in seen:
            raise ValueError('产品核对表有重复身份，停止修正')
        seen.add(k)
        nc, nn = ccount[k], ncount[k]
        cc, tc = p3.evaluate_counts([None]*nc, [None]*nn, p3.parse_int(r.get('页面张数')),
                                   p3.parse_int(r.get('收录商品数量')), r.get('系列', ''), r.get('详情名称', ''))
        r.update({'抓取卡牌数': nc, '抓取非卡数': nn, '抓取总项目数': nc+nn, '卡牌核对': cc, '总项目核对': tc})
        if str(r.get('完成依据', '')).startswith('END') and '不一致' not in (cc, tc):
            r['统计状态'] = '完成（纯周边产品，分类已修正）' if nc == 0 else '完成（分类已修正）'
        else:
            r['统计状态'] = '待复核（分类已修正，保留原采集依据）'
        r['备注'] = (f'2026-11-19 类型规则修正：卡牌 {nc}，非卡 {nn}，总项目 {nc+nn}；'
                   '页面张数保留来源值；未重新抓取，抓取时间和完成依据保留。原备注（历史）：' + str(r.get('备注', '')))
    if seen != affected:
        raise ValueError('待修正产品缺少核对记录，停止写入')
    return kept, noncards, checks, {'moved': len(moved), 'types': dict(Counter(r['稀有度'] for r in moved)),
                                  'products': [list(k) for k in sorted(affected)]}


def repair_csv_files(cards_path, noncards_path, checks_path):
    if not Path(cards_path).exists():
        return {'moved': 0}
    values = repair(read_csv(cards_path), read_csv(noncards_path), read_csv(checks_path))
    if values[3]['moved']:
        for path, headers, records in zip((cards_path, noncards_path, checks_path),
                                         (p3.CARD_COLUMNS, p3.NONCARD_COLUMNS, p3.CHECK_COLUMNS), values[:3]):
            write_csv(path, headers, records)
    return values[3]


def bootstrap_details(inventory, checks, multi):
    """Recover only known values; unknown homepage/date fields remain blank."""
    parents = {(r['系列'], r['父列表编号'], r['父列表名称']) for r in multi}
    result = []
    for src in inventory:
        r = {h: '' for h in p2.DETAIL_COLUMNS}
        r.update({h: src[h] for h in ('系列', '列表编号', '列表名称')})
        matches = [c for c in checks if c['系列'] == src['系列'] and c['详情编号'] == src['列表编号']
                   and c['详情名称'] == src['列表名称']]
        if len(matches) == 1:
            r.update({h: matches[0].get(h, '') for h in ('详情编号', '详情名称', '页面张数', '收录商品数量')})
        is_multi = (src['系列'], src['列表编号'], src['列表名称']) in parents
        r['统计状态'] = '多子类待处理' if is_multi else '待重新读取详情'
        r['备注'] = '由现有 Phase1/Phase3 表迁移已知字段；非本次采集，首页、发售日期等缺失字段待 Phase2 补全。'
        result.append(r)
    return result
