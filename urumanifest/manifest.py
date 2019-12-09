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
from collections import defaultdict
from dataclasses import dataclass
import functools
import logging
from pathlib import Path
import pprint

from constants import *

pformat = functools.partial(pprint.pformat, indent=2, compact=True)

@dataclass
class ListEntry:
    file_name : Path = Path()
    file_size : int = 0


@dataclass
class ManifestEntry:
    file_name : Path = Path()
    download_name : Path = Path()
    file_hash : str = ""
    download_hash : str = ""
    file_size : int = 0
    download_size : int = 0
    flags : int = 0


class ManifestDB(abc.ABC):
    @classmethod
    def get(cls, db_type):
        db_type = db_type.lower()
        return next((i for i in cls.__subclasses__() if i.__name__.lower() == db_type), None)

    @classmethod
    @abc.abstractmethod
    def load_db(cls, mfs_path, list_path):
        pass

    @classmethod
    @abc.abstractmethod
    def read_list(cls, path):
        pass

    @classmethod
    @abc.abstractmethod
    def write_list(cls, path, name, entries):
        pass

    @classmethod
    @abc.abstractmethod
    def read_manifest(cls, path):
        pass

    @classmethod
    @abc.abstractmethod
    def write_manifest(cls, path, name, entries):
        pass
