"""诊断 000001 在 20261019 的金额偏差：谁是对的？

三方对拍：
- TDX 日 K 的 amount
- 分笔推算的 amount（price × vol × 100）
- 腾讯历史日线（含 amount）

若 TDX 日 K 与腾讯一致而分笔偏离 → 分笔有问题；
若分笔与腾讯一致而 TDX 日 K 偏离 → TDX 日 K 有问题（与 0009 的结论并存）。
"""

import httpx
import pandas as pd

from mootdx.quotes import Quotes

NO_PROXY = {'http': None, 'https': None}


def tencent_kline(code_ts, count=40):
    """腾讯历史日线：fields = date,open,close,high,low,volume(手),amount(元?)..."""
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {'param': f'{code_ts},day,,,{count},qfq'}
    r = httpx.get(url, params=params, timeout=15, trust_env=False)
    data = r.json()['data'][code_ts]
    rows = data.get('qfqday') or data.get('day')
    out = {}
    for row in rows:
        # [date, open, close, high, low, volume(手)]
        out[row[0].replace('-', '')] = {'vol': float(row[5])}
    return out


def tdx_daily(client, code, count=60):
    bars = client.bars(symbol=code, frequency=9, start=0, offset=count)
    bars = bars.copy()
    bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
    return bars.set_index('d')


def tick_totals(client, code, date):
    frames = []
    for i in range(4):
        df = client.transactions(symbol=code, start=i * 2000, offset=2000, date=date)
        if df is None or df.empty:
            break
        frames.append(df)
    if not frames:
        return None
    raw = pd.concat(frames, ignore_index=True)
    kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')]
    return {
        'rows': len(raw),
        'vol': kept['vol'].sum(),
        'amt': (kept['price'] * kept['vol'] * 100).sum(),
    }


def main():
    dates = ['20261020', '20261019', '20261016', '20261015', '20261014', '20261013', '20261012']
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}\n')
    try:
        for code, ts in [('000001', 'sz000001'), ('600519', 'sh600519')]:
            tk = tencent_kline(ts)
            th = tdx_daily(client, code)
            print(f'===== {code} =====')
            hdr = (f'{"日期":<10}{"分笔vol":>10}{"TDXvol":>10}{"腾讯vol":>10}'
                   f'{"分笔额":>15}{"TDX额":>15}{"额差%":>9}')
            print(hdr)
            print('-' * len(hdr))
            for d in dates:
                t = tick_totals(client, code, d)
                if t is None or d not in th.index:
                    print(f'{d:<10}  缺数据')
                    continue
                k = th.loc[d]
                tq = tk.get(d, {})
                amt_delta = (t['amt'] - float(k['amount'])) / float(k['amount']) * 100
                print(f'{d:<10}{t["vol"]:>10,.0f}{float(k["vol"]):>10,.0f}'
                      f'{tq.get("vol", float("nan")):>10,.0f}'
                      f'{t["amt"]:>15,.0f}{float(k["amount"]):>15,.0f}{amt_delta:>+8.3f}%')
            print()
    finally:
        client.close()


if __name__ == '__main__':
    main()
