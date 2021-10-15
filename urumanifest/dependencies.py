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
import concurrent.futures
import functools
import io
import itertools
from json.encoder import JSONEncoder
import logging
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, NamedTuple, Set, Tuple
import subprocess

from assets import Asset, AssetError, build_server_path, lookup_asset
from constants import *
import encryption
import manifest
import plasma_sdl
import plasmoul

class _Dependency(NamedTuple):
    client_path: Path
    server_path: Path


class _FlaggedDependency(NamedTuple):
    client_path: Path
    server_path: Path
    flags: ManifestFlags


class _SDLDependency(NamedTuple):
    client_path: Path
    manager: plasma_sdl.Manager


def find_age_dependencies(source_assets: Dict[Path, Asset],
                          staged_assets: Dict[Path, manifest.ManifestEntry],
                          ncpus: Optional[int] = None) -> Dict[str, Set[Path]]:
    logging.info("Finding age dependencies...")
    manifests = defaultdict(set)

    def find_age_pages(age_info: plasmoul.plAge):
        for i in age_info.all_pages:
            client_path = Path("dat", f"{age_info.name}_District_{i}.prp")
            server_path = build_server_path(client_path)
            yield _Dependency(client_path, server_path)

    def track_dependency(age_name: str, client_path: Path, server_path: Path, flags: ManifestFlags = 0):
        if server_path not in source_assets:
            logging.error(f"Dependency file '{client_path}' (provided by: '{server_path}') missing! Used by '{age_name}'.")
        else:
            logging.trace(f"Age '{age_name}' dependency: {client_path}")
            staged_asset = staged_assets[server_path]
            staged_asset.file_name = client_path
            staged_asset.flags |= flags
            if not flags & ManifestFlags.script:
                manifests[age_name].add(server_path)

    def handle_page_externals(age_name: str, future: concurrent.futures.Future) -> None:
        for i in future.result():
            track_dependency(age_name, *i)

    # Load all ages and enumerate their dependencies
    age_files = ((server_path, asset) for server_path, asset in source_assets.items() if asset.client_path.suffix.lower() == ".age")
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        for age_server_path, age_asset in age_files:
            logging.trace(f"Reading age info '{age_asset.client_path.stem}'...")

            age_info = plasmoul.plAge(age_asset.source_path)
            age_name = age_info.name

            for page_client_path, page_server_path in find_age_pages(age_info):
                page_asset = source_assets.get(page_server_path)
                if page_asset is None:
                    # Missing pages are sometimes intentional, so don't die on this hill.
                    logging.warning(f"Could not load page '{page_server_path.name}'!")
                else:
                    track_dependency(age_name, page_client_path, page_server_path)
                    future = executor.submit(_find_page_externals, page_asset.source_path)
                    future.add_done_callback(functools.partial(handle_page_externals, age_name))

            # Track the age core stuff
            track_dependency(age_name, age_asset.client_path, age_server_path)
            funny_server_path, funny_asset = lookup_asset(
                source_assets,
                age_asset.client_path.with_suffix(".fni"),
                category="data"
            )
            if funny_asset is not None:
                track_dependency(age_name, funny_asset.client_path, funny_server_path)
            elif age_info.prefix >= 0:
                logging.warning(f"No funny (FNI) file found for age '{age_name}'!")

    # HAX fragile count
    logging.debug(f"Found {len(staged_assets)} age dependenices")

    return manifests


