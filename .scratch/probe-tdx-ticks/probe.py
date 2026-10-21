"""一次性探针：验证通达信分笔接口的完整性与口径。

用法：python probe.py <code> <yyyymmdd>
不复用任何仓库代码分支，只调 mootdx/t dxpy 的公开入口。
"""

import sys
import warnings
from collections import Counter

import pandas as pd

from mootdx.quotes import Quotes


def connect():
    client = Quotes.factory(market='std', timeout=15)
    print(f'[ok] connected: server={client.server}')
    return client


def probe_current(client, code):
    print('\n=== A. 当日接口 transaction() ===')
    warnings.simplefilter('always')
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            df = client.transaction(symbol=code, start=0, offset=2000)
        for w in caught:
            print(f'  [warn] {w.category.__name__}: {w.message}')
        print(f'  rows={0 if df is None else len(df)}')
        if df is not None and len(df):
            print(df.head(3).to_string())
    except Exception as exc:  # noqa: BLE001
        print(f'  [raise] {type(exc).__name__}: {exc}')


def page_all(client, code, date, page=2000, max_pages=20):
    """自 start=0 起按 +=page 推进，直到空页或达上限。"""
    print(f'\n=== B. 历史接口 transactions(date={date}) 翻页 ===')
    frames = []
    for i in range(max_pages):
        start = i * page
        try:
            df = client.transactions(symbol=code, start=start, offset=page, date=date)
        except Exception as exc:  # noqa: BLE001
            print(f'  page {i} start={start}: [raise] {type(exc).__name__}: {exc}')
            break
        n = 0 if df is None else len(df)
        print(f'  page {i} start={start} -> rows={n}')
        if n == 0:
            break
        df = df.copy()
        df['_page'] = i
        df['_seq'] = range(len(df))
        frames.append(df)
    if not frames:
        print('  [empty] 没有取到任何数据')
        return None
    allrows = pd.concat(frames, ignore_index=True)
    print(f'  total rows = {len(allrows)}')
    print(f'  columns = {list(allrows.columns)}')
    return allrows


def analyze(allrows, label):
    print(f'\n=== C. 分析 {label} ===')
    if allrows is None or allrows.empty:
        print('  [skip] 无数据')
        return

    print(f'  首行: {allrows.iloc[0].to_dict()}')
    print(f'  末行: {allrows.iloc[-1].to_dict()}')

    times = allrows['time'].tolist()
    print(f'  time 去重数 = {len(set(times))}')
    print(f'  time 首/末 = {times[0]} / {times[-1]}')

    # 单调性：判断 start 方向
    def mono(seq):
        up = sum(1 for a, b in zip(seq, seq[1:]) if a <= b)
        down = sum(1 for a, b in zip(seq, seq[1:]) if a >= b)
        return up, down, len(seq) - 1

    up, down, total = mono(times)
    print(f'  time 单调: 非减对={up}/{total} 非增对={down}/{total}')

    # 按页看时间范围，判断每一页覆盖哪一段
    print('  每页时间范围:')
    for p, grp in allrows.groupby('_page'):
        print(f'    page {p}: n={len(grp):5d} {grp["time"].iloc[0]} .. {grp["time"].iloc[-1]}')

    # 五分钟粒度桶
    buckets = Counter()
    for t in times:
        hh, mm = t.split(':')
        buckets[f'{hh}:{int(mm) // 5 * 5:02d}'] += 1
    print(f'  5 分钟桶数 = {len(buckets)}（完整一天应为 48 个）')
    missing = []
    for start_min in range(9 * 60 + 30, 11 * 60 + 30, 5):
        key = f'{start_min // 60:02d}:{start_min % 60:02d}'
        if key not in buckets:
            missing.append(key)
    for start_min in range(13 * 60, 15 * 60, 5):
        key = f'{start_min // 60:02d}:{start_min % 60:02d}'
        if key not in buckets:
            missing.append(key)
    print(f'  缺失的 5 分钟桶 = {missing if missing else "无"}')

    # 金额校验
    vol = allrows['vol'].sum()
    amt = (allrows['price'] * allrows['vol']).sum()
    print(f'  Σvol = {vol:,.0f} 手 = {vol * 100:,.0f} 股')
    print(f'  Σ(price×vol) = {amt:,.0f} 元（未乘 100）')
    if 'num' in allrows.columns:
        print(f'  Σnum = {allrows["num"].sum():,.0f}')
    if 'buyorsell' in allrows.columns:
        print(f'  buyorsell 取值分布 = {dict(Counter(allrows["buyorsell"]))}')


def compare_with_daily(client, code, date):
    print(f'\n=== D. 与日 K 对账（date={date}） ===')
    try:
        bars = client.bars(symbol=code, frequency=9, start=0, offset=400)
    except Exception as exc:  # noqa: BLE001
        print(f'  [raise] {type(exc).__name__}: {exc}')
        return
    if bars is None or bars.empty:
        print('  [empty]')
        return
    bars = bars.copy()
    bars['d'] = bars['datetime'].astype(str).str[:10].str.replace('-', '')
    hit = bars[bars['d'] == str(date)]
    if hit.empty:
        print(f'  未找到 {date} 的日 K 行（最近日期 = {bars["d"].iloc[-1]}）')
        return
    row = hit.iloc[0]
    print(f'  日K: vol={row["vol"]:,.0f} 手  amount={row.get("amount", "n/a")}')
    return row


if __name__ == '__main__':
    code = sys.argv[1] if len(sys.argv) > 1 else '600519'
    date = sys.argv[2] if len(sys.argv) > 2 else '20261017'
    print(f'probe: code={code} date={date}')
    client = connect()
    try:
        probe_current(client, code)
        rows = page_all(client, code, date)
        analyze(rows, f'{code}@{date}')
        compare_with_daily(client, code, date)
    finally:
        client.close()
