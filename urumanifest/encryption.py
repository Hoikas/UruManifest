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
import enum
import functools
import io
import logging
from os import PathLike
import struct
import sys
from typing import Iterable, Tuple, Union

class Mode(enum.Enum):
    ReadBinary = enum.auto()
    ReadText = enum.auto()
    WriteBinary = enum.auto()
    WriteText = enum.auto()


class Encryption(enum.Enum):
    Unspecified = enum.auto()
    XTEA = enum.auto()
    BTEA = enum.auto()


_Encryptions = {
    b"whatdoyousee": Encryption.XTEA,
    b"BriceIsSmart": Encryption.XTEA,
    b"notthedroids": Encryption.BTEA,
}

class _Stream(abc.ABC, io.RawIOBase):
    def __init__(self, handle : io.IOBase, key : Union[Iterable[int], int, str, None]):
        self._init_key(key)

        self._handle = handle
        self._size = 0
        self._pos = 0
        self._buffer = bytearray(8)

        if handle.readable():
            # Don't assert due to BriceIsSmart
            handle.read(12)
            self._size = self._readu32()
        if handle.writable():
            self._write_header()

    def _init_key(self, key):
        assert key is not None

        if isinstance(key, str):
            key = int(key, 16)
        if isinstance(key, int):
            try:
                buf = key.to_bytes(length=16, byteorder="big")
            except OverflowError:
                raise RuntimeError("The encryption key should be a 128-byte integer")
            else:
                key = []
                key.append(int.from_bytes(buf[0:4], byteorder="big"))
                key.append(int.from_bytes(buf[4:8], byteorder="big"))
                key.append(int.from_bytes(buf[8:12], byteorder="big"))
                key.append(int.from_bytes(buf[12:16], byteorder="big"))
        assert len(key) == 4
        self._key = key

    def _readu32(self) -> int:
        return int(struct.unpack("<I", self._handle.read(4))[0])

    def _writeu32(self, value : int):
        self._handle.write(struct.pack("<I", value))

    def _write_header(self):
        self._handle.write(self.magic_string)
        self._writeu32(self._size)

    def close(self):
        if not self._handle.closed and self._handle.writable():
            if self._pos % 8 != 0:
                # Flush crypt buffer (unrolled for performance)
                myints = self.encipher(struct.unpack("<II", self._buffer))
                self._handle.write(struct.pack("<II", *myints))
            self._handle.seek(0, io.SEEK_SET)
            self._write_header()
        self._handle.close()

    def readable(self) -> bool:
        return self._handle.readable()

    def read(self, size : int = -1) -> bytes:
        # Encrypted in blocks of two unsigned 32-bit integers
        if size < 0:
            size = self._size - self._pos
        else:
            size = min(size, self._size - self._pos)
        assert self._pos + size <= self._size

        buf = bytearray(size)
        bp, lp = 0, self._pos % 8
        st = struct.Struct("<II")
        unpack, pack, read, decipher = st.unpack, st.pack, self._handle.read, self.decipher
        while bp < size:
            if lp == 0:
                myints = decipher(unpack(read(8)))
                self._buffer = pack(*myints)
            if lp + (size - bp) >= 8:
                buf[bp:bp+8-lp] = self._buffer[lp:]
                bp += 8 - lp
                lp = 0
            else:
                buf[bp:size-bp] = self._buffer[lp:lp+size-bp]
                bp = size

        self._pos += size
        return bytes(buf[:size])

    def readall(self) -> bytes:
        return self.read(-1)

    def readinto(self, buf : bytearray) -> int:
        # Terrible hack...
        size = min(len(buf), self._size - self._pos)
        buf[:size] = self.read(size)
        return size

    def writable(self) -> bool:
        return self._handle.writable()

    def write(self, buf : bytes):
        bp, lp, size = 0, self._pos % 8, len(buf)
        st = struct.Struct("<II")
        unpack, pack, write, encipher = st.unpack, st.pack, self._handle.write, self.encipher
        while bp < size:
            if lp + (size - bp) >= 8:
                self._buffer[lp:] = buf[bp:bp+8-lp]
                assert len(self._buffer) == 8, (len(self._buffer), bp, lp, size)

                # Flush crypt buffer (unrolled for performance)
                myints = encipher(unpack(self._buffer))
                write(pack(*myints))

                bp += 8 - lp
                lp = 0
            else:
                # Clear out old junk from the crypt buffer
                self._buffer = bytearray(8)
                self._buffer[lp:lp+size-bp] = buf[bp:]
                assert len(self._buffer) == 8, (len(self._buffer), bp, lp, size)
                bp = size

        self._pos += size
        self._size = max(self._size, self._pos)
        return size

    def seekable(self) -> bool:
        # For now, let's avoid this. We don't need it, currently.
        return False

    @property
    @abc.abstractmethod
    def magic_string(self) -> bytes:
        ...

    @abc.abstractmethod
    def encipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        ...

    @abc.abstractmethod
    def decipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        ...


