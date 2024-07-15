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
from contextlib import closing, contextmanager, ExitStack
from dataclasses import asdict, dataclass, field
import functools
import itertools
import logging
import math
import json
import os
from pathlib import Path, PurePosixPath
import requests
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple
import zipfile

from constants import *

import tqdm
import tqdm.contrib.logging as tqdm_logging

class GitHubActionsArtifact(NamedTuple):
    name: str
    size: int
    url: str
    expired: bool


class GitHubError(Exception):
    pass


class GitHubPullRequest(NamedTuple):
    name: str
    fork: str
    ref: str
    sha: str


class GitHub:
    def __init__(self, token: Optional[str] = None):
        if not token:
            logging.warning("No GitHub token was supplied - this may cause GitHub to rate-limit you!")
        self._token = token

    def _invoke_request(self, method: str, endpoint: str, **query) -> Dict[str, Any]:
        url = f"https://api.github.com{endpoint}"
        logging.trace(f"Sending {method} request to {url}")

        headers = dict(Accept="application/vnd.github.v3+json")
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            res = requests.request(method, url, json=query, headers=headers)
            res.raise_for_status()
        except requests.RequestException as e:
            if e.response.status_code == 403:
                raise GitHubError("GitHub says 'verbotten'... You may be rate-limited.")
            raise
        else:
            return res.json()

    def _invoke_paginated_request(self, method: str, endpoint: str, **query) -> Iterator[Dict[str, Any]]:
        count_per_req = 30
        i, num_pages = 1, 2
        while i < num_pages:
            kwargs = dict(**query)
            kwargs["per_page"] = count_per_req
            kwargs["page"] = i
            result = self._invoke_request(method, endpoint, **kwargs)
            num_pages = math.ceil(result["total_count"] / count_per_req)
            yield result
            i += 1

    def get_head(self, repo: str, branch: str) -> str:
        logging.debug(f"Looking up the current HEAD of branch {branch}")
        try:
            result = self._invoke_request(
                "GET",
                f"/repos/{repo}/branches/{branch}"
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise GitHubError(f"Repository '{repo}' branch {branch} was not found.")
            raise
        else:
            return result["commit"]["sha"]

    def get_pull_request(self, repo: str, pr: int) -> GitHubPullRequest:
        logging.debug(f"Looking up pull request #{pr}")

        result = self._invoke_request(
            "GET",
            f"/repos/{repo}/pulls/{pr}"
        )
        return GitHubPullRequest(
            result["title"],
            result["head"]["repo"]["full_name"],
            result["head"]["ref"],
            result["head"]["sha"]
        )

    def get_workflow_run(self, repo: str, rev: str, run_name: str = "CI") -> Optional[int]:
        logging.debug(f"Looking up Actions run ID for commit {rev}")
        all_pages = self._invoke_paginated_request(
            "GET",
            f"/repos/{repo}/actions/runs",
            event="push"
        )
        for result in all_pages:
            matching_run = next((run for run in result["workflow_runs"] if run["name"] == run_name and run["head_sha"] == rev), None)
            if matching_run is not None:
                logging.debug(f"Found run {matching_run['id']} (status: {matching_run['status']})")
                return matching_run["id"]

        raise GitHubError(f"No workflow run found for revision {rev}")

    def get_workflow_artifacts(self, repo: str, run: int) -> Iterator[GitHubActionsArtifact]:
        logging.debug(f"Requesting download links for Actions run ID {run}")

        result = self._invoke_paginated_request(
            "GET",
            f"/repos/{repo}/actions/runs/{run}/artifacts",
        )

        for artifact in itertools.chain.from_iterable(i["artifacts"] for i in result):
            logging.debug(f"Found artifact: {artifact['name']}")
            yield GitHubActionsArtifact(
                artifact["name"],
                int(artifact["size_in_bytes"]),
                artifact["archive_download_url"],
                bool(artifact["expired"])
            )

    def get_workflow_result(self, repo: str, run: int) -> str:
        def query_result() -> str:
            import time
            while True:
                result = self._invoke_request(
                    "GET",
                    f"/repos/{repo}/actions/runs/{run}"
                )
                if result["status"] == "completed":
                    return result["conclusion"]
                time.sleep(30.0)

        # I would generally prefer asyncio, but UruManifest in general does not use asyncio at all,
        # and Python 3.6's asyncio is prehistoric compared to the improvements made in 3.7. Better
        # to just run this in a worker thread ("pool").
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            fut = executor.submit(query_result)
            try:
                # Don't wait for longer than about two hours on this.
                result = fut.result(timeout=120 * 60)
            except concurrent.futures.TimeoutError:
                raise GitHubError("The workflow is taking too long to complete.")
            else:
                return result

    def download_file(self, url: str, hook: Callable[[int, int, int], None] = None, delete: bool = True) -> tempfile.NamedTemporaryFile:
        logging.trace(f"Downloading {url}")
        headers = dict()
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"

        res = requests.get(url, headers=headers, stream=True)
        with closing(res) as req:
            fp = tempfile.NamedTemporaryFile("w+b", delete=delete)
            size = 0
            for content in req.iter_content(None):
                fp.write(content)
                chunksize = len(content)
                size += chunksize
                if hook is not None:
                    hook(chunksize, size, req.headers.get("Content-Length", -1))
            fp.seek(0)
            return fp


@contextmanager
def download_artifacts(*artifact_names: Sequence[str], repo: str, branch: str = "master",
                       rev: str = None, token: Optional[str] = None,
                       delete: bool = True) -> Dict[str, tempfile.NamedTemporaryFile]:
    logging.info(f"Downloading {len(artifact_names)} artifacts...")

    gh = GitHub(token)
    if rev is None:
        rev = gh.get_head(repo, branch)
    run = gh.get_workflow_run(repo, rev)
    conclusion = gh.get_workflow_result(repo, run)
    if conclusion != "success":
        raise GitHubError(f"The CI run did not succeed (GHA says: {conclusion})")

    ci_artifacts = list(gh.get_workflow_artifacts(repo, run))
    if not ci_artifacts:
        logging.warning("Hmm... There were no artifacts returned by the CI run. This smells fishy...")
    if any((i.expired for i in ci_artifacts)):
        raise GitHubError("One or more of the CI artifacts have expired.")

    with ExitStack() as outer_stack:
        def report_hook(chunk_bytes: int, downloaded_bytes: int, total_bytes: int, *, progress_bar):
            progress_bar.update(chunk_bytes)

        def iter_downloads():
            for i, name in enumerate(artifact_names):
                my_artifact = next((i for i in ci_artifacts if i.name == name), None)
                if my_artifact is None:
                    raise GitHubError(f"Artifact {name} is missing?")
                yield name, my_artifact.url

        downloads = {}

        def on_download_done(fut: concurrent.futures.Future, *, name, progress_bar):
            logging.debug(f"Downloaded: {name}")
            downloads[name] = outer_stack.enter_context(fut.result())
            if progress_bar is not None:
                progress_bar.close()
            fut.result()

        progress = tqdm_logging.tqdm_logging_redirect(
            desc=f"Downloading {len(artifact_names)} of {len(ci_artifacts)} workflow artifacts",
            leave=False,
            total=float("inf"),  # Ideally, this would be my_artifact.size, but that's the uncompressed size.
            unit="bytes",
            unit_scale=True
        )

        with progress as progress, concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            for name, url in iter_downloads():
                fut = executor.submit(gh.download_file, url, functools.partial(report_hook, progress_bar=progress), delete)
                fut.add_done_callback(functools.partial(on_download_done, name=name, progress_bar=progress))

        # Progress bars are terminated at this level.
        yield downloads

@dataclass
class _WorkflowDatabase:
    current_sha: str = "UNSPECIFIED"
    workflow_gathers: Dict[str, List[str]] = field(default_factory=dict)
    valid: bool = True


@contextmanager
def _generate_database(input_path: Path, output_path: Path) -> _WorkflowDatabase:
    def _load_workflow_database(staging_path: Path) -> _WorkflowDatabase:
        database_path = staging_path.joinpath("gha_workflow.json")
        if not database_path.exists():
            return _WorkflowDatabase(valid=False)

        with database_path.open("r") as fp:
            database = json.load(fp)
        return _WorkflowDatabase(**database)

    def _save_workflow_database(staging_path: Path, database: Optional[_WorkflowDatabase] = None) -> None:
        database_path = staging_path.joinpath("gha_workflow.json")
        if database is None:
            database = _WorkflowDatabase(valid=False)

        with database_path.open("w") as fp:
            json.dump(asdict(database), fp, indent=2)

    database = _load_workflow_database(input_path)
    try:
        yield database
    except Exception:
        logging.debug("An exception happened while the GHA workflow database was open, invalidating it.")
        database.valid = False
        raise
    finally:
        _save_workflow_database(output_path, database)

def find_client_gather_paths(input_path: Path, output_path: Path, game_path: Path, repo: str,
                             branch: str, rev: str = "", token: str = "") -> Iterator[Path]:
    if not token:
        logging.warning("Skipping GHA workflow integration because no token was provided.")
        return

    logging.info("Checking client executables..")

    with _generate_database(input_path, output_path) as database:
        up_to_date, desired_rev = _have_desired_artifacts(game_path, database, repo, branch, rev, token)
        if not up_to_date:
            _update_artifacts(output_path, database, repo, desired_rev, token)
            result_path = output_path
        else:
            logging.debug("Artifacts are already up-to-date, great!")
            result_path = input_path
        for i in database.workflow_gathers.values():
            yield result_path.joinpath(i)

def _have_desired_artifacts(game_path: Path, database: _WorkflowDatabase, repo: str, branch: str, rev: str, token: str) -> Tuple[bool, str]:
    logging.debug("Checking for desired artifacts...")

    current_rev = database.current_sha if database.valid else None
    if not rev and rev != "HEAD":
        # Make sure that we actually have git, first.
        info = subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if info.returncode == 0:
            info = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding=sys.getdefaultencoding(),
                cwd=game_path
            )
            if info.returncode == 0:
                rev = info.stdout.rstrip()
            else:
                logging.warning(f"Unable to determine the current revision of the engine fork: {info.stderr}")
        else:
            logging.warning(f"Unable to determine the current revision of the engine fork: git is not available")
    elif rev != "HEAD":
        logging.debug(f"Using forced revision {rev}")
    else:
        rev = GitHub(token).get_head(repo, branch)
        logging.debug(f"Using current HEAD revision {rev}")

    # We're only up-to-date if the revisions match and we have all of the expected gathers.
    missing = frozenset(workflow_lut.keys()) - frozenset(database.workflow_gathers.keys())
    if missing:
        logging.debug(f"Missing {len(missing)} workflow packages, refetching...")
    if current_rev != rev:
        logging.debug(f"Revision mismatch: {current_rev} != {rev}")
    up_to_date = not missing and current_rev == rev

    return (up_to_date, rev)

