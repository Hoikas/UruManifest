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

from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
import itertools
import json
import logging
from pathlib import Path
from typing import Sequence

from constants import *
import manifest

@dataclass
class Asset:
    gather_path : Path = Path()
    source_path : Path = Path()
    client_path : Path = Path()
    categories : Sequence[str] = field(default_factory=set)


class AssetError(Exception):
    pass


def load_asset_database(mfs_path, list_path, db_type):
    logging.info("Reading asset database...")

    db_cls = manifest.ManifestDB.get(db_type)
    if db_cls is None:
        raise AssetError(f"Invalid asset db type '{db_type}'")
    manifests, lists = db_cls.load_db(mfs_path, list_path)

    # Merge assets into case insensitive dict and verify hashes. Use a custom type so we don't
    # compare the file flags, which can legally differ (eg sound decompression)
    asset = namedtuple("AssetEntry", ("file_hash", "download_hash", "file_size", "download_size"))
    assets, conflicts = {}, 0
    for mfs_name, mfs_entries in manifests.items():
        for mfs_entry in mfs_entries:
            mfs_asset = asset(mfs_entry.file_hash, mfs_entry.download_hash,
                              mfs_entry.file_size, mfs_entry.download_size)
            if assets.setdefault(mfs_entry.file_name, mfs_asset) != mfs_asset:
                logging.warn(f"CONFLICT: '{mfs_entry.file_name}'")
                conflicts += conflicts
                assets[mfs_entry.file_name] = None
    if conflicts:
        logging.warn(f"Discarded {conflicts} conflicting asset entries!")
    logging.trace(f"Loaded {len(assets)} asset entries from {len(manifests)} manifests, with {len(lists)} legacy auth-lists.")

    db = namedtuple("AssetDb", ("assets", "manifests", "lists"))
    return db(assets, manifests, lists)

def load_gather_assets(*paths):
    logging.info(f"Gathering assets...")

    gathers = defaultdict(Asset)

    def append_asset(gather_path, asset_path, client_path, category):
        # HACK: no json files may be an asset due to their being control files...
        if client_path.suffix.lower() == ".json":
            return 0

        if client_path in gathers and gathers[client_path].gather_path != gather_path:
            raise AssetError(f"Gather asset conflict '{client_path}'")
        if asset_path.exists():
            gather = gathers[client_path]
            gather.gather_path = gather_path
            gather.source_path = asset_path
            gather.client_path = client_path
            gather.categories.add(category)
            logging.trace(gather)
            return 1
        else:
            logging.error(f"Asset not available: {asset_path}")
            return 0

    def handle_control_assets(gather_path, source_path, client_directory, category, gather_assets):
        num_assets = 0
        if "*" in gather_assets:
            gather_assets.remove("*")
            if gather_assets:
                logging.warning(f"Wildcard and explicit file list used in section '{category}'. Hmmm...")
            for i in source_path.iterdir():
                if not i.is_file():
                    continue
                client_path = Path(client_directory, i.relative_to(source_path))
                num_assets += append_asset(gather_path, i, client_path, category)

        for i in gather_assets:
            if any((j in i for j in naughty_path_sequences)):
                logging.error(f"SECURITY: ATTEMPT TO ESCAPE CWD BY: {source_path}")
                continue
            asset_path = source_path.joinpath(i)
            client_path = Path(client_directory, i)
            num_assets += append_asset(gather_path, asset_path, client_path, category)
        return num_assets

    def handle_control_folder(gather_path, source_path, subdir_name, subcontrol_name):
        if any((j in subcontrol_name for j in naughty_path_sequences)):
            logging.error(f"SECURITY: ATTEMPT TO ESCAPE CWD BY: {source_path}")
            return 0
        subcontrol_path = source_path.joinpath(subdir_name, subcontrol_name)
        return handle_gather_package(gather_path, subcontrol_path)

    def handle_gather_package(gather_path, control_path=None):
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
            client_directory = gather_lut.get(key.lower())
            if client_directory is None:
                if key.lower() != "folders":
                    logging.warning(f"Invalid section '{key}' in control file '{control_path}'")
                    continue
                for subdir_name, subcontrol_name in value.items():
                    num_assets += handle_control_folder(gather_path, source_path, subdir_name, subcontrol_name)
            else:
                num_assets += handle_control_assets(gather_path, source_path, client_directory, key, value)
        return num_assets

    gather_iter = (path.iterdir() for path in paths)
    for i in itertools.chain(*gather_iter):
        if not i.is_dir():
            logging.warning(f"Skipping non-directory gather path '{i.name}'!")
            continue

        logging.trace(f"Loading Gather package '{i.name}'...")
        num_assets = handle_gather_package(i)
        logging.trace(f"Loaded {num_assets} from Gather package '{i.name}'")

    logging.debug(f"Loaded {len(gathers)} assets from gathers.")
    return gathers

