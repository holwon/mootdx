"""定位 000001 @ 20261019 的 0.109% 金额差：逐笔重建金额，看差额落在哪一段。"""

import httpx
import pandas as pd

from mootdx.quotes import Quotes

DATE = '20261019'


def fetch(client, code, date):
    frames = []
    for i in range(4):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main():
    client = Quotes.factory(market='std', timeout=20)
    try:
        raw = fetch(client, '000001', DATE)
        kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')].copy()
        kept['min'] = kept['time'].map(lambda t: int(t[:2]) * 60 + int(t[3:]))

        # 1) 名义金额（price × vol × 100）
        kept['amt_nominal'] = kept['price'] * kept['vol'] * 100

        # 2) 若按 2dp 价格去尾（价格本就 2dp，见 probe10），误差不应来自舍入
        print('=== 1. 价格精度检查 ===')
        bad = kept[kept['price'].map(lambda p: round(p, 2) != p)]
        print(f'  非 2dp 价格行数 = {len(bad)}')
        if len(bad):
            print(bad[['time', 'price', 'vol']].head(10).to_string())

        # 3) 差额集中在哪一分钟
        k = client.bars(symbol='000001', frequency=9, start=0, offset=1000).copy()
        k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
        krow = k[k['d'] == DATE].iloc[0]
        total_nominal = kept['amt_nominal'].sum()
        k_amt = float(krow['amount'])
        gap = total_nominal - k_amt
        print(f'\n=== 2. 总差额 ===')
        print(f'  名义 {total_nominal:,.0f}  日K {k_amt:,.0f}  差 {gap:+,.0f} ({gap / k_amt * 100:+.4f}%)')

        print('\n=== 3. 按分钟找异常（名义金额 vs 均价的偏离）===')
        g = kept.groupby('min').agg(
            rows=('vol', 'size'), vol=('vol', 'sum'), amt=('amt_nominal', 'sum'),
            pmin=('price', 'min'), pmax=('price', 'max'), vol_min=('vol', 'min'), vol_max=('vol', 'max'),
        )
        g['avg'] = g['amt'] / (g['vol'] * 100)
        g['spread'] = g['pmax'] - g['pmin']
        # 均价落在 [pmin,pmax] 之外 = 该分钟有行价格与成交量不匹配
        g['bad_avg'] = (g['avg'] < g['pmin'] - 1e-9) | (g['avg'] > g['pmax'] + 1e-9)
        print(f'  均价越界的分钟数 = {int(g["bad_avg"].sum())}')
        if g['bad_avg'].any():
            print(g[g['bad_avg']].head(10).to_string())

        print('\n=== 4. 逐分钟名义金额累计 vs 用日K反推的累计 ===')
        g['cum_amt'] = g['amt'].cumsum()
        g['cum_vol'] = g['vol'].cumsum()
        g['cum_avg'] = g['cum_amt'] / (g['cum_vol'] * 100)
        # 若某段价格整体偏离，累计均价会出现台阶
        jumps = g['cum_avg'].diff().abs()
        top = jumps.nlargest(8)
        print('  累计均价跳变最大的 8 分钟:')
        for m in top.index:
            hh, mm = divmod(int(m), 60)
            prev = g['cum_avg'].shift(1).loc[m]
            print(f'    {hh:02d}:{mm:02d}  cum_avg {prev:.5f} -> {g.loc[m, "cum_avg"]:.5f} '
                  f'(Δ{jumps.loc[m]:.5f})  rows={g.loc[m, "rows"]:.0f} vol={g.loc[m, "vol"]:,.0f}')

        print('\n=== 5. 09:25 竞价那笔的隐式均价 ===')
        a = kept[kept['time'] <= '09:26']
        if len(a):
            av = a['amt_nominal'].sum() / (a['vol'].sum() * 100)
            print(f'  rows={len(a)} vol={a["vol"].sum():,.0f} amt={a["amt_nominal"].sum():,.0f} 均价={av:.4f}')
            print(a[['time', 'price', 'vol', 'buyorsell']].to_string())

        print('\n=== 6. 分笔隐含均价 vs 日K隐含均价 ===')
        print(f'  分笔均价 = {total_nominal / (kept["vol"].sum() * 100):.6f}')
        print(f'  日K均价  = {k_amt / (float(krow["vol"]) * 100):.6f}')
        print(f'  差       = {total_nominal / (kept["vol"].sum() * 100) - k_amt / (float(krow["vol"]) * 100):+.6f}')

        print('\n=== 7. 该日 OHLC ===')
        print(f'  open={krow["open"]} high={krow["high"]} low={krow["low"]} close={krow["close"]}')
        print(f'  分笔价格范围 {kept["price"].min()} .. {kept["price"].max()}')
        out_of_range = kept[(kept['price'] < float(krow['low'])) | (kept['price'] > float(krow['high']))]
        print(f'  价格越出日K高低价的行数 = {len(out_of_range)}')
        if len(out_of_range):
            print(out_of_range[['time', 'price', 'vol', 'buyorsell']].head(20).to_string())
    finally:
        client.close()


if __name__ == '__main__':
    main()
