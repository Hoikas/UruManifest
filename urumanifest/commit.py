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
import gzip
from hashlib import md5
import itertools
import logging
from pathlib import Path
from PyHSPlasma import *
import shutil
import tempfile

from assets import Asset, AssetError
from constants import *
import manifest

_BUFFER_SIZE = 10 * 1024 * 1024

def _compress_asset(client_path, source_path, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as in_stream:
        with gzip.open(output_path, "wb") as gz_stream:
            _io_loop(in_stream, gz_stream.write)
    with output_path.open("rb") as in_stream:
        h = md5()
        _io_loop(in_stream, h.update)
    return h.hexdigest(), output_path.stat().st_size

def _copy_asset(args):
    source_path, output_path, client_path = args
    asset_output_path = output_path.joinpath(client_path)
    asset_output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, asset_output_path)
    except shutil.SameFileError:
        # Hmmm...
        pass
    return client_path, asset_output_path.stat().st_size

def _hash_asset(args):
    client_path, source_path = args
    h = md5()
    with source_path.open("rb") as in_stream:
        _io_loop(in_stream, h.update)
    return client_path, h.hexdigest(), source_path.stat().st_size

def _io_loop(in_stream, out_func):
    while True:
        if isinstance(in_stream, hsStream):
            bufsz = min(in_stream.size - in_stream.pos, _BUFFER_SIZE)
            buf = in_stream.read(bufsz) if bufsz else None
        else:
            buf = in_stream.read(_BUFFER_SIZE)
        if not buf:
            break
        out_func(buf)

def compress_dirty_assets(manifests, cached_assets, source_assets, staged_assets, output_path, force, ncpus=None):
    logging.info("Compressing dirty assets...")

    def on_compress(asset, future):
        h, sz = future.result()
        logging.trace(f"{asset.download_name}: {h}")
        asset.download_hash = h
        asset.download_size = sz

    # We only want to compress assets listed in a manifest. Some assets may be staged but in a
    # secure list only OR consumed into another staged asset.
    compressed_assets = set(itertools.chain.from_iterable(manifests.values()))
    logging.debug(f"Checking {len(compressed_assets)} of {len(staged_assets)} assets...")

    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        asset_iter = ((i, staged_assets.get(i), source_assets.get(i), cached_assets.get(i))
                      for i in compressed_assets)
        for client_path, staged_asset, source_asset, cached_asset in asset_iter:
            # ensure no files get put into the manifest directory...
            if not client_path.parent.name:
                asset_output_path = output_path.joinpath("base", client_path)
            else:
                asset_output_path = output_path.joinpath(client_path)
            asset_output_path = asset_output_path.with_suffix(f"{asset_output_path.suffix}.gz")
            staged_asset.download_name = asset_output_path.relative_to(output_path)

            # While the old, sucky manifest generator was picky about what it compressed, we're not
            # mostly just to make life easier when supporting the legacy Cyan client, which only
            # supports gzipped downloads. Sigh...
            staged_asset.flags |= ManifestFlags.file_gzipped

            if staged_asset.flags & ManifestFlags.dirty or force:
                future = executor.submit(_compress_asset, client_path,
                                         source_asset.source_path,
                                         asset_output_path)
                future.add_done_callback(functools.partial(on_compress, staged_asset))
            else:
                staged_asset.download_hash = cached_asset.download_hash
                staged_asset.download_size = cached_asset.download_size

def copy_secure_assets(secure_lists, source_assets, staged_assets, output_path, ncpus=None):
    logging.info("Copying secure assets...")

    # Sadly, the so-called "secure lists" do not store hashes. So, we must unconditionally copy
    # every single fracking time. SIGH.
    secure_assets = set(itertools.chain.from_iterable(secure_lists.values()))

    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        asset_iter = ((source_assets[i].source_path, output_path, i)
                      for i in secure_assets)
        for client_path, size in executor.map(_copy_asset, asset_iter, chunksize=64):
            logging.trace(f"{client_path}: {size}")
            staged_assets[client_path].file_size = size