def _update_artifacts(staging_path: Path, database: _WorkflowDatabase, repo: str, rev: str, token: str):
    def nuke_recurse(nuke: Path):
        if nuke.is_dir():
            for i in nuke.iterdir():
                nuke_recurse(i)
            nuke.rmdir()
        else:
            nuke.unlink()

    # Invalidate the database to indicate that we're in a transitional state.
    database.valid = False

    # Go ahead and trash the entire staging directory to prevent the accumulation of BS
    if staging_path.exists():
        for i in staging_path.iterdir():
            nuke_recurse(i)
    else:
        staging_path.mkdir(parents=True)
    database.workflow_gathers.clear()

    with download_artifacts(*workflow_lut.keys(), repo=repo, rev=rev, token=token) as artifacts:
        # TODO: consider parallelizing this.
        for artifact_name, downloaded_artifact in artifacts.items():
            _unpack_artifact(staging_path, database, rev, artifact_name, downloaded_artifact)

    # Now that we're done, the database should be valid once again
    database.current_sha = rev
    database.valid = True

class ArtifactInfo(NamedTuple):
    path: Path
    zipinfo: zipfile.ZipInfo
    name: str
    bundle: bool = False


def _unpack_artifact(staging_path: Path, database: _WorkflowDatabase, rev: str, name: str, artifact: tempfile.NamedTemporaryFile):
    logging.debug(f"Decompressing {name}.zip from {artifact.name}")

    with zipfile.ZipFile(artifact) as archive:
        subdir_name = f"{rev[:7]}_{name}"
        output_path = staging_path.joinpath(subdir_name)
        output_path.mkdir(parents=True, exist_ok=True)

        def iter_client_dir():
            for i in archive.infolist():
                member_path = PurePosixPath(i.filename)
                client_folder_name = "client"
                if len(member_path.parts) >=  2 and member_path.parts[0] == client_folder_name:
                    is_mac_app_bundle = member_path.parts[1].endswith(".app")
                    if not i.is_dir() and len(member_path.parts) ==  2:
                        yield ArtifactInfo(path=member_path.name, zipinfo=i, name=member_path.name)
                    elif is_mac_app_bundle and not i.is_dir():
                        # remove "client/"
                        path = member_path.relative_to(client_folder_name)
                        yield ArtifactInfo(path=path, zipinfo=i, name=member_path.parts[1], bundle=True)



        desired_members = list(iter_client_dir())
        with tqdm_logging.tqdm_logging_redirect(
            desired_members,
            desc=f"Extracting {name}",
            unit="file",
            leave=False
        ) as progress:
            for info in progress:
                logging.trace(f"Extracting {info.zipinfo.filename}")
                _unpack_member(output_path, info.path, archive, info.zipinfo)

        gather_key = workflow_lut[name]
        desired_files = [i.name for i in filter(lambda x: x.bundle == False, desired_members)]
        # Bundles might add multiple members with the same name - clean that up
        desired_bundles = list(set([i.name for i in list(filter(lambda x: x.bundle == True, desired_members))]))
        gather_package = { gather_key: desired_files }
        if desired_bundles:
            key = "macBundleExternal" if gather_key == "macExternal" else "macBundleInternal"
            gather_package[key] = desired_bundles
        with output_path.joinpath("control.json").open("w") as fp:
            json.dump(gather_package, fp, indent=2)

        database.workflow_gathers[name] = subdir_name

def _unpack_member(output_path: Path, output_subpath: Path, archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    with archive.open(info, "r") as infile:
        full_path = output_path.joinpath(output_subpath)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with full_path.open("wb") as outfile:
            block = 1024 * 8
            while True:
                buf = infile.read(block)
                if not buf:
                    break
                outfile.write(buf)


# Test code
if __name__ == "__main__":
    logging.basicConfig(format="[%(asctime)s] %(levelname)s: %(message)s", level=5)
    logging.addLevelName(5, "TRACE")
    logging.trace = functools.partial(logging.log, 5)

    with download_artifacts("plasma-windows-x86-internal-release", repo="H-uru/Plasma") as files:
        for f in files:
            with closing(zipfile.ZipFile(f)) as zip:
                zip.printdir()