def _find_page_externals(source_path: Path) -> Iterable[_FlaggedDependency]:
    # FIXME: This should probably also consider movies on a per-Age basis. However, implementing
    # a reader for plLayerAnimation is nontrivial. Further, movies are currently listed in the
    # client manifest. So, we'll omit them for now.
    with plasmoul.plPage(source_path) as page_info:
        result = []
        for pfm in page_info.get_objects(plasmoul.plPythonFileMod):
            client_path = Path("Python", f"{pfm.file_name}.py")
            server_path = build_server_path(client_path)
            flags = ManifestFlags.python_file_mod | ManifestFlags.script | ManifestFlags.consumable
            result.append(_FlaggedDependency(client_path, server_path, flags))

        for sbuf in page_info.get_objects(plasmoul.plSoundBuffer):
            client_path = Path("sfx", sbuf.file_name)
            server_path = build_server_path(client_path)
            if sbuf.stream:
                flags = ManifestFlags.sound_stream_compressed
            else:
                if sbuf.split_channel:
                    flags = ManifestFlags.sound_cache_split
                else:
                    flags = ManifestFlags.sound_cache_stereo
            result.append(_FlaggedDependency(client_path, server_path, flags))

        if page_info.get_keys(plasmoul.plRelevanceRegion):
            client_path = Path("dat", f"{page_info.age}.csv")
            server_path = build_server_path(client_path)
            result.append(_FlaggedDependency(client_path, server_path, 0))
    return result

def find_client_dependencies(source_assets: Dict[Path, Asset],
                             staged_assets: Dict[Path, manifest.ManifestEntry]) -> Dict[str, Set[Path]]:
    logging.info("Finding client dependencies...")
    manifests = defaultdict(set)

    def iter_client_dep_mfs():
        for server_path, asset in source_assets.items():
            for category in asset.categories:
                manifest_defns = gather_manifests.get(category)
                if manifest_defns:
                    manifest_names = [manifest_defns.thin, manifest_defns.full]

                    if manifest_defns.patcher:
                        patcher_exe = patcher_lut.get(category)
                        if patcher_exe is None or patcher_exe == asset.client_path.name:
                            manifest_names.append(manifest_defns.patcher)

                    yield asset.client_path, server_path, category, manifest_names

    def iter_client_dep_sources():
        for server_path, asset in source_assets.items():
            if asset.client_path.suffix.lower() in {".age", ".p2f", ".loc"}:
                yield _Dependency(asset.client_path, server_path)

            # FIXME: remove if/when video playback from python is detected...
            if asset.client_path.suffix.lower() in {".avi", ".bik", ".webm"}:
                yield _Dependency(asset.client_path, server_path)

    def track_dependency(client_path: Path, server_path: Path, flags=0):
        entry = staged_assets[server_path]
        entry.file_name = client_path
        entry.flags |= flags

    def track_client_dependency(client_path: Path, server_path: Path):
        track_dependency(client_path, server_path)
        for manifest_names in itertools.chain(gather_manifests.values()):
            for i in (manifest_names.full, manifest_names.thin):
                mfs = manifests.get(i)
                if mfs:
                    mfs.add(server_path)

    def track_manifest_dependency(client_path: Path, server_path: Path, category: str, manifest_names):
        flags = ManifestFlags.installer if category in gather_installers else 0
        track_dependency(client_path, server_path, flags)
        for name in manifest_names:
            if name:
                manifests[name].add(server_path)

    # Initializes client manifests that we know about
    for client_path, server_path, category, manifest_names in iter_client_dep_mfs():
        track_manifest_dependency(client_path, server_path, category, manifest_names)

    # Adds core files to the staging area and all the valid client manifests
    for client_path, server_path in iter_client_dep_sources():
        track_client_dependency(client_path, server_path)

    return manifests

