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
from dataclasses import dataclass
import functools
import gzip
import hashlib
import itertools
import logging
from pathlib import Path
import shutil
from typing import Dict, NamedTuple, Optional, Set, Tuple, Union

from assets import Asset, AssetDatabase, AssetError, AssetEntry
from constants import *
import encryption
import manifest

_BUFFER_SIZE = 10 * 1024 * 1024

def _compress_asset(source_path: Path, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with source_path.open("rb") as in_stream:
        with gzip.open(output_path, "wb") as gz_stream:
            _io_loop(in_stream, gz_stream.write)
    with output_path.open("rb") as in_stream:
        h = hashlib.md5()
        _io_loop(in_stream, h.update)
    return h.hexdigest(), output_path.stat().st_size

def _hash_asset(args: Tuple[Path, Path]) -> Tuple[Path, str, int]:
    server_path, source_path = args
    # One day, we will not use such a vulnerable hashing algo...
    h = hashlib.md5()
    with source_path.open("rb") as in_stream:
        _io_loop(in_stream, h.update)
    return server_path, h.hexdigest(), source_path.stat().st_size

def _compare_files(newFile: Path, prevFile: Path, *, key=None) -> bool:
    if not prevFile.exists() or not newFile.exists():
        return False
    oldSize = prevFile.stat().st_size if prevFile.is_file() else 0
    newSize = newFile.stat().st_size if newFile.is_file() else 0
    if oldSize != newSize:
        return False

    # Optimization: if the two files have the same encryption type, we can just hash them directly.
    # Otherwise, we need to decrypt for hashing. This is primarily to avoid decrypting python.pak
    # twice in the same thread immediately after we spent a buttload of time encrypting it.
    # Will be a moot point if the encryption module ever gets rewritten in C or something.
    if encryption.determine(prevFile) == encryption.determine(newFile):
        opener = functools.partial(open, mode="rb")
    else:
        opener = functools.partial(encryption.stream, mode=encryption.Mode.ReadBinary, key=key)

    prevHash = hashlib.sha512()
    with opener(prevFile) as s:
        _io_loop(s, prevHash.update)
    newHash = hashlib.sha512()
    with opener(newFile) as s:
        _io_loop(s, newHash.update)
    return prevHash.digest() == newHash.digest()

def _encrypt_asset(source_path: Path, out_path: Path, *, enc: encryption.Encryption, key=None):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with encryption.stream(out_path, encryption.Mode.WriteBinary, enc=enc, key=key) as out_stream:
        with encryption.stream(source_path, encryption.Mode.ReadBinary) as in_stream:
            _io_loop(in_stream, out_stream.write)

def _io_loop(in_stream, out_func):
    while True:
        buf = in_stream.read(_BUFFER_SIZE)
        if not buf:
            break
        out_func(buf)

def compress_dirty_assets(manifests: Dict[str, Set[Path]], cached_assets: Dict[Path, AssetEntry],
                          source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry],
                          output_path: Path, force: bool, ncpus: Optional[int] = None):
    logging.info("Compressing dirty assets...")

    def on_compress(asset: manifest.ManifestEntry, future: concurrent.futures.Future) -> None:
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
        for server_path, staged_asset, source_asset, cached_asset in asset_iter:
            assert server_path.parent.name
            asset_output_path = output_path.joinpath(server_path).with_suffix(f"{server_path.suffix}.gz")
            staged_asset.download_name = asset_output_path.relative_to(output_path)

            # While the old, sucky manifest generator was picky about what it compressed, we're not
            # mostly just to make life easier when supporting the legacy Cyan client, which only
            # supports gzipped downloads. Sigh...
            staged_asset.flags |= ManifestFlags.file_gzipped

            if staged_asset.flags & ManifestFlags.dirty or force:
                future = executor.submit(_compress_asset,
                                         source_asset.source_path,
                                         asset_output_path)
                future.add_done_callback(functools.partial(on_compress, staged_asset))
            else:
                staged_asset.download_hash = cached_asset.download_hash
                staged_asset.download_size = cached_asset.download_size

