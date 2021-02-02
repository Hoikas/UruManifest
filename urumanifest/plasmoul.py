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

import contextlib
from io import IOBase
import struct
from typing import Any, Callable, Union

class plUoid:
    def __init__(self):
        self.location = (0, 0)
        self.class_type = 0x8000
        self.name = None

    def __eq__(self, rhs):
        if self.location == rhs.location:
            if self.class_type == rhs.class_type:
                if self.name == rhs.name:
                    return True
        return False

    def read(self, s):
        contents = s.readu8()
        self.location = s.read_location()

        # load mask
        if contents & 0x02:
            s.readu8()

        self.class_type = s.readu16()
        s.readu32() # object ID -- we don't give a rat's
        self.name = s.read_safe_string()

        # clone IDs
        if contents & 0x01:
            s.readu16() # clone ID
            s.readu16() # garbage
            s.readu32() # clone player ID


class plStream:
    def __init__(self, file : IOBase):
        self._file = file

    def read(self, size : int = -1) -> bytes:
        return self._file.read(size)

    def read_location(self):
        #self._file.read(6) # seqnum (32) + flags (16)
        num = self.readu32()
        flags = self.readu16()

        if num & 0x80000000:
            num -= 0xFF000001
            prefix = num >> 16
            suffix = num - (prefix << 16)
            prefix *= -1
        else:
            num -= 33
            prefix = num >> 16
            suffix = num - (prefix << 16)
        return (prefix, suffix)

    def read_safe_string(self) -> str:
        _chars = self.readu16()
        if (_chars & 0xF000) == 0:
            self._file.read(2) # old style 32-bit count
        _chars &= ~0xF000
        if not _chars:
            return ""

        _buf = bytearray(self._file.read(_chars))
        if _buf[0] & 0x80:
            for i in range(_chars):
                _buf[i] = ~_buf[i] & 0xFF
        return _buf.decode("utf-8")

    def readu8(self) -> int:
        return int(struct.unpack("<B", self._file.read(1))[0])

    def readu16(self) -> int:
        return int(struct.unpack("<H", self._file.read(2))[0])

    def readu32(self):
        return int(struct.unpack("<I", self._file.read(4))[0])

    def read_uoid(self) -> plUoid:
        if self.readu8():
            u = plUoid()
            u.read(self)
            return u
        return None

    def set_position(self, pos : int) -> int:
        self._file.seek(pos, 0)

    def writeu8(self, value : int):
        self._file.write(struct.pack("<B", value))

    def writeu16(self, value : int):
        self._file.write(struct.pack("<H", value))

    def writeu32(self, value : int):
        self._file.write(struct.pack("<I", value))

    def write_safe_string(self, value : str):
        buf = value.encode("utf-8")
        self.writeu16(len(buf) | 0xf000)
        for i in buf:
            self.writeu8(~i & 0xff)

    def write(self, value : Union[str, bytes]) -> int:
        return self._file.write(value)


@contextlib.contextmanager
def stream(opener : Callable[[Any], IOBase] = open, *args, **kwargs):
    with opener(*args, **kwargs) as stream:
        yield plStream(stream)
