"""探针 7：历史日分笔是否可取全 + 回绕边界精确定位。

两个问题：
A. 历史日（非当日）逐页取，Σv 是否等于当日日 K vol？
B. start 的回绕点到底在哪（决定分页循环的守卫怎么写）。
"""

import sys

import pandas as pd

from mootdx.quotes import Quotes

DATES = ['20261020', '20261019', '20261016', '20261009',
         '20260901', '20260701', '20260401', '20260105', '20250106']


def page_all(client, code, date, page=2000, cap=60):
    """遇空即停（这是唯一安全的终止条件）。"""
    frames = []
    pages = 0
    for i in range(cap):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        frames.append(df)
        pages += 1
    return (pd.concat(frames, ignore_index=True) if frames else None), pages


def daily_bar(client, code, date):
    bars = client.bars(symbol=code, frequency=9, start=0, offset=1000)
    if bars is None or bars.empty:
        return None
    bars = bars.copy()
    bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '')
    hit = bars[bars['d'] == str(date)]
    return None if hit.empty else hit.iloc[0]


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        code = '600519'
        print(f'=== A. {code} 历史日完整性 ===')
        hdr = (f'{"日期":<10}{"页数":>4}{"行数":>7}{"首行":>7}{"末行":>7}'
               f'{"分笔量":>11}{"日K量":>11}{"量差%":>9}{"金额差%":>9}{"判读":>7}')
        print(hdr)
        print('-' * len(hdr))
        for d in DATES:
            bar = daily_bar(client, code, d)
            df, pages = page_all(client, code, d)
            if df is None or df.empty:
                print(f'{d:<10}{pages:>4}{"无分笔":>7}')
                continue
            if bar is None:
                print(f'{d:<10}{pages:>4}{len(df):>7}{"无日K":>7}')
                continue
            clean = df[(df['vol'] > 0) & (df['time'] <= '15:00')]
            k_vol, k_amt = float(bar['vol']), float(bar['amount'])
            t_vol = clean['vol'].sum()
            t_amt = (clean['price'] * clean['vol'] * 100).sum()
            dvp = (t_vol - k_vol) / k_vol * 100 if k_vol else 0
            dap = (t_amt - k_amt) / k_amt * 100 if k_amt else 0
            verdict = 'OK' if abs(dvp) < 0.5 and abs(dap) < 0.5 else 'DIFF'
            print(f'{d:<10}{pages:>4}{len(df):>7}{df["time"].iloc[-1]:>7}{df["time"].iloc[0]:>7}'
                  f'{t_vol:>11,.0f}{k_vol:>11,.0f}{dvp:>+8.2f}%{dap:>+8.3f}%{verdict:>7}')

        print('\n=== B. start 回绕边界精确定位（002594 @ 20261020）===')
        base, _ = page_all(client, '002594', '20261020')
        total = len(base)
        print(f'  该日总量 = {total} 行')
        ref0 = (base['time'].iloc[-1], base['time'].iloc[0], int(base['vol'].sum()))
        print(f'  page0 (start=0) 指纹 = 首{ref0[0]} 末{ref0[1]} Σv={ref0[2]}')

        print('\n  粗扫（每 2000 一档，找哪些 start 返回非空）：')
        hits = []
        for start in range(0, 65536, 2000):
            df = client.transactions(symbol='002594', start=start, offset=2000, date='20261020')
            n = 0 if df is None else len(df)
            if n:
                is_page0 = (df['time'].iloc[-1] == ref0[0] and df['time'].iloc[0] == ref0[1])
                hits.append((start, n, df['time'].iloc[-1], df['time'].iloc[0], is_page0))
        for start, n, t0, t1, p0 in hits:
            print(f'    start={start:>6} rows={n:>5} {t0} -> {t1} {"<== 等同 page0（回绕）" if p0 else ""}')

        print('\n  细扫回绕起点（在最后一个合法 start 与第一个回绕 start 之间）：')
        last_ok = max(s for s, *_ in hits if not _[3]) if hits else 0
        wrap_start = min((s for s, *_, p0 in hits if p0), default=None)
        print(f'    合法区间末 = {last_ok}   回绕起点 = {wrap_start}')
        if wrap_start:
            lo = max(0, wrap_start - 4000)
            for start in range(lo, wrap_start + 1000, 500):
                df = client.transactions(symbol='002594', start=start, offset=2000, date='20261020')
                n = 0 if df is None else len(df)
                tag = ''
                if n and df['time'].iloc[-1] == ref0[0]:
                    tag = ' 回绕'
                print(f'    start={start:>6} rows={n:>5} {tag}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
