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

import argparse
from collections import defaultdict
import functools
import logging
from pathlib import Path
from PyHSPlasma import *
import sys
import tempfile
import time

import assets
from config import dump_default_config, read_config
import commit
import dependencies
import manifest, dirtsand

program_description = "Uru Manifest Generator"
main_parser = argparse.ArgumentParser(description=program_description)
main_parser.add_argument("--config", type=Path, help="config file", default="config.ini")
main_parser.add_argument("--log-file", type=Path)

log_group = main_parser.add_mutually_exclusive_group()
log_group.add_argument("-d", "--debug", action="store_true", help="print debug log output")
log_group.add_argument("-q", "--quiet", action="store_true", help="only print critical information")
log_group.add_argument("-v", "--verbose", action="store_true", help="print trace-level log output")

sub_parsers = main_parser.add_subparsers(title="command", dest="command", required=True)
dumpconfig_parser = sub_parsers.add_parser("dumpconfig")

generate_parser = sub_parsers.add_parser("generate")
generate_parser.add_argument("--dry-run", action="store_true", default=False)
generate_parser.add_argument("--threads", type=int, help="maximum worker thread count", default=0)

def dumpconfig(args):
    dump_default_config(args.config)
    return True

def generate(args):
    config = read_config(args.config)

    try:
        db_type = config.get("server", "type")
        mfs_path = config.getoutdirpath("output", "manifests")
        list_path = config.getoutdirpath("output", "lists")
        game_data_path = config.getindirpath("source", "data_path")
        game_scripts_path = config.getindirpath("source", "scripts_path")
        gather_path = config.getindirpathopt("source", "gather_path")
    except ValueError as e:
        # reraise as AssetError so config errors look sane.
        raise assets.AssetError(f"Config problem: {e}")

    cached_db = assets.load_asset_database(mfs_path, list_path, db_type)
    prebuilts = assets.load_prebuilt_assets(game_data_path, game_scripts_path)
    gathers = assets.load_gather_assets(gather_path)
    source_assets = assets.merge_asset_dicts(prebuilts, gathers)

    ncpus = args.threads if args.threads > 0 else None
    staged_assets = defaultdict(manifest.ManifestEntry)
    age_manifests = dependencies.find_age_dependencies(source_assets, staged_assets, ncpus)
    client_manifests = dependencies.find_client_dependencies(source_assets, staged_assets)
    dependencies.find_script_dependencies(source_assets, staged_assets)

    with tempfile.TemporaryDirectory() as td:
        temp_path = Path(td)
        if args.dry_run:
            list_path, mfs_path = out_path, out_path

        commit.encrypt_staged_assets(source_assets, staged_assets, temp_path, config["server"]["droid_key"])
        commit.hash_staged_assets(source_assets, staged_assets, ncpus)
        commit.find_dirty_assets(cached_db.assets, staged_assets)

        # Need to merge everything before we can begin the compress proc
        secure_manifests, secure_lists = commit.make_secure_downloads(staged_assets, config["server"]["secure_manifest"])
        manifests = commit.merge_manifests(age_manifests, client_manifests, secure_manifests)

        commit.compress_dirty_assets(manifests, cached_db.assets, source_assets, staged_assets, mfs_path, ncpus)
        commit.copy_secure_assets(secure_lists, source_assets, staged_assets, list_path)

        assets.save_asset_database(staged_assets, manifests, secure_lists, mfs_path, list_path, db_type)

    return True

if __name__ == "__main__":
    start_time = time.perf_counter()
    args = main_parser.parse_args()

    # HACK: add a trace log level for even more noisy debugging stuff
    logging.addLevelName(5, "TRACE")
    logging.trace = functools.partial(logging.log, 5)

    if args.quiet:
        level = logging.ERROR
    elif args.verbose:
        level = 5
    elif args.debug:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(format="[%(asctime)s] %(levelname)s: %(message)s", level=level)
    if getattr(args.log_file, "name", None):
        handler = logging.FileHandler(args.log_file.with_suffix(".log"), mode="w")
        logging.getLogger().addHandler(handler)
    logging.debug(f"{program_description} __main__...")
    logging.trace("harro!")

    try:
        plDebug.Init(plDebug.kDLNone)

        # Go go go
        try:
            result = globals()[args.command](args)
        except assets.AssetError as e:
            logging.error(str(e))
            raise
        except Exception as e:
            # Programming error
            logging.exception("Uncaught exception!", exc_info=e)
            raise
    except:
        result = False
    finally:
        end_time = time.perf_counter()
        delta = end_time - start_time
        if not result:
            logging.error(f"{program_description} exiting with errors in {delta:.2f}s.")
        else:
            logging.info(f"{program_description} completed successfully in {delta:.2f}s.")
        sys.exit(0 if result else 1)
