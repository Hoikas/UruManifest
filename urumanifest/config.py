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

import configparser
from dataclasses import dataclass
import functools
import logging
from pathlib import Path

@dataclass
class _ConfigItem:
    default : str
    comment : str = ""

    def __repr__(self):
        return self.default


_defaults = {
    "output": {
        "lists": _ConfigItem("~/uru/output/authsrv",
            "Directory to output legacy auth server \"secure downloads\". These are requested by\n"
            "legacy clients and some tools (eg MoulKI). Note that MOSS uses a funky directory structure\n"
            "for these downloads to allow different accounts to download different sets of data.\n"
            "All clients should use the same Python and SDL files, therefore, it is expected that this\n"
            "should be set to the subdirectory named \"default\" of the \"auth_download_dir\" specified\n"
            "in moss.cfg."),

        "manifests": _ConfigItem("~/uru/output/filesrv",
            "Directory to output file server manifests and downloads."),
    },

    "python": {
        "major": _ConfigItem("2", "Major version of the Python interpreter used by the game client."),
        "minor": _ConfigItem("7", "Minor version of the Python interpreter used by the game client."),
        "path": _ConfigItem("", "Path to the Python interpreter used by the game client."),
    },

    "server": {
        "age_directory": _ConfigItem("",
            "Directory to copy the decrypted *.age files to for use by the server."),

        "droid_key": _ConfigItem("31415926535897932384626433832795",
            "64-byte integer hex string used to encrypt the client Python and SDL."),

        "sdl_directory": _ConfigItem("",
            "Directory to copy the decrypted *.sdl files to for use by the server.\n"
            "Note that MOSS uses a funky directory structure that organizes SDLs into subdirectories...\n"
            "The easiest way to work around this design flaw is to set this to the subdirectory named\n"
            "\"SDL/common\" of the \"game_data_dir\" specified in moss.cfg."),

        "secure_manifest": _ConfigItem("true",
            "Should the \"so-called\" secure files be served over the filesrv? This allows the skipping of the\n"
            "mandatory download at game launch. Note that MOSS servers may have difficulty with this option."),

        "type": _ConfigItem("dirtsand"),
    },

    "source": {
        "data_path": _ConfigItem("~/uru/game_data",
            "This is the path to the directory containing the game's avi, dat, and sfx subdirectories.\n"
            "This typically points to the \"compiled\" subdirectory of the moul-assets repository."),

        "gather_path": _ConfigItem("~/uru/gather_data",
            "This is the path to the directory containing gather-build assets to include."),

        "scripts_path": _ConfigItem("~/uru/scripts",
            "This is the path to the moul-scripts repository used by this game."),
    }
}

def _get_path(value, must_exist=None, is_dir=None, mkdir=False):
    assert (must_exist is not None)
    if not must_exist and not value:
        return None

    p = Path(value).expanduser()
    if must_exist:
        exists = p.is_dir if is_dir else p.exists
        if not exists():
            raise ValueError(f"Path '{p}' does not exist.")
    elif mkdir:
        if is_dir is True:
            p.mkdir(parents=True, exist_ok=True)
        elif is_dir is False:
            p.parent.mkdir(parents=True, exist_ok=True)
    return p

_converters = {
    "path": functools.partial(_get_path, must_exist=True),
    "infilepathopt": functools.partial(_get_path, must_exist=False, is_dir=False),
    "indirpath": functools.partial(_get_path, must_exist=True, is_dir=True),
    "indirpathopt": functools.partial(_get_path, must_exist=False, is_dir=True),
    "outdirpath": functools.partial(_get_path, must_exist=False, is_dir=True, mkdir=True),
}

_header = """
;    This file is part of UruManifest
;
;    UruManifest is free software: you can redistribute it and/or modify
;    it under the terms of the GNU General Public License as published by
;    the Free Software Foundation, either version 3 of the License, or
;    (at your option) any later version.
;
;    UruManifest is distributed in the hope that it will be useful,
;    but WITHOUT ANY WARRANTY; without even the implied warranty of
;    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;    GNU General Public License for more details.
;
;    You should have received a copy of the GNU General Public License
;    along with UruManifest.  If not, see <http://www.gnu.org/licenses/>.

"""

def dump_default_config(config_path):
    with config_path.open("w") as fp:
        fp.write(_header.lstrip())
        for section, values in _defaults.items():
            fp.write(f"\n[{section}]\n\n")
            for option_name, option_value in values.items():
                if option_value.comment:
                    for comment_line in option_value.comment.split("\n"):
                        fp.write(f"; {comment_line}\n")
                fp.write(f"{option_name} = {option_value}\n\n")

def read_config(config_path):
    parser = configparser.ConfigParser(converters=_converters)
    parser.read_dict(_defaults)
    if config_path.is_file():
        parser.read(config_path)
    else:
        logging.critical(f"Could not read configuration from '{config_path}'!")
    return parser
