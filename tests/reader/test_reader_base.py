import unittest

from mootdx.reader import ReaderBase


# @pytest.mark.skip
class TestReaderBase(unittest.TestCase):
    def test_find_path(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sh000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path2(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sz', 'sz000001', ['lc1', '1']) == result, result

    def test_find_path3(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='34#000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('ds', '34#000001', ['lc1', '1']) == result, result

    def test_find_path4(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sh000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path5(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='SH000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sh', 'sh000001', ['lc1', '1']) == result, result

    def test_find_path6(self):
        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='sz000001', subdir='minline', suffix=['lc1', '1'], debug=True)
        assert ('sz', 'sz000001', ['lc1', '1']) == result, result

    def test_ext_symbol_keeps_its_case(self):
        """扩展市场代码含大写字母，不能跟着沪/深一起转小写。

        ``4#CF7D0LAO.day`` 在 Linux 的大小写敏感文件系统上只有原样拼写才找得到；
        一旦被转成 ``4#cf7d0lao.day`` 就静默返回空 DataFrame（CI 曾因此全红）。
        """

        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='4#CF7D0LAO', subdir='lday', suffix='day', debug=True)
        assert ('ds', '4#CF7D0LAO', ['day']) == result, result

    def test_lowercase_ext_symbol_is_not_rewritten_to_uppercase(self):
        """小写扩展代码不会被「猜」成大写——归一化只做保真，不做大小写猜测。"""

        reader = ReaderBase('tests/fixtures')
        result = reader.find_path(symbol='4#cf7d0lao', subdir='lday', suffix='day', debug=True)
        assert ('ds', '4#cf7d0lao', ['day']) == result, result

    def test_prefixed_symbols_are_still_normalized_to_lowercase(self):
        reader = ReaderBase('tests/fixtures')
        assert reader.find_path(symbol='SH600000', subdir='lday', suffix='day', debug=True) == (
            'sh',
            'sh600000',
            ['day'],
        )


if __name__ == '__main__':
    unittest.main()
