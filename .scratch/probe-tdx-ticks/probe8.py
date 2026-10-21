"""探针 8：二分定位 start 回绕阈值，并验证是否为固定值。"""

from mootdx.quotes import Quotes


def rows_at(client, code, date, start):
    df = client.transactions(symbol=code, start=start, offset=2000, date=date)
    return 0 if df is None else len(df), df


def find_wrap(client, code, date, lo, hi):
    """返回 (最后一个返回空/正常的上界, 第一个发生回绕的 start)。"""
    def is_wrap(start):
        n, df = rows_at(client, code, date, start)
        if n == 0:
            return False
        _, first = rows_at(client, code, date, 0)
        return (df['time'].iloc[-1] == first['time'].iloc[-1]
                and df['time'].iloc[0] == first['time'].iloc[0]
                and len(df) == len(first))

    # 保证 hi 一定回绕
    while not is_wrap(hi):
        hi *= 2
        if hi > 65535:
            return None, None
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if is_wrap(mid):
            hi = mid
        else:
            lo = mid
    return lo, hi


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        cases = [
            ('002594', '20261020'),
            ('600519', '20261020'),
            ('601398', '20261020'),
            ('300750', '20261019'),
        ]
        for code, date in cases:
            n0, _ = rows_at(client, code, date, 0)
            n_tail, _ = rows_at(client, code, date, 4000)
            total, _ = 0, None
            frames = []
            for i in range(40):
                n, df = rows_at(client, code, date, i * 2000)
                if n == 0:
                    break
                total += n
            print(f'--- {code}@{date}: 总量={total} 行 ---')
            lo, hi = find_wrap(client, code, date, 6000, 65536)
            if lo is None:
                print('  未发现回绕')
            else:
                print(f'  最后一个不回绕的 start = {lo}')
                print(f'  第一个回绕的 start     = {hi}')
                print(f'  ★ 回绕阈值 = {hi}（0x{hi:04X}）')

                # 阈值附近细看
                for s in (hi - 2, hi - 1, hi, hi + 1):
                    n, df = rows_at(client, code, date, s)
                    rng = f'{df["time"].iloc[-1]}..{df["time"].iloc[0]}' if n else '(空)'
                    print(f'    start={s:>6} rows={n:>5} {rng}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
