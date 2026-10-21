"""探针 4：多标的完整性交叉验证。

判据不是「行数够不够」，而是「Σ分笔成交量 == 日 K 成交量」。
只要这个等式在各流动性档位都成立，就说明没丢数据；反之差额就是丢失量。
"""

import sys

import pandas as pd

from mootdx.quotes import Quotes

CASES = [
    ('600519', '贵州茅台 沪主板 低换手'),
    ('601398', '工商银行 沪主板 低价大盘'),
    ('300750', '宁德时代 创业板'),
    ('688981', '中芯国际 科创板'),
    ('002594', '比亚迪 深主板'),
    ('000001', '平安银行 深主板'),
    ('301389', '创业板小盘'),
    ('603259', '药明康德 沪主板'),
]


def page_all(client, code, date, page=2000, max_pages=40):
    frames = []
    for i in range(max_pages):
        df = client.transactions(symbol=code, start=i * page, offset=page, date=date)
        n = 0 if df is None else len(df)
        if n == 0:
            break
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def daily_bar(client, code, date):
    bars = client.bars(symbol=code, frequency=9, start=0, offset=60)
    if bars is None or bars.empty:
        return None
    bars = bars.copy()
    bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '')
    hit = bars[bars['d'] == str(date)]
    return None if hit.empty else hit.iloc[0]


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else '20261020'
    client = Quotes.factory(market='std', timeout=15)
    print(f'server={client.server}  date={date}\n')
    header = f'{"代码":<8}{"说明":<20}{"行数":>7}{"分笔量":>11}{"日K量":>11}{"量差":>9}{"量差%":>8}{"金额差%":>9}{"V=0":>6}{"判读":>7}'
    print(header)
    print('-' * len(header))
    try:
        for code, note in CASES:
            try:
                bar = daily_bar(client, code, date)
                if bar is None:
                    print(f'{code:<8}{note:<20}{"无日K":>7}')
                    continue
                k_vol = float(bar['vol'])
                k_amt = float(bar['amount'])

                df = page_all(client, code, date)
                if df is None or df.empty:
                    print(f'{code:<8}{note:<20}{"无分笔":>7}')
                    continue

                # 只保留连续竞价 + 集合竞价成交，剔除 15:00 之后的盘后与 vol=0 占位
                clean = df[(df['vol'] > 0) & (df['time'] <= '15:00')]
                t_vol = clean['vol'].sum()
                t_amt = (clean['price'] * clean['vol'] * 100).sum()

                dv = t_vol - k_vol
                dvp = dv / k_vol * 100 if k_vol else 0
                dap = (t_amt - k_amt) / k_amt * 100 if k_amt else 0
                v0 = int((df['vol'] == 0).sum())
                verdict = 'OK' if abs(dvp) < 0.5 and abs(dap) < 0.5 else 'DIFF'
                print(f'{code:<8}{note:<20}{len(df):>7}{t_vol:>11,.0f}{k_vol:>11,.0f}'
                      f'{dv:>+9,.0f}{dvp:>+7.2f}%{dap:>+8.3f}%{v0:>6}{verdict:>7}')
            except Exception as exc:  # noqa: BLE001
                print(f'{code:<8}{note:<20}  [raise] {type(exc).__name__}: {exc}')
    finally:
        client.close()


if __name__ == '__main__':
    main()
