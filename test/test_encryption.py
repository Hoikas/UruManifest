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
import tempfile
from typing import Union
import unittest

from urumanifest import encryption

_TestData = [
    "The quick brown fox jumps over the lazy dog!",
    "Бо́же, Царя́ храни́!",
    "わが君は千代に八千代にさざれ石の巌となりて苔の生すまで",
    "동해 물과 백두산이 마르고 닳도록, 하느님이 보우하사 우리나라 만세",
]

class _EncryptionTest(abc.ABC):
    @property
    @abc.abstractmethod
    def stream_key(self):
        ...

    @property
    @abc.abstractmethod
    def stream_type(self):
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


class XTEAEncryptionTest(_EncryptionTest, unittest.TestCase):
    @property
    def stream_key(self):
        return None

    @property
    def stream_type(self):
        return encryption.Encryption.XTEA


class BTEAEncryptionTest(_EncryptionTest, unittest.TestCase):
    @property
    def stream_key(self):
        return "31415926535897932384626433832795"

    @property
    def stream_type(self):
        return encryption.Encryption.BTEA
