"""抓取历史分笔的**原始响应字节**，逐字节分析结构。

目的：判定「没有秒」是协议限制，还是 mootdx/tdxpy 解析时丢了字段。
不猜，只看字节。
"""

import struct

import tdxpy.parser.std.get_history_transaction_data as histmod
from tdxpy.hq import TdxHq_API

captured = {}
_orig = histmod.GetHistoryTransactionData.parseResponse


def patched(self, body_buf):
    captured['buf'] = bytes(body_buf)
    return _orig(self, body_buf)


histmod.GetHistoryTransactionData.parseResponse = patched

CODE = '600519'
DATE = 20261020
MARKET = 1  # 沪


def hexdump(buf, offset=0, width=16):
    for i in range(0, len(buf), width):
        chunk = buf[i:i + width]
        hexpart = ' '.join(f'{b:02x}' for b in chunk)
        asciipart = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {offset + i:04x}  {hexpart:<{width * 3}}  {asciipart}')


def get_price(buf, pos):
    """复刻 tdxpy.helper.get_price 的变长编码（带符号）。"""
    if pos >= len(buf):
        return None, pos
    pos_byte = 6
    bdata = buf[pos]
    int_data = bdata & 0x3F
    sign = bool(bdata & 0x40)
    if bdata & 0x80:
        while True:
            pos += 1
            bdata = buf[pos]
            int_data += (bdata & 0x7F) << pos_byte
            pos_byte += 7
            if not (bdata & 0x80):
                break
    pos += 1
    return (-int_data if sign else int_data), pos


def main():
    api = TdxHq_API()
    with api.connect('117.34.114.16', 7709):
        print('connected')
        # 取 start=0 的 10 条，样本小、好分析
        rows = api.get_history_transaction_data(MARKET, CODE, 0, 10, DATE)
        buf = captured.get('buf')
        if buf is None:
            print('未捕获到原始响应')
            return

        print(f'\n原始响应总长 = {len(buf)} 字节')
        print('\n=== 前 120 字节 ===')
        hexdump(buf[:120])

        n = struct.unpack('<H', buf[:2])[0]
        print(f'\n=== 头部解析 ===')
        print(f'  [0:2]  记录数 = {n}')
        print(f'  [2:6]  被跳过的 4 字节 = {buf[2:6].hex(" ")}')

        print(f'\n=== 按 tdxpy 的方式解析这 {min(n, 10)} 条 ===')
        pos = 2 + 4
        last = 0
        for i in range(min(n, 10)):
            start_pos = pos
            minutes, pos = struct.unpack('<H', buf[pos:pos + 2])[0], pos + 2
            price_raw, pos = get_price(buf, pos)
            vol, pos = get_price(buf, pos)
            a, pos = get_price(buf, pos)
            b, pos = get_price(buf, pos)
            last += price_raw
            print(f'  #{i:<2} 偏移 {start_pos:>4}..{pos - 1:<4} '
                  f'分钟数={minutes:<5} ({minutes // 60:02d}:{minutes % 60:02d})  '
                  f'price_raw={price_raw:<6} 累计价={last / 100:<9.2f}  '
                  f'vol={vol:<6} 字段A={a:<4} 字段B={b:<4} '
                  f'（本条 {pos - start_pos} 字节）')

        print(f'\n=== 剩余未消费字节 = {len(buf) - pos} ===')
        if len(buf) > pos:
            hexdump(buf[pos:pos + 64], offset=pos)

        print(f'\n=== 对照：当日接口的解析顺序是 ===')
        print('  time, price_raw, vol, num(成交笔数), buyorsell, 未知')
        print('而历史接口解析的是：')
        print('  time, price_raw, vol, A, B　（少一个字段）')
        print('\n  首 10 条里字段A 的取值：', end='')
        print('（见上表）')


if __name__ == '__main__':
    main()
