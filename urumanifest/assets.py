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

from collections import defaultdict
from dataclasses import dataclass, field
import itertools
import json
import logging
from pathlib import Path, PureWindowsPath
import pickle
import subprocess
import sys
from typing import Any, Dict, Iterable, Optional, Sequence, Set, Tuple, Union

from constants import *
import manifest
import utils

@dataclass
class Asset:
    gather_path : Path = Path()
    source_path : Path = Path()
    client_path : Path = Path()
    categories : Sequence[str] = field(default_factory=set)


@dataclass(frozen=True)
class AssetEntry:
    file_hash: str
    download_hash: str
    file_size: int
    download_size: int


@dataclass
class AssetDatabase:
    assets: Dict[Path, AssetEntry]
    manifests: Dict[str, Sequence[manifest.ManifestEntry]]
    lists: Dict[Tuple[str, str], Sequence[manifest.ListEntry]]


class AssetError(Exception):
    pass


def build_server_path(client_path: Path, server_directory: Optional[str] = None,
                      category: Optional[str] = None) -> Path:

    # If no category or server directory was manually specified, we can possibly figure it out
    # based on the asset's client path. The root directory is where life gets problematic.
    if server_directory is None and category is None:
        if not client_path.parent.name:
            raise ValueError("server_directory or category must be specified for files in the client root")
        category, server_directory = next(
            ((category, directories.server_directory) for category, directories in gather_lut.items() if directories.client_directory.lower() == client_path.parent.name.lower()),
            (None, None)
        )
        assert category and server_directory, f"build_server_path: {client_path} {category} {server_directory}"

    if not server_directory:
        server_directory = gather_lut[category].server_directory
    server_subdirectory = server_subdirectory_lut.get(client_path.suffix.lower(), "")

    # Something of a hack, but we lop off the first subdirectory of any path so we don't lose
    # any of the Python package info.
    parts = client_path.parts[1:] if len(client_path.parts) > 1 else client_path.parts
    return Path(server_directory, server_subdirectory, *parts)

def lookup_asset(source_assets: Dict[Path, Asset], client_path: Path,
                 server_directory: Optional[str] = None,
                 category: Optional[str] = None) -> Union[Tuple[Path, Asset], Tuple[None, None]]:
    # Simple by path lookup first
    if server_directory or category:
        server_path = build_server_path(client_path, server_directory, category)
        checked_asset = source_assets.get(server_path)
        if checked_asset is not None and checked_asset.client_path == client_path:
            return server_path, checked_asset

    # Slow bruteforce search
    lookup = ((sp, asset) for sp, asset in source_assets.items() if asset.client_path == client_path)
    server_path, asset = next(lookup, (None, None))
    warn_count = sum((1 for _ in lookup))
    if warn_count:
        logging.warning(f"Asset lookup for {client_path} resulted in {warn_count + 1} results, expected 1. Tighten up this search, ok?")
    return server_path, asset

def load_asset_database(mfs_path: Path, list_path: Path, db_type: str) -> AssetDatabase:
    logging.info("Reading asset database...")

    db_cls = manifest.ManifestDB.get(db_type)
    if db_cls is None:
        raise AssetError(f"Invalid asset db type '{db_type}'")
    manifests, lists = db_cls.load_db(mfs_path, list_path)

    # Merge assets into case insensitive dict and verify hashes. Use a custom type so we don't
    # compare the file flags, which can legally differ (eg sound decompression)
    assets, conflicts = {}, 0
    for mfs_name, mfs_entries in manifests.items():
        for mfs_entry in mfs_entries:
            mfs_asset = AssetEntry(mfs_entry.file_hash, mfs_entry.download_hash,
                                   mfs_entry.file_size, mfs_entry.download_size)

            # HAX: lop off the spurious compression extension.
            # TODO: if we ever add support for something other than .gz, we will need to revisit this.
            if len(mfs_entry.download_name.suffixes) > 1 and mfs_entry.download_name.suffix.lower() == ".gz":
                server_path = mfs_entry.download_name.with_suffix("")
            else:
                server_path = mfs_entry.download_name

            if assets.setdefault(server_path, mfs_asset) != mfs_asset:
                logging.warn(f"CONFLICT: '{server_path}'")
                conflicts += conflicts
                assets[mfs_entry.download_name] = None
    if conflicts:
        logging.warn(f"Discarded {conflicts} conflicting asset entries!")
    logging.trace(f"Loaded {len(assets)} asset entries from {len(manifests)} manifests, with {len(lists)} legacy auth-lists.")

    return AssetDatabase(assets, manifests, lists)

