"""探针 2：定位历史分笔的时间戳异常与 buyorsell 异常值。"""

import sys
from collections import Counter

import pandas as pd

from mootdx.quotes import Quotes


def page_all(client, code, date, page=2000, max_pages=20):
    frames = []
    for i in range(max_pages):
        start = i * page
        df = client.transactions(symbol=code, start=start, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        df = df.copy()
        df['_page'] = i
        df['_pos'] = range(len(df))
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def to_minutes(t):
    hh, mm = t.split(':')
    return int(hh) * 60 + int(mm)


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else '600519'
    date = sys.argv[2] if len(sys.argv) > 2 else '20261020'
    client = Quotes.factory(market='std', timeout=15)
    print(f'server={client.server}')
    try:
        df = page_all(client, code, date)
        if df is None or df.empty:
            print('[empty]')
            return

        df['_min'] = df['time'].map(to_minutes)

        print(f'\n--- 各页时间序列单调性 ---')
        for p, grp in df.groupby('_page'):
            seq = grp['_min'].tolist()
            up = sum(1 for a, b in zip(seq, seq[1:]) if a <= b)
            down = sum(1 for a, b in zip(seq, seq[1:]) if a >= b)
            tot = len(seq) - 1
            print(f'page {p}: n={len(grp):5d} 非减={up}/{tot} 非增={down}/{tot} '
                  f'min={grp["time"].iloc[seq.index(min(seq))]} max={grp["time"].iloc[seq.index(max(seq))]}')

        print(f'\n--- 非交易时段的时间戳（<09:15 或 >15:00） ---')
        bad = df[(df['_min'] < 9 * 60 + 15) | (df['_min'] > 15 * 60)]
        print(f'count={len(bad)}')
        if len(bad):
            print(bad[['_page', '_pos', 'time', 'price', 'vol', 'buyorsell']].head(20).to_string())

        print(f'\n--- buyorsell 异常值（不在 0/1/2） ---')
        weird = df[~df['buyorsell'].isin([0, 1, 2])]
        print(f'count={len(weird)}  value_counts={dict(Counter(weird["buyorsell"]))}')
        if len(weird):
            print(weird[['_page', '_pos', 'time', 'price', 'vol', 'buyorsell']].head(20).to_string())

        print(f'\n--- page 0 前 30 行 ---')
        print(df[df['_page'] == 0][['time', 'price', 'vol', 'buyorsell']].head(30).to_string())

        print(f'\n--- page 0 时间跳变的位置 ---')
        p0 = df[df['_page'] == 0].reset_index(drop=True)
        prev = None
        shown = 0
        for i, row in p0.iterrows():
            cur = row['_min']
            if prev is not None and abs(cur - prev) > 1:
                print(f'  行 {i}: {p0.loc[i - 1, "time"]} -> {row["time"]}  '
                      f'(price {p0.loc[i - 1, "price"]}->{row["price"]})')
                shown += 1
                if shown >= 15:
                    print('  ...')
                    break
            prev = cur
    finally:
        client.close()


if __name__ == '__main__':
    main()