def copy_server_assets(source_assets, staged_assets, age_path, sdl_path):
    logging.info("Copying core assets to server directories...")

    def copy_asset_template(asset_category, asset_suffix, output_path):
        for client_path, asset in source_assets.items():
            if asset_category in asset.categories and client_path.suffix.lower() == asset_suffix:
                copy_asset(asset.source_path, output_path)

    def copy_asset(asset_source_path, output_path):
        asset_output_path = output_path.joinpath(asset_source_path.name)
        if plEncryptedStream.IsFileEncrypted(asset_source_path):
            logging.trace(f"Decrypting '{asset_source_path.name}' for the server.")
            with plEncryptedStream().open(asset_source_path, fmRead, plEncryptedStream.kEncAuto) as in_stream:
                with asset_output_path.open("wb") as out_stream:
                    _io_loop(in_stream, out_stream.write)
        else:
            logging.trace(f"Copying '{asset_source_path.name}' for the server.")
            shutil.copy2(asset_source_path, asset_output_path)

    if age_path:
        age_path.mkdir(exist_ok=True, parents=True)
        copy_asset_template("data", ".age", age_path)
    if sdl_path:
        sdl_path.mkdir(exist_ok=True, parents=True)
        copy_asset_template("sdl", ".sdl", sdl_path)

def find_dirty_assets(cached_assets, staged_assets):
    logging.info("Comparing asset hashes...")

    def iter_asset_hashes():
        for client_path, staged_asset in staged_assets.items():
            if not staged_asset.flags & ManifestFlags.consumable:
                cached_asset = cached_assets.get(client_path)
                yield staged_asset, getattr(cached_asset, "file_hash", None), staged_asset.file_hash

    for staged_asset, cached_hash, staged_hash in iter_asset_hashes():
        dirty = cached_hash != staged_hash
        if dirty:
            staged_asset.flags |= ManifestFlags.dirty
        logstr = "dirty" if dirty else "clean"
        logging.trace(f"{staged_asset.file_name}: {logstr}")

    cached_set = frozenset(cached_assets.keys())
    staged_set = frozenset((cp for cp, sa in staged_assets.items() if not sa.flags & ManifestFlags.consumable))
    all_assets = frozenset(itertools.chain(cached_set, staged_set))
    dirty_assets = frozenset((cp for cp, sa in staged_assets.items() if sa.flags & ManifestFlags.dirty))
    added_assets = staged_set - cached_set
    deleted_assets = cached_set - staged_set
    changed_assets = dirty_assets - added_assets - deleted_assets
    logging.info(f"{len(dirty_assets)} assets dirty of {len(all_assets)}: {len(added_assets)} added, {len(changed_assets)} changed, {len(deleted_assets)} deleted.")

