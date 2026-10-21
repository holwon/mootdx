"""钉死分页拼装规则：页内顺序、页序、边界重复。

此前的 ADR 写「合并后整体反转为升序」，但那与实测矛盾：
page0 是 13:00..15:28（升序），page1 是 09:32..13:00（升序），page2 是 09:15..09:32（升序）。
整体 reverse 会得到「页内降序」的乱序序列。本探针验证正确的拼装方式。
"""

from collections import Counter

import pandas as pd

from mootdx.quotes import Quotes

CASES = [
    ('600519', '20261020'),
    ('000001', '20261019'),
    ('601398', '20261020'),
]


def to_min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def fetch_pages(client, code, date, page=2000, cap=6):
    out = []
    for i in range(cap):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        if df is None or df.empty:
            break
        df = df.copy()
        df['_page'] = i
        df['_row'] = range(len(df))
        df['_min'] = df['time'].map(to_min)
        out.append(df)
    return out


def monotonic(seq):
    up = sum(1 for a, b in zip(seq, seq[1:]) if a <= b)
    eq = sum(1 for a, b in zip(seq, seq[1:]) if a == b)
    return up, len(seq) - 1, eq


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        for code, date in CASES:
            pages = fetch_pages(client, code, date)
            total = sum(len(p) for p in pages)
            print(f'===== {code} @ {date}（{len(pages)} 页 / {total} 行）=====')

            print('  页内顺序：')
            for p in pages:
                vals = p['_min'].tolist()
                up, tot, eq = monotonic(vals)
                order = '升序' if up == tot else ('降序' if vals[0] >= vals[-1] else '乱序')
                print(f'    page {p["_page"].iloc[0]}: {len(p):5d} 行  '
                      f'{p["time"].iloc[0]} .. {p["time"].iloc[-1]}  [{order}]')

            print('  页序（各页时间区间）：')
            mids = [(p['_min'].iloc[0], p['_min'].iloc[-1]) for p in pages]
            for i, (a, b) in enumerate(mids):
                print(f'    page {i}: {a // 60:02d}:{a % 60:02d} .. {b // 60:02d}:{b % 60:02d}')

            print('\n  三种拼装策略的单调性：')
            # A. 直接拼接
            a = pd.concat(pages, ignore_index=True)['_min'].tolist()
            # B. 仅反转页序
            b = pd.concat(list(reversed(pages)), ignore_index=True)['_min'].tolist()
            # C. 直接拼接后整体反转
            c = pd.concat(pages, ignore_index=True)['_min'].tolist()[::-1]
            for name, seq in [('A 直接拼接', a), ('B 反转页序、页内不动', b), ('C 拼接后整体反转', c)]:
                up, tot, eq = monotonic(seq)
                strict = sum(1 for x, y in zip(seq, seq[1:]) if x < y)
                print(f'    {name:24s} 非减={up}/{tot} 相等对={eq}/{tot} 严格增={strict}/{tot}  '
                      f'{"✓ 单调" if up == tot else "✗ 乱序"}')

            print('\n  边界重复检查（B 策略下页与页的接缝）：')
            rev = list(reversed(pages))
            for prev, cur in zip(rev, rev[1:]):
                pv = prev['_min'].tolist()
                cv = cur['_min'].tolist()
                print(f'    page{prev["_page"].iloc[0]}末 {prev["time"].iloc[-1]} '
                      f'→ page{cur["_page"].iloc[0]}首 {cur["time"].iloc[0]}')
                # 是否有连续相同时间（无法判断是否真重复，因为分钟粒度）
                print(f'      接缝处时间相等 = {pv[-1] == cv[0]}')

            # 逐交易日的「连续重复块」：同一分钟出现多次是正常的，但整块重复才是问题
            print('\n  整块重复检测（相邻 50 行窗口是否完全相同）：')
            whole = pd.concat(rev, ignore_index=True)
            sig = list(zip(whole['_min'], (whole['price'] * 100).round().astype(int), whole['vol'], whole['buyorsell']))
            n = len(sig)
            dup_windows = 0
            seen = set()
            win = 50
            for i in range(0, n - win + 1, win):
                key = tuple(sig[i:i + win])
                if key in seen:
                    dup_windows += 1
                seen.add(key)
            print(f'    非重叠 {win} 行窗口数 = {len(seen) + dup_windows}，其中重复 = {dup_windows}')

            print('\n  逐笔唯一性（同一时间戳内相邻行是否完全相同）：')
            same = 0
            for x, y in zip(sig, sig[1:]):
                if x == y:
                    same += 1
            print(f'    完全相同的相邻行 = {same} / {n}')

            print(f'\n  B 策略下：首 {whole["time"].iloc[0]}  末 {whole["time"].iloc[-1]}  '
                  f'分钟数={whole["_min"].nunique()}')
            print()
    finally:
        client.close()


if __name__ == '__main__':
    main()
