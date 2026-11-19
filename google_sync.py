"""Google Sheets adapter: scoped writes, optimistic checks and atomic batches."""
from copy import deepcopy
import json
import os
from jhs_tables import SPECS, SPREADSHEET_ID, decode, encode


def cell(value):
    if value is None or value == '':
        return {}
    return {'userEnteredValue': {'numberValue': value} if isinstance(value, (int, float)) else {'stringValue': str(value)}}


def normalized(rows, width):
    return [[('' if v is None else v) for v in r[:width]] + ['']*max(0, width-len(r)) for r in rows]


def plan_sheet(sheet, before, after, spec):
    sid, title, headers, _ = spec
    width = len(headers)
    old, new = normalized(before, width), normalized(after, width)
    height = max(len(old), len(new))
    old += [['']*width for _ in range(height-len(old))]
    new += [['']*width for _ in range(height-len(new))]
    changes = [i for i in range(height) if old[i] != new[i]]
    if not changes:
        return [], None
    start, end = min(changes), max(changes)+1
    requests = []
    if sheet is None:
        requests.append({'addSheet': {'properties': {'sheetId': sid, 'title': title,
            'gridProperties': {'rowCount': max(1000, height), 'columnCount': max(26, width), 'frozenRowCount': 1}}}})
    else:
        sid = sheet['properties']['sheetId']
        grid = sheet['properties']['gridProperties']
        for dimension, count, current in [('ROWS', height, grid['rowCount']), ('COLUMNS', width, grid['columnCount'])]:
            if count > current:
                requests.append({'appendDimension': {'sheetId': sid, 'dimension': dimension, 'length': count-current}})
        # Excel import merged blank language/number cells. Unmerge only rewritten rows
        # so future nonblank values cannot be swallowed by those old merged cells.
        intersections = [m for m in sheet.get('merges', []) if m.get('startRowIndex',0) < end
                         and m['endRowIndex'] > start and m.get('startColumnIndex',0) < width]
        for m in intersections:
            if m.get('startRowIndex',0) < start or m['endRowIndex'] > end or m['endColumnIndex'] > width:
                raise ValueError('合并单元格跨越写入边界，停止同步')
        if intersections:
            requests.append({'unmergeCells': {'range': {'sheetId': sid, 'startRowIndex': start,
                'endRowIndex': end, 'startColumnIndex': 0, 'endColumnIndex': width}}})
        if len(after) > len(before) and len(before) > 1:
            requests.append({'copyPaste': {'source': {'sheetId': sid, 'startRowIndex': len(before)-1,
                'endRowIndex': len(before), 'startColumnIndex': 0, 'endColumnIndex': width},
                'destination': {'sheetId': sid, 'startRowIndex': len(before), 'endRowIndex': len(after),
                                'startColumnIndex': 0, 'endColumnIndex': width}, 'pasteType': 'PASTE_FORMAT'}})
    requests.append({'updateCells': {'range': {'sheetId': sid, 'startRowIndex': start, 'endRowIndex': end,
        'startColumnIndex': 0, 'endColumnIndex': width},
        'rows': [{'values': [cell(v) for v in r]} for r in new[start:end]], 'fields': 'userEnteredValue'}})
    if sheet is None:
        requests.extend([
            {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': len(after), 'startColumnIndex': 0, 'endColumnIndex': width},
                'cell': {'userEnteredFormat': {'textFormat': {'fontFamily': 'Arial', 'fontSize': 10}, 'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat'}},
            {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': width},
                'cell': {'userEnteredFormat': {'backgroundColor': {'red': .9, 'green': .9, 'blue': .9}, 'textFormat': {'bold': True}}}, 'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold'}},
            {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': width}, 'properties': {'pixelSize': 160}, 'fields': 'pixelSize'}},
        ])
    if sheet is None or sheet.get('basicFilter'):
        f = deepcopy(sheet.get('basicFilter', {})) if sheet else {}
        f['range'] = {**f.get('range', {}), 'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': len(after), 'startColumnIndex': 0, 'endColumnIndex': width}
        if f.get('filterSpecs'):
            f.pop('criteria', None)
        requests.append({'setBasicFilter': {'filter': f}})
    for table in (sheet or {}).get('tables', []):
        rng = table['range']
        if rng.get('startRowIndex',0) == 0 and rng.get('startColumnIndex',0) == 0 and rng['endColumnIndex'] == width:
            requests.append({'updateTable': {'table': {'tableId': table['tableId'], 'range': {**rng, 'endRowIndex': len(after)}}, 'fields': 'range'}})
        else:
            raise ValueError('未识别的表格结构，停止写入')
    return requests, (start, end, width)


class GoogleSheets:
    def __init__(self, sid=None, service=None):
        self.sid = sid or os.getenv('SPREADSHEET_ID') or SPREADSHEET_ID
        if not self.sid:
            raise ValueError('请配置 SPREADSHEET_ID Variable')
        if service is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')
            if not raw:
                raise ValueError('请配置 GOOGLE_SERVICE_ACCOUNT_JSON Secret')
            credentials = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=['https://www.googleapis.com/auth/spreadsheets'])
            service = build('sheets', 'v4', credentials=credentials, cache_discovery=False).spreadsheets()
        self.service = service

    def snapshot(self):
        meta = self.service.get(spreadsheetId=self.sid, includeGridData=False).execute()
        sheets = {s['properties']['sheetId']: s for s in meta['sheets']}
        result = {}
        for name, spec in SPECS.items():
            sid, title, headers, _ = spec
            sheet = sheets.get(sid)
            if not sheet and name != 'details':
                raise ValueError(f'找不到固定 sheetId {sid}（{title}）')
            if not sheet:
                if any(s['properties']['title'] == title for s in sheets.values()):
                    raise ValueError('Phase2 同名页 sheetId 不同，请检查配置')
                result[name] = {'sheet': None, 'values': [], 'records': []}
                continue
            p = sheet['properties']
            # Read managed columns plus one guard column; never treat a truncated header as valid.
            last = chr(ord('A') + len(headers))
            rng = "'" + p['title'].replace("'", "''") + f"'!A1:{last}{p['gridProperties']['rowCount']}"
            values = self.service.values().get(spreadsheetId=self.sid, range=rng, valueRenderOption='UNFORMATTED_VALUE').execute().get('values', [])
            result[name] = {'sheet': sheet, 'values': values, 'records': decode(values, headers)}
        return result

    def apply(self, baseline, desired):
        # Optimistic check catches edits made while the emulator was being scanned.
        current = self.snapshot()
        for name in desired:
            if current[name]['values'] != baseline[name]['values']:
                raise RuntimeError('采集期间在线表格已被修改，请重新运行：' + name)
        requests = []
        verify = {}
        for name, records in desired.items():
            spec = SPECS[name]
            after = encode(records, spec[2])
            sheet = current[name]['sheet']
            req, bounds = plan_sheet(sheet, current[name]['values'], after, spec)
            if not req:
                continue
            if sheet is not None:
                start, end, width = bounds
                p = sheet['properties']
                bounded_end = min(end, p['gridProperties']['rowCount'])
                if start < bounded_end:
                    title = p['title'].replace("'", "''")
                    rng = f"'{title}'!A{start+1}:{chr(64+width)}{bounded_end}"
                    data = self.service.get(spreadsheetId=self.sid, ranges=[rng], includeGridData=True,
                        fields='sheets.data.rowData.values(userEnteredValue,dataValidation,chipRuns,userEnteredFormat.textFormat.link)').execute()
                    for sh in data.get('sheets', []):
                        for block in sh.get('data', []):
                            for row in block.get('rowData', []):
                                for c in row.get('values', []):
                                    if c.get('chipRuns') or c.get('dataValidation') or 'formulaValue' in c.get('userEnteredValue', {}) or c.get('userEnteredFormat', {}).get('textFormat', {}).get('link'):
                                        raise ValueError('写入区出现公式/验证/智能芯片/链接，请先检查结构')
            requests.extend(req)
            verify[name] = after
        if requests:
            # No automatic replay after an uncertain network write.
            self.service.batchUpdate(spreadsheetId=self.sid, body={'requests': requests}).execute(num_retries=0)
            got = self.snapshot()
            for name, want in verify.items():
                if normalized(got[name]['values'], len(SPECS[name][2])) != normalized(want, len(SPECS[name][2])):
                    raise RuntimeError('写后核验失败，请检查表格：' + name)
        return len(verify)
