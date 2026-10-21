"""原样打印真实分笔：不做任何清洗、转换。

目的：让人看见上游到底给了什么。
"""

import pandas as pd

from mootdx.quotes import Quotes

CODE = '600519'
DATE = '20261020'
BAR = '=' * 78
THIN = '-' * 78


def main():
    client = Quotes.factory(market='std', timeout=20)
    try:
        frames = []
        for i in range(6):
            df = client.transactions(symbol=CODE, start=i * 2000, offset=2000, date=DATE)
            if df is None or df.empty:
                break
            frames.append(df)

        print(f'标的 {CODE}  日期 {DATE}  共 {len(frames)} 页  '
              f'每页行数 {[len(f) for f in frames]}')
        print(BAR)

        p0 = frames[0]
        print(f'\n【第 0 页原样】start=0, count=2000 -> 实际 {len(p0)} 行')
        print(f'列名: {list(p0.columns)}')
        print(f'dtype: {dict(p0.dtypes.astype(str))}')
        print(THIN)
        print(f'{"#":>4}  {"time":<7}{"price":>10}{"vol":>7}{"buyorsell":>11}{"volume":>8}')
        print(THIN)
        for i in range(25):
            r = p0.iloc[i]
            print(f'{i:>4}  {r["time"]:<7}{r["price"]:>10.2f}{r["vol"]:>7}'
                  f'{r["buyorsell"]:>11}{r["volume"]:>8}')
        print('  ...(还有 1975 行)')

        # 反转页序拼成时间序（只为好看，不是数据本身的转换）
        seq = pd.concat(list(reversed(frames)), ignore_index=True)

        counts = seq.groupby('time').size()
        target = counts[counts == 20].index[3]
        block = seq[seq['time'] == target]

        print(f'\n【一分钟的完整数据】{target} 共 {len(block)} 行')
        print('  <- 一行 = 一个 3 秒快照（60/3 = 20）')
        print(THIN)
        print(f'{"#":>4}  {"time":<7}{"price":>10}{"vol":>7}{"buyorsell":>11}')
        print(THIN)
        for i, (_, r) in enumerate(block.iterrows()):
            print(f'{i:>4}  {r["time"]:<7}{r["price"]:>10.2f}{r["vol"]:>7}{r["buyorsell"]:>11}')
        print(THIN)
        print(f'  vol 合计 = {int(block["vol"].sum())} 手　'
              f'时间戳去重 = {block["time"].nunique()} 个（全都一样）')

        print('\n【一天的关键点】')
        print(THIN)
        for label, mask in [
            ('开盘首行', None),
            ('09:25 竞价成交', seq['time'] == '09:25'),
            ('15:00 收盘', seq['time'] == '15:00'),
            ('15:05 盘后首行', seq['time'] == '15:05'),
            ('最晚一行', None),
        ]:
            if mask is None:
                r = seq.iloc[0] if label == '开盘首行' else seq.iloc[-1]
            else:
                sub = seq[mask]
                if sub.empty:
                    print(f'{label:<16} 无')
                    continue
                r = sub.iloc[0]
            print(f'{label:<16} time={r["time"]}  price={r["price"]:>8.2f}  '
                  f'vol={r["vol"]:>6}  dir={r["buyorsell"]}')

        print('\n【buyorsell 每种值的真实实例】')
        print(THIN)
        for code in sorted(seq['buyorsell'].unique()):
            rows = seq[seq['buyorsell'] == code]
            r = rows.iloc[0]
            print(f'  dir={code}  行数={len(rows):>5}  例子: time={r["time"]}  '
                  f'price={r["price"]:.2f}  vol={r["vol"]}')

        print('\n【全天统计】')
        print(THIN)
        print(f'  总行数        = {len(seq):,}')
        print(f'  不同时间值    = {seq["time"].nunique()} 个')
        print(f'  每分行数      = min {counts.min()}, max {counts.max()}, mean {counts.mean():.1f}')

        k = client.bars(symbol=CODE, frequency=9, start=0, offset=60).copy()
        k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
        hit = k[k['d'] == DATE]
        if not hit.empty:
            kv = float(hit.iloc[0]['vol'])
            raw = int(seq['vol'].sum())
            clean = seq[(seq['vol'] > 0) & (seq['time'] <= '15:00')]
            cv = int(clean['vol'].sum())
            print(f'  日K vol       = {kv:,.0f} 手')
            print(f'  原始 Σvol     = {raw:,} 手　　差 {raw - kv:+,.0f}')
            print(f'  剔两类后 Σvol = {cv:,} 手　　差 {cv - kv:+,.0f}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
