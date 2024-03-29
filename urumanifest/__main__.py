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
from contextlib import ExitStack
import functools
import itertools
import logging
from pathlib import Path
import shutil
import sys
import tempfile
import time

import assets
from config import dump_default_config, read_config
import commit
import dependencies
import github
import manifest, dirtsand, moss
import plasma_python
import utils
import working

program_description = "Uru Manifest Generator"
main_parser = argparse.ArgumentParser(description=program_description)
main_parser.add_argument("--config", type=Path, help="config file", default="config.ini")
main_parser.add_argument("--log-file", type=Path)

log_group = main_parser.add_mutually_exclusive_group()
log_group.add_argument("-d", "--debug", action="store_true", help="print debug log output")
log_group.add_argument("-q", "--quiet", action="store_true", help="only print critical information")
log_group.add_argument("-v", "--verbose", action="store_true", help="print trace-level log output")

# Begin HAX: `required` argument for add_sub_parsers added in Python 3.7...
_sub_parsers_call = functools.partial(main_parser.add_subparsers, title="command", dest="command")
if (sys.version_info[0] == 3 and sys.version_info[1] >= 7) or sys.version_info[0] > 3:
    sub_parsers = _sub_parsers_call(required=True)
else:
    sub_parsers = _sub_parsers_call()
# End HAX

def _add_pull_args(parser):
    parser.add_argument("--head", action="store_true", default=False, help="use the latest HEAD from the remote")
    parser.add_argument("--revision", type=str, help="use a specific revision (commit sha)")

dumpconfig_parser = sub_parsers.add_parser("dumpconfig")

generate_parser = sub_parsers.add_parser("generate")
method_group = generate_parser.add_mutually_exclusive_group()
method_group.add_argument("--dry-run", action="store_true", default=False, help="don't produce any output")
method_group.add_argument("--force", action="store_true", default=False, help="force regeneration of all files")
method_group.add_argument("--stage", action="store_true", default=False, help="stage the delta into the staging directory")
generate_parser.add_argument("--reuse-python", action="store_true", default=False, help="skip regeneration of python.pak and reuse existing generated python assets")
generate_parser.add_argument("--threads", type=int, help="maximum worker thread count", default=0)
pull_group = generate_parser.add_mutually_exclusive_group()
pull_group.add_argument("--no-pull-gha", action="store_true", default=False, help="don't download the game client from github")
specific_ex_group = pull_group.add_mutually_exclusive_group()
_add_pull_args(specific_ex_group)
pull_group.add_argument("--rebuild-working", choices=["engine", "assets"], nargs="*", help="rebuild working branches")

pull_parser = sub_parsers.add_parser("pull-gha")
_add_pull_args(pull_parser.add_mutually_exclusive_group())
pull_parser.add_argument("--dry-run", action="store_true", default=False, help="don't produce any output")

working_parser = sub_parsers.add_parser("rebuild-working")
act_group = working_parser.add_mutually_exclusive_group()
act_group.add_argument("--dry-run", action="store_true", default=False, help="don't produce any output")
act_group.add_argument("--push", action="store_true", default=False, help="push the result to upstream")
skip_group = working_parser.add_argument_group()
skip_group.add_argument("--no-engine", action="store_true", help="don't rebuild the engine working branch")
skip_group.add_argument("--no-assets", action="store_true", help="don't rebuild the assets working branch")

def dumpconfig(args):
    dump_default_config(args.config)
    return True

