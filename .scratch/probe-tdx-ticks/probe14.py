"""回看 000001 在 20261019 的金额差：是当日偶发，还是某种可解释的形态？

逐笔检查：把每一笔按「价格精度」分类（A股最小变动 0.01 元），
看差额是否恰好等于若干笔的 1 分钱误差累积。
"""

import pandas as pd

from mootdx.quotes import Quotes

DATE = '20261019'


def fetch(client, code, date):
    frames = []
    for i in range(4):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main():
    client = Quotes.factory(market='std', timeout=20)
    try:
        raw = fetch(client, '000001', DATE)
        kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')].copy()
        kept['amt'] = kept['price'] * kept['vol'] * 100

        k = client.bars(symbol='000001', frequency=9, start=0, offset=1000).copy()
        k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
        krow = k[k['d'] == DATE].iloc[0]
        k_amt = float(krow['amount'])
        gap = kept['amt'].sum() - k_amt
        avg_price = kept['amt'].sum() / (kept['vol'].sum() * 100)
        print(f'总差额 = {gap:+,.0f} 元')
        print(f'折合「以均价计的多少手」= {gap / (avg_price * 100):,.1f} 手')
        print(f'折合「1 分钱 × 多少股」= {gap / 0.01:,.0f} 股 = {gap / 0.01 / 100:,.1f} 手')

        # 若差额 = 某笔被按错误价格计价的差
        print('\n--- 与腾讯日线对比（是否 TDX 日K 自身偏差）---')
        import httpx
        r = httpx.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                      params={'param': 'sz000001,day,,,40,qfq'}, timeout=15, trust_env=False)
        rows = r.json()['data']['sz000001'].get('qfqday') or r.json()['data']['sz000001'].get('day')
        for row in rows:
            if row[0].replace('-', '') == DATE:
                print(f'  腾讯 {DATE}: {row}')

        print('\n--- 相邻日期是否也有同类偏差（连续性是判据）---')
        for d in ['20261012', '20261013', '20261014', '20261015', '20261016', '20261019', '20261020']:
            t = fetch(client, '000001', d)
            kk = k[k['d'] == d]
            if t is None or kk.empty:
                continue
            kk = kk.iloc[0]
            kk = kk.iloc[0] if hasattr(kk, 'iloc') else kk
            tc = t[(t['vol'] > 0) & (t['time'] <= '15:00')]
            a = (tc['price'] * tc['vol'] * 100).sum()
            kb = float(kk['amount'])
            print(f'  {d}: 分笔={a:,.0f} 日K={kb:,.0f} 差={a - kb:+,.0f} ({(a - kb) / kb * 100:+.4f}%)')
    finally:
        client.close()


if __name__ == '__main__':
    main()
