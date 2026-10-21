"""探针 3：验证「取全」与否 + 精确对账。

关键问题：
1. 不同 page size 下总行数是否一致（一致 = 分页无丢失）
2. Σvol 与日 K vol 的差额来自哪一段
3. vol=0 / buyorsell=8 / buyorsell=5 三类异常分别是什么
4. 集合竞价段（09:15-09:25）是否在数据里
"""

import sys
from collections import Counter

import pandas as pd

from mootdx.quotes import Quotes


def page_all(client, code, date, page, max_pages=40):
    frames = []
    for i in range(max_pages):
        start = i * page
        df = client.transactions(symbol=code, start=start, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else '600519'
    date = sys.argv[2] if len(sys.argv) > 2 else '20261020'
    client = Quotes.factory(market='std', timeout=15)
    print(f'server={client.server}  code={code}  date={date}')
    try:
        print('\n=== 1. 翻页一致性（不同 page size）===')
        results = {}
        for page in (2000, 1000, 800, 500):
            df = page_all(client, code, date, page)
            results[page] = df
            print(f'  page={page:5d} -> rows={0 if df is None else len(df)}  '
                  f'vol_sum={0 if df is None else df["vol"].sum():,}')

        df = results[2000]
        if df is None or df.empty:
            print('[empty]')
            return

        print('\n=== 2. 与日 K 精确对账 ===')
        bars = client.bars(symbol=code, frequency=9, start=0, offset=60).copy()
        bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '')
        hit = bars[bars['d'] == str(date)]
        if hit.empty:
            print(f'  未找到 {date} 日 K；最近={bars["d"].iloc[-1]}')
            return
        row = hit.iloc[0]
        k_vol = float(row['vol'])
        k_amt = float(row['amount'])
        print(f'  日K      vol={k_vol:,.0f} 手   amount={k_amt:,.0f} 元')

        tick_vol = df['vol'].sum()
        tick_amt = (df['price'] * df['vol'] * 100).sum()
        print(f'  分笔全量  vol={tick_vol:,.0f} 手   金额={tick_amt:,.0f} 元')
        print(f'  差额      vol={tick_vol - k_vol:+,.0f} 手  金额={tick_amt - k_amt:+,.0f} 元')

        print('\n=== 3. 异常分类 ===')
        zero = df[df['vol'] == 0]
        print(f'  vol=0 行数 = {len(zero)}  buyorsell 分布={dict(Counter(zero["buyorsell"]))}')
        if len(zero):
            print(f'    时间范围 {zero["time"].min()} .. {zero["time"].max()}')

        after = df[df['time'] > '15:00']
        print(f'  15:00 之后行数 = {len(after)}  vol={after["vol"].sum():,} 手  '
              f'buyorsell={dict(Counter(after["buyorsell"]))}')
        if len(after):
            print(f'    时间范围 {after["time"].min()} .. {after["time"].max()}')

        pre = df[df['time'] < '09:30']
        print(f'  09:30 之前行数 = {len(pre)}  vol={pre["vol"].sum():,} 手  '
              f'buyorsell={dict(Counter(pre["buyorsell"]))}')
        if len(pre):
            print(f'    时间范围 {pre["time"].min()} .. {pre["time"].max()}')

        print('\n=== 4. 剔除各类异常后的对账 ===')
        variants = {
            '全部': df,
            '剔除 vol=0': df[df['vol'] > 0],
            '剔除 vol=0 与 15:00 后': df[(df['vol'] > 0) & (df['time'] <= '15:00')],
            '再剔除 09:30 前': df[(df['vol'] > 0) & (df['time'] <= '15:00') & (df['time'] >= '09:30')],
        }
        for name, sub in variants.items():
            v = sub['vol'].sum()
            a = (sub['price'] * sub['vol'] * 100).sum()
            print(f'  {name:22s} rows={len(sub):5d} vol={v:>10,} 手 (差{k_vol - v:+10,.0f})  '
                  f'金额={a:>15,.0f} (差{k_amt - a:+15,.0f})')

        print('\n=== 5. 集合竞价段（09:15-09:25）明细 ===')
        auction = df[(df['time'] >= '09:15') & (df['time'] <= '09:25')]
        print(f'  rows={len(auction)}  vol={auction["vol"].sum():,} 手')
        if len(auction):
            print(auction[['time', 'price', 'vol', 'buyorsell']].head(15).to_string())
            print('  ...')
            print(auction[['time', 'price', 'vol', 'buyorsell']].tail(15).to_string())

        print('\n=== 6. 收盘集合竞价（14:57-15:00）明细 ===')
        close_auction = df[(df['time'] >= '14:57') & (df['time'] <= '15:00')]
        print(f'  rows={len(close_auction)}  vol={close_auction["vol"].sum():,} 手  '
              f'buyorsell={dict(Counter(close_auction["buyorsell"]))}')

        print('\n=== 7. 首/末各 5 行（全量，按 page0..N 顺序）===')
        print(df.head(5)[['time', 'price', 'vol', 'buyorsell']].to_string())
        print('  ...')
        print(df.tail(5)[['time', 'price', 'vol', 'buyorsell']].to_string())
    finally:
        client.close()


if __name__ == '__main__':
    main()
