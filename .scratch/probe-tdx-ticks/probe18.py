"""直接证明「桶末价 × 桶量」假设。

如果能用一个**独立**的方法复现 0.109% 这个数字，假设就成立。

方法：把分笔按分钟聚合成「桶」，然后看
  A. Σ(桶末价 × 桶量)     —— 就是我们现在算的（等价于逐行 price×vol）
  B. Σ(桶内均价 × 桶量)   —— 需要桶内逐笔，拿不到
  C. 用典型的「桶内价格漂移」估计误差量级

更直接的检验：找出**桶内价格确实变过**的分钟，看这些分钟的
「末价 × 量」与「量加权均价 × 量」差多少。若把每分钟拆成
前半 / 后半两半各自算，两半的差异能间接反映出桶内漂移的规模。
"""

import pandas as pd

from mootdx.quotes import Quotes

CODE = '000001'
DATES = ['20261019', '20261020', '20261015', '20261016']


def fetch(client, code, date, pages=5):
    frames = []
    for i in range(pages):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def main():
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        for date in DATES:
            raw = fetch(client, CODE, date)
            if raw is None:
                continue
            kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')].copy()
            # 反转页序得到的正确时间序
            kept = kept.iloc[::-1].reset_index(drop=True)
            kept['px_cents'] = (kept['price'] * 100).round().astype('int64')
            kept['amt'] = kept['px_cents'] * kept['vol']

            k = client.bars(symbol=CODE, frequency=9, start=0, offset=200).copy()
            k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
            hit = k[k['d'] == date]
            if hit.empty:
                continue
            krow = hit.iloc[0]
            k_amt = float(krow['amount'])
            k_vol = float(krow['vol'])
            k_high, k_low = float(krow['high']), float(krow['low'])

            total = int(kept['amt'].sum())
            print(f'===== {CODE} @ {date}　振幅 {(k_high - k_low) / k_low * 100:.2f}% =====')
            print(f'  日K amount        = {k_amt:>16,.0f}')
            print(f'  分笔 Σ(价×量)      = {total:>16,.0f}   差 {total - k_amt:+,.0f} ({(total - k_amt) / k_amt * 100:+.4f}%)')

            # 关键检验：桶内价格漂移的规模
            # 用「同分钟内相邻两行的价格差」加权，估计桶内真实漂移
            kept['min'] = kept['time'].map(lambda t: int(t[:2]) * 60 + int(t[3:]))
            g = kept.groupby('min')
            drift_rows = 0
            drift_vol = 0
            drift_amount_impact = 0
            for m, grp in g:
                if len(grp) < 2:
                    continue
                first_px = grp['px_cents'].iloc[0]
                last_px = grp['px_cents'].iloc[-1]
                if first_px == last_px:
                    continue
                v = grp['vol'].sum()
                drift_rows += 1
                drift_vol += v
                # 若整桶都按末价成交，相对首价的偏差上界
                drift_amount_impact += (last_px - first_px) * v

            print(f'  桶内价格变过的分钟数 = {drift_rows} / {g.ngroups}')
            print(f'  这些分钟的量占比     = {drift_vol / kept["vol"].sum() * 100:.1f}%')
            print(f'  「末价 − 首价」× 量合计 = {drift_amount_impact:+,.0f} 元　（量级参考）')

            # 另一个视角：用「分钟末价」替代「3秒末价」
            minute_last = kept.groupby('min').agg(px=('px_cents', 'last'), vol=('vol', 'sum'))
            coarse = int((minute_last['px'] * minute_last['vol']).sum())
            print(f'  若按「分钟末价 × 分钟量」= {coarse:>16,.0f}   差 {coarse - k_amt:+,.0f} ({(coarse - k_amt) / k_amt * 100:+.4f}%)')

            # 用「分钟首价」
            minute_first = kept.groupby('min').agg(px=('px_cents', 'first'), vol=('vol', 'sum'))
            cf = int((minute_first['px'] * minute_first['vol']).sum())
            print(f'  若按「分钟首价 × 分钟量」= {cf:>16,.0f}   差 {cf - k_amt:+,.0f} ({(cf - k_amt) / k_amt * 100:+.4f}%)')
            print()
    finally:
        client.close()


if __name__ == '__main__':
    main()
