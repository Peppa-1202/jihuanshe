from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

import jhs_lorcana_en_phase3_all_fix7 as p3
from jhs_tables import repair, repair_csv_files, SPECS, bootstrap_details, write_csv, read_csv
from google_sync import plan_sheet, GoogleSheets
from run_sync import validate_phase3, reconcile_phase2, main
from publish_dashboard import publish


def record(kind='徽章', name='推广徽章', code='LCNZ-SC008'):
    return {'系列':'简体中文','详情编号':'LCNZ','详情名称':'[简中]官方周边','页面张数':1,'抓取序号':1,
            '中文名':name,'英文名':'','编号显示':code,'稀有度':kind,'语言':'简','全部可见文字':name+' | '+kind}


def check(cards=1, noncards=0):
    return {'系列':'简体中文','详情编号':'LCNZ','详情名称':'[简中]官方周边','页面张数':1,
            '收录商品数量':cards+noncards,'抓取卡牌数':cards,'抓取非卡数':noncards,'抓取总项目数':cards+noncards,
            '卡牌核对':'不一致','总项目核对':'一致','完成依据':'END','统计状态':'抓取完成（END，数量待复核）','统计时间':'old','备注':'old'}


class MerchandiseTests(unittest.TestCase):
    def test_explicit_types(self):
        for kind in ['徽章','收纳盒','收納盒','玩偶','原盒','卡垫']:
            self.assertTrue(p3.classify_noncard(kind, []))
    def test_real_card_name_does_not_trigger(self):
        self.assertFalse(p3.classify_noncard('R', ['徽章','收纳盒','玩偶']))
        self.assertFalse(p3.classify_noncard('Promo', ['推广徽章 米奇']))
        self.assertFalse(p3.classify_noncard('', ['玩偶大师']))
    def test_parser_uses_type(self):
        root=ET.Element('node')
        for rid,text in [('officialNameTv','积分兑换玩偶 朱迪'),('versionTv','LCNZ-SC066'),('rarityTv','玩偶'),('lanTv','简')]:
            ET.SubElement(root,'node',{'resource-id':'com.jihuanshe:id/'+rid,'text':text,'bounds':'[0,0][100,100]'})
        parsed=p3.parse_card_item(root)
        self.assertTrue(parsed['非卡']);self.assertEqual(parsed['稀有度'],'玩偶')
    def test_move_and_idempotency(self):
        cards=[record()];oldcheck=check()
        c,n,ch,r=repair(cards,[],[oldcheck])
        self.assertEqual((len(c),len(n),r['moved']),(0,1,1))
        self.assertEqual(n[0]['项目类型'],'徽章')
        self.assertEqual(ch[0]['卡牌核对'],'不适用（纯周边产品）')
        self.assertEqual(ch[0]['统计时间'],'old')
        self.assertEqual(oldcheck['抓取卡牌数'],1)
        self.assertEqual(repair(c,n,ch)[:3],(c,n,ch))
        self.assertEqual(repair(c,n,ch)[3]['moved'],0)
    def test_no_end_stays_unverified(self):
        old=check();old['完成依据']='稳定无END（待复核）'
        ch=repair([record()],[],[old])[2][0]
        self.assertTrue(ch['统计状态'].startswith('待复核'))
    def test_same_name_distinct_items_and_products_not_deduped(self):
        a,b=record(),record();b['详情名称']='官方周边';b['系列']='周边商品'
        cb=check();cb.update({'详情名称':b['详情名称'],'系列':b['系列']})
        c,n,ch,r=repair([a,a,b],[],[check(2),cb])
        self.assertEqual(len(n),3);self.assertEqual([x['项目序号'] for x in n],[1,2,1])
    def test_normal_pool_count_not_exempted(self):
        cc,tc=p3.evaluate_counts([], [None],1,1,'补充包','普通卡池')
        self.assertEqual(cc,'不一致')
    def test_missing_check_stops(self):
        with self.assertRaises(ValueError):repair([record()],[],[])
    def test_csv_resume_repaired_before_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            paths=[Path(d)/f'{x}.csv' for x in ['c','n','ch']]
            for path,h,rows in zip(paths,[p3.CARD_COLUMNS,p3.NONCARD_COLUMNS,p3.CHECK_COLUMNS],[[record()],[],[check()]]):
                write_csv(path,h,rows)
            self.assertEqual(repair_csv_files(*paths)['moved'],1)
            self.assertEqual(len(read_csv(paths[0])),0)
            self.assertEqual(len(read_csv(paths[1])),1)
            self.assertEqual(repair_csv_files(*paths)['moved'],0)


