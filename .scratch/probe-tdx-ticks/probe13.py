"""生产对账基准核查：分笔 vs 快照成交额 vs 日K成交额。

生产 `TickReconciler` 用的是**快照成交额**，容差 = expected × 0.1% + 1000。
本探针回答：tdx 分笔命中时，该容差够不够？
"""

import httpx
import pandas as pd

from mootdx.quotes import Quotes


def tencent_snapshot(ts_code):
    r = httpx.get(f'https://qt.gtimg.cn/q={ts_code}', timeout=10, trust_env=False)
    r.encoding = 'gbk'
    body = r.text.split('="', 1)[1].rstrip('";')
    f = body.split('~')
    return {
        'name': f[1],
        'vol_hand': float(f[6]) if f[6] else None,
        'amount_wan': float(f[37]) if len(f) > 37 and f[37] else None,  # 万元
    }


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
    return kept['vol'].sum(), (kept['price'] * kept['vol'] * 100).sum()


def tdx_daily(client, code, date):
    k = client.bars(symbol=code, frequency=9, start=0, offset=1000).copy()
    k['d'] = k['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
    hit = k[k['d'] == date]
    return None if hit.empty else hit.iloc[0]


def main():
    date = pd.Timestamp.now(tz='Asia/Shanghai').strftime('%Y%m%d')
    cases = [
        ('600519', 'sh600519'), ('000001', 'sz000001'), ('601398', 'sh601398'),
        ('300750', 'sz300750'), ('002594', 'sz002594'), ('688981', 'sh688981'),
    ]
    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}  date={date}  (今日快照可用)\n')
    hdr = (f'{"代码":<8}{"分笔量":>11}{"快照量":>11}{"日K量":>11}'
           f'{"分笔额(亿)":>11}{"快照额(亿)":>11}{"日K额(亿)":>11}'
           f'{"计快照差%":>10}{"生产容差%":>10}{"会告警?"}')
    print(hdr)
    print('-' * len(hdr))
    try:
        for code, ts in cases:
            t = tick_totals(client, code, date)
            s = tencent_snapshot(ts)
            k = tdx_daily(client, code, date)
            if t is None or k is None or not s['amount_wan']:
                print(f'{code:<8} 缺数据')
                continue
            t_vol, t_amt = t
            k_amt = float(k['amount'])
            s_amt = s['amount_wan'] * 10000
            diff_s = abs(t_amt - s_amt)
            tol_s = s_amt * 0.001 + 1000
            warn = '会' if diff_s > tol_s else '不会'
            print(f'{code:<8}{t_vol:>11,.0f}{s["vol_hand"]:>11,.0f}{float(k["vol"]):>11,.0f}'
                  f'{t_amt / 1e8:>11.4f}{s_amt / 1e8:>11.4f}{k_amt / 1e8:>11.4f}'
                  f'{(t_amt - s_amt) / s_amt * 100:>+9.4f}%{0.1:>9.1f}%{warn:>7}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
