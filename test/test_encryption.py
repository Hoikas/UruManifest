#    This file is part of UruManifest
#
#    UruManifest is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    UruManifest is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with UruManifest.  If not, see <http://www.gnu.org/licenses/>.

import abc
import contextlib
from pathlib import Path
import struct
import sys
import tempfile
import time
from typing import NamedTuple, Union
import unittest

from urumanifest import encryption

_TestData = [
    "The quick brown fox jumps over the lazy dog!",
    "Бо́же, Царя́ храни́!",
    "わが君は千代に八千代にさざれ石の巌となりて苔の生すまで",
    "동해 물과 백두산이 마르고 닳도록, 하느님이 보우하사 우리나라 만세",
]

class _KnownValues(NamedTuple):
    source: str
    BTEA: bytes
    XTEA: bytes


_KnownData = [
    _KnownValues(
        "The quick brown fox jumps over the lazy dog!",
        BTEA=bytes([
            0x6E, 0x6F, 0x74, 0x74, 0x68, 0x65, 0x64, 0x72, 0x6F, 0x69, 0x64, 0x73,
            0x2C, 0x00, 0x00, 0x00, 0x52, 0xB6, 0xB5, 0xDB, 0x0E, 0x76, 0x8F, 0x44,
            0x45, 0x31, 0xC6, 0xE4, 0xA9, 0x64, 0x86, 0x6C, 0x9B, 0x1D, 0x33, 0x7B,
            0x6B, 0xF5, 0xE4, 0x1D, 0x61, 0xFE, 0x27, 0x54, 0x1E, 0xB2, 0x9A, 0x6D,
            0x52, 0x0D, 0x17, 0x4D, 0xA7, 0x07, 0xDC, 0x2B, 0x93, 0xC8, 0x83, 0xFE,
            0x93, 0xA4, 0xA7, 0xA7
        ]),
        XTEA=bytes([
            0x77, 0x68, 0x61, 0x74, 0x64, 0x6F, 0x79, 0x6F, 0x75, 0x73, 0x65, 0x65,
            0x2C, 0x00, 0x00, 0x00, 0x9B, 0xE3, 0xC7, 0xF6, 0xA7, 0x3C, 0xEE, 0xB2,
            0x9D, 0x4D, 0x5F, 0x32, 0x7E, 0x66, 0xF7, 0x6C, 0x0E, 0x52, 0x24, 0x2E,
            0x3E, 0x61, 0x8B, 0xB8, 0x46, 0xE3, 0xF4, 0xDF, 0x33, 0xAD, 0x28, 0x6D,
            0x8A, 0x6C, 0xD2, 0x2F, 0x95, 0x24, 0x1D, 0xEA, 0x06, 0x8E, 0xF6, 0x39,
            0x27, 0x7C, 0x4B, 0xA2
        ])
    )
]

class _EncryptionTest(abc.ABC):
    def setUp(self):
        self.start_time = time.perf_counter()

    def tearDown(self):
        print(f"{time.perf_counter() - self.start_time:.3f}s", end=" ")
        sys.stdout.flush()

    @property
    def extra_args(self):
        return {}

    @property
    @abc.abstractmethod
    def stream_key(self):
        ...

    @property
    @abc.abstractmethod
    def stream_type(self) -> encryption.Encryption:
        ...

    def _round_trip(self, data_out : Union[bytes, str]):
        kwargs = dict(enc=self.stream_type, key=self.stream_key)
        if isinstance(data_out, bytes):
            read_mode = encryption.Mode.ReadBinary
            write_mode = encryption.Mode.WriteBinary
        else:
            read_mode = encryption.Mode.ReadText
            write_mode = encryption.Mode.WriteText
            kwargs["encoding"] = "utf-8"
        kwargs.update(self.extra_args)

        fname = Path(tempfile.mktemp(suffix=".fni"))
        try:
            with encryption.stream(fname, mode=write_mode, **kwargs) as stream:
                stream.write(data_out)
            with encryption.stream(fname, mode=read_mode, **kwargs) as stream:
                data_in = stream.read()
        except:
            raise
        else:
            self.assertEqual(data_out, data_in)
        finally:
            fname.unlink()

    @contextlib.contextmanager
    def _write_test(self):
        fname = Path(tempfile.mktemp(suffix=".fni"))
        kwargs = dict(mode=encryption.Mode.WriteBinary, enc=self.stream_type, key=self.stream_key)
        kwargs.update(self.extra_args)
        try:
            with encryption.stream(fname, **kwargs) as stream:
                yield stream
        finally:
            fname.unlink()

    def test_textMode(self):
        for i in _TestData:
            self._round_trip(i)

    def test_binaryUtf8(self):
        for i in _TestData:
            self._round_trip(i.encode("utf-8"))

    def test_binaryUtf16(self):
        for i in _TestData:
            self._round_trip(i.encode("utf-16-le"))

    def _test_known(self, value: str, result: bytes):
        fname = Path(tempfile.mktemp(suffix=".fni"))
        kwargs = dict(mode=encryption.Mode.WriteText, enc=self.stream_type, key=self.stream_key, encoding="UTF-8")
        kwargs.update(self.extra_args)
        try:
            with encryption.stream(fname, **kwargs) as stream:
                stream.write(value)
            with fname.open(mode="rb") as stream:
                data_in = stream.read()
        except:
            raise
        else:
            self.assertEqual(result, data_in)
        finally:
            fname.unlink()

    def test_knownValues(self):
        for i in _KnownData:
            self._test_known(i.source, getattr(i, self.stream_type.name))

    def test_randomWrites(self):
        with self._write_test() as stream:
            stream.write(struct.pack("<B", 1))
            stream.write(struct.pack("<I", 666))
            stream.write(bytes([0xEF, 0xBE, 0xAD, 0xDE] * 6))
            stream.write(bytes([0xBE, 0xBA, 0xAD, 0xAB] * 66))

    def test_emptyWrite(self):
        with self._write_test() as stream:
            stream.write(bytes())
        with self._write_test() as stream:
            stream.write(bytes([0xEF, 0xBE, 0xAD, 0xDE] * 6))
            stream.write(bytes())

    def test_bigWrite(self):
        with self._write_test() as stream:
            # 1 MiB write
            stream.write(bytes([0xFF] * 1000000))


class _PureTest:
    @property
    def extra_args(self):
        return dict(pure=True)


class _XTEAEncryptionTest(_EncryptionTest):
    @property
    def stream_key(self):
        return None

    @property
    def stream_type(self):
        return encryption.Encryption.XTEA


class _BTEAEncryptionTest(_EncryptionTest):
    @property
    def stream_key(self):
        return "31415926535897932384626433832795"

    @property
    def stream_type(self):
        return encryption.Encryption.BTEA

try:
    import _urumanifest
except ImportError:
    print("WARNING: C encryption module is NOT available. Only testing Python encryption!")
else:
    class XTEACxxEncryptionTest(_XTEAEncryptionTest, unittest.TestCase): pass
    class BTEACxxEncryptionTest(_BTEAEncryptionTest, unittest.TestCase): pass

class XTEAPureEncryptionTest(_PureTest, _XTEAEncryptionTest, unittest.TestCase): pass
class BTEAPureEncryptionTest(_PureTest, _BTEAEncryptionTest, unittest.TestCase): pass
