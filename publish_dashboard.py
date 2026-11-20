"""Publish a persistent Markdown board and JSON history; no credentials/raw card dumps."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from run_sync import summary


def safe(value):
    return str(value).replace('|','\\|').replace('\n',' ').replace('<','&lt;').replace('>','&gt;')


def publish(report, destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    path=destination/'history.json'
    history=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    identity=lambda r:(str(r.get('run_id')),str(r.get('attempt','1')))
    history=[r for r in history if identity(r)!=identity(report)]+[report]
    history=sorted(history,key=lambda r:r.get('started_at',''),reverse=True)[:200]
    path.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 集换社洛卡纳运行看板','','[运行工作流](../../actions/workflows/sync.yml) · [配置说明](../README.md)', '',
           '每次运行后自动更新；保留最近 200 次记录，较早记录可从 Git 历史查看。', '',
           '## 最近一次', '',summary(history[0]).replace('# 集换社洛卡纳运行总结','').strip(), '',
           '## 运行历史', '', '| 时间（北京时间） | 阶段 | 状态 | 写入 | 卡牌 | 非卡 | 待复核 | 运行详情 |',
           '| --- | --- | --- | --- | ---: | ---: | ---: | --- |']
    for r in history:
        counts=r.get('after',{})
        url=r.get('run_url','')
        link=f'[Actions]({url})' if url.startswith('https://github.com/') and '/actions/runs/' in url else '初始化'
        lines.append('| '+' | '.join([safe(r.get('started_at','')),safe(r.get('phase','')),safe(r.get('status','')),
            '是' if r.get('applied') else '否',str(counts.get('cards','—')),str(counts.get('noncards','—')),
            str(counts.get('review','—')),link])+' |')
    reviews=history[0].get('review_products',[])
    if reviews:
        lines += ['', '## 最新待复核产品', '', '| 系列 | 编号 | 名称 | 状态 | 完成依据 |', '| --- | --- | --- | --- | --- |']
        for r in reviews:
            lines.append('| '+' | '.join(safe(r.get(k,'')) for k in ['series','code','name','status','evidence'])+' |')
    (destination/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',required=True);p.add_argument('--output',default='dashboard');a=p.parse_args()
    path=Path(a.report)
    if path.exists():
        report=json.loads(path.read_text(encoding='utf-8'))
    else:
        report={'started_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
                'status':'未取得运行报告：'+os.getenv('SYNC_RESULT','unknown'), 'phase':os.getenv('PHASE','unknown'),
                'applied':False,'run_id':os.getenv('GITHUB_RUN_ID','unknown'),'attempt':os.getenv('GITHUB_RUN_ATTEMPT','1'),
                'run_url':'https://github.com/'+os.getenv('GITHUB_REPOSITORY','zhucui1202/jihuanshe')+'/actions/runs/'+os.getenv('GITHUB_RUN_ID',''),
                'warnings':['报告缺失，不能据此判断表格是否发生过写入；请检查 Actions 日志。']}
    publish(report,a.output)

if __name__=='__main__':main()
