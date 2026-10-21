"""判定「那个被丢弃的字节」是什么。

若是秒：全天 4000+ 条里应出现 0..59 的各种值，且 3 秒桶应呈 00/03/06… 的规律。
若是填充/标志：应恒为同一个值。

同时统计每条记录的字节构成，反推协议字段宽度。
"""

import struct
from collections import Counter

import tdxpy.parser.std.get_history_transaction_data as histmod
from tdxpy.hq import TdxHq_API

captured = []
_orig = histmod.GetHistoryTransactionData.parseResponse


def patched(self, body_buf):
    captured.append(bytes(body_buf))
    return _orig(self, body_buf)


histmod.GetHistoryTransactionData.parseResponse = patched

CODE = '600519'
DATE = 20261020
MARKET = 1


def read_varint(buf, pos):
    if pos >= len(buf):
        return None, pos
    pos_byte = 6
    b = buf[pos]
    val = b & 0x3F
    sign = bool(b & 0x40)
    if b & 0x80:
        while True:
            pos += 1
            b = buf[pos]
            val += (b & 0x7F) << pos_byte
            pos_byte += 7
            if not (b & 0x80):
                break
    pos += 1
    return (-val if sign else val), pos


def main():
    api = TdxHq_API()
    with api.connect('117.34.114.16', 7709):
        pages = []
        for i in range(6):
            captured.clear()
            rows = api.get_history_transaction_data(MARKET, CODE, i * 2000, 2000, DATE)
            if not rows:
                break
            pages.append(captured[0])

        print(f'抓到 {len(pages)} 页原始响应，'
              f'字节数 {[len(p) for p in pages]}')

        field5 = Counter()
        record_lens = Counter()
        total = 0
        header4 = []
        time_vals = []

        for buf in pages:
            n = struct.unpack('<H', buf[:2])[0]
            header4.append(buf[2:6])
            pos = 6
            for _ in range(n):
                begin = pos
                t = struct.unpack('<H', buf[pos:pos + 2])[0]
                pos += 2
                time_vals.append(t)
                for _f in range(3):          # price, vol, buyorsell
                    _, pos = read_varint(buf, pos)
                last, pos = read_varint(buf, pos)   # 被丢弃的那个
                field5[last] += 1
                record_lens[pos - begin] += 1
                total += 1
            assert pos == len(buf), f'未对齐：{pos} != {len(buf)}'

        print(f'\n=== 总记录数 {total:,}，全部字节精确对齐（零剩余）===')
        print('这本身就证明了字段宽度是对的——多读/少读一个字节都会立刻错位。')

        print(f'\n=== 那个「被丢弃的最后一字段」的取值分布 ===')
        for v, c in field5.most_common(20):
            print(f'  {v:>6} 出现 {c:>6} 次  ({c / total * 100:.1f}%)')
        print(f'  不同取值个数 = {len(field5)}')

        print(f'\n=== 每条记录的字节长度分布 ===')
        for v, c in sorted(record_lens.items()):
            print(f'  {v} 字节  出现 {c:>6} 次')

        print(f'\n=== 响应头 [2:6] 那 4 字节（tdxpy 直接跳过）===')
        for i, h in enumerate(header4):
            be = struct.unpack('>I', h)[0]
            le = struct.unpack('<I', h)[0]
            print(f'  page{i}: {h.hex(" ")}  大端={be:,}  小端={le:,}')

        print(f'\n=== 时间字段 ===')
        print(f'  取值个数 = {len(set(time_vals))}')
        print(f'  范围 = {min(time_vals)} .. {max(time_vals)} '
              f'({min(time_vals) // 60:02d}:{min(time_vals) % 60:02d} .. '
              f'{max(time_vals) // 60:02d}:{max(time_vals) % 60:02d})')
        print(f'  2 字节 uint16 的理论上限 = 65535 分钟 = {65535 // 1440} 天')
        print('  → 2 字节只能表示「第几分钟」，物理上放不下秒')


if __name__ == '__main__':
    main()
