"""终极定位：逐页均价 + 整数分币精确金额，判断差额是「均匀价格偏移」还是「某页错位」。

若某页整体价格偏移，该页均价相对相邻时间段会跳档；
若全量均匀偏移，则各页偏移量相同 —— 说明是基准（日K amount）而非分笔的问题。
"""

import pandas as pd

from mootdx.quotes import Quotes

DATE = '20261019'
CODE = '000001'


def fetch_paged(client, code, date, pages=4):
    out = []
    for i in range(pages):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        df = df.copy()
        df['_page'] = i
        out.append(df)
    return out


def main():
    client = Quotes.factory(market='std', timeout=20)
    try:
        pages = fetch_paged(client, CODE, DATE)
        k = client.bars(symbol=CODE, frequency=9, start=0, offset=1000).copy()
        k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
        krow = k[k['d'] == DATE].iloc[0]
        k_amt = float(krow['amount'])
        k_vol = float(krow['vol'])

        print(f'日K: vol={k_vol:,.0f} amount={k_amt:,.0f}')

        print('\n=== 1. 逐页（含整数分币精确金额）===')
        total_cents = 0
        total_vol = 0
        for df in pages:
            d = df[(df['vol'] > 0) & (df['time'] <= '15:00')].copy()
            if d.empty:
                continue
            cents = (d['price'] * 100).round().astype('int64')
            amt_int = int((cents * d['vol']).sum())  # price_cents × vol(手) = 元
            v = int(d['vol'].sum())
            total_cents += amt_int
            total_vol += v
            t0, t1 = d['time'].iloc[-1], d['time'].iloc[0]
            print(f'  page {df["_page"].iloc[0]}: rows={len(d):5d} {t0}..{t1} '
                  f'vol={v:>10,} amt(整数)={amt_int:>15,} 均价={amt_int / (v * 100):.6f}')

        print(f'\n  合计 vol={total_vol:,} amt={total_cents:,}')
        print(f'  与日K: vol差={total_vol - k_vol:+,.0f}  amt差={total_cents - k_amt:+,.0f} '
              f'({(total_cents - k_amt) / k_amt * 100:+.4f}%)')

        print('\n=== 2. 均匀偏移假设检验 ===')
        implied = total_cents / (total_vol * 100)
        k_implied = k_amt / (k_vol * 100)
        shift = implied - k_implied
        print(f'  分笔均价 {implied:.6f}  日K均价 {k_implied:.6f}  偏移 {shift:+.6f} 元/股')
        print(f'  偏移若为「每笔价格 +1 分」→ 总额外 = {total_vol * 100 * 0.01:,.0f} 元')
        print(f'  实际额外                                   = {total_cents - k_amt:,.0f} 元')
        print(f'  比值 = {(total_cents - k_amt) / (total_vol * 100 * 0.01):.4f}')

        print('\n=== 3. 若日K amount 反算「应有总量」 ===')
        print(f'  用日K均价 × 分笔量 = {k_implied * total_vol * 100:,.0f}')
        print(f'  用分笔均价 × 日K量 = {implied * k_vol * 100:,.0f}')

        print('\n=== 4. 分页边界价格连续性 ===')
        for prev, cur in zip(pages, pages[1:]):
            pe = prev.iloc[-1]
            cs = cur.iloc[0]
            print(f'  page{prev["_page"].iloc[0]}末 {pe["time"]} {pe["price"]}  →  '
                  f'page{cur["_page"].iloc[0]}首 {cs["time"]} {cs["price"]}   Δ={cs["price"] - pe["price"]:+.2f}')

        print('\n=== 5. 同股相邻日对比（判断是否本日偶发）===')
        for d in ['20261013', '20261014', '20261015', '20261016', '20261019', '20261020']:
            chunk = fetch_paged(client, CODE, d)
            kk = k[k['d'] == d]
            if not chunk or kk.empty:
                continue
            kk = kk.iloc[0]
            rows = pd.concat([x[(x['vol'] > 0) & (x['time'] <= '15:00')] for x in chunk])
            if rows.empty:
                continue
            cents = (rows['price'] * 100).round().astype('int64')
            a = int((cents * rows['vol']).sum())
            v = int(rows['vol'].sum())
            kb = float(kk['amount'])
            print(f'  {d}: vol={v:>10,} amt={a:>15,} | 日K vol={float(kk["vol"]):>10,.0f} amt={kb:>15,.0f} '
                  f'| 差={a - kb:>+12,.0f} ({(a - kb) / kb * 100:+.4f}%)')
    finally:
        client.close()


if __name__ == '__main__':
    main()
