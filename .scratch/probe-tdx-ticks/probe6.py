"""探针 6：钉死 start 上界异常 + 历史留存深度。

背景：probe5 发现 start=60000 反而返回 2000 行（total 只有 4709）。
若分页循环不在此处停下，会陷入死循环或重复取数。
"""

import sys

from mootdx.quotes import Quotes


def total_of(client, code, date, page=2000, cap=40):
    n = 0
    for i in range(cap):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        m = 0 if df is None else len(df)
        if m == 0:
            break
        n += m
    return n


def scan_start(client, code, date, starts):
    print(f'  {"start":>7}  {"rows":>5}  首行 -> 末行')
    for start in starts:
        df = client.transactions(symbol=code, start=start, offset=2000, date=date)
        n = 0 if df is None else len(df)
        if n:
            print(f'  {start:>7}  {n:>5}  {df["time"].iloc[0]} -> {df["time"].iloc[-1]}')
        else:
            print(f'  {start:>7}  {n:>5}  (空)')


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else '20261020'
    client = Quotes.factory(market='std', timeout=15)
    print(f'server={client.server}\n')
    try:
        code = '002594'
        total = total_of(client, code, date)
        print(f'=== A. {code}@{date} 总量 = {total} 行 ===')
        print('  在 [total-5, total+5] 附近：')
        scan_start(client, code, date, list(range(total - 3, total + 4)))
        print('  在更大 start 值上（找翻转点）：')
        scan_start(client, code, date, [10000, 20000, 30000, 40000, 50000, 55000, 60000, 65000, 65535])

        print('\n=== B. 另一只票（600519）复核翻转点 ===')
        t2 = total_of(client, '600519', date)
        print(f'  总量 = {t2}')
        scan_start(client, '600519', date, [10000, 20000, 30000, 40000, 50000, 60000, 65535])

        print('\n=== C. 历史留存深度（同一只票，往前找）===')
        probe_dates = ['20261019', '20261016', '20261009', '20260930',
                       '20260901', '20260803', '20260701', '20260601',
                       '20260401', '20260105', '20251020', '20250106']
        for d in probe_dates:
            df = client.transactions(symbol='600519', start=0, offset=2000, date=d)
            n = 0 if df is None else len(df)
            rng = ''
            if n:
                rng = f'{df["time"].iloc[0]} .. {df["time"].iloc[-1]}'
            print(f'  {d}: rows={n:>5}  {rng}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
