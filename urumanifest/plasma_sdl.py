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

from dataclasses import dataclass, field
import enum
from io import IOBase
import logging
from os import PathLike
from pathlib import Path
import re
from typing import Iterable, List, Union

try:
    from urumanifest import encryption
except ImportError:
    import encryption

_kill_single_comments = re.compile(r"(?:#|\/\/).*$", flags=re.MULTILINE)
_kill_block_comments = re.compile(r"\/\*.*\*\/", flags=re.DOTALL)
_tokenize = re.compile(r"([\{\}\[\]\(\)\=\,\;\t\n\s])")

class VariableType(enum.Enum):
    Invalid = enum.auto()
    Int32 = enum.auto()
    Float = enum.auto()
    Bool = enum.auto()
    String = enum.auto()
    Key = enum.auto()
    Descriptor = enum.auto()
    Creatable = enum.auto()
    Double = enum.auto()
    Time = enum.auto()
    AgeTimeOfDay = enum.auto()
    Byte = enum.auto()
    Short = enum.auto()
    Vector3 = enum.auto()
    Point3 = enum.auto()
    RGB = enum.auto()
    RGBA = enum.auto()
    Quat = enum.auto()
    RGB8 = enum.auto()
    RGBA8 = enum.auto()


_VariableTypeLUT = {
    "int": VariableType.Int32,
    "float": VariableType.Float,
    "bool": VariableType.Bool,
    "string32": VariableType.String,
    "plkey": VariableType.Key,
    "creatable": VariableType.Creatable,
    "message": VariableType.Creatable,
    "double": VariableType.Double,
    "time": VariableType.Time,
    "agetimeofday": VariableType.AgeTimeOfDay,
    "byte": VariableType.Byte,
    "short": VariableType.Short,
    "vector3": VariableType.Vector3,
    "point3": VariableType.Point3,
    "rgb": VariableType.RGB,
    "rgba": VariableType.RGBA,
    "quat": VariableType.Quat,
    "quaternion": VariableType.Quat,
    "rgb8": VariableType.RGB8,
    "rgba8": VariableType.RGBA8,
}


class Variable:
    type : VariableType = VariableType.Invalid
    descriptor : Union[None, str] = None
    name : str = ""
    range : Union[None, int] = None


@dataclass
class Descriptor:
    version : Union[None, int] = None
    name : str = ""
    variables : List[Variable] = field(default_factory=list)


class Manager:
    def __init__(self, path : Union[None, str, PathLike] = None):
        self.descriptors = []
        if path is not None:
            path = Path(path)
            if path.is_dir():
                for i in path.glob("*.sdl"):
                    with encryption.stream(i, mode=encryption.Mode.ReadText, encoding="utf-8") as s:
                        self.read_descriptors(s)
            else:
                with encryption.stream(path, mode=encryption.Mode.ReadText, encoding="utf-8") as s:
                    self.read_descriptors(s)

    def __contains__(self, value : str) -> bool:
        for i in self.descriptors:
            if i.name == value:
                return True
        return False

    def find_descriptor(self, name : str) -> Union[None, Descriptor]:
        desc = None
        for i in self.find_descriptors(name):
            if desc is None or desc.version < i.version:
                desc = i
        return desc

    def find_descriptors(self, name : str) -> Iterable[Descriptor]:
        for i in self.descriptors:
            if i.name.lower() == name.lower():
                if i.name != name:
                    logging.warn(f"Matching SDL request '{name}' to '{i.name}' -- prepare for unforseen consequences.")
                yield i

    def read_descriptors(self, s : IOBase):
        class _State(enum.Enum):
            Invalid = enum.auto()
            # Got the descriptor name, no version
            StateDesc = enum.auto()
            # Next token is the version
            Version = enum.auto()
            # Parsing a variable descriptor
            Variable = enum.auto()
            VariableType = enum.auto()
            VariableName = enum.auto()
            VariableRange = enum.auto()
            VariableDefault = enum.auto()
            VariableFlags = enum.auto()


        contents = s.read()
        # Filter out comments
        contents = re.sub(_kill_single_comments, "", contents)
        contents = re.sub(_kill_block_comments, "", contents)

        tokens = (i.strip() for i in re.split(_tokenize, contents))
        state = _State.Invalid
        desc, var = None, None

        for token in (i for i in tokens if i):
            if state == _State.Invalid:
                if token.lower() == "statedesc":
                    state = _State.StateDesc
                    assert desc is None
                    desc = Descriptor()
                else:
                    raise RuntimeError(f"Unexpected token {token}")
            elif state == _State.StateDesc:
                if not desc.name:
                    desc.name = token
                elif token == "{":
                    state = _State.Version
                else:
                    raise RuntimeError(f"Unexpected token after STATEDESC: {token}")
            elif state == _State.Version:
                if token.lower() == "version" and desc.version is None:
                    continue
                else:
                    try:
                        desc.version = int(token)
                    except:
                        raise RuntimeError(f"STATEDESC {desc.name} version should be an integer, not {token}")
                    state = _State.Variable
            elif state == _State.Variable:
                ltok = token.lower()
                if ltok == "var":
                    if var is not None:
                        desc.variables.append(var)
                    var = Variable()
                    state = _State.VariableType
                elif ltok == "default":
                    state = _State.VariableDefault
                elif ltok == "defaultoption":
                    state = _State.VariableFlags
                elif ltok == "}":
                    if var is not None:
                        desc.variables.append(var)
                    self.descriptors.append(desc)
                    desc, var = None, None
                    state = _State.Invalid
                else:
                    # Purposefully not erroring due to lazy flags/default handling. Gulp
                    pass
            elif state == _State.VariableType:
                if token.startswith("$"):
                    var.type = VariableType.Descriptor
                    var.descriptor = token[1:]
                else:
                    var.type = _VariableTypeLUT.get(token.lower())
                if var.type is None:
                    raise RuntimeError(f"Variable in {desc.name}#{desc.version} of unkown type '{token}'")
                state = _State.VariableName
            elif state == _State.VariableName:
                var.name = token
                state = _State.VariableRange
            elif state == _State.VariableRange:
                if token == "[":
                    continue
                elif token == "]":
                    state = _State.Variable
                    continue
                else:
                    try:
                        var.range = int(token)
                    except:
                        raise RuntimeError(f"Variable range {desc.name}#{desc.version}->{var.name} should be an integer, not {token}")
            elif state in (_State.VariableDefault, _State.VariableFlags):
                # Who cares? Just try to find our next valid token...
                state = _State.Variable
