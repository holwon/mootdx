from __future__ import annotations

import pandas as pd
import pytest

from mootdx import config
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import Quotes, StdQuotes, valid_server
from mootdx.utils import to_data


class FakeHqApi:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.client = None
        self.connected = None

    def connect(self, ip, port, time_out):
        self.connected = (ip, port, time_out)
        return self

    def close(self):
        self.client = None

    def get_security_bars(self, *args):
        return [{"close": 1.0}]


def test_std_client_uses_default_when_bestip_is_empty(monkeypatch):
    monkeypatch.setattr("mootdx.quotes.TdxHq_API", FakeHqApi)
    config.setup(force=True)
    config.set("BESTIP.HQ", "")

    client = Quotes.factory("std", timeout=3)

    expected = tuple(config.get("SERVER.HQ")[0][1:3])
    assert client.server == expected
    assert client.client.connected == (*expected, 3)


def test_std_client_probes_the_full_host_list_not_only_the_builtin_subset(monkeypatch):
    """候选池必须包含 tdxpy 自带的主机，否则干净机器上只剩那 38 台可能全部失效。

    这是真实踩到的故障：``consts.HQ_HOSTS`` 里的 38 台在某段时间全部取不到数据，
    而 ``tdxpy.constants.hq_hosts`` 里的 104 台里有可用的。若工厂只探前者，
    没有 ``~/.mootdx/config.json`` 的干净环境（例如刚拉的镜像）就完全取不到数。
    """

    probed: list[tuple[str, int]] = []

    class RecordingApi(FakeHqApi):
        def connect(self, ip, port, time_out):
            probed.append((ip, port))
            return super().connect(ip, port, time_out)

    monkeypatch.setattr("mootdx.quotes.TdxHq_API", RecordingApi)
    config.setup(force=True)
    config.set("BESTIP.HQ", "")

    Quotes.factory("std", timeout=3)

    from tdxpy.constants import hq_hosts

    builtin = {(item[1], item[2]) for item in config.get("SERVER.HQ")}
    tdxpy_only = {(item[1], item[2]) for item in hq_hosts} - builtin

    assert tdxpy_only, "tdxpy 主机清单应包含不在内建清单里的条目，否则该断言无意义"
    assert tdxpy_only & set(probed), "候选池漏掉了 tdxpy 自带的主机"


@pytest.mark.parametrize("server", [(), ("127.0.0.1",), ("bad-ip", 7709), ("127.0.0.1", 0)])
def test_valid_server_rejects_invalid_values(server):
    with pytest.raises(ValueError):
        valid_server(server)


def test_factory_rejects_unknown_market():
    with pytest.raises(ValueError, match="market"):
        Quotes.factory("unknown")


class _BarsClient:
    def __init__(self):
        self.calls = []

    def get_security_bars(self, *args):
        self.calls.append(args)
        return [{"datetime": "2024-01-02 00:00", "open": 1.0}]


def test_bars_normalizes_prefixed_code_and_validates_window():
    quotes = object.__new__(StdQuotes)
    quotes.client = _BarsClient()

    result = quotes.bars("SH.600000", offset=1)

    assert not result.empty
    assert quotes.client.calls == [(9, 1, "600000", 0, 1)]
    with pytest.raises(MootdxValidationException, match="offset"):
        quotes.bars("600000", offset=0)


def test_get_k_data_returns_empty_for_empty_or_reversed_ranges():
    quotes = object.__new__(StdQuotes)
    quotes.client = _BarsClient()
    assert quotes.get_k_data("600000", "2024-01-01", "2024-01-01").empty
    assert quotes.get_k_data("600000", None, "2024-01-02").empty


def test_to_data_accepts_numpy_like_values():
    import numpy as np

    result = to_data(np.array([[1, 2], [3, 4]]))
    assert isinstance(result, pd.DataFrame)
    assert result.shape == (2, 2)