def copy_secure_assets(secure_lists: Dict[Tuple[str, str], Set[Path]], source_assets: Dict[Path, Asset],
                       staged_assets: Dict[Path, manifest.ManifestEntry], input_path: Path,
                       output_path: Path, droid_key, ncpus: Optional[int] = None):
    logging.info("Copying secure assets...")

    secure_assets = set(itertools.chain.from_iterable(secure_lists.values()))

    def copy_asset(asset_source_path: Path, asset_output_path: Path,
                   server_path: Path, future: concurrent.futures.Future) -> None:
        size = asset_source_path.stat().st_size
        staged_assets[server_path].file_size = size
        if not future.result():
            asset_output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(asset_source_path, asset_output_path)
            except shutil.SameFileError:
                # Hmmm...
                pass
            else:
                logging.debug(f"Copied {server_path} ({size:n} bytes)")

    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        # Only copy over things that we can verify have changed to better enable staging changesets.
        # Sadly, due to the fact that these are "secure lists" we cannot rely on every server knowing
        # the hash of the asset (ironic, right?) - so we have to hash against whatever the server has
        # lying around. Gulp.
        for server_path in secure_assets:
            source_asset = source_assets[server_path]
            asset_input_path = input_path.joinpath(source_asset.client_path)
            asset_output_path = output_path.joinpath(source_asset.client_path)
            asset_source_path = source_asset.source_path
            fut = executor.submit(_compare_files, asset_source_path, asset_input_path, key=droid_key)
            fut.add_done_callback(functools.partial(copy_asset, asset_source_path, asset_output_path, server_path))

def copy_server_assets(source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry],
                       age_path_in: Path, sdl_path_in: Path, age_path_out: Path,
                       sdl_path_out: Path, ncpus: Optional[int] = None) -> None:
    logging.info("Copying core assets to server directories...")

    # source_path: the definitive copy of this asset that we want to ship (always exists)
    # input_path: the path to the current asset on the server (may or may not exist)
    # output_put: the destination path we will copy `source_path` to - might or might not be the same as `input_path`
    class ServerAsset(NamedTuple):
        source_path: Path
        input_path: Path
        output_path: Path

    def discover_server_assets(asset_category: str, asset_suffix: str, input_path: Path, output_path: Path):
        for server_path, asset in source_assets.items():
            if asset_category in asset.categories and asset.client_path.suffix.lower() == asset_suffix:
                yield ServerAsset(asset.source_path, input_path.joinpath(server_path.name), output_path.joinpath(server_path.name))

    def copy_asset(asset_source_path: Path, asset_output_path: Path, fut: concurrent.futures.Future):
        if not fut.result():
            asset_output_path.parent.mkdir(parents=True, exist_ok=True)
            if encryption.determine(asset_source_path) != encryption.Encryption.Unspecified:
                logging.debug(f"Decrypting '{asset_source_path.name}' for the server.")
                with encryption.stream(asset_source_path, encryption.Mode.ReadBinary) as in_stream:
                    with asset_output_path.open("wb") as out_stream:
                        _io_loop(in_stream, out_stream.write)
            else:
                logging.debug(f"Copying '{asset_source_path.name}' for the server.")
                shutil.copy2(asset_source_path, asset_output_path)

    server_assets = []
    if age_path_in and age_path_out:
        server_assets.extend(discover_server_assets("data", ".age", age_path_in, age_path_out))
    if sdl_path_in and sdl_path_out:
        server_assets.extend(discover_server_assets("sdl", ".sdl", sdl_path_in, sdl_path_out))
    if not server_assets:
        return

    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        for server_asset in server_assets:
            fut = executor.submit(_compare_files, server_asset.source_path, server_asset.input_path)
            fut.add_done_callback(functools.partial(copy_asset, server_asset.source_path, server_asset.output_path))

def find_dirty_assets(cached_assets: Dict[Path, AssetEntry], staged_assets: Dict[Path, manifest.ManifestEntry]):
    logging.info("Comparing asset hashes...")

    def iter_asset_hashes():
        for server_path, staged_asset in staged_assets.items():
            if not staged_asset.flags & ManifestFlags.consumable:
                cached_asset = cached_assets.get(server_path)
                yield staged_asset, getattr(cached_asset, "file_hash", None), staged_asset.file_hash

    for staged_asset, cached_hash, staged_hash in iter_asset_hashes():
        dirty = cached_hash != staged_hash
        if dirty:
            staged_asset.flags |= ManifestFlags.dirty
        logstr = "dirty" if dirty else "clean"
        logging.trace(f"{staged_asset.download_name}: {logstr}")

    cached_set = frozenset(cached_assets.keys())
    staged_set = frozenset((sp for sp, sa in staged_assets.items() if not sa.flags & ManifestFlags.consumable))
    all_assets = frozenset(itertools.chain(cached_set, staged_set))
    dirty_assets = frozenset((sp for sp, sa in staged_assets.items() if sa.flags & ManifestFlags.dirty and not sa.flags & ManifestFlags.consumable))
    added_assets = staged_set - cached_set
    deleted_assets = cached_set - staged_set
    changed_assets = dirty_assets - added_assets - deleted_assets
    logging.info(f"{len(dirty_assets)} assets dirty of {len(all_assets)}: {len(added_assets)} added, {len(changed_assets)} changed, {len(deleted_assets)} deleted.")

    def dump_set(set_type, the_set):
        if the_set:
            logging.debug(f"--- BEGIN {set_type} ASSETS ---")
            for i in the_set:
                logging.debug(i)
            logging.debug(f"--- END {set_type} ASSETS ---")

    dump_set("ADDED", added_assets)
    dump_set("CHANGED", changed_assets)
    dump_set("DELETED", deleted_assets)