def generate(args):
    config = read_config(args.config)

    try:
        db_type = config.get("server", "type")

        mfs_path_in = config.getoutdirpath("output", "manifests")
        list_path_in = config.getoutdirpath("output", "lists")
        server_age_path_in = config.getoutdirpath("server", "age_directory")
        server_sdl_path_in = config.getoutdirpath("server", "sdl_directory")
        if args.stage:
            mfs_path_out = config.getoutdirpath("stage", "manifests")
            list_path_out = config.getoutdirpath("stage", "lists")
            server_age_path_out = config.getoutdirpath("stage", "age_directory")
            server_sdl_path_out = config.getoutdirpath("stage", "sdl_directory")
        else:
            mfs_path_out, list_path_out = mfs_path_in, list_path_in
            server_age_path_out, server_sdl_path_out = server_age_path_in, server_sdl_path_in

        game_data_path = config.getindirpath("source", "data_path")
        game_scripts_path = config.getindirpath("source", "scripts_path")
        gather_path = config.getindirpathopt("source", "gather_path")

        droid_key = utils.get_droid_key(config.get("server", "droid_key"))
        make_preloader_mfs = config.getboolean("server", "secure_manifest")

        py_version = (config.getint("python", "major"), config.getint("python", "minor"))
        py_exe = config.getinfilepathopt("python", "path")

        repo = config.get("github", "repository")
        branch = config.get("github", "branch")
        token = config.get("github", "token")
        gha_staging_path_in = config.getoutdirpath("github", "staging_path")

        if args.rebuild_working is not None:
            rebuild_working_engine = not args.rebuild_working or "engine" in args.rebuild_working
            rebuild_working_assets = not args.rebuild_working or "assets" in args.rebuild_working
        else:
            rebuild_working_engine = False
            rebuild_working_assets = False

        if rebuild_working_engine:
            engine_repo_path = config.getindirpath("working", "engine_repo")
        else:
            engine_repo_path = None
        if rebuild_working_assets:
            assets_repo_path = config.getindirpath("working", "assets_repo")
        else:
            assets_repo_path = None
    except Exception as e:
        # reraise as AssetError so config errors look sane.
        raise assets.AssetError(f"Config problem: {e}")

    # Rebuild the working branches first.
    if rebuild_working_engine or rebuild_working_assets:
        if args.dry_run:
            raise assets.AssetError("Rebuilding working branches and dry run are mutually exclusive.")
        if engine_repo_path is not None and not utils.is_path_relative_to(engine_repo_path, game_scripts_path):
            raise assets.AssetError(
                "The provided game scripts path is not a subdirectory of the engine repository. "
                "Rebuilding the engine's working branch is a spurious operation."
            )
        if assets_repo_path is not None and not utils.is_path_relative_to(assets_repo_path, game_data_path):
            raise assets.AssetError(
                "The provided game data path is not a subdirectory of the assets repository. "
                "Rebuilding the assets' working branch is a spurious operation."
            )

        working_path = gather_path.joinpath("working.json")
        if not working_path.exists():
            raise assets.AssetError(f"The working branch instructions file '{working_path}' is missing!")
        working.rebuild_working_branch(
            engine_repo_path,
            assets_repo_path,
            working_path,
            token,
            dry_run=False,
            push=True
        )

    # If we are staging, we'll want to clear out the contents of the staging paths.
    if args.stage:
        logging.info("Clearing staging directories...")
        staging_dirs = [mfs_path_out.iterdir(), list_path_out.iterdir()]
        if server_age_path_out:
            staging_dirs.append(server_age_path_out.iterdir())
        if server_sdl_path_out:
            staging_dirs.append(server_sdl_path_out.iterdir())
        for i in itertools.chain.from_iterable(staging_dirs):
            if i.exists() and i.is_dir():
                shutil.rmtree(i)
            else:
                i.unlink(missing_ok=True)
        logging.warn("No output will be staged for DELETED content.")

    # Find python2-compatible schtuff
    if py_exe and py_exe.is_file() and utils.check_python_version(py_exe, py_version):
        logging.debug(f"Using configured Python executable: {py_exe}")
    else:
        py_exe = utils.find_python_exe(py_version)
        if not utils.check_python_version(py_exe, py_version):
            py_exe = None
    if not py_exe:
        logging.critical(f"Could not find Python {py_version[0]}.{py_version[1]}")

    with tempfile.TemporaryDirectory() as td:
        temp_path = Path(td)
        if args.dry_run:
            list_path_out, mfs_path_out = temp_path, temp_path

            # dry-run forces these files to be copied for testing purposes
            server_age_path_out = temp_path.joinpath("server_age_files")
            server_sdl_path_out = temp_path.joinpath("server_sdl_files")

        all_gather_paths = [i for i in gather_path.iterdir() if i.is_dir()]
        if not args.no_pull_gha:
            if args.dry_run:
                gha_staging_path_out = temp_path.joinpath("gha_staging")
            else:
                gha_staging_path_out = gha_staging_path_in

            rev = "HEAD" if args.head else args.revision
            gha_gathers = github.find_client_gather_paths(
                gha_staging_path_in,
                gha_staging_path_out,
                game_scripts_path,
                repo, branch,
                rev,
                token
            )
            all_gather_paths.extend(gha_gathers)

        cached_db = assets.load_asset_database(mfs_path_in, list_path_in, db_type)
        prebuilts = assets.load_prebuilt_assets(game_data_path, game_scripts_path, py_exe)
        gathers = assets.load_gather_assets(*all_gather_paths)
        source_assets = assets.merge_asset_dicts(prebuilts, gathers)

        ncpus = args.threads if args.threads > 0 else None
        staged_assets = defaultdict(manifest.ManifestEntry)
        age_manifests = dependencies.find_age_dependencies(source_assets, staged_assets, ncpus)
        client_manifests = dependencies.find_client_dependencies(source_assets, staged_assets)
        dependencies.find_script_dependencies(source_assets, staged_assets)

        if args.reuse_python:
            # Dry runs can overwrite the list output path with a temp location. We want to use the
            # old output as input, so get the actual value.
            cfg_list_path = config.getoutdirpath("output", "lists")
            plasma_python.reuse(cached_db.lists, source_assets, staged_assets, cfg_list_path)
        else:
            plasma_python.process(source_assets, staged_assets, temp_path, droid_key, py_exe, py_version)

        commit.copy_server_assets(source_assets, staged_assets, server_age_path_in, server_sdl_path_in,
                                  server_age_path_out, server_sdl_path_out, ncpus)

        commit.encrypt_staged_assets(source_assets, staged_assets, temp_path, droid_key, ncpus)
        commit.hash_staged_assets(source_assets, staged_assets, ncpus)
        commit.find_dirty_assets(cached_db.assets, staged_assets)

        # Need to merge everything before we can begin the compress proc
        secure_manifests, secure_lists = commit.make_secure_downloads(staged_assets, make_preloader_mfs)
        manifests = commit.merge_manifests(age_manifests, client_manifests, secure_manifests)

        commit.compress_dirty_assets(manifests, cached_db.assets, source_assets, staged_assets,
                                     mfs_path_out, args.force, ncpus)
        commit.copy_secure_assets(secure_lists, source_assets, staged_assets, list_path_in, list_path_out,
                                  droid_key, ncpus)
        commit.nuke_unstaged_assets(cached_db, staged_assets, mfs_path_out, list_path_out)
        assets.nuke_dead_manifests(cached_db.manifests, cached_db.lists, manifests, secure_lists,
                                   mfs_path_out, list_path_out, db_type)
        assets.save_asset_database(cached_db.manifests, cached_db.lists, staged_assets, manifests,
                                   secure_lists, mfs_path_out, list_path_out, db_type, droid_key)

    return True

