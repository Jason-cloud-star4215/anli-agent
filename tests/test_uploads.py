"""上传材料的接口兼容性。

踩过的坑：客户端把 files 传成「文件名字符串数组」时，处理函数按字典取 name/data，
直接抛 AttributeError 把连接打断（浏览器只看到「加载失败」，服务端没有 400 提示）。
这里锁死：两种写法都要能受理，只有文件名的条目只登记不落盘。
"""

import base64
import tempfile
import unittest
from pathlib import Path

import anli.web as web


class UploadsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._original_root = web.ROOT
        web.ROOT = self.root

    def tearDown(self):
        web.ROOT = self._original_root
        self._tmp.cleanup()

    def save(self, files):
        # _save_uploads 只读模块级 ROOT/MAX_UPLOAD_BYTES，不用 self，这里传 None 即可
        return web.Handler._save_uploads(None, "t-1", files)

    def test_string_entries_are_accepted(self):
        """只给文件名（字符串）时不能崩，按名单登记。"""

        result = self.save(["事故认定书.jpg", "医疗费发票.jpg"])
        self.assertEqual(result["names"], ["事故认定书.jpg", "医疗费发票.jpg"])
        written = list((self.root / "cases" / "t-1" / "uploads").iterdir())
        self.assertEqual(written, [])  # 只有名字，没有内容，不落盘

    def test_dict_entries_with_data_are_written(self):
        """带内容的条目照旧落盘，文件名里的路径分隔符要被清掉。"""

        payload = base64.b64encode(b"fake-jpg-bytes").decode("ascii")
        result = self.save([{"name": "../医疗费发票.jpg", "data": payload}])
        self.assertEqual(result["names"], ["../医疗费发票.jpg"])
        written = list((self.root / "cases" / "t-1" / "uploads").iterdir())
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].name, "..医疗费发票.jpg")
        self.assertEqual(written[0].read_bytes(), b"fake-jpg-bytes")

    def test_mixed_entries_and_empty_name_fall_back(self):
        """混合写法与空条目都要有兜底名字，不能出现 None。"""

        result = self.save([{"data": ""}, None, "票据.png"])
        self.assertEqual(result["names"], ["材料", "材料", "票据.png"])

    def test_oversized_upload_is_rejected_with_message(self):
        """超过体积上限要返回错误信息，由调用方转成 400。"""

        payload = base64.b64encode(b"x" * (web.MAX_UPLOAD_BYTES + 1)).decode("ascii")
        result = self.save([{"name": "大文件.zip", "data": payload}])
        self.assertIn("8MB", result.get("error", ""))


if __name__ == "__main__":
    unittest.main()