class _XTEAStream(_Stream):
    def __init__(self, handle : io.IOBase, key : Union[Iterable[int], int, str, None]):
        if key is None:
            key = (0x6c0a5452, 0x03827d0f, 0x3a170b92, 0x16db7fc2)
        super().__init__(handle, key)

    @property
    def magic_string(self) -> bytes:
        return b"whatdoyousee"

    def encipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        v0, v1 = buf
        delta, mask = 0x9e3779b9, 0xffffffff
        key = 0
        ekey = self._key
        for i in range(32):
            v0 = (v0 + (((v1 << 4 ^ v1 >> 5) + v1) ^ (key + ekey[key & 3]))) & mask
            key = (key + delta) & mask
            v1 = (v1 + (((v0 << 4 ^ v0 >> 5) + v0) ^ (key + ekey[key >> 11 & 3]))) & mask
        return v0, v1

    def decipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        v0, v1 = buf
        delta = 0x9e3779b9
        key = (delta * 32) & 0xffffffff
        ekey = self._key
        for i in range(32):
            v1 = (v1 - (((v0 << 4 ^ v0 >> 5) + v0) ^ (key + ekey[key >> 11 & 3]))) & 0xffffffff
            key = (key - delta) & 0xffffffff
            v0 = (v0 - (((v1 << 4 ^ v1 >> 5) + v1) ^ (key + ekey[key & 3]))) & 0xffffffff
        return v0, v1