class TableTests(unittest.TestCase):
    def test_counts_not_used_as_details(self):
        self.assertNotEqual(SPECS['counts'][0],SPECS['details'][0])
        self.assertEqual(len(SPECS['counts'][2]),2)
        self.assertEqual(len(SPECS['details'][2]),15)
    def test_bootstrap_does_not_invent_complete_status(self):
        inv=[{'系列':'简体中文','列表编号':'LCNZ','列表名称':'[简中]官方周边'}]
        d=bootstrap_details(inv,[check()],[])[0]
        self.assertEqual(d['统计状态'],'待重新读取详情');self.assertEqual(d['发售日期'],'')
    def test_missed_phase2_product_is_not_old_success(self):
        inv=[{'系列':'S','列表编号':'A','列表名称':'a'}]
        old=[{**inv[0],'统计状态':'完成','页面张数':2}]
        new=reconcile_phase2(old,[],inv)
        self.assertEqual(new[0]['页面张数'],2)
        self.assertTrue(new[0]['统计状态'].startswith('未完成'))
    def test_inconsistent_checkpoint_rejected(self):
        with self.assertRaises(ValueError):validate_phase3({'cards':[],'noncards':[],'checks':[check()]})
    def test_scoped_values_write_preserves_unrelated_structure(self):
        spec=(1,'test',['A','B'],'test.csv')
        sh={'properties':{'sheetId':1,'gridProperties':{'rowCount':20,'columnCount':5}},
            'merges':[{'sheetId':1,'startRowIndex':10,'endRowIndex':11,'startColumnIndex':0,'endColumnIndex':2}]}
        req,bounds=plan_sheet(sh,[['A','B'],['a','old']],[['A','B'],['a','new']],spec)
        self.assertEqual(bounds,(1,2,2))
        self.assertFalse(any('unmergeCells' in r for r in req))
        self.assertEqual(req[0]['updateCells']['fields'],'userEnteredValue')
    def test_only_intersecting_data_merges_unmerged(self):
        spec=(1,'test',['A','B'],'test.csv')
        sh={'properties':{'sheetId':1,'gridProperties':{'rowCount':20,'columnCount':5}},
            'merges':[{'sheetId':1,'startRowIndex':1,'endRowIndex':2,'startColumnIndex':0,'endColumnIndex':2}]}
        req,_=plan_sheet(sh,[['A','B'],['a','']],[['A','B'],['a','new']],spec)
        self.assertEqual(req[0]['unmergeCells']['range']['startRowIndex'],1)
    def test_literal_strings_not_formulas(self):
        req,_=plan_sheet(None,[],[['A','B'],['=cmd','001']],(1,'test',['A','B'],'test.csv'))
        cells=next(x['updateCells']['rows'][1]['values'] for x in req if 'updateCells' in x)
        self.assertEqual(cells[0]['userEnteredValue'],{'stringValue':'=cmd'})
        self.assertEqual(cells[1]['userEnteredValue'],{'stringValue':'001'})
    def test_no_change_no_requests(self):
        self.assertEqual(plan_sheet(None,[['A']],[['A']],(1,'x',['A'],'x')) ,([],None))
    def test_concurrent_edit_aborts_before_mutation(self):
        gs=GoogleSheets('test-sheet',service=object());gs.snapshot=lambda:{'counts':{'values':[['changed']]}}
        with self.assertRaises(RuntimeError):gs.apply({'counts':{'values':[['old']]}},{'counts':[]})
    def test_dry_run_does_not_write(self):
        baseline={k:{'records':[]} for k in SPECS}
        baseline['cards']['records']=[record()];baseline['checks']['records']=[check()]
        with tempfile.TemporaryDirectory() as d, patch('run_sync.GoogleSheets') as mock:
            mock.return_value.snapshot.return_value=baseline
            with patch('sys.argv',['run_sync.py','--phase','repair','--output',d]):
                self.assertEqual(main(),0)
            mock.return_value.apply.assert_not_called()
            r=json.loads((Path(d)/'report.json').read_text(encoding="utf-8"))
            self.assertFalse(r['applied']);self.assertEqual(r['repair']['moved'],1)
    def test_dashboard_rerun_identity_and_history(self):
        with tempfile.TemporaryDirectory() as d:
            r={'run_id':'1','attempt':'1','status':'完成','phase':'repair','started_at':'2026-11-19','applied':True}
            publish(r,d);publish(r,d)
            self.assertEqual(len(json.loads((Path(d)/'history.json').read_text())),1)
            r={**r,'attempt':'2','status':'失败'};publish(r,d)
            self.assertEqual(len(json.loads((Path(d)/'history.json').read_text())),2)
            self.assertIn('运行历史',(Path(d)/'README.md').read_text(encoding="utf-8"))


