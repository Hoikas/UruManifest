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

import codecs
import logging
import itertools
import os
from pathlib import Path, PureWindowsPath
from PyHSPlasma import *

import manifest
import utils

_utf16 = codecs.lookup("utf-16_le")

class MOSS(manifest.ManifestDB):
    @classmethod
    def delete_manifests(cls, path, *manifests):
        for name in manifests:
            out_path = path.joinpath(name).with_suffix(".mbm")
            if out_path.is_file():
                logging.debug(f"Deleting manifest '{out_path.name}'")
                out_path.unlink()

    @classmethod
    def delete_lists(cls, path, *lists):
        for name, _ in lists:
            out_path = path.joinpath(name).with_suffix(".mbam")
            if out_path.is_file():
                logging.debug(f"Deleting secure list '{out_path.name}'")
                out_path.unlink()

    @classmethod
    def load_db(cls, mfs_path, list_path): 
        manifests = { i.stem: list(cls.read_manifest(i)) for i in mfs_path.glob("*.mbm") }
        lists = cls._read_lists(list_path)
        return manifests, lists

    @classmethod
    def _read_wstr(cls, s):
        def _read_wchars():
            while True:
                wc = s.readShort()
                if not wc:
                    break
                yield wc

        buf = itertools.chain(*(i.to_bytes(length=2, byteorder="little") for i in _read_wchars()))
        return _utf16.decode(bytes(buf))[0]

    @classmethod
    def _read_int(cls, s):
        buf = s.read(4)
        assert s.readShort() == 0
        return int.from_bytes(buf, byteorder="big")

    @classmethod
    def _read_lists(cls, path):
        value = {}
        for i in path.glob("*.mbam"):
            for entry in cls.read_list(i):
                entries = value.setdefault((i.stem, entry.file_name.suffix[1:]), [])
                entries.append(entry)
        return value

    @classmethod
    def read_list(cls, path):
        logging.debug(f"Reading secure list: {path}")

        with hsFileStream().open(path, fmRead) as s:
            if not s.size:
                logging.error(f"List '{path.name}' is empty!")
                return

            while s.pos < s.size:
                try:
                    entry = manifest.ListEntry()
                    entry.file_name = Path(PureWindowsPath(cls._read_wstr(s)))
                    entry.file_size = cls._read_int(s)
                except Exception as e:
                    logging.error(f"Malformed list '{path.name}' entry {i}")
                    logging.error("Bailing on secure list!")
                    # Since we failed, we are in an indeterminant state -- bail.
                    return
                else:
                    logging.trace(manifest.pformat(entry))
                    yield entry

    @classmethod
    def read_manifest(cls, path):
        logging.debug(f"Reading manifest: {path.name}")

        with hsFileStream().open(path, fmRead) as s:
            if not s.size:
                logging.error(f"Manifest '{path.name}' is empty!")
                return

            num_entries = s.readInt()
            logging.trace(f"{num_entries} entries")
            for i in range(num_entries):
                try:
                    length = s.readInt()
                except IOError:
                    logging.error(f"Manifest '{path.name}' unexpected EOF at entry {i}.")
                    return
                else:
                    endpos = s.pos + length

                try:
                    entry = manifest.ManifestEntry()
                    entry.file_name = Path(PureWindowsPath(cls._read_wstr(s)))
                    entry.download_name = Path(PureWindowsPath(cls._read_wstr(s)))
                    entry.file_hash = cls._read_wstr(s)
                    entry.download_hash = cls._read_wstr(s)
                    entry.file_size = cls._read_int(s)
                    entry.download_size = cls._read_int(s)
                    entry.flags = cls._read_int(s)
                except Exception as e:
                    logging.error(f"Malformed manifest '{path.name}' entry {i}")
                else:
                    logging.trace(manifest.pformat(entry))
                    if s.pos != endpos:
                        logging.warning(f"Hmmm... Manifest '{path.name}' entry {i} underrun.")
                    yield entry
                finally:
                    if s.pos != endpos:
                        s.seek(endpos)

    @classmethod
    def _write_wstr(cls, s, value):
        if isinstance(value, os.PathLike):
            value = str(value)
        buf = _utf16.encode(value)[0]
        s.write(buf)
        s.writeShort(0)

    @classmethod
    def _write_int(cls, s, value):
        buf = value.to_bytes(length=4, byteorder="big")
        s.write(buf)
        s.writeShort(0)

    @classmethod
    def _write_droid_key(cls, path, droid_key):
        out_path = path.joinpath("encryption.key")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logging.debug(f"Writing NTD key: {out_path}")

        with hsFileStream().open(out_path, fmWrite) as s:
            for key in droid_key:
                s.write(key.to_bytes(length=4, byteorder="big"))

    @classmethod
    def write_list(cls, path, name, entries):
        out_path = path.joinpath(name).with_suffix(".mbam")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        logging.debug(f"Writing secure list: {out_path}")

        with hsFileStream().open(out_path, fmWrite) as s:
            for entry in entries:
                cls._write_wstr(s, entry.file_name)
                cls._write_int(s, entry.file_size)

    @classmethod
    def write_lists(cls, path, droid_key, contents):
        cls._write_droid_key(path, droid_key)
        for dir_name, _ in contents.keys():
            # Nuke the extension and just use the directory name
            entries = itertools.chain(*(value for key, value in contents.items() if key[0] == dir_name))
            cls.write_list(path, dir_name, entries)

    @classmethod
    def write_manifest(cls, path, name, entries):
        out_path = path.joinpath(name).with_suffix(".mbm")
        logging.debug(f"Writing manifest: {out_path}")

        # Manifest entries are provided by a generator, so collect them here.
        entries = tuple(entries)

        with hsFileStream().open(out_path, fmCreate) as s:
            s.writeInt(len(entries))
            for entry in entries:
                entryS = hsRAMStream()
                cls._write_wstr(entryS, PureWindowsPath(entry.file_name))
                cls._write_wstr(entryS, PureWindowsPath(entry.download_name))
                cls._write_wstr(entryS, entry.file_hash)
                cls._write_wstr(entryS, entry.download_hash)
                cls._write_int(entryS, entry.file_size)
                cls._write_int(entryS, entry.download_size)
                cls._write_int(entryS, entry.flags & 0xFFFF)

                s.writeInt(entryS.size)
                s.write(entryS.buffer)

