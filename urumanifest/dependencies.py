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
from PyHSPlasma import *
import subprocess

from assets import AssetError
from constants import *
import manifest

def find_age_dependencies(source_assets, staged_assets, ncpus=None):
    logging.info("Finding age dependencies...")
    manifests = defaultdict(set)

    def find_age_pages(age_info):
        # Collect a list of all age pages to be abused for the purpose of finding its resources
        # Would be nice if this were a common function of libHSPlasma...
        for i in range(age_info.getNumPages()):
            yield Path("dat", age_info.getPageFilename(i, pvMoul))
        for i in range(age_info.getNumCommonPages(pvMoul)):
            yield Path("dat", age_info.getCommonPageFilename(i, pvMoul))

    def track_dependency(age_name, client_path, flags=0):
        if client_path not in source_assets:
            logging.error(f"Dependency file '{client_path}' missing! Used by '{age_name}'.")
        else:
            logging.trace(f"Age '{age_name}' dependency: {client_path}")
            staged_asset = staged_assets[client_path]
            staged_asset.file_name = client_path
            staged_asset.flags |= flags
            if not flags & ManifestFlags.script:
                manifests[age_name].add(client_path)

    def handle_page_externals(age_name, future):
        for i in future.result():
            track_dependency(age_name, *i)

    # Load all ages and enumerate their dependencies
    age_files = ((client_path, asset) for client_path, asset in source_assets.items() if client_path.suffix.lower() == ".age")
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        for age_client_path, age_asset in age_files:
            logging.trace(f"Reading age info '{age_client_path.stem}'...")

            age_info = plAgeInfo()
            age_info.readFromFile(age_asset.source_path)
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
            elif age_info.seqPrefix >= 0:
                logging.warning(f"No funny (FNI) file found for age '{age_name}'!")

    # HAX fragile count
    logging.debug(f"Found {len(staged_assets)} age dependenices")

    return manifests

def _find_page_externals(client_path, source_path, dlevel=plDebug.kDLNone):
    def read_pko(key):
        koStub = key.object
        if isinstance(koStub, hsKeyedObjectStub):
            # This is somewhat inefficient WRT copying data, but better than trying to handle all
            # the doggong DSpan reading...
            stream = hsRAMStream(pvMoul)
            stream.writeShort(koStub.stub.ClassIndexVer(pvMoul))
            stream.write(koStub.stub.getData())
            stream.rewind()
            # important to use a new manager -- the old one will crash...
            return plResManager(pvMoul).ReadCreatable(stream)
        return koStub

    plDebug.Init(dlevel)
    mgr = plResManager()
    ## FIXME: We would prefer to stub the keyed objects and read them in on demand, but that seems
    # to cause safe strings to become corrupted on Linux.
    page_info = mgr.ReadPage(source_path)

    pfm_idx = plFactory.ClassIndex("plPythonFileMod")
    sfx_idx = plFactory.ClassIndex("plSoundBuffer")
    rel_idx = plFactory.ClassIndex("plRelevanceRegion")
    movie_idxes = [plFactory.ClassIndex(i.__class__.__name__) for i in plLayerMovie.__subclasses__()]
    movie_idxes.append(plFactory.ClassIndex("plLayerMovie"))
    get_keys = functools.partial(mgr.getKeys, page_info.location)

    result = []
    for i in get_keys(pfm_idx):
        pfm = read_pko(i)
        client_path = Path("Python", f"{pfm.filename}.py")
        flags = ManifestFlags.python_file_mod | ManifestFlags.script | ManifestFlags.consumable
        result.append((client_path, flags))

    for i in get_keys(sfx_idx):
        sbuf = read_pko(i)
        client_path = Path("sfx", sbuf.fileName)
        if sbuf.flags & plSoundBuffer.kStreamCompressed:
            flags = ManifestFlags.sound_stream_compressed
        else:
            if sbuf.flags & plSoundBuffer.kOnlyLeftChannel or sbuf.flags & plSoundBuffer.kOnlyRightChannel:
                flags = ManifestFlags.sound_cache_split
            else:
                flags = ManifestFlags.sound_cache_stereo
        result.append((client_path, flags))

    if get_keys(rel_idx):
        result.append((Path("dat", f"{page_info.age}.csv"), 0))

    for i in itertools.chain(*map(get_keys, movie_idxes)):
        movie = read_pko(i)
        result.append((Path(movie.movieName), 0))

    return result

