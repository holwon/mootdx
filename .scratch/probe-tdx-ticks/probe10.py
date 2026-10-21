"""查 000001 在 20261019 金额差 0.109% 的成因。"""

import pandas as pd

from mootdx.quotes import Quotes

client = Quotes.factory(market='std', timeout=20)
try:
    code, date = '000001', '20261019'
    frames = []
    for i in range(4):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')]

    bar = client.bars(symbol=code, frequency=9, start=0, offset=1000).copy()
    bar['d'] = bar['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
    row = bar[bar['d'] == date].iloc[0]

    tick_amt = (kept['price'] * kept['vol'] * 100).sum()
    k_amt = float(row['amount'])
    print(f'日K: vol={row["vol"]:,.0f} amount={k_amt:,.0f}')
    print(f'分笔: vol={kept["vol"].sum():,.0f} amount={tick_amt:,.0f}')
    print(f'差额 = {tick_amt - k_amt:+,.0f} 元 ({(tick_amt - k_amt) / k_amt * 100:+.4f}%)')
    print(f'隐含均价: 日K={k_amt / (float(row["vol"]) * 100):.6f}  分笔={tick_amt / (kept["vol"].sum() * 100):.6f}')

    print('\n--- 价格分布（分笔）---')
    print(kept['price'].describe())
    print(f'\nprice 小数位数分布:')
    dec = kept['price'].map(lambda p: len(str(p).split('.')[-1]) if '.' in str(p) else 0)
    print(dec.value_counts().to_dict())

    print('\n--- 按时间分段的量/额 ---')
    kept = kept.copy()
    kept['seg'] = pd.cut(
        kept['time'].map(lambda t: int(t[:2]) * 60 + int(t[3:])),
        bins=[0, 570, 690, 780, 900, 2000],
        labels=['<09:30', '09:30-11:30', '11:30-13:00', '13:00-15:00', '>15:00'],
    )
    g = kept.groupby('seg', observed=True).apply(
        lambda d: pd.Series({'rows': len(d), 'vol': d['vol'].sum(),
                             'amt': (d['price'] * d['vol'] * 100).sum(),
                             'avg': (d['price'] * d['vol']).sum() / d['vol'].sum()}),
        include_groups=False,
    )
    print(g.to_string())

    print('\n--- 前 5 / 后 5 行 ---')
    print(kept.head(5)[['time', 'price', 'vol', 'buyorsell']].to_string())
    print(kept.tail(5)[['time', 'price', 'vol', 'buyorsell']].to_string())

    print('\n--- 是否 09:25 竞价那笔异常 ---')
    a = kept[kept['time'] <= '09:26']
    print(f'  集合竞价段: rows={len(a)} vol={a["vol"].sum():,} amt={(a["price"] * a["vol"] * 100).sum():,.0f}')
finally:
    client.close()