def encrypt_staged_assets(source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry],
                          working_path: Path, droid_key, ncpus: Optional[int] = None) -> None:
    logging.info("Encrypting assets...")

    def on_asset_encrypt(server_path: Path, fut: concurrent.futures.Future):
        logging.trace(f"Encrypted: {server_path}")
        # Propagate any exceptions...
        fut.result()

    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        for server_path, staged_asset in staged_assets.items():
            desired_crypt = crypt_types.get(staged_asset.file_name.suffix.lower())
            if desired_crypt is None or staged_asset.flags & ManifestFlags.dont_encrypt:
                continue

            source_asset = source_assets[server_path]
            current_crypt = encryption.determine(source_asset.source_path)

            if current_crypt != desired_crypt and current_crypt != encryption.Encryption.BTEA:
                out_path = working_path.joinpath(server_path)

                kwargs = {
                    "enc": desired_crypt,
                    "key": droid_key if desired_crypt == encryption.Encryption.BTEA else None,
                }

                fut = executor.submit(_encrypt_asset, source_asset.source_path, out_path, **kwargs)
                fut.add_done_callback(functools.partial(on_asset_encrypt, server_path))

                source_asset.source_path = out_path
                # I'm not sure why we forced the encrypted files as dirty. The hash stage is after this,
                # so we should catch any differences. Gonna leave it in, commented out, in case I'm
                # just not thinking about something. :/
                #staged_asset.flags |= ManifestFlags.dirty
            elif current_crypt == desired_crypt and desired_crypt == encryption.Encryption.BTEA:
                logging.warning(f"Asset '{source_asset.source_path}' is already droid encrypted??? This will prevent trivial key changes.")
            elif current_crypt != desired_crypt:
                raise AssetError(f"Asset '{source_asset.source_path}' was pre-encrypted incorrectly. Please decrypt it manually.")

def hash_staged_assets(source_assets: Dict[Path, Asset], staged_assets: Dict[Path, manifest.ManifestEntry],
                       ncpus: Optional[int] = None) -> None:
    logging.info("Hashing all staged assets...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=ncpus) as executor:
        args = ((sp, source_assets[sp].source_path) for sp, sa in staged_assets.items() if not sa.flags & ManifestFlags.consumable)
        for server_path, h, sz in executor.map(_hash_asset, args, chunksize=64):
            logging.trace(f"{server_path}: {h}")
            staged_asset = staged_assets[server_path]
            staged_asset.file_hash = h
            staged_asset.file_size = sz
    logging.debug(f"Hashed {len(staged_assets)} files.")

def make_secure_downloads(staged_assets: Dict[Path, manifest.ManifestEntry],
                          manifest: bool = True) -> Tuple[Dict[str, Set[Path]], Dict[Tuple[str, str], Set[Path]]]:
    logging.info("Preparing secure preload download...")

    def iter_secure_assets():
        for server_path, asset in staged_assets.items():
            if asset.file_name.suffix.lower() in {".pak", ".sdl"}:
                yield server_path, asset

    secure_manifests = defaultdict(set)
    if manifest:
        secure_manifests["SecurePreloader"].update((i[0] for i in iter_secure_assets()))
    secure_lists = defaultdict(set)
    for server_path, staged_asset in iter_secure_assets():
        secure_lists[(staged_asset.file_name.parent.name, staged_asset.file_name.suffix.lower()[1:])].add(server_path)
    return secure_manifests, secure_lists

def merge_manifests(age_manifests: Dict[str, Set[Path]], client_manifests: Dict[str, Set[Path]],
                    secure_manifests: Dict[str, Set[Path]]) -> Dict[str, Set[Path]]:
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

def nuke_unstaged_assets(cached_db: AssetDatabase, staged_assets: Dict[Path, manifest.ManifestEntry],
                         mfs_path: Path, list_path: Path):
    logging.info("Nuking unstaged assets...")

    cached_assets, manifests, lists = cached_db.assets, cached_db.manifests, cached_db.lists
    deleted_assets = frozenset(cached_assets.keys()) - frozenset(staged_assets.keys())

    def unlink_asset(server_path: Path):
        logging.trace(f"Unlinking asset '{server_path}'")
        if server_path.name.lower().endswith(".prp.gz"):
            logging.error(f"Unlinking page '{server_path}' -- this may cause issues on legacy clients!")

        deletions = 0
        for entry in itertools.chain(itertools.chain.from_iterable(manifests.values()),
                                     itertools.chain.from_iterable(lists.values())):
            if entry.file_name == server_path:
                deletions += unlink_entry(entry)
        return deletions

    def unlink_entry(entry: Union[manifest.ListEntry, manifest.ManifestEntry]) -> int:
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