def load_gather_assets(*paths: Path) -> Dict[Path, Asset]:
    logging.info(f"Gathering assets...")

    gathers = defaultdict(Asset)

    def append_asset(gather_path: Path, asset_path: Path, client_path: Path, server_path: Path, category: str) -> int:
        # HACK: no json files may be an asset due to their being control files...
        if client_path.suffix.lower() == ".json":
            return 0

        if server_path in gathers and gathers[server_path].client_path != client_path:
            raise AssetError(f"Gather asset conflict '{server_path}' (providing client asset: '{client_path}')")
        if asset_path.exists():
            gather = gathers[server_path]
            gather.gather_path = gather_path
            gather.source_path = asset_path
            gather.client_path = client_path
            gather.categories.add(category)
            logging.trace(gather)
            return 1
        else:
            logging.error(f"Asset not available: {asset_path}")
            return 0

    def handle_control_assets(gather_path: Path, source_path: Path, client_directory: str,
                              server_directory: str, category: str, gather_assets: Dict[str, str]) -> int:
        num_assets = 0
        if "*" in gather_assets:
            gather_assets.remove("*")
            if gather_assets:
                logging.warning(f"Wildcard and explicit file list used in section '{category}'. Hmmm...")
            for i in source_path.iterdir():
                if not i.is_file():
                    continue
                client_path = Path(client_directory, i.relative_to(source_path))
                server_path = build_server_path(client_path, server_directory)
                num_assets += append_asset(gather_path, i, client_path, server_path, category)

        for i in (PureWindowsPath(i) for i in gather_assets):
            if any((j in i.name for j in naughty_path_sequences)):
                logging.error(f"SECURITY: ATTEMPT TO ESCAPE CWD BY: {source_path}")
                continue

            # NOTE: directory structure of the gather package will be pitched
            asset_path = source_path.joinpath(i)
            client_path = Path(client_directory, i.name)
            server_path = build_server_path(client_path, server_directory)
            num_assets += append_asset(gather_path, asset_path, client_path, server_path, category)
        return num_assets

    def handle_control_folder(gather_path: Path, source_path: Path, subdir_name: str, subcontrol_name: str) -> int:
        if any((j in subcontrol_name for j in naughty_path_sequences)):
            logging.error(f"SECURITY: ATTEMPT TO ESCAPE CWD BY: {source_path}")
            return 0
        subcontrol_path = source_path.joinpath(subdir_name, subcontrol_name)
        return handle_gather_package(gather_path, subcontrol_path)

    def handle_gather_package(gather_path: Path, control_path: Optional[Path] = None) -> int:
        if control_path is None:
            source_path = gather_path

            # Potential GOTCHA: .json extension must be lowercase...
            control_paths = source_path.glob("*.json")
            control_path = next(control_paths, None)
            if control_path is None:
                logging.error(f"Control file missing for gather package '{gather_path.name}'")

            # No way to differentiate multiple JSON files in the gather root.
            if next(control_paths, None) is not None:
                logging.warning(f"Multiple control file candidates for gather package '{gather_path.name}'")
        else:
            source_path = control_path.parent

        if not control_path.is_file():
            logging.error(f"Control file '{control_path}' does not exist!")
            return 0

        logging.trace(f"Reading Gather control file '{control_path}'...")
        with control_path.open("r") as fp:
            gather_control = json.load(fp)

        num_assets = 0
        for key, value in gather_control.items():
            client_directory, server_directory = gather_lut.get(key.lower(), (None, None))
            if client_directory is None:
                if key.lower() != "folders":
                    logging.warning(f"Invalid section '{key}' in control file '{control_path}'")
                    continue
                for subdir_name, subcontrol_name in value.items():
                    num_assets += handle_control_folder(gather_path, source_path, subdir_name, subcontrol_name)
            else:
                num_assets += handle_control_assets(gather_path, source_path, client_directory, server_directory, key, value)
        return num_assets

    for i in (Path(i) for i in paths):
        if i.is_file() and i.suffix.lower() == ".json":
            gather_path, control_path = i.parent, i
        elif i.is_dir():
            gather_path, control_path = i, None
        else:
            logging.warning(f"Skipping unknown gather path type {i.name}")
            continue

        logging.trace(f"Loading Gather package '{i.name}'...")
        num_assets = handle_gather_package(gather_path, control_path)
        logging.trace(f"Loaded {num_assets} from Gather package '{i.name}'")

    logging.debug(f"Loaded {len(gathers)} assets from gathers.")
    return gathers

