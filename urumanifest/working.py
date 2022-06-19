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

import concurrent.futures
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import *

import github

class WorkingRebuildException(Exception):
    pass


@dataclass
class _RefCommit:
    type: str
    url: str
    ref: str
    sha: Optional[str] = None

def _get_ref_sha(url: str, ref: str) -> Tuple[int, str]:
    ref_sha_result = subprocess.run(
        ["git", "ls-remote", "--exit-code", url, ref],
        stdout=subprocess.PIPE,
        text=True
    )
    if ref_sha_result.returncode == 2:
        return ref
    elif ref_sha_result.returncode == 0:
        sha = re.match(r"(?P<sha>\w+)\s", ref_sha_result.stdout).group("sha")
        logging.debug(f"Mapped {ref} to {sha}")
        return sha
    else:
        raise RuntimeError()

def _iter_refs(defns: Dict[str, Any], token: Optional[str] = None) -> Iterable[_RefCommit]:
    def _iter_defns(*keys):
        for i in keys:
            value = defns.get(i)
            if isinstance(value, list):
                yield from ((i, j) for j in value)
            elif value:
                yield (i, value)

    def _iter_ref_commits(arg: Tuple[str, Dict[str, str]]) -> _RefCommit:
        tp, entry = arg
        if "pull_request" in entry:
            pr = gh.get_pull_request(defns["origin"]["fork"], entry["pull_request"])
            logging.debug(f"Mapped PR #{entry['pull_request']} to {pr.sha}")
            return _RefCommit(
                tp,
                f"git@github.com:{pr.fork}.git",
                pr.ref,
                pr.sha
            )
        elif "ref" in entry:
            url = f"git@github.com:{entry['fork']}.git"
            ref = entry["ref"]
            return _RefCommit(tp, url, ref, _get_ref_sha(url, ref))
        else:
            raise ValueError()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        gh = github.GitHub(token)
        result_iter = executor.map(
            _iter_ref_commits,
            _iter_defns("origin", "upstream", "cherry-pick"),
            chunksize=4
        )
        yield from result_iter

def _fetch_all_refs(output_path: Path, defns: Dict[str, Any], token: Optional[str] = None) -> Iterable[_RefCommit]:
    logging.info("Fetching all refs...")

    for i in _iter_refs(defns, token):
        subprocess.run(
            ["git", "fetch", "--no-recurse-submodules", i.url, i.sha],
            cwd=output_path,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            check=True
        )
        lfs_fetch = subprocess.run(
            ["git", "lfs", "fetch", i.url, i.sha],
            cwd=output_path,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        if lfs_fetch.returncode != 0:
            logging.warning("LFS fetch failed, this means bad things may happen...")

        # LTGM, yield the commit for future use
        yield i

def _check_branch(output_path: Path, branch_name: str) -> bool:
    verify_branch = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=output_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return verify_branch.returncode == 0

def _delete_branch(output_path: Path, branch_name: str, safe_ref: str = "master") -> bool:
    logging.debug(f"Deleting branch '{branch_name}'")

    if not _check_branch(output_path, branch_name):
        logging.debug("Branch did not exist!")
        return False

    head_abbrev = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=output_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True
    )
    if head_abbrev.returncode == 0  and head_abbrev.stdout.strip() == branch_name:
        logging.debug(f"Switching to '{safe_ref}'...")
        subprocess.run(
            ["git", "checkout", safe_ref],
            cwd=output_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True
        )

    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=output_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        text=True
    )
    return True

def _create_branch(output_path: Path, branch_name: str, ref: str):
    logging.debug(f"Creating branch '{branch_name}'...")

    already_exists = _check_branch(output_path, branch_name)
    if already_exists:
        logging.debug(f"Branch '{branch_name}' already exists! Switching to it.")
        switch_to = branch_name
    else:
        switch_to = ref

    subprocess.run(
        ["git", "checkout", switch_to],
        cwd=output_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        text=True
    )
    if not already_exists:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=output_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True
        )
        return True
    return False

