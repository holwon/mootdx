"""探针 9：验证 3 秒粒度 + 钉死 buyorsell 的 0/1 语义。

A. 每分钟行数分布：若上界 ≈20，则每行是 3 秒快照（60/3=20）。
B. 与腾讯行情的「外盘/内盘」对拍：腾讯 qt.gtimg.cn 报文里含外盘、内盘，
   两者应分别对应 TDX 的某一种 buyorsell 值。这是唯一能钉死方向的实测。
"""

import sys
from collections import Counter

import httpx
import pandas as pd

from mootdx.quotes import Quotes


def page_all(client, code, date, page=2000, cap=40):
    frames = []
    for i in range(cap):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def tencent_quote(ts_code):
    """ts_code 形如 sz002594 / sh600519。返回 dict（腾讯 v 报文，字段顺序固定）。"""
    url = f'https://qt.gtimg.cn/q={ts_code}'
    resp = httpx.get(url, timeout=10)
    resp.encoding = 'gbk'
    text = resp.text.strip()
    body = text.split('="', 1)[1].rstrip('";')
    f = body.split('~')
    # 腾讯字段表：3=现价 6=成交量(手) 7=外盘 8=内盘 37=成交额(万)
    return {
        'name': f[1],
        'price': f[3],
        'vol_hand': float(f[6]) if f[6] else None,
        'outer': float(f[7]) if f[7] else None,
        'inner': float(f[8]) if f[8] else None,
        'amount_wan': float(f[37]) if len(f) > 37 and f[37] else None,
    }


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else '20261020'
    cases = [('002594', 'sz002594'), ('600519', 'sh600519'),
             ('601398', 'sh601398'), ('300750', 'sz300750')]

    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}  date={date}\n')
    try:
        print('=== A. 每分钟行数分布（验证 3 秒粒度）===')
        for code, _ in cases:
            df = page_all(client, code, date)
            if df is None or df.empty:
                continue
            cnt = df.groupby('time').size()
            print(f'  {code}: 分钟数={len(cnt)} 每分行数 min={cnt.min()} '
                  f'median={cnt.median():.0f} max={cnt.max()} mean={cnt.mean():.1f}  '
                  f'{"3秒粒度(≤20) ✓" if cnt.max() <= 20 else "⚠ 超过 20，粒度更细"}')
            if cnt.max() > 20:
                print(f'    超过 20 行的分钟: {cnt[cnt > 20].head(5).to_dict()}')

        print('\n=== B. buyorsell 语义对拍（腾讯外盘/内盘）===')
        hdr = f'{"代码":<8}{"名称":<10}{"腾讯外盘":>11}{"腾讯内盘":>11}{"TDX dir=0":>12}{"TDX dir=1":>12}{"匹配":>8}'
        print(hdr)
        print('-' * len(hdr))
        for code, ts in cases:
            q = tencent_quote(ts)
            df = page_all(client, code, date)
            if df is None or df.empty:
                print(f'{code:<8}  无分笔')
                continue
            clean = df[(df['vol'] > 0) & (df['time'] <= '15:00')]
            v0 = clean.loc[clean['buyorsell'] == 0, 'vol'].sum()
            v1 = clean.loc[clean['buyorsell'] == 1, 'vol'].sum()
            outer, inner = q['outer'], q['inner']
            # 谁对上外盘，谁就是「买」
            match = '?'
            if outer and inner:
                if abs(v0 - outer) / outer < 0.01 and abs(v1 - inner) / inner < 0.01:
                    match = '0=买 1=卖'
                elif abs(v1 - outer) / outer < 0.01 and abs(v0 - inner) / inner < 0.01:
                    match = '1=买 0=卖'
                else:
                    match = '对不上'
            print(f'{code:<8}{q["name"]:<10}{outer:>11,.0f}{inner:>11,.0f}'
                  f'{v0:>12,.0f}{v1:>12,.0f}{match:>8}')

        print('\n=== C. 详细验证（002594 明细）===')
        q = tencent_quote('sz002594')
        df = page_all(client, '002594', date)
        clean = df[(df['vol'] > 0) & (df['time'] <= '15:00')]
        dist = Counter()
        for d, v in zip(clean['buyorsell'], clean['vol']):
            dist[d] += v
        print(f'  腾讯: 现价={q["price"]} 量={q["vol_hand"]:,.0f}手 外盘={q["outer"]:,.0f} 内盘={q["inner"]:,.0f}')
        print(f'  TDX buyorsell 成交量分布 = { {k: f"{v:,.0f}" for k, v in dist.items()} }')
        print(f'  腾讯外盘+内盘 = {q["outer"] + q["inner"]:,.0f}  '
              f'TDX dir0+dir1 = {dist.get(0, 0) + dist.get(1, 0):,.0f}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
