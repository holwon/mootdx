"""隔离层级：TDX 的分时数据（minutes）是否也偏？

若分时也对不上日 K → 问题在通达信的分笔/分时生成侧；
若分时对得上而 3 秒分笔对不上 → 问题在 3 秒聚合这一层。
"""

import pandas as pd

from mootdx.quotes import Quotes

CODE = '000001'
DATES = ['20261019', '20261020', '20261015', '20261016']


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        # 先看 minutes 的列形态
        probe = client.minutes(symbol=CODE, date=DATES[0])
        print('=== minutes() 返回形态 ===')
        if probe is None or probe.empty:
            print('  空')
        else:
            print(f'  columns = {list(probe.columns)}')
            print(f'  dtypes  = {dict(probe.dtypes.astype(str))}')
            print(probe.head(5).to_string())
            print('  ...')
            print(probe.tail(3).to_string())

        print('\n=== 与日 K 对账 ===')
        for date in DATES:
            mm = client.minutes(symbol=CODE, date=date)
            k = client.bars(symbol=CODE, frequency=9, start=0, offset=200).copy()
            k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
            hit = k[k['d'] == date]
            if mm is None or mm.empty or hit.empty:
                print(f'  {date}: 缺数据')
                continue
            krow = hit.iloc[0]
            k_amt = float(krow['amount'])
            k_vol = float(krow['vol'])

            print(f'\n  --- {date} ---')
            print(f'    minutes 列: {list(mm.columns)}')

            # 尝试各种可能的量/额列
            vol_col = next((c for c in ('vol', 'volume') if c in mm.columns), None)
            amt_col = next((c for c in ('amount', 'turnover') if c in mm.columns), None)
            px_col = next((c for c in ('price', 'close') if c in mm.columns), None)

            if vol_col:
                v = float(mm[vol_col].sum())
                print(f'    Σ{vol_col} = {v:,.0f}   日K vol = {k_vol:,.0f}   差 {v - k_vol:+,.0f}')
            if amt_col:
                a = float(mm[amt_col].sum())
                print(f'    Σ{amt_col} = {a:,.0f}   日K amount = {k_amt:,.0f}   差 {a - k_amt:+,.0f} ({(a - k_amt) / k_amt * 100:+.4f}%)')
            if vol_col and px_col:
                a2 = float((mm[px_col] * mm[vol_col] * 100).sum())
                print(f'    Σ(价×量×100) = {a2:,.0f}   差 {a2 - k_amt:+,.0f} ({(a2 - k_amt) / k_amt * 100:+.4f}%)')
    finally:
        client.close()


if __name__ == '__main__':
    main()