def load_prebuilt_assets(data_path: Path, scripts_path: Path, py_exe: Path) -> Dict[Path, Asset]:
    logging.info("Loading prebuilt assets...")

    prebuilts = {}

    def find_python_dist_packages() -> Optional[Path]:
        if py_exe is None or not py_exe.exists():
            logging.critical("Python is not available???")
            return None

        py_tools_path = utils.find_python2_tools()
        if not py_tools_path.exists():
            raise AssetError("Could not find Python2 helper module")

        proc = subprocess.Popen(
            (str(py_exe), str(py_tools_path)),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=False
        )
        buf = pickle.dumps({"cmd": "get_python_lib"}, 0)

        # Whoosh... off it goes...
        stdout, stderr = proc.communicate(buf)
        if stderr:
            logging.error(stderr.decode(sys.getdefaultencoding()))
        if stdout:
            result = pickle.loads(stdout, encoding="bytes")
            if result["returncode"] == PyToolsResultCodes.success:
                return Path(result["python_lib"].decode("utf-8").strip())

            # If we're still here, py2tools crashed.
            logging.critical(f"Py2Tools crashed fetching the stdlib")
            for i in result.get("traceback", []):
                try:
                    logging.critical(i.decode("utf-8").strip())
                except UnicodeError:
                    pass

            raise AssetError("Failed to fetch the Python stdlib")

    def handle_prebuilts(category: str, server_directory: str, base_path: Path,
                         source_path: Optional[Path] = None, prefix_path: Path = Path(),
                         follow_dirs: bool = True, skip_dirs: Set[str] = set()) -> None:
        if source_path is None:
            source_path = base_path

        for i in source_path.iterdir():
            if i.is_file():
                client_path = prefix_path.joinpath(i.relative_to(base_path))
                server_path = build_server_path(client_path, server_directory, category)
                prebuilts[server_path] = Asset(None, i, client_path, set((category,)))
            elif i.is_dir() and follow_dirs and i.stem not in skip_dirs:
                handle_prebuilts(category, server_directory, base_path, i, prefix_path=prefix_path)

    for category, (client_directory, server_directory) in gather_lut.items():
        if not client_directory:
            continue
        data_source_path = data_path.joinpath(client_directory)
        scripts_source_path = scripts_path.joinpath(client_directory)
        if not data_source_path.is_dir() and not scripts_source_path.is_dir():
            raise AssetError(f"'{client_directory}' missing from sources.")

        # The age files in the scripts directory tend to list "dead" pages that only exist
        # on Cyan's AssMan machine. So, we prefer the compiled data...
        if scripts_source_path.is_dir():
            handle_prebuilts(category, server_directory, scripts_path, scripts_source_path)
        if data_source_path.is_dir():
            handle_prebuilts(category, server_directory, data_path, data_source_path)

    # We used to handle the client root directory here. However, per the README:
    #   "UruManifest is currently unable to automate detection of client executables, libraries,
    #    and redistributables."
    # so that action was spurious. Even more so now that we attempt to fetch both x86 and x64
    # clients from GitHub Actions. So fuggedaboutit.

    # Load the python standard library in, if needed.
    if not scripts_path.joinpath("Python", "system").is_dir():
        logging.debug("Using build system's python stdlib...")
        stdlib_path = find_python_dist_packages()
        if stdlib_path is not None and stdlib_path.is_dir():
            logging.debug(f"... from {stdlib_path}")
            handle_prebuilts("python", "", stdlib_path, prefix_path=Path("Python", "system"),
                             skip_dirs={"__pycache__", "site-packages", "asyncio", "concurrent",
                                        "ctypes", "curses", "dbm", "distutils", "ensurepip", "email",
                                        "html", "http", "idlelib", "lib2to3", "msilib", "multiprocessing",
                                        "pydoc_data", "sqlite3", "test", "tkinter", "turtledemo",
                                        "unittest", "urllib", "venv", "wsgiref", "xml", "xmlrpc"})
        else:
            logging.critical(f"Python stdlib path is invalid: {stdlib_path}")

    logging.debug(f"Loaded {len(prebuilts)} prebuilt assets.")
    return prebuilts

