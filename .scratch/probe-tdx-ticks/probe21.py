"""找 10-19 的异常行：是否存在价格远离邻域的孤立行？

若 tick 里混入了价格错误的行，它们会表现为「与前后价格差距很大的孤点」。
"""

import pandas as pd

from mootdx.quotes import Quotes

CODE = '000001'
DATE = '20261019'


def main():
    client = Quotes.factory(market='std', timeout=20)
    try:
        frames = []
        for i in range(5):
            df = client.transactions(symbol=CODE, start=i * 2000, offset=2000, date=DATE)
            if df is None or df.empty:
                break
            frames.append(df)
        raw = pd.concat(frames, ignore_index=True).iloc[::-1].reset_index(drop=True)
        d = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')].copy()
        d['px'] = (d['price'] * 100).round().astype('int64')
        d['min'] = d['time'].map(lambda t: int(t[:2]) * 60 + int(t[3:]))

        print(f'行数={len(d)}  价格范围 {d["px"].min() / 100} .. {d["px"].max() / 100}')

        print('\n=== 相邻价格跳变最大的 15 行（按量加权）===')
        d['dp'] = d['px'].diff().abs()
        d['impact'] = d['dp'] * d['vol']
        top = d.nlargest(15, 'impact')[['time', 'px', 'vol', 'buyorsell', 'dp', 'impact']]
        top = top.copy()
        top['px'] = top['px'] / 100
        top['dp'] = top['dp'] / 100
        print(top.to_string(index=False))

        print('\n=== 每分钟 VWAP vs 末价 vs 首价（前 15 分钟）===')
        g = d.groupby('min').agg(
            n=('px', 'size'), vol=('vol', 'sum'),
            first=('px', 'first'), last=('px', 'last'),
            pmin=('px', 'min'), pmax=('px', 'max'),
        )
        g['vwap'] = d.groupby('min').apply(
            lambda x: (x['px'] * x['vol']).sum() / x['vol'].sum(), include_groups=False)
        g['last_minus_vwap'] = (g['last'] - g['vwap']) / 100
        g['spread'] = (g['pmax'] - g['pmin']) / 100
        g['impact'] = ((g['last'] - g['vwap']) * g['vol']) / 100
        show = g.head(15).copy()
        show['first'] /= 100; show['last'] /= 100; show['vwap'] /= 100
        show['pmin'] /= 100; show['pmax'] /= 100
        print(show[['n', 'vol', 'pmin', 'pmax', 'first', 'last', 'vwap', 'spread', 'last_minus_vwap']].to_string())

        print('\n=== 偏差最大的 10 分钟（末价 − VWAP）× 量 ===')
        topp = g.nlargest(10, 'impact')[['n', 'vol', 'pmin', 'pmax', 'last', 'vwap', 'impact']].copy()
        for c in ('pmin', 'pmax', 'last', 'vwap'):
            topp[c] = topp[c] / 100
        print(topp.to_string())

        print('\n=== 若改用每分钟 VWAP，总金额偏差 ===')
        total_vwap = int((g['vwap'].round() * g['vol']).sum())
        k = client.bars(symbol=CODE, frequency=9, start=0, offset=200).copy()
        k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
        krow = k[k['d'] == DATE].iloc[0]
        k_amt = float(krow['amount'])
        print(f'  Σ(分钟VWAP×量) = {total_vwap:,.0f}')
        print(f'  日K amount     = {k_amt:,.0f}')
        print(f'  差              = {total_vwap - k_amt:+,.0f} ({(total_vwap - k_amt) / k_amt * 100:+.4f}%)')
        print('  （注：分钟 VWAP 是用同一批 tick 算的，所以与逐行求和等价——本行只是自证）')
    finally:
        client.close()


if __name__ == '__main__':
    main()