def _merge(output_path: Path, origin: _RefCommit, upstream: _RefCommit, ref: _RefCommit, dry_run: bool = False):
    # These are not things we act on, so bail.
    if ref.type in {"upstream", "origin"}:
        return
    logging.info(f"Pulling {ref.sha} from {ref.url}")

    # Make sure this is not already merged... Otherwise, bad things happen.
    is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ref.sha, origin.sha],
        cwd=output_path,
        stdout=subprocess.DEVNULL,
        text=True
    )
    if is_ancestor.returncode == 0:
        logging.warning(f"The commit {ref.sha} is an ancestor of the origin {upstream.sha} - remove '{ref.ref}' from the working.json!")
        return
    else:
        logging.debug(f"{ref.sha} does not seem to be an ancestor of {upstream.sha}, merge taim!")

    # Figure out the branch point from the base ref (origin) so we can apply the
    # range of commits to the working branch.
    merge_base = subprocess.run(
        ["git", "merge-base", origin.sha, ref.sha],
        cwd=output_path,
        stdout=subprocess.PIPE,
        check=False,
        text=True
    )
    if merge_base.returncode != 0:
        raise WorkingRebuildException(f"We weren't able to resolve a common ancestor for {ref.ref}")

    rev_range = f"{merge_base.stdout.strip()}..{ref.sha}"
    if ref.type == "cherry-pick":
        logging.debug(f"Cherry-picking {rev_range} into the working branch...")
        if not dry_run:
            environ = os.environ.copy()
            environ["GIT_LFS_SKIP_SMUDGE"] = "1"
            cherry_pick = subprocess.run(
                ["git", "cherry-pick", "--allow-empty", "-n", rev_range],
                cwd=output_path,
                env=environ,
                stdout=subprocess.DEVNULL,
                text=True
            )
            if cherry_pick.returncode != 0:
                # We were cherry-picking to the index, so we need to reset.
                subprocess.run(
                    ["git", "reset", "--hard", "master"],
                    cwd=output_path,
                    stdout=subprocess.DEVNULL,
                    text=True
                )
                subprocess.run(
                    ["git", "cherry-pick", "--abort"],
                    cwd=output_path,
                    stdout=subprocess.DEVNULL,
                    text=True
                )
                _delete_branch(output_path, upstream.ref)
                raise WorkingRebuildException("cherry-pick failed; everything has been deleted.")

            # Squash it all down as a single commit to avoid polluting the history.
            commit_log = subprocess.run(
                ["git", "log", "--reverse", "--pretty=format:%h %an <%ae>\n%B", rev_range],
                cwd=output_path,
                stdout=subprocess.PIPE,
                check=True,
                text=True
            )
            if ref.ref != ref.sha:
                message = f"Squash cherry-pick {ref.ref} from {ref.url}\n\n{commit_log.stdout.strip()}"
            else:
                message = f"Squash cherry-pick {ref.sha}\n\n{commit_log.stdout.strip()}"
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=output_path,
                stdout=subprocess.DEVNULL,
                check=True,
                text=True
            )

def _lfs_fetch(output_path: Path):
    logging.info("Checking out files from git-lfs...")
    subprocess.run(
        ["git", "lfs", "checkout"],
        cwd=output_path,
        stdout=subprocess.DEVNULL,
        check=True,
        text=True
    )

def _push(output_path: Path, url: str, ref: str):
    logging.info(f"Pushing result to {url}...")
    subprocess.run(
        ["git", "push", "--no-verify", "-f", url, f"{ref}:{ref}"],
        cwd=output_path,
        stdout=subprocess.PIPE,
        check=True,
        text=True
    )

def _rebuild_working_branch_for_repo(repo_name: str, output_path: Path, defns: Dict[str, Any],
                                     token: Optional[str] = None, dry_run: bool = False,
                                     push: bool = False):
    logging.info(f"Rebuilding working branch for {repo_name}...")

    # Sanity...
    required_keys = {"origin", "upstream"}
    my_keys = frozenset(defns.keys())
    if not my_keys >= required_keys:
        missing_keys = required_keys - my_keys
        raise WorkingRebuildException(f"working.json missing '{', '.join(missing_keys)}' for the {repo_name} repository.")

    all_refs = list(_fetch_all_refs(output_path, defns, token))
    origin = next((i for i in all_refs if i.type == "origin"))
    upstream = next((i for i in all_refs if i.type == "upstream"))
    if not dry_run:
        _delete_branch(output_path, upstream.ref)
        _create_branch(output_path, upstream.ref, origin.sha)
    for ref in all_refs:
        _merge(output_path, origin, upstream, ref, dry_run)
    _lfs_fetch(output_path)
    if push and not dry_run:
        _push(output_path, upstream.url, upstream.ref)

def rebuild_working_branch(engine_repo: Optional[Path], assets_repo: Optional[Path],
                           defns_path: Path, token: Optional[str] = None,
                           dry_run: bool = False, push: bool = False):
    logging.info("Rebuilding all working branches...")
    if not defns_path.exists():
        logging.error(f"Definitions file {defns_path} was not found. Skipping.")
        return

    repos = dict(assets=assets_repo, engine=engine_repo)
    with defns_path.open("r") as defns_file:
        defns = json.load(defns_file)
        for repo_name, repo_defns in defns.items():
            if repos[repo_name]:
                _rebuild_working_branch_for_repo(repo_name, repos[repo_name], repo_defns, token, dry_run, push)
            else:
                logging.info(f"Skipping '{repo_name}' repo!")