def merge_asset_dicts(prebuilts: Dict[Path, Asset], gathers: Dict[Path, Asset]) -> Dict[Path, Asset]:
    logging.info("Merging staged assets...")
    assets = {}
    assets.update(prebuilts)
    assets.update(gathers)
    logging.debug(f"Total known assets: {len(assets)}")
    return assets

def nuke_dead_manifests(cached_manifests: Dict[str, manifest.ManifestEntry],
                        cached_lists: Dict[Tuple[str, str], Sequence[manifest.ListEntry]],
                        staged_manifests: Dict[str, Set[Path]],
                        staged_lists: Dict[Tuple[str, str], Set[Path]],
                        mfs_path: Path, list_path: Path, db_type: str) -> None:
    logging.info("Nuking defunct database files...")

    dead_mfs = frozenset(cached_manifests.keys()) - frozenset(staged_manifests.keys())
    dead_lists = frozenset(cached_lists.keys()) - frozenset(staged_lists.keys())

    db_cls = manifest.ManifestDB.get(db_type)
    if dead_mfs:
        db_cls.delete_manifests(mfs_path, *dead_mfs)
    if dead_lists:
        db_cls.delete_lists(list_path, *dead_lists)

def save_asset_database(cached_manifests: Dict[str, Sequence[manifest.ManifestEntry]],
                        cached_lists: Dict[Tuple[str, str], Sequence[manifest.ListEntry]],
                        staged_assets: Dict[Path, manifest.ManifestEntry],
                        staged_manifests: Dict[str, Set[Path]],
                        staged_lists: Dict[Tuple[str, str], Set[Path]],
                        mfs_path: Path, list_path: Path, db_type: str, droid_key):
    logging.info("Saving asset database...")

    def dump_set(set_type: str, the_set: Set[str]) -> None:
        if the_set:
            logging.debug(f"--- BEGIN {set_type} ASSETS ---")
            for i in the_set:
                logging.debug(i)
            logging.debug(f"--- END {set_type} ASSETS ---")

    def iter_manifest(mfs_name: str):
        for server_path in staged_manifests[mfs_name]:
            yield staged_assets[server_path]

        # Some manifests may also want to hijack the contents of another manifest.
        # We do that here because, in some cases (eg ExternalPatcher), order is important.
        # NOTE: no recursive copying. just no. *shudder*
        for copy_mfs_name in manifest_copy_from.get(mfs_name, []):
            for server_path in staged_manifests.get(copy_mfs_name, []):
                yield staged_assets[server_path]

    def iter_secure_list(key: Tuple[str, str]):
        for server_path in staged_lists[key]:
            staged_asset = staged_assets[server_path]
            yield manifest.ListEntry(staged_asset.file_name, staged_asset.file_size)

    def is_manifest_dirty(name: str,
                          cached_manifest: Sequence[manifest.ManifestEntry],
                          staged_manifest: Sequence[manifest.ManifestEntry]) -> bool:
        assert cached_manifest or staged_manifest, "Got a pair of deleted manifests?"

        cached_contents = frozenset(i.file_name for i in cached_manifest)
        staged_contents = frozenset(i.file_name for i in staged_manifest)
        dirty_contents = frozenset(i.file_name for i in staged_manifest if i.flags & ManifestFlags.dirty)
        added_contents = staged_contents - cached_contents
        deleted_contents = cached_contents - staged_contents

        if dirty_contents or added_contents or deleted_contents:
            status = "new" if not cached_manifest else "dirty"
            logging.debug(f"Manifest '{name}' is {status}: {len(added_contents)} added, {len(dirty_contents)} changed, {len(deleted_contents)} deleted.")
            dump_set("ADDED", added_contents)
            dump_set("CHANGED", dirty_contents)
            dump_set("DELETED", deleted_contents)
            return True
        return False

    db_cls = manifest.ManifestDB.get(db_type)
    for i in staged_manifests.keys():
        staged_manifest = set(iter_manifest(i))
        if is_manifest_dirty(i, cached_manifests.get(i, []), staged_manifest):
            db_cls.write_manifest(mfs_path, i, staged_manifest)

    # Note... lists are always dirty because they do not contain file hashes.
    lists_contents = { key: tuple(iter_secure_list(key)) for key in staged_lists.keys() }
    db_cls.write_lists(list_path, droid_key, lists_contents)
