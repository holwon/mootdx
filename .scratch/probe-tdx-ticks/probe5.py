"""探针 5：极端流动性 + 边界探针。

1. 找全市场最高换手的票，看行数上限
2. 验证 page 边界（正好整除 / 最后一页满页）
3. 验证 start 超出总量时的返回
4. 验证「连续多页 start 跳页」不会静默丢数据
"""

import sys

import pandas as pd

from mootdx.quotes import Quotes

CANDIDATES = [
    # (code, label) — 高换手 / 高成交笔数候选
    ('300059', '东方财富 创业板 高活跃'),
    ('002230', '科大讯飞 深主板 高活跃'),
    ('601127', '赛力斯 沪主板 高活跃'),
    ('600030', '中信证券 沪主板'),
    ('000725', '京东方A 深主板 超低价高量'),
    ('002415', '海康威视 深主板'),
    ('688111', '金山办公 科创板'),
    ('300308', '中际旭创 创业板 高换手'),
]


def page_all(client, code, date, page=2000, max_pages=60):
    frames = []
    for i in range(max_pages):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def daily_bar(client, code, date):
    bars = client.bars(symbol=code, frequency=9, start=0, offset=60)
    if bars is None or bars.empty:
        return None
    bars = bars.copy()
    bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '')
    hit = bars[bars['d'] == str(date)]
    return None if hit.empty else hit.iloc[0]


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else '20261020'
    client = Quotes.factory(market='std', timeout=15)
    print(f'server={client.server}  date={date}\n')
    try:
        print('=== A. 高流动性标的完整性 ===')
        hdr = f'{"代码":<8}{"说明":<24}{"行数":>7}{"量差%":>9}{"金额差%":>9}{"判读":>7}'
        print(hdr)
        print('-' * len(hdr))
        for code, note in CANDIDATES:
            try:
                bar = daily_bar(client, code, date)
                if bar is None:
                    print(f'{code:<8}{note:<24}{"无日K":>7}')
                    continue
                df = page_all(client, code, date)
                if df is None or df.empty:
                    print(f'{code:<8}{note:<24}{"无分笔":>7}')
                    continue
                clean = df[(df['vol'] > 0) & (df['time'] <= '15:00')]
                k_vol, k_amt = float(bar['vol']), float(bar['amount'])
                dvp = (clean['vol'].sum() - k_vol) / k_vol * 100 if k_vol else 0
                dap = ((clean['price'] * clean['vol'] * 100).sum() - k_amt) / k_amt * 100 if k_amt else 0
                verdict = 'OK' if abs(dvp) < 0.5 and abs(dap) < 0.5 else 'DIFF'
                print(f'{code:<8}{note:<24}{len(df):>7}{dvp:>+8.2f}%{dap:>+8.3f}%{verdict:>7}')
            except Exception as exc:  # noqa: BLE001
                print(f'{code:<8}{note:<24}  [raise] {type(exc).__name__}: {exc}')

        print('\n=== B. 边界探针（以 600519 为例）===')
        code = '002594'
        full = page_all(client, code, date)
        total = 0 if full is None else len(full)
        print(f'  基准总量 = {total} 行')

        print('\n  B1. start 超出总量：')
        for start in (total - 10, total, total + 100, total + 2000, 60000):
            df = client.transactions(symbol=code, start=start, offset=2000, date=date)
            n = 0 if df is None else len(df)
            print(f'    start={start:>6} -> rows={n}')

        print('\n  B2. 请求 count 超过 2000（协议上限）：')
        for offset in (2000, 2001, 3000, 5000):
            df = client.transactions(symbol=code, start=0, offset=offset, date=date)
            n = 0 if df is None else len(df)
            print(f'    offset={offset:>5} -> rows={n}')

        print('\n  B3. count=1 逐条抽样一致性（取 0/1000/2000/3000）：')
        for start in (0, 1000, 2000, 3000):
            df = client.transactions(symbol=code, start=start, offset=1, date=date)
            if df is not None and len(df):
                r = df.iloc[0]
                print(f'    start={start:>5} -> {r["time"]} price={r["price"]} vol={r["vol"]} '
                      f'buyorsell={r["buyorsell"]}')

        print('\n  B4. 同一页重复请求是否稳定（start=2000 取三次）：')
        sigs = []
        for i in range(3):
            df = client.transactions(symbol=code, start=2000, offset=2000, date=date)
            sig = None if df is None or df.empty else (
                len(df), df['time'].iloc[0], df['time'].iloc[-1],
                float(df['price'].iloc[0]), float(df['price'].iloc[-1]), int(df['vol'].sum()))
            sigs.append(sig)
            print(f'    第{i + 1}次: {sig}')
        print(f'    三次一致 = {len(set(sigs)) == 1}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
