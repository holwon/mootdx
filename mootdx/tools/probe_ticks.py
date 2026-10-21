"""通达信分笔健康检查：Σ分笔成交量 ≡ 当日日 K 成交量。

用途：怀疑上游变型（字段语义变化、分页语义变化、新增异常行类型）时重跑。

**判据是成交量，不是行数，也不是成交额**：

- 行数无法自证完整性——只有事先知道应有行数时才可用。
- 成交额天然对不上：分笔 ``price`` 是 3 秒桶内**最后一笔**的价、``vol`` 是桶内**合计**，
  ``price × vol`` 与真实成交额必然有差（实测单日最高 0.109%）。故额差只提示、不判失败。

用法：
    python -m mootdx.tools.probe_ticks                      # 默认样本 4 只，日期按上海时区取今日
    python -m mootdx.tools.probe_ticks 600519 20261020      # 指定标的与日期
    python -m mootdx.tools.probe_ticks 600519 601398        # 多个标的，日期取今日
    python -m mootdx.tools.probe_ticks --wide               # 12 只覆盖各板别 / 流动性

位置参数顺序自由：8 位数字视为日期，其余视为标的代码。两者都可省略。
注意「当日」在交易时段内可能取不全（历史端点对当日数据的归档时机未探明），
故本工具宜在**收盘后**运行。

退出码：0 = 全部通过；1 = 有标的偏离容差；2 = 连接失败。

背景与结论见 astock-mcp 仓 `docs/research/0010-tdx-ticks-calibration.md`。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd

from mootdx.quotes import Quotes

#: 单次请求上限。服务器对更大的值静默截断，故恒传此值。
PAGE_SIZE = 2000

#: 页数守卫。实测最大 5040 行；真正的终止条件是「遇空即停」，
#: 此值只防「上游永远吐非空」导致死循环，且须远离 start >= 32768 的回绕区。
MAX_PAGES = 4

#: 成交量容差：≤1 手。实测 12 只 × 2 日全部满足。
VOL_TOLERANCE = 1

#: 成交额偏离的**提示**阈值。分笔的 price 是 3 秒桶内最后一笔价、vol 是桶内合计，
#: 故 `price × vol` 天然不等于真实成交额；实测单日最高 0.109%。
#: 超过此值只报告「额偏」，**不算失败**——量对得上就说明数据是完整的。
AMOUNT_NOTE_RATIO = 0.0005

#: 默认样本：沪深主板 / 创业板 / 科创板各取一只，含高低换手。
DEFAULT_CASES = ['600519', '601398', '300750', '002594']

#: --wide 样本：12 只，覆盖板别与流动性全谱。
WIDE_CASES = [
    '600519',  # 沪主板 低换手
    '601398',  # 沪主板 低价大盘
    '603259',  # 沪主板
    '601127',  # 沪主板 高活跃
    '000001',  # 深主板
    '000725',  # 深主板 超低价高量
    '002594',  # 深主板
    '002230',  # 深主板 高活跃
    '300750',  # 创业板
    '300059',  # 创业板 高活跃
    '688981',  # 科创板
    '688111',  # 科创板
]


@dataclass
class ProbeResult:
    """单只标的的核对结果。"""

    code: str
    rows: int
    pages: int
    tick_vol: float
    kline_vol: float
    tick_amount: float
    kline_amount: float
    dropped_zero: int
    dropped_after_close: int

    @property
    def vol_delta(self) -> float:
        return self.tick_vol - self.kline_vol

    @property
    def amount_delta_ratio(self) -> float:
        if not self.kline_amount:
            return 0.0
        return (self.tick_amount - self.kline_amount) / self.kline_amount

    @property
    def passed(self) -> bool:
        """数据完整性只看成交量。

        成交额不用作判据：分笔 ``price`` 是 3 秒桶内最后一笔价、``vol`` 是桶内合计，
        ``price × vol`` 与真实成交额天然有差（实测最高 0.109%）。用额判失败会把
        正常数据判成有问题。
        """

        return abs(self.vol_delta) <= VOL_TOLERANCE

    @property
    def amount_off(self) -> bool:
        """成交额偏离是否超过提示阈值（只提示，不影响通过/失败）。"""

        return abs(self.amount_delta_ratio) > AMOUNT_NOTE_RATIO


def fetch_ticks(client, code: str, date: str) -> tuple[pd.DataFrame | None, int]:
    """逐页取回某一日的全部分笔。

    ``start`` 自最新一笔往回数，故各页拼接后需整体反转成正序。
    **终止条件只能是「遇空即停」**：``start >= 32768`` 时服务器会回绕成第 0 页
    并返回 2000 行完整复制，固定页数的循环会静默产出重复数据。
    """

    frames: list[pd.DataFrame] = []
    for index in range(MAX_PAGES):
        page = client.transactions(symbol=code, start=index * PAGE_SIZE, offset=PAGE_SIZE, date=date)
        if page is None or page.empty:
            break
        frames.append(page)
    if not frames:
        return None, 0
    return pd.concat(frames, ignore_index=True), len(frames)


def daily_bar(client, code: str, date: str):
    """取该日的日 K 行，作为独立对账基准。"""

    bars = client.bars(symbol=code, frequency=9, start=0, offset=1000)
    if bars is None or bars.empty:
        return None
    bars = bars.copy()
    bars['_day'] = bars['datetime'].astype(str).str[:10].str.replace('-', '', regex=False)
    hit = bars[bars['_day'] == date]
    return None if hit.empty else hit.iloc[0]


def clean_ticks(raw: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """剔除两类非连续竞价行，返回 (清理后数据, 剔除的 vol=0 行数, 剔除的盘后行数)。

    两类行的判据与 ``buyorsell`` 编码严格等价（实测 154/154 与 12/12 双向精确）：

    - ``vol == 0``  ⟺ ``buyorsell == 8``：集合竞价虚拟撮合（09:15–09:25），从未成交
    - ``time > 15:00`` ⟺ ``buyorsell == 5``：盘后固定价格交易，**不计入日 K 成交量**

    09:15–09:25 的**真实**竞价成交（``09:25`` 那笔 ``buyorsell == 2``、``vol > 0``）必须保留。
    盘后段若保留会让每次对账都超差（600519 实测多 32 手 / 418 万元）。
    """

    dropped_zero = int((raw['vol'] == 0).sum())
    dropped_after_close = int((raw['time'] > '15:00').sum())
    kept = raw[(raw['vol'] > 0) & (raw['time'] <= '15:00')]
    return kept, dropped_zero, dropped_after_close


def probe_one(client, code: str, date: str) -> ProbeResult | None:
    """核对单只标的；日 K 或分笔缺失时返回 None。"""

    bar = daily_bar(client, code, date)
    if bar is None:
        return None
    raw, pages = fetch_ticks(client, code, date)
    if raw is None or raw.empty:
        return None
    kept, dropped_zero, dropped_after_close = clean_ticks(raw)
    return ProbeResult(
        code=code,
        rows=len(raw),
        pages=pages,
        tick_vol=float(kept['vol'].sum()),
        kline_vol=float(bar['vol']),
        tick_amount=float((kept['price'] * kept['vol'] * 100).sum()),
        kline_amount=float(bar['amount']),
        dropped_zero=dropped_zero,
        dropped_after_close=dropped_after_close,
    )


def _is_date(token: str) -> bool:
    """判断一个位置参数是日期（8 位数字）还是标的代码。"""

    return len(token) == 8 and token.isdigit()


def _parse_args(argv: list[str]) -> tuple[list[str], str]:
    """解析位置参数，返回 (标的列表, 日期)。

    位置参数顺序自由：8 位数字视为日期，其余视为标的代码；两者都可省略。
    ``--wide`` 只在未显式给标的时生效。
    """

    positional = [item for item in argv[1:] if not item.startswith('-')]
    wide = '--wide' in argv or '-w' in argv

    codes = [item for item in positional if not _is_date(item)]
    dates = [item for item in positional if _is_date(item)]

    if len(dates) > 1:
        raise SystemExit(f'最多给一个日期，收到 {dates}')
    if len(codes) > len(WIDE_CASES):
        raise SystemExit(f'标的数量 {len(codes)} 超过上限 {len(WIDE_CASES)}')

    date = dates[0] if dates else pd.Timestamp.now(tz='Asia/Shanghai').strftime('%Y%m%d')
    cases = codes or (WIDE_CASES if wide else DEFAULT_CASES)
    return cases, date


def main(argv: list[str]) -> int:
    cases, date = _parse_args(argv)

    client = Quotes.factory(market='std', timeout=20)
    print(f'server={client.server}  date={date}  cases={len(cases)}')
    header = (
        f'{"代码":<8}{"页数":>4}{"行数":>7}{"Σ分笔量":>12}{"日K量":>12}'
        f'{"量差":>7}{"金额差%":>9}{"剔vol=0":>9}{"剔盘后":>7}{"判读":>6}'
    )
    print(header)
    print('-' * len(header))

    failures = 0
    amount_notes: list[str] = []
    try:
        for code in cases:
            try:
                result = probe_one(client, code, date)
            except Exception as exc:  # noqa: BLE001 — 探针：单只失败不中断全样本
                print(f'{code:<8}  [raise] {type(exc).__name__}: {exc}')
                failures += 1
                continue
            if result is None:
                print(f'{code:<8}{"无日K或无分笔":>7}')
                failures += 1
                continue
            verdict = 'OK' if result.passed else 'DIFF'
            if not result.passed:
                failures += 1
            elif result.amount_off:
                amount_notes.append(f'{result.code} {result.amount_delta_ratio * 100:+.3f}%')
            print(
                f'{result.code:<8}{result.pages:>4}{result.rows:>7}'
                f'{result.tick_vol:>12,.0f}{result.kline_vol:>12,.0f}'
                f'{result.vol_delta:>+7,.0f}{result.amount_delta_ratio * 100:>+8.3f}%'
                f'{result.dropped_zero:>9}{result.dropped_after_close:>7}{verdict:>6}'
            )
    except ConnectionError as exc:
        print(f'[连接失败] {exc}')
        return 2
    finally:
        client.close()

    if amount_notes:
        print(f'\n额偏提示（不影响判读，见模块文档）：{", ".join(amount_notes)}')
    print(f'\n{"全部通过" if failures == 0 else f"{failures} 只未通过"}')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