class _BTEAStream(_Stream):
    def __init__(self, handle : io.IOBase, key : Union[Iterable[int], int, str, None]):
        if key is None:
            raise RuntimeError("BTEA Streams require an encryption key.")
        super().__init__(handle, key)

    @property
    def magic_string(self) -> bytes:
        return b"notthedroids"

    def encipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        v = list(buf)
        y, z = buf[0], buf[1]
        delta = 0x9e3779b9
        key = 0

        # Unrolled for performance
        #mx = lambda: (z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (self._key[p&3^e]^z)

        ekey = self._key
        for q in range(32): # 6 + 52 // num
            key = (key + delta) & 0xffffffff
            e = (key >> 2) & 3

            # Unrolled loop for performance
            y = v[1]
            #v[0] = (v[0] + mx()) & 0xffffffff
            v[0] = (v[0] + ((z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (ekey[0^e]^z))) & 0xffffffff
            z = v[0]

            # Last word
            y = v[0]
            #v[num - 1] = (v[num - 1] + mx()) & 0xffffffff
            v[1] = (v[1] + ((z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (ekey[1^e]^z))) & 0xffffffff
            z = v[1]

        return tuple(v)

    def decipher(self, buf : Tuple[int, int]) -> Tuple[int, int]:
        v = list(buf)
        y, z = buf[0], buf[1]
        delta = 0x9e3779b9
        # key = ((6 + 52 // 2) * delta) & 0xffffffff
        key = (32 * delta) & 0xffffffff

        # Unrolled for performance...
        #mx = lambda: (z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (self._key[p&3^e]^z)

        ekey = self._key
        while key > 0:
            e = (key >> 2) & 3

            # Unrolled loop for performance
            z = v[0]
            #v[1] = (v[1] - mx()) & 0xffffffff
            v[1] = (v[1] - ((z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (ekey[1^e]^z))) & 0xffffffff
            y = v[1]

            # Last word
            z = v[1]
            #v[0] = (v[0] - mx()) & 0xffffffff
            v[0] = (v[0] - ((z>>5 ^ y<<2) + (y>>3 ^ z<<4) ^ (key^y) + (ekey[0^e]^z))) & 0xffffffff
            y = v[0]

            key = (key - delta) & 0xffffffff
        return tuple(v)


_WARNED_ABOUT_C = False

def _wrap_c_crypto(stream: _Stream):
    global _WARNED_ABOUT_C
    try:
        import _urumanifest
    except ImportError:
        if not _WARNED_ABOUT_C:
            logging.warning("_urumanifest C module is not available, using pure Python crypto")
            _WARNED_ABOUT_C = True
    else:
        if isinstance(stream, _XTEAStream):
            stream.encipher = functools.partial(_urumanifest.xtea_encipher, stream._key)
            stream.decipher = functools.partial(_urumanifest.xtea_decipher, stream._key)
        elif isinstance(stream, _BTEAStream):
            stream.encipher = functools.partial(_urumanifest.btea_encipher, stream._key)
            stream.decipher = functools.partial(_urumanifest.btea_decipher, stream._key)

@contextlib.contextmanager
def stream(filename : Union[str, bytes, PathLike],
           mode : Mode, *,
           key : Union[Iterable[int], int, str, None] = None,
           enc : Encryption = Encryption.Unspecified,
           **kwargs) -> io.BufferedIOBase:

    assert enc in Encryption, "Invalid encryption type"
    if enc == Encryption.Unspecified:
        if mode in (Mode.WriteBinary, Mode.WriteText):
            raise io.UnsupportedOperation("Writable encrypted streams require an explicit encryption type")
        else:
            enc = determine(filename)

    if enc == Encryption.BTEA and key is None:
        raise io.UnsupportedOperation("BTEA encrypted streams require an explicit encryption key")

    # Use the Python encipher/decipher methods
    pure_python: bool = kwargs.pop("pure", False)

    # If no encryption magic was detected, we pretend that it's just a plain old text file.
    if enc == Encryption.Unspecified:
        assert mode in (Mode.ReadBinary, Mode.ReadText)
        open_mode = "rt" if mode == Mode.ReadText else "rb"
        with open(filename, open_mode) as stream:
            yield stream
    else:
        open_mode = "rb" if mode in (Mode.ReadBinary, Mode.ReadText) else "wb"
        with open(filename, open_mode) as handle:
            stream_type = _XTEAStream if enc == Encryption.XTEA else _BTEAStream
            stream = stream_type(handle, key)
            if not pure_python:
                _wrap_c_crypto(stream)
            try:
                if mode in (Mode.ReadText, Mode.WriteText):
                    stream = io.TextIOWrapper(stream, **kwargs)
                elif mode == Mode.ReadBinary:
                    stream = io.BufferedReader(stream, **kwargs)
                elif mode == Mode.WriteBinary:
                    stream = io.BufferedWriter(stream, **kwargs)
                else:
                    raise RuntimeError()
                yield stream
            except:
                raise
            finally:
                stream.close()

def determine(filename : Union[str, bytes, PathLike]) -> Encryption:
    with open(filename, "rb") as fo:
        try:
            magic = fo.read(12)
        except:
            return Encryption.Unspecified
        else:
            return _Encryptions.get(magic, Encryption.Unspecified)

# Benchmarking code ahoy!
if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="encryption benchmark utility")
    parser.add_argument("-e", "--encryption", type=Encryption, default=Encryption.BTEA, help="encryption type")
    parser.add_argument("-r", "--roundtrip", action="store_true", default=False, help="read back in the written data")
    parser.add_argument("-n", "--number", type=int, default=5, help="number of iterations")
    parser.add_argument("-p", "--pure", action="store_true", help="use pure python crypto")
    args = parser.parse_args()

    def test():
        kwargs = dict(enc=args.encryption, key=key, pure=args.pure)
        with stream(path, Mode.WriteBinary, **kwargs) as s:
            s.write(data)
        if args.roundtrip:
            with stream(path, Mode.ReadBinary, **kwargs) as s:
                s.read()

    import tempfile
    import timeit

    path = Path(tempfile.mktemp(suffix=".fni"))
    key = "31415926535897932384626433832795" if args.encryption == Encryption.BTEA else None
    data = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 0] * 50000) # .5 metric megabyte

    print(f"Here we go... Running it {args.number:n} time(s).")
    try:
        time = timeit.timeit(stmt="test()", number=args.number, globals=globals())
        per_iter = time / args.number
        print(f"Total: {time:.05} s ({args.number:n} iterations), {per_iter:.05} s per iteration.")
    finally:
        path.unlink()