def pull_gha(args):
    config = read_config(args.config)

    try:
        repo = config.get("github", "repository")
        branch = config.get("github", "branch")
        token = config.get("github", "token")
        gha_staging_path_in = config.getoutdirpath("github", "staging_path")
        game_scripts_path = config.getindirpath("source", "scripts_path")
    except Exception as e:
        # reraise as AssetError so config errors look sane.
        raise assets.AssetError(f"Config problem: {e}")

    with ExitStack() as stack:
        if args.dry_run:
            temp_path = stack.enter_context(tempfile.TemporaryDirectory())
            gha_staging_path_out = Path(temp_path)
        else:
            gha_staging_path_out = gha_staging_path_in

        rev = "HEAD" if args.head else args.revision
        gather_paths = github.find_client_gather_paths(
            gha_staging_path_in,
            gha_staging_path_out,
            game_scripts_path,
            repo,
            branch,
            rev,
            token
        )

        # Exhaust the generator
        for i in gather_paths:
            logging.info(f"Workflow gather path: {i}")

    return True

def rebuild_working(args):
    config = read_config(args.config)

    try:
        token = config.get("github", "token")
        gather_path = config.getindirpath("source", "gather_path")
        if not args.no_engine:
            engine_path = config.getindirpath("working", "engine_repo")
        else:
            engine_path = None
        if not args.no_assets:
            assets_path = config.getindirpath("working", "assets_repo")
        else:
            assets_path = None
    except Exception as e:
        # reraise as AssetError so config errors look sane.
        raise assets.AssetError(f"Config problem: {e}")

    working_path = gather_path.joinpath("working.json")
    if not working_path.exists():
        raise assets.AssetError(f"The working branch instructions file '{working_path}' is missing!")

    working.rebuild_working_branch(
        engine_path,
        assets_path,
        working_path,
        token,
        args.dry_run,
        args.push
    )
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

    logging_kwargs = {}
    logging_kwargs["format"] = "[%(asctime)s] %(levelname)s: %(message)s"
    logging_kwargs["level"] = level
    if getattr(args.log_file, "name", None):
        file_handler = logging.FileHandler(args.log_file.with_suffix(".log"), mode="w")
        stream_handler = logging.StreamHandler()
        logging_kwargs["handlers"] = (file_handler, stream_handler)
    logging.basicConfig(**logging_kwargs)
    logging.debug(f"{program_description} __main__...")

    try:
        # Go go go
        try:
            cmdcall = globals().get(args.command.replace("-", "_"))
            if cmdcall:
                result = cmdcall(args)
            else:
                logging.error("No command specified. Use `-h` to see help.")
                result = False
        except (assets.AssetError, github.GitHubError) as e:
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
