"""接缝精确检查：页边界是否重复了一行？

若 page2 末行 == page1 首行（逐字段相同），说明服务器在切页时把边界行给了两次。
这会直接影响对账（量会多出），必须在适配器里处理。
"""

import pandas as pd

from mootdx.quotes import Quotes

CASES = [('600519', '20261020'), ('000001', '20261019'), ('601398', '20261020'), ('002594', '20261020')]


def fetch(client, code, date, start, offset=2000):
    df = client.transactions(symbol=code, start=start, offset=offset, date=date)
    return None if df is None or df.empty else df


def sig(row):
    return (row['time'], round(float(row['price']), 2), int(row['vol']), int(row['buyorsell']))


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        for code, date in CASES:
            print(f'===== {code} @ {date} =====')
            pages = []
            for i in range(6):
                p = fetch(client, code, date, i * 2000)
                if p is None:
                    break
                pages.append(p)

            total = sum(len(p) for p in pages)
            print(f'  页数={len(pages)} 总行={total}')

            # 接缝检查
            for i, (a, b) in enumerate(zip(pages, pages[1:])):
                a_last = a.iloc[-1]
                b_first = b.iloc[0]
                print(f'  page{i}末 {sig(a_last)}  vs  page{i + 1}首 {sig(b_first)}  '
                      f'{"★ 完全相同" if sig(a_last) == sig(b_first) else "不同"}')

            # 反转页序后全局检查：连续相同的完整行有多少
            rev = list(reversed(pages))
            whole = pd.concat(rev, ignore_index=True)
            sigs = [sig(r) for _, r in whole.iterrows()]
            consec = sum(1 for x, y in zip(sigs, sigs[1:]) if x == y)
            print(f'  反转页序后：连续完全相同行 = {consec} / {len(sigs)}')

            # 若接缝重复，剔除重复后的对账
            dedup = []
            prev = None
            for s in sigs:
                if s != prev:
                    dedup.append(s)
                prev = s
            kept_vol = sum(s[2] for s in dedup)
            raw_vol = sum(s[2] for s in sigs)
            print(f'  剔除相邻重复：{len(sigs)} -> {len(dedup)} 行，vol {raw_vol:,} -> {kept_vol:,}（差 {raw_vol - kept_vol} 手）')

            # 与日 K 对账
            k = client.bars(symbol=code, frequency=9, start=0, offset=1000).copy()
            k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
            hit = k[k['d'] == date]
            if not hit.empty:
                kv = float(hit.iloc[0]['vol'])
                print(f'  日K vol={kv:,.0f}  |  未去重差={raw_vol - kv:+,.0f}  去重后差={kept_vol - kv:+,.0f}')
            print()
    finally:
        client.close()


if __name__ == '__main__':
    main()
