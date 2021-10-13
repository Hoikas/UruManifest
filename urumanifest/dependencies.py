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
import logging
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional, Set, Tuple
import subprocess

from assets import Asset, AssetError
from constants import *
import encryption
import manifest
import plasma_sdl
import plasmoul

def find_age_dependencies(source_assets: Dict[Path, Asset],
                          staged_assets: Dict[Path, manifest.ManifestEntry],
                          ncpus: Optional[int] = None) -> Dict[str, Set[Path]]:
    logging.info("Finding age dependencies...")
    manifests = defaultdict(set)

    def find_age_pages(age_info):
        for i in age_info.all_pages:
            yield Path("dat", f"{age_info.name}_District_{i}.prp")

    def track_dependency(age_name: str, client_path: Path, flags: ManifestFlags = 0):
        if client_path not in source_assets:
            logging.error(f"Dependency file '{client_path}' missing! Used by '{age_name}'.")
        else:
            logging.trace(f"Age '{age_name}' dependency: {client_path}")
            staged_asset = staged_assets[client_path]
            staged_asset.file_name = client_path
            staged_asset.flags |= flags
            if not flags & ManifestFlags.script:
                manifests[age_name].add(client_path)

    def handle_page_externals(age_name: str, future: concurrent.futures.Future) -> None:
        for i in future.result():
            track_dependency(age_name, *i)

    # Load all ages and enumerate their dependencies
    age_files = ((client_path, asset) for client_path, asset in source_assets.items() if client_path.suffix.lower() == ".age")
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        for age_client_path, age_asset in age_files:
            logging.trace(f"Reading age info '{age_client_path.stem}'...")

            age_info = plasmoul.plAge(age_asset.source_path)
            age_name = age_info.name

            for page_client_path in find_age_pages(age_info):
                page_asset = source_assets.get(page_client_path)
                if page_asset is None:
                    # Missing pages are sometimes intentional, so don't die on this hill.
                    logging.warning(f"Could not load page '{page_client_path.name}'!")
                else:
                    track_dependency(age_name, page_client_path)
                    future = executor.submit(_find_page_externals, page_client_path, page_asset.source_path)
                    future.add_done_callback(functools.partial(handle_page_externals, age_name))

            # Track the age core stuff
            track_dependency(age_name, age_client_path)
            funny_client_path = age_client_path.with_suffix(".fni")
            if funny_client_path in source_assets:
                track_dependency(age_name, funny_client_path)
            elif age_info.prefix >= 0:
                logging.warning(f"No funny (FNI) file found for age '{age_name}'!")

    # HAX fragile count
    logging.debug(f"Found {len(staged_assets)} age dependenices")

    return manifests

def _find_page_externals(client_path: Path, source_path: Path) -> Iterable[Tuple[Path, ManifestFlags]]:
    # FIXME: This should probably also consider movies on a per-Age basis. However, implementing
    # a reader for plLayerAnimation is nontrivial. Further, movies are currently listed in the
    # client manifest. So, we'll omit them for now.
    with plasmoul.plPage(source_path) as page_info:
        result = []
        for pfm in page_info.get_objects(plasmoul.plPythonFileMod):
            client_path = Path("Python", f"{pfm.file_name}.py")
            flags = ManifestFlags.python_file_mod | ManifestFlags.script | ManifestFlags.consumable
            result.append((client_path, flags))

        for sbuf in page_info.get_objects(plasmoul.plSoundBuffer):
            client_path = Path("sfx", sbuf.file_name)
            if sbuf.stream:
                flags = ManifestFlags.sound_stream_compressed
            else:
                if sbuf.split_channel:
                    flags = ManifestFlags.sound_cache_split
                else:
                    flags = ManifestFlags.sound_cache_stereo
            result.append((client_path, flags))

        if page_info.get_keys(plasmoul.plRelevanceRegion):
            result.append((Path("dat", f"{page_info.age}.csv"), 0))
    return result

