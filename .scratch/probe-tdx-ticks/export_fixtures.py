"""导出真实夹具，供 prototype 内嵌使用（不是生产代码）。

输出：.scratch/probe-tdx-ticks/fixtures.json
每个标的：分页原始数据（未做任何清洗）+ 当日日 K 的 vol/amount。
"""

import json
import sys
from pathlib import Path

from mootdx.quotes import Quotes

CASES = [
    ('600519', '20261020', '贵州茅台 · 沪主板 · 低换手（量差 +1 手、额差 +0.001%）'),
    ('000001', '20261019', '平安银行 · 深主板 · **额差 0.109% 的那个样本**'),
    ('601398', '20261020', '工商银行 · 沪主板 · 最大行数样本'),
]

PAGE = 2000
CAP = 6


def main():
    client = Quotes.factory(market='std', timeout=20)
    out = {'server': list(client.server), 'cases': []}
    try:
        for code, date, note in CASES:
            pages = []
            for i in range(CAP):
                df = client.transactions(symbol=code, start=i * PAGE, offset=PAGE, date=date)
                if df is None or df.empty:
                    break
                pages.append([
                    [str(r['time']), round(float(r['price']), 2), int(r['vol']), int(r['buyorsell'])]
                    for _, r in df.iterrows()
                ])

            k = client.bars(symbol=code, frequency=9, start=0, offset=1000).copy()
            k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
            hit = k[k['d'] == date]
            if hit.empty:
                print(f'{code}: 无日K，跳过', file=sys.stderr)
                continue
            row = hit.iloc[0]

            out['cases'].append({
                'code': code,
                'date': date,
                'note': note,
                'pageSize': PAGE,
                'pages': pages,
                'daily': {'vol': float(row['vol']), 'amount': float(row['amount']),
                          'open': float(row['open']), 'high': float(row['high']),
                          'low': float(row['low']), 'close': float(row['close'])},
            })
            print(f'{code}@{date}: {len(pages)} 页 / {sum(len(p) for p in pages)} 行')
    finally:
        client.close()

    path = Path(__file__).parent / 'fixtures.json'
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    size = path.stat().st_size
    print(f'\n写出 {path}  ({size / 1024:.0f} KB)')


if __name__ == '__main__':
    main()