class Phase3ReviewTests(unittest.TestCase):
    def test_count_mismatch_and_pending_details_sync_successfully(self):
        self.run_workflow(p3.REVIEW_EXIT_CODE, expected_exit=0, writes=True)

    def test_product_capture_failure_remains_failure(self):
        self.run_workflow(p3.PRODUCT_FAILURE_EXIT_CODE, expected_exit=1, writes=True)

    def test_unexpected_exit_does_not_publish(self):
        self.run_workflow(1, expected_exit=1, writes=False)

    def test_write_failure_remains_failure(self):
        self.run_workflow(p3.REVIEW_EXIT_CODE, expected_exit=1, writes=True, write_error=True)

    def run_workflow(self, collector_exit, expected_exit, writes, write_error=False):
        baseline = {k: {'records': []} for k in SPECS}
        baseline['cards']['records'] = [record('R', '测试卡')]
        baseline['checks']['records'] = [{**check(), '收录商品数量': 0, '卡牌核对': '一致', '总项目核对': '不一致'}]
        baseline['details']['records'] = [
            {'系列': '推广卡', '列表编号': 'P3', '列表名称': 'Year 3 推广卡', '统计状态': '待重新读取详情'},
        ]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adb = root / 'adb.exe'
            adb.touch()
            argv = ['run_sync.py', '--phase', 'phase3', '--apply', '--adb', str(adb), '--output', d]
            sheets = Mock()
            sheets.snapshot.return_value = baseline
            if write_error:
                sheets.apply.side_effect = RuntimeError('写入失败')
            with (patch('sys.argv', argv), patch('run_sync.GoogleSheets', return_value=sheets),
                  patch('run_sync.subprocess.run', return_value=Mock(returncode=0, stdout='device')),
                  patch('run_sync.execute_phase', return_value=collector_exit)):
                self.assertEqual(main(), expected_exit)
            self.assertEqual(sheets.apply.called, writes)
            report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['applied'], writes and not write_error)
            if expected_exit:
                self.assertIn('失败', report['status'])
                return
            self.assertEqual(report['status'], '完成（有待复核）')
            self.assertEqual(report['review_products'][0]['total_check'], '不一致')
            self.assertEqual(report['review_products'][0]['page_products'], 0)
            self.assertEqual(report['phase3_pending'][0]['code'], 'P3')
            publish(report, root / 'dashboard')
            for report_path in [root / 'summary.md', root / 'dashboard' / 'README.md']:
                rendered = report_path.read_text(encoding='utf-8')
                self.assertIn('人工复核', rendered)
                self.assertIn('Year 3 推广卡', rendered)

    def test_collector_reports_review_for_an_unready_phase2(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            details = root / 'details.csv'
            write_csv(details, SPECS['details'][2], [
                {'系列': '推广卡', '列表编号': 'P3', '列表名称': 'Year 3', '统计状态': '待重新读取详情'},
            ])
            args = ['phase3', '--details', str(details), '--check-output', str(root / 'checks.csv')]
            with patch('sys.argv', args + ['--report-review']):
                self.assertEqual(p3.main(), p3.REVIEW_EXIT_CODE)
            pending = read_csv(root / 'checks_unprocessed.csv')
            self.assertEqual(pending[0]['列表编号'], 'P3')
            self.assertIn('待重新读取详情', pending[0]['未处理原因'])

    def test_collector_preserves_mismatched_counts_for_review(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            details = root / 'details.csv'
            checks = root / 'checks.csv'
            write_csv(details, SPECS['details'][2], [
                {'系列': '推广卡', '列表编号': 'P3', '列表名称': 'Year 3',
                 '页面张数': 1, '收录商品数量': 1, '统计状态': '完成'},
            ])
            args = ['phase3', '--details', str(details), '--check-output', str(checks),
                    '--output', str(root / 'cards.csv'), '--noncard-output', str(root / 'noncards.csv'),
                    '--report-review']
            with (patch('sys.argv', args), patch.object(p3, '_LAST_POOL_END', 'END'),
                  patch.object(p3, 'ensure_library_home_page'), patch.object(p3, 'ensure_category_page'),
                  patch.object(p3, 'open_category_product'),
                  patch.object(p3, 'collect_one_card_pool',
                               return_value=([record('R', '测试卡')], [record()]))):
                self.assertEqual(p3.main(), p3.REVIEW_EXIT_CODE)
            saved = read_csv(checks)[0]
            self.assertEqual((saved['卡牌核对'], saved['总项目核对']), ('一致', '不一致'))
            self.assertEqual(saved['抓取总项目数'], '2')
            self.assertIn('待复核', saved['统计状态'])


if __name__=='__main__':unittest.main()
