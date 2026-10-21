"""终极检验：偏差是否来自「价格标签取的是窗口内最后一笔」？

若成立，则：偏差符号 = 该窗口价格漂移方向，偏差大小 ∝ 漂移幅度。
用全天数据构造检验：把每个 3 秒桶的「末价」替换成「该桶所有价的中位数」
（中位数更接近量加权均价），看偏差是否收窄。
"""

import statistics

import pandas as pd

from mootdx.quotes import Quotes

CODE = '000001'
DATES = ['20261019', '20261020', '20261015', '20261016', '20261013', '20261014']


def fetch(client, code, date, pages=5):
    frames = []
    for i in range(pages):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    if not frames:
        return None
    raw = pd.concat(frames, ignore_index=True).iloc[::-1].reset_index(drop=True)
    return raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')].copy()


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    hdr = (f'{"日期":<10}{"方向":>6}{"振幅%":>7}'
           f'{"Δ(末价)":>11}{"Δ(首价)":>11}{"Δ(中位)":>11}{"Δ(高低中)":>11}')
    print(hdr)
    print('-' * len(hdr))
    try:
        for date in DATES:
            df = fetch(client, CODE, date)
            if df is None:
                continue
            k = client.bars(symbol=CODE, frequency=9, start=0, offset=200).copy()
            k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
            hit = k[k['d'] == date]
            if hit.empty:
                continue
            krow = hit.iloc[0]
            k_amt = float(krow['amount'])
            k_high, k_low = float(krow['high']), float(krow['low'])
            k_open, k_close = float(krow['open']), float(krow['close'])
            amp = (k_high - k_low) / k_low * 100
            direction = '涨' if k_close > k_open else ('跌' if k_close < k_open else '平')

            df['px'] = (df['price'] * 100).round().astype('int64')
            df['min'] = df['time'].map(lambda t: int(t[:2]) * 60 + int(t[3:]))

            def amt(price_series, vol_series):
                return int((price_series * vol_series).sum())

            # A. 逐行末价（也就是现状）
            a = amt(df['px'], df['vol'])

            # B. 分钟首价
            g_first = df.groupby('min').agg(px=('px', 'first'), vol=('vol', 'sum'))
            b = amt(g_first['px'], g_first['vol'])

            # C. 分钟中位价
            g_med = df.groupby('min').agg(px=('px', 'median'), vol=('vol', 'sum'))
            c = amt(g_med['px'].round().astype('int64'), g_med['vol'])

            # D. 分钟 (high+low)/2
            g_hl = df.groupby('min').agg(pxmin=('px', 'min'), pxmax=('px', 'max'), vol=('vol', 'sum'))
            d = amt(((g_hl['pxmin'] + g_hl['pxmax']) // 2), g_hl['vol'])

            def pct(x):
                return (x - k_amt) / k_amt * 100

            print(f'{date:<10}{direction:>6}{amp:>7.2f}'
                  f'{pct(a):>+10.3f}%{pct(b):>+10.3f}%{pct(c):>+10.3f}%{pct(d):>+10.3f}%')
    finally:
        client.close()


if __name__ == '__main__':
    main()