def find_script_dependencies(source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry]) -> None:
    logging.info("Finding script dependencies...")

    def iter_pfm_assets():
        for asset in filter(lambda x: x.flags & ManifestFlags.python_file_mod, staged_assets.values()):
            yield asset

    def track_dependency(client_path: Path, server_path: Path, flags: ManifestFlags = 0):
        logging.trace(server_path)
        staged_asset = staged_assets[server_path]
        staged_asset.file_name = client_path
        staged_asset.flags |= ManifestFlags.script | flags

    logging.debug(f"Loading all SDL...")
    sdl_mgrs = _load_sdl_descriptors(source_assets)

    find_sdl_deps = functools.partial(_find_script_sdl_dependencies, sdl_mgrs)
    find_sdl_opt = functools.partial(_find_script_sdl_dependencies, sdl_mgrs, optional=True)

    # Unconditionally add the SDLs used by plSynchedObject
    logging.debug("Finding client core SDLs...")
    client_sdl_dependencies = frozenset(itertools.chain.from_iterable(map(find_sdl_deps, client_sdl)))
    logging.debug(f"Found {len(client_sdl_dependencies)} client core SDL files.")
    for i in client_sdl_dependencies:
        track_dependency(*i)

    # Find optional SDLs (generally Age SDLs)...
    logging.debug("Finding optional Python SDLs...")
    pfm_class_names = frozenset((asset.file_name.stem for asset in iter_pfm_assets()))
    logging.debug(f"Searching over {len(pfm_class_names)} possible Python Classes")
    py_sdl_dependencies = frozenset(itertools.chain.from_iterable(map(find_sdl_opt, pfm_class_names)))
    logging.debug(f"Found {len(py_sdl_dependencies)} optional SDL files.")
    for i in py_sdl_dependencies:
        track_dependency(*i)

    # What, you don't like verbosity?
    all_sdl_files = frozenset(itertools.chain(client_sdl_dependencies, py_sdl_dependencies))
    logging.debug(f"Found {len(all_sdl_files)} SDL file dependencies")

    # Ensure all .py files are known for later usage by the compyler
    all_py_assets = ((sp, asset) for sp, asset in source_assets.items() if asset.client_path.suffix.lower() == ".py")
    for server_path, asset in all_py_assets:
        track_dependency(asset.client_path, server_path, ManifestFlags.consumable)

def _find_script_sdl_dependencies(sdl_mgrs: Dict[Path, _SDLDependency], descriptor_name: str,
                                  optional: bool = False) -> Iterable[_Dependency]:

    def find_sdls(sdl_mgrs: Dict[Path, _SDLDependency], descriptor_name: str,
                  embedded_sdr: bool = False, optional: bool = False) -> Tuple[Set[_Dependency], Set[str]]:
        dependencies = set()
        descriptors = set()

        for server_sdl_path, (client_sdl_path, mgr) in sdl_mgrs.items():
            # Be sure to loop over all files in case someone moves a record to a new file
            # from version to version. Please don't do that, though. It's mean :<
            for descriptor in mgr.find_descriptors(descriptor_name):
                dependencies.add(_Dependency(client_sdl_path, server_sdl_path))
                descriptors.add(descriptor.name)

                # We need to see if there are any embedded state descriptor variables...
                sdrs = (i for i in descriptor.variables if i.descriptor is not None and i.descriptor not in descriptors)
                for variable in sdrs:
                    more_dependencies, more_descriptors = find_sdls(sdl_mgrs, variable.descriptor, True)
                    dependencies.update(more_dependencies)
                    descriptors.update(more_descriptors)

        if descriptor_name not in descriptors:
            if embedded_sdr:
                raise AssetError(f"Embedded SDL Descriptor '{descriptor_name}' is missing.")
            elif not optional:
                raise AssetError(f"Top-level SDL '{descriptor_name}' is missing.")
            else:
                logging.trace(f"Optional SDL Descriptor '{descriptor_name}' not found.")
        return dependencies, descriptors

    return find_sdls(sdl_mgrs, descriptor_name, optional=optional)[0]

def _load_sdl_descriptors(source_assets: Dict[Path, Asset]) -> Dict[Path, _SDLDependency]:
    sdl_mgrs = {}
    sdl_files = ((server_path, asset) for server_path, asset in source_assets.items() if asset.client_path.suffix.lower() == ".sdl")
    for server_path, asset in sdl_files:
        logging.trace(f"Reading SDL '{asset.client_path.name}' from '{asset.source_path}'.")

        # Strictly speaking, due to the configurable nature of the key, btea/notthedroids encrypted
        # SDL files are not allowed here. So, let's detect that.
        if encryption.determine(asset.source_path) != encryption.Encryption.Unspecified:
            raise AssetError(f"SDL File '{asset.source_path.name}' is encrypted and cannot be used for packaging.")

        sdl_mgrs[server_path] = _SDLDependency(asset.client_path, plasma_sdl.Manager(asset.source_path))
    return sdl_mgrs