def find_client_dependencies(source_assets, staged_assets):
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

    def track_dependency(client_path, flags=0):
        entry = staged_assets[client_path]
        entry.file_name = client_path
        entry.flags |= flags

    def track_client_dependency(client_path):
        track_dependency(client_path)
        for manifest_names in itertools.chain(gather_manifests.values()):
            for i in (manifest_names.full, manifest_names.thin):
                mfs = manifests.get(i)
                if mfs:
                    mfs.add(client_path)

    def track_manifest_dependency(client_path, category, manifest_names):
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

def find_script_dependencies(source_assets, staged_assets):
    logging.info("Finding script dependencies...")

    def iter_pfms():
        for client_path, asset in staged_assets.items():
            if asset.flags & ManifestFlags.python_file_mod:
                yield client_path, asset

    def track_dependency(client_path, flags=0):
        logging.trace(client_path)
        staged_assets[client_path].file_name = client_path
        staged_assets[client_path].flags |= ManifestFlags.script | flags

    logging.debug(f"Loading all SDL...")
    sdl_mgrs = _load_sdl_descriptors(source_assets)

    find_sdl_deps = functools.partial(_find_script_sdl_dependencies, sdl_mgrs)
    find_sdl_opt = functools.partial(_find_script_sdl_dependencies, sdl_mgrs, optional=True)

    # Unconditionally add the SDLs used by plSynchedObject
    logging.debug("Finding client core SDLs...")
    client_sdl_paths = frozenset(itertools.chain(*map(find_sdl_deps, client_sdl)))
    logging.debug(f"Found {len(client_sdl_paths)} client core SDL files.")
    for i in client_sdl_paths:
        track_dependency(i)

    # Find optional SDLs (generally Age SDLs)...
    logging.debug("Finding optional Python SDLs...")
    pfm_class_names = frozenset((client_path.stem for client_path, _ in iter_pfms()))
    logging.debug(f"Searching over {len(pfm_class_names)} possible Python Classes")
    py_sdl_paths = frozenset(itertools.chain(*map(find_sdl_opt, pfm_class_names)))
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

def _find_script_sdl_dependencies(sdl_mgrs, descriptor_name, optional=False):
    def find_sdls(sdl_mgrs, descriptor_name, embedded_sdr=False, optional=False):
        dependencies = set()
        descriptors = set()

        for sdl_file, mgr in sdl_mgrs.items():
            descriptor = mgr.getDescriptor(descriptor_name)
            if descriptor is not None:
                break
        else:
            if embedded_sdr:
                raise AssetError(f"Embedded SDL Descriptor '{descriptor_name}' is missing.")
            elif not optional:
                raise AssetError(f"Top-level SDL '{descriptor_name}' is missing.")
            else:
                logging.debug(f"Optional SDL Descriptor '{descriptor_name}' not found...")
            return dependencies, descriptors

        dependencies.add(sdl_file)
        descriptors.add(descriptor.name)

        # We need to see if there are any embedded state descriptor variables...
        for variable in descriptor.variables:
            if variable.type == plVarDescriptor.kStateDescriptor and not variable.stateDescType in descriptors:
                more_dependencies, more_descriptors = find_sdls(sdl_mgrs, variable.stateDescType, True)
                dependencies.update(more_dependencies)
                descriptors.update(more_descriptors)
        return dependencies, descriptors
    return find_sdls(sdl_mgrs, descriptor_name, optional=optional)[0]

def _load_sdl_descriptors(assets):
    sdl_mgrs = {}
    sdl_files = ((client_path, asset) for client_path, asset in assets.items() if client_path.suffix.lower() == ".sdl")
    for client_path, asset in sdl_files:
        logging.trace(f"Reading SDL '{client_path.name}' from '{asset.source_path}'.")

        # Strictly speaking, due to the configurable nature of the key, btea/notthedroids encrypted
        # SDL files are not allowed here. So, let's detect that.
        if plEncryptedStream.IsFileEncrypted(asset.source_path):
            raise AssetError(f"SDL File '{asset.source_path.name}' is encrypted and cannot be used for packaging.")
            continue

        mgr = plSDLMgr()
        mgr.readDescriptors(asset.source_path)
        sdl_mgrs[client_path] = mgr
    return sdl_mgrs
