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
import itertools
from os import PathLike
from pathlib import Path
import struct
from typing import Any, Callable, Iterable, Union

try:
    from . import encryption
except ImportError:
    import encryption

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

    def close(self):
        self._file.close()

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


class plKey(plUoid):
    def __init__(self):
        self.uoid = None
        self.pos = None
        self.length = -1

    def read(self, s):
        self.uoid = plUoid()
        self.uoid.read(s)
        self.pos = s.readu32()
        self.length = s.readu32()


class _KeyedObject:
    class_type = 0x0002

    def read(self, s):
        self.uoid = s.read_uoid()
        assert self.uoid


class _SynchedObject(_KeyedObject):
    class_type = 0x0028

    def read(self, s):
        super().read(s)

        flags = s.readu32()

        # Excluded state list
        if flags & 0x10:
            count = s.readu16()
            for i in range(count):
                s.read(s.readu16())

        # Volatile state list
        if flags & 0x40:
            count = s.readu16()
            for i in range(count):
                s.read(s.readu16())


class _ObjInterface(_SynchedObject):
    class_type = 0x0010 # No, you aren't going crazy...

    def read(self, s):
        super().read(s)

        self.owner = s.read_uoid()

        # bit vector flags... who cares?
        count = s.readu32()
        s.read(count * 4)


class _MultiModifier(_SynchedObject):
    class_type = 0x0027 # No, you aren't going crazy...

    def read(self, s):
        super().read(s)

        # bit vector flags... who cares?
        count = s.readu32()
        s.read(count * 4)


class plSoundBuffer(_KeyedObject):
    class_type = 0x0029

    IS_EXTERNAL = 0x01
    ALWAYS_EXTERNAL = 0x02
    ONLY_LEFT_CHANNEL = 0x04
    ONLY_RIGHT_CHANNEL = 0x08
    STREAM_COMPRESSED = 0x10

    @property
    def has_ogg_file(self):
        return bool(self.flags & plSoundBuffer.IS_EXTERNAL)

    @property
    def split_channel(self):
        if self.flags & plSoundBuffer.ONLY_LEFT_CHANNEL:
            return True
        elif self.flags & plSoundBuffer.ONLY_RIGHT_CHANNEL:
            return True
        else:
            return False

    @property
    def stream(self):
        return bool(self.flags & plSoundBuffer.STREAM_COMPRESSED)

    def read(self, s):
        super().read(s)

        self.flags = s.readu32()
        self.data_length = s.readu32()
        self.file_name = s.read_safe_string()

        self.format_tag = s.readu16()
        self.channels = s.readu16()
        self.samples_per_sec = s.readu32()
        self.avg_bytes_per_sec = s.readu32()
        self.block_align = s.readu16()
        self.bits_per_sample = s.readu16()


class plPythonFileMod(_MultiModifier):
    class_type = 0x00A2

    def read(self, s):
        super().read(s)

        self.file_name = s.read_safe_string()
        # receivers, parameters... who cares?


class plRelevanceRegion(_ObjInterface):
    class_type = 0x011E

    def read(self, s):
        super().read(s)
        self.region = s.read_uoid()


# all plasma classes -- leave out ABCs to save time.
_pClasses = (plSoundBuffer, plPythonFileMod, plRelevanceRegion)


class plPage:
    def __init__(self, fn : Union[str, PathLike]):
        self._stream = plStream(open(fn, "rb"))

    def __enter__(self):
        self._read_header()
        self._read_keyring()
        return self

    def __exit__(self, type, value, tb):
        self._stream.close()

    def __str__(self):
        return "[AGE: {}] [PAGE: {}] [LOC: {}]".format(self._age, self._page, self._location)

    @property
    def age(self):
        return self._age

    @property
    def name(self):
        return self._page

    def _read_header(self):
        s = self._stream # lazy

        assert s.readu32() == 6 # PRP Version
        self._location = s.read_location()
        self._age = s.read_safe_string()
        self._page = s.read_safe_string()
        self._version = s.readu16()
        s.readu32() # checksum
        s.readu32() # data start
        self._index_pos = s.readu32()

    def _read_keyring(self):
        s = self._stream # lazy
        s.set_position(self._index_pos)

        self._keyring = {}

        types = s.readu32()
        for i in range(types):
            pClass = s.readu16()
            s.readu32() # key list length (in bytes) -- garbage
            s.readu8() # nonsense
            numKeys = s.readu32()

            self._keyring[pClass] = [None] * numKeys
            for j in range(numKeys):
                self._keyring[pClass][j] = plKey()
                self._keyring[pClass][j].read(s)

    def get_keys(self, pClass) -> Iterable[plKey]:
        try:
            return tuple(self._keyring[pClass.class_type])
        except LookupError:
            return tuple()

    def get_object(self, key : plKey):
        s = self._stream
        assert key.pos, "Invalid position"
        s.set_position(key.pos)

        # pCre idx
        pClass = s.readu16()
        assert pClass == key.uoid.class_type

        pType = next((i for i in _pClasses if i.class_type == pClass), None)
        if pType is None:
            raise RuntimeError(f"need to implement 0x{pClass:04x}")

        obj = pType()
        obj.key = key
        obj.read(s)
        return obj

    def get_objects(self, pClass) -> Iterable:
        for i in self.get_keys(pClass):
            yield self.get_object(i)


class plAge:
    def __init__(self, path : Union[str, PathLike]):
        self._pages = []
        self._prefix = 0

        path = Path(path)
        self._name = path.stem
        with encryption.stream(path, mode=encryption.Mode.ReadText, encoding="utf-8") as s:
            self.read(s)

    @property
    def name(self):
        return self._name

    @property
    def prefix(self):
        return self._prefix

    @property
    def pages(self):
        return self._pages

    @property
    def common_pages(self):
        # uint8_t me
        yield "BuiltIn"
        yield "Textures"

    @property
    def all_pages(self):
        return itertools.chain(self.pages, self.common_pages)

    def read(self, s : IOBase):
        for line in (i.strip() for i in s):
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line[:5].lower() == "page=":
                args = line[5:].split(",")
                # Maybe one day someone will want the flags. For now... who cares??
                self._pages.append(args[0])
            elif line[:15].lower() == "sequenceprefix=":
                self._prefix = int(line[15:])

    def open_all_pages(self, dir : Union[None, str, PathLike] = None) -> Iterable[plPage]:
        assert self._name, "Name of the Age is not set."
        for i in self.all_pages:
            path = Path(dir if dir else "").joinpath(f"{self._name}_District_{i}.prp")
            with plPage(path) as page:
                yield page


# Test code... Not a unit test because it requires an actual PRP...
if __name__ == "__main__":
    age = plAge("G:\\Plasma\\Games\\MOULa\\dat\\Garden.age")
    for prp in age.open_all_pages("G:\\Plasma\\Games\\MOULa\\dat"):
        print(str(prp))
        print("Sound Buffers:")
        for sfx in prp.get_objects(plSoundBuffer):
            print(f"[OBJ: {sfx.uoid.name}] [FILE: {sfx.file_name}]")
        print("Python File Mods:")
        for pymod in prp.get_objects(plPythonFileMod):
            print(f"[OBJ: {pymod.uoid.name}] [FILE: {pymod.file_name}]")
        print()