def find_client_dependencies(source_assets: Dict[Path, Asset],
                             staged_assets: Dict[Path, manifest.ManifestEntry]) -> Dict[str, Set[Path]]:
    logging.info("Finding client dependencies...")
    manifests = defaultdict(set)

    def iter_client_dep_mfs():
        for client_path, asset in source_assets.items():
            for category in asset.categories:
                manifest_defns = gather_manifests.get(category)
                if manifest_defns:
                    manifest_names = [manifest_defns.thin, manifest_defns.full]

                    if manifest_defns.patcher:
                        patcher_exe = patcher_lut.get(category)
                        if patcher_exe is None or patcher_exe == client_path.name:
                            manifest_names.append(manifest_defns.patcher)

                    yield client_path, category, manifest_names

    def iter_client_dep_sources():
        for client_path in source_assets.keys():
            if client_path.suffix.lower() in {".age", ".p2f", ".loc"}:
                yield client_path

            # FIXME: remove if/when video playback from python is detected...
            if client_path.suffix.lower() in {".avi", ".bik", ".webm"}:
                yield client_path

    def track_dependency(client_path: Path, flags=0):
        entry = staged_assets[client_path]
        entry.file_name = client_path
        entry.flags |= flags

    def track_client_dependency(client_path: Path):
        track_dependency(client_path)
        for manifest_names in itertools.chain(gather_manifests.values()):
            for i in (manifest_names.full, manifest_names.thin):
                mfs = manifests.get(i)
                if mfs:
                    mfs.add(client_path)

    def track_manifest_dependency(client_path: Path, category: str, manifest_names):
        flags = ManifestFlags.installer if category in gather_installers else 0
        track_dependency(client_path, flags)
        for name in manifest_names:
            if name:
                manifests[name].add(client_path)

    # Initializes client manifests that we know about
    for client_path, category, manifest_names in iter_client_dep_mfs():
        track_manifest_dependency(client_path, category, manifest_names)

    # Adds core files to the staging area and all the valid client manifests
    for client_path in iter_client_dep_sources():
        track_client_dependency(client_path)

    return manifests

def find_script_dependencies(source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry]) -> None:
    logging.info("Finding script dependencies...")

    def iter_pfms():
        for client_path, asset in staged_assets.items():
            if asset.flags & ManifestFlags.python_file_mod:
                yield client_path, asset

    def track_dependency(client_path: Path, flags: ManifestFlags = 0):
        logging.trace(client_path)
        staged_assets[client_path].file_name = client_path
        staged_assets[client_path].flags |= ManifestFlags.script | flags

    logging.debug(f"Loading all SDL...")
    sdl_mgrs = _load_sdl_descriptors(source_assets)

    find_sdl_deps = functools.partial(_find_script_sdl_dependencies, sdl_mgrs)
    find_sdl_opt = functools.partial(_find_script_sdl_dependencies, sdl_mgrs, optional=True)

    # Unconditionally add the SDLs used by plSynchedObject
    logging.debug("Finding client core SDLs...")
    client_sdl_paths = frozenset(itertools.chain.from_iterable(map(find_sdl_deps, client_sdl)))
    logging.debug(f"Found {len(client_sdl_paths)} client core SDL files.")
    for i in client_sdl_paths:
        track_dependency(i)

    # Find optional SDLs (generally Age SDLs)...
    logging.debug("Finding optional Python SDLs...")
    pfm_class_names = frozenset((client_path.stem for client_path, _ in iter_pfms()))
    logging.debug(f"Searching over {len(pfm_class_names)} possible Python Classes")
    py_sdl_paths = frozenset(itertools.chain.from_iterable(map(find_sdl_opt, pfm_class_names)))
    logging.debug(f"Found {len(py_sdl_paths)} optional SDL files.")
    for i in py_sdl_paths:
        track_dependency(i)

    # What, you don't like verbosity?
    all_sdl_files = frozenset(itertools.chain(client_sdl_paths, py_sdl_paths))
    logging.debug(f"Found {len(all_sdl_files)} SDL file dependencies")

    # Ensure all .py files are known for later usage by the compyler
    py_client_paths = (client_path for client_path in source_assets.keys()
                                   if client_path.suffix.lower() == ".py")
    for i in py_client_paths:
        track_dependency(i, ManifestFlags.consumable)

def _find_script_sdl_dependencies(sdl_mgrs, descriptor_name: str, optional=False):
    def find_sdls(sdl_mgrs, descriptor_name, embedded_sdr=False, optional=False):
        dependencies = set()
        descriptors = set()

        for sdl_file, mgr in sdl_mgrs.items():
            # Be sure to loop over all files in case someone moves a record to a new file
            # from version to version. Please don't do that, though. It's mean :<
            for descriptor in mgr.find_descriptors(descriptor_name):
                dependencies.add(sdl_file)
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

def _load_sdl_descriptors(assets: Dict[Path, Asset]) -> Dict[Path, plasma_sdl.Manager]:
    sdl_mgrs = {}
    sdl_files = ((client_path, asset) for client_path, asset in assets.items() if client_path.suffix.lower() == ".sdl")
    for client_path, asset in sdl_files:
        logging.trace(f"Reading SDL '{client_path.name}' from '{asset.source_path}'.")

        # Strictly speaking, due to the configurable nature of the key, btea/notthedroids encrypted
        # SDL files are not allowed here. So, let's detect that.
        if encryption.determine(asset.source_path) != encryption.Encryption.Unspecified:
            raise AssetError(f"SDL File '{asset.source_path.name}' is encrypted and cannot be used for packaging.")
            continue

        sdl_mgrs[client_path] = plasma_sdl.Manager(asset.source_path)
    return sdl_mgrs
