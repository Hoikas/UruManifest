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

import logging
from pathlib import Path, PureWindowsPath

import manifest

class Dirtsand(manifest.ManifestDB):
    @classmethod
    def delete_manifests(cls, path, *manifests):
        for name in manifests:
            out_path = path.joinpath(name).with_suffix(".mfs")
            if out_path.is_file():
                logging.debug(f"Deleting manifest '{out_path.name}'")
                out_path.unlink()

    @classmethod
    def delete_lists(cls, path, *lists):
        for key in lists:
            out_path = path.joinpath("{0}_{1}.list".format(*key))
            if out_path.is_file():
                logging.debug(f"Deleting secure list '{out_path.name}'")
                out_path.unlink()

    @classmethod
    def load_db(cls, mfs_path, list_path): 
        manifests = { i.stem: list(cls.read_manifest(i)) for i in mfs_path.glob("*.mfs") }
        lists = cls._read_lists(list_path)
        return manifests, lists

    @classmethod
    def _read_lists(cls, path):
        value = {}
        for i in path.glob("*.list"):
            try:
                directory_name, extension = i.stem.split("_", 1)
            except:
                logging.error(f"Malformed list filename '{i.name}'")
            else:
                value[(directory_name, extension)] = tuple(cls.read_list(i))
        return value

    @classmethod
    def read_list(cls, path):
        logging.debug(f"Reading secure list: {path}")
        with path.open(mode="r") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                data = line.split(',')
                if len(data) != 2:
                    logging.error(f"Malformed secure list '{path}' line #{i}")
                    continue

                try:
                    entry = manifest.ListEntry()
                    entry.file_name = Path(PureWindowsPath(data[0]))
                    entry.file_size = int(data[1])
                except Exception as e:
                    logging.error(f"Malformed secure list '{path}' line #{i}")
                else:
                    logging.trace(manifest.pformat(entry))
                    yield entry

    @classmethod
    def write_list(cls, path, name, entries):
        out_path = path.joinpath(name).with_suffix(".list")
        logging.debug(f"Writing secure list: {out_path}")
        with out_path.open("w") as f:
            for i in entries:
                fn = PureWindowsPath(i.file_name)
                ln = f"{fn},{i.file_size}"
                logging.trace(ln)
                f.write(f"{ln}\n")

    @classmethod
    def write_lists(cls, path, droid_key, contents):
        for key, entries in contents.items():
            cls.write_list(path, "{0}_{1}.list".format(*key), entries)

    @classmethod
    def read_manifest(cls, path):
        logging.debug(f"Reading manifest: {path}")
        with path.open(mode="r") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                data = line.split(',')
                if len(data) != 7:
                    logging.error(f"Malformed manifest '{path}' line #{i}")
                    continue

                try:
                    entry = manifest.ManifestEntry()
                    entry.file_name = Path(PureWindowsPath(data[0]))
                    entry.download_name = Path(PureWindowsPath(data[1]))
                    entry.file_hash = data[2]
                    entry.download_hash = data[3]
                    entry.file_size = int(data[4])
                    entry.download_size = int(data[5])
                    entry.flags = int(data[6])
                except Exception as e:
                    logging.error(f"Malformed manifest '{path}' line #{i}")
                else:
                    logging.trace(manifest.pformat(entry))
                    yield entry

    @classmethod
    def write_manifest(cls, path, name, entries):
        out_path = path.joinpath(name).with_suffix(".mfs")
        logging.debug(f"Writing manifest: {out_path}")
        with out_path.open("w") as f:
            for i in entries:
                fn, dn = PureWindowsPath(i.file_name), PureWindowsPath(i.download_name)
                ln = f"{fn},{dn},{i.file_hash},{i.download_hash},{i.file_size},{i.download_size},{int(i.flags) & 0xFFFF}"
                logging.trace(ln)
                f.write(f"{ln}\n")