def load_prebuilt_assets(data_path, scripts_path):
    logging.info("Loading prebuilt assets...")

    prebuilts = {}

    def handle_prebuilts(category, base_path, source_path=None, follow_dirs=True):
        if source_path is None:
            source_path = base_path

        for i in source_path.iterdir():
            if i.is_file():
                client_path = i.relative_to(base_path)
                prebuilts[client_path] = Asset(None, i, client_path, set((category,)))
            elif i.is_dir() and follow_dirs:
                handle_prebuilts(category, base_path, i)

    for category, client_directory in gather_lut.items():
        if not client_directory:
            continue
        data_source_path = data_path.joinpath(client_directory)
        scripts_source_path = scripts_path.joinpath(client_directory)
        if not data_source_path.is_dir() and not scripts_source_path.is_dir():
            raise AssetError(f"'{client_directory}' missing from sources.")

        # The age files in the scripts directory tend to list "dead" pages that only exist
        # on Cyan's AssMan machine. So, we prefer the compiled data...
        if scripts_source_path.is_dir():
            handle_prebuilts(category, scripts_path, scripts_source_path)
        if data_source_path.is_dir():
            handle_prebuilts(category, data_path, data_source_path)

    # Have to handle the client root a bit differently due to duplication of the gather sections.
    handle_prebuilts(None, data_path, follow_dirs=False)

    logging.debug(f"Loaded {len(prebuilts)} prebuilt assets.")
    return prebuilts

def merge_asset_dicts(prebuilts, gathers):
    logging.info("Merging staged assets...")
    assets = {}
    assets.update(prebuilts)
    assets.update(gathers)
    logging.debug(f"Total known assets: {len(assets)}")
    return assets

def nuke_dead_manifests(cached_manifests, cached_lists, staged_manifests, staged_lists,
                        mfs_path, list_path, db_type):
    logging.info("Nuking defunct database files...")

    dead_mfs = frozenset(cached_manifests.keys()) - frozenset(staged_manifests.keys())
    dead_lists = frozenset(cached_lists.keys()) - frozenset(staged_lists.keys())

    db_cls = manifest.ManifestDB.get(db_type)
    if dead_mfs:
        db_cls.delete_manifests(mfs_path, *dead_mfs)
    if dead_lists:
        db_cls.delete_lists(list_path, *dead_lists)

def save_asset_database(staged_assets, manifests, lists, mfs_path, list_path, db_type, droid_key):
    logging.info("Saving asset database...")

    def iter_manifest(mfs_name):
        for client_path in manifests[mfs_name]:
            yield staged_assets[client_path]

        # Some manifests may also want to hijack the contents of another manifest.
        # We do that here because, in some cases (eg ExternalPatcher), order is important.
        # NOTE: no recursive copying. just no. *shudder*
        for copy_mfs_name in manifest_copy_from.get(mfs_name, []):
            for client_path in manifests.get(copy_mfs_name, []):
                yield staged_assets[client_path]

    def iter_secure_list(key):
        for client_path in lists[key]:
            staged_asset = staged_assets[client_path]
            yield manifest.ListEntry(staged_asset.file_name, staged_asset.file_size)

    db_cls = manifest.ManifestDB.get(db_type)
    for i in manifests.keys():
        db_cls.write_manifest(mfs_path, i, iter_manifest(i))

    lists_contents = { key: tuple(iter_secure_list(key)) for key in lists.keys() }
    db_cls.write_lists(list_path, droid_key, lists_contents)
