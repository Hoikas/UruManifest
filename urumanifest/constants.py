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
gather_installers = frozenset(("prereq",))

_manifests = namedtuple("GatherManifests", ("thin", "patcher", "full"))
gather_manifests = {
    # Windows (x86)
    "external": _manifests("ThinExternal", "ExternalPatcher", "External"),
    "internal": _manifests("ThinInternal", "InternalPatcher", "Internal"),
    "prereq": _manifests(None, "DependencyPatcher", None),

    # Legacy -- to be deleted??? -- TransGaming Cider Wrapper (macOS)
    "mac": _manifests(None, None, "macExternal"),
}

gather_lut = {
    "data": "dat",
    "sdl": "SDL",
    "python": "Python",
    "sfx": "sfx",
    "avi": "avi",

    "external": "",
    "internal": "",
    "mac": "",
    "prereq": "",
}

# HAX: Copy the contents of the value's manifest to the end of the key's manifest
manifest_copy_from = {
    "ExternalPatcher": ("DependencyPatcher",),
    "InternalPatcher": ("DependencyPatcher",),
}

class ManifestFlags(enum.IntFlag):
    sound_cache_split = (1<<0)
    sound_stream_compressed = (1<<1)
    sound_cache_stereo = (1<<2)
    file_gzipped = (1<<3)
    installer = (1<<4)

    # Internal flags
    python_file_mod = (1<<16)
    script = (1<<17)
    dirty = (1<<18)
    dont_encrypt = (1<<19)
    consumable = (1<<20)


naughty_path_sequences = {"..", "../", "..\\"}

patcher_lut = {
    "external": "UruLauncher.exe",
    "internal": "plUruLauncher.exe",
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