def encrypt_staged_assets(source_assets, staged_assets, working_path, droid_key):
    logging.info("Encrypting assets...")

    for client_path, staged_asset in staged_assets.items():
        desired_crypt = crypt_types.get(client_path.suffix.lower())
        if desired_crypt is None or staged_asset.flags & ManifestFlags.dont_encrypt:
            continue

        source_asset = source_assets[client_path]
        try:
            src_stream = plEncryptedStream()
            src_stream.open(source_asset.source_path, fmRead, plEncryptedStream.kEncAuto)
        except IOError:
            current_crypt = plEncryptedStream.kEncNone
        else:
            current_crypt = src_stream.getEncType()
        finally:
            src_stream.close()

        if current_crypt == plEncryptedStream.kEncNone:
            logging.trace(f"Encrypting '{client_path}'...")

            out_path = working_path.joinpath(client_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with plEncryptedStream().open(out_path, fmCreate, desired_crypt) as out_stream:
                if desired_crypt == plEncryptedStream.kEncDroid:
                    out_stream.setKey(droid_key)
                with source_asset.source_path.open("rb") as in_stream:
                    _io_loop(in_stream, out_stream.write)

            source_asset.source_path = out_path
            staged_asset.flags |= ManifestFlags.dirty
        elif current_crypt == desired_crypt:
            if desired_crypt == plEncryptedStream.kEncDroid:
                logging.warning(f"Asset '{client_path}' is already droid encrypted??? That's a bad idea(TM)...")
            else:
                logging.debug(f"Asset '{client_path}' is already encrypted!")
        else:
            raise AssetError(f"Incorrect encryption type: {source_asset.source_path}")

def hash_staged_assets(source_assets, staged_assets, ncpus=None):
    logging.info("Hashing all staged assets...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        args = ((cp, source_assets[cp].source_path) for cp, sa in staged_assets.items() if not sa.flags & ManifestFlags.consumable)
        for client_path, h, sz in executor.map(_hash_asset, args, chunksize=64):
            logging.trace(f"{client_path}: {h}")
            staged_asset = staged_assets[client_path]
            staged_asset.file_hash = h
            staged_asset.file_size = sz
    logging.debug(f"Hashed {len(staged_assets)} files.")

def make_secure_downloads(staged_assets, manifest=True):
    logging.info("Preparing secure preload download...")

    def iter_secure_assets():
        for client_path in staged_assets.keys():
            if client_path.suffix.lower() in {".pak", ".sdl"}:
                yield client_path

    secure_manifests = defaultdict(set)
    if manifest:
        secure_manifests["SecurePreloader"].update(iter_secure_assets())
    secure_lists = defaultdict(set)
    for client_path in iter_secure_assets():
        secure_lists[(client_path.parent.name, client_path.suffix.lower()[1:])].add(client_path)
    return secure_manifests, secure_lists

def merge_manifests(age_manifests, client_manifests, secure_manifests):
    logging.info("Merging manifests...")

    for manifest_names in gather_manifests.values():
        manifest = client_manifests.get(manifest_names.full)
        if manifest:
            manifest.update(itertools.chain.from_iterable(age_manifests.values()))
            manifest.update(itertools.chain.from_iterable(secure_manifests.values()))
        manifest = client_manifests.get(manifest_names.thin)
        if manifest:
            manifest.update(itertools.chain.from_iterable(secure_manifests.values()))

    manifests = {}
    manifests.update(age_manifests)
    manifests.update(client_manifests)
    manifests.update(secure_manifests)
    return manifests

def nuke_unstaged_assets(cached_db, staged_assets, mfs_path, list_path):
    logging.info("Nuking unstaged assets...")

    cached_assets, manifests, lists = cached_db
    deleted_assets = frozenset(cached_assets.keys()) - frozenset(staged_assets.keys())

    def unlink_asset(client_path):
        logging.trace(f"Unlinking asset '{client_path}'")
        if client_path.suffix.lower() == ".prp":
            logging.error(f"Unlinking page '{client_path.name}' -- this may cause issues on legacy clients!")

        deletions = 0
        for entry in itertools.chain(itertools.chain.from_iterable(manifests.values()),
                                     itertools.chain.from_iterable(lists.values())):
            if entry.file_name == client_path:
                deletions += unlink_entry(entry)
        return deletions

    def unlink_entry(entry):
        if isinstance(entry, manifest.ListEntry):
            asset_path = list_path.joinpath(entry.file_name)
        elif isinstance(entry, manifest.ManifestEntry):
            asset_path = mfs_path.joinpath(entry.download_name)
        else:
            raise TypeError()
        if asset_path.is_file():
            asset_path.unlink()
            return 1
        return 0

    total_deletions = sum(map(unlink_asset, deleted_assets))
    logging.debug(f"Unlinked {total_deletions} instances of {len(deleted_assets)} unstaged assets.")
