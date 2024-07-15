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

from collections import namedtuple
from typing import NamedTuple
import enum

from encryption import Encryption
import py2constants

client_sdl = frozenset((
    "AGMaster",
    "avatar",
    "avatarPhysical",
    "CloneMessage",
    "clothing",
    "Layer",
    "MorphSequence",
    "ParticleSystem",
    "physical",
    "Responder",
    "Sound",
    "XRegion",
))

crypt_types = {
    ".age": Encryption.XTEA,
    ".csv": Encryption.XTEA,
    ".fni": Encryption.XTEA,
    ".pak": Encryption.BTEA,
    ".sdl": Encryption.BTEA,
}

# All gather sections that list installer prerequisites
gather_installers = frozenset(("prereq", "prereq64"))
mac_bundles = frozenset(("macBundleInternal", "macBundleExternal"))

class _manifests(NamedTuple):
    thin: str
    patcher: str
    full: str


gather_manifests = {
    # Windows
    "external": _manifests("ThinExternal", "ExternalPatcher", "External"),
    "external64": _manifests("ThinExternal64", "ExternalPatcher64", "External64"),
    "internal": _manifests("ThinInternal", "InternalPatcher", "Internal"),
    "internal64": _manifests("ThinInternal64", "InternalPatcher64", "Internal64"),
    "prereq": _manifests(None, "DependencyPatcher", None),
    "prereq64": _manifests(None, "DependencyPatcher64", None),

    # Legacy -- to be deleted??? -- TransGaming Cider Wrapper (macOS)
    "mac": _manifests(None, None, "macExternal"),
    "macInternal": _manifests("MacThinInternal", None, "MacInternal"),
    "macExternal": _manifests("MacThinExternal", None, "MacExternal"),
    "macBundleInternal": _manifests("MacThinInternal", None, "MacInternal"),
    "macBundleExternal": _manifests("MacThinExternal", None, "MacExternal"),
}

class _directorytuple(NamedTuple):
    client_directory: str
    server_directory: str


gather_lut = {
    "data": _directorytuple("dat", "data"),
    "sdl": _directorytuple("SDL", "scripts"),
    "python": _directorytuple("Python", "scripts"),
    "sfx": _directorytuple("sfx", "audio"),
    "avi": _directorytuple("avi", "video"),

    "external": _directorytuple("", "client/windows_ia32/external"),
    "external64": _directorytuple("", "client/windows_amd64/external"),
    "internal": _directorytuple("", "client/windows_ia32/internal"),
    "internal64": _directorytuple("", "client/windows_amd64/internal"),
    "macexternal": _directorytuple("", "client/mac/external"),
    "macinternal": _directorytuple("", "client/mac/internal"),
    "macbundleexternal": _directorytuple("", "client/mac/external"),
    "macbundleinternal": _directorytuple("", "client/mac/internal"),
    "mac": _directorytuple("", "client/macos_ia32/external"),
    "prereq": _directorytuple("", "dependencies/windows_ia32"),
    "prereq64": _directorytuple("", "dependencies/windows_amd64"),
}

# HAX: Copy the contents of the value's manifest to the end of the key's manifest
manifest_copy_from = {
    "ExternalPatcher": ("DependencyPatcher",),
    "ExternalPatcher64": ("DependencyPatcher64",),
    "InternalPatcher": ("DependencyPatcher",),
    "InternalPatcher64": ("DependencyPatcher64",),
}

class ManifestFlags(enum.IntFlag):
    sound_cache_split = (1<<0)
    sound_stream_compressed = (1<<1)
    sound_cache_stereo = (1<<2)
    file_gzipped = (1<<3)
    installer = (1<<4)
    bundle = (1<<5)

    # Internal flags
    python_file_mod = (1<<16)
    script = (1<<17)
    dirty = (1<<18)
    dont_encrypt = (1<<19)
    consumable = (1<<20)
    optional = (1<<21)


naughty_path_sequences = {"..", "../", "..\\"}

patcher_lut = {
    "external": "UruLauncher.exe",
    "external64": "UruLauncher.exe",
    "internal": "plUruLauncher.exe",
    "internal64": "plUruLauncher.exe",
}

@enum.unique
class PfmGlue(enum.IntEnum):
    indeed = py2constants.PFM_INDEED
    not_a_modifier = py2constants.PFM_NOT_A_MODIFIER
    no_class = py2constants.PFM_NO_CLASS
    ast_crashed = py2constants.PFM_AST_CRASHED


@enum.unique
class PyToolsResultCodes(enum.IntEnum):
    success = py2constants.TOOLS_SUCCESS
    tools_crashed = py2constants.TOOLS_CRASHED
    invalid_command = py2constants.TOOLS_INVALID_COMMAND
    traceback = py2constants.TOOLS_MODULE_TRACEBACK
    file_not_found = py2constants.TOOLS_FILE_NOT_FOUND


server_subdirectory_lut = {
    ".age": "age",
    ".csv": "csv",
    ".fni": "fni",
    ".loc": "localization",
    ".p2f": "font",
    ".pak": "python_pak",
    ".prp": "prp",
    ".py": "python_code",
    ".sdl": "sdl",
}

workflow_lut = {
    "plasma-windows-x86-external-release": "external",
    "plasma-windows-x64-external-release": "external64",
    "plasma-windows-x86-internal-release": "internal",
    "plasma-windows-x64-internal-release": "internal64",
    "plasma-macos-x64-internal-release": "macInternal",
    "plasma-macos-x64-external-release": "macExternal",
}
