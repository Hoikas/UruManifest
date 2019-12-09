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

from collections import Counter
import concurrent.futures
import functools
import logging
from pathlib import Path
try:
    import cPickle as pickle
except ImportError:
    import pickle
from PyHSPlasma import *
import subprocess
from threading import Lock

from assets import Asset
from constants import *
import utils

def _build_module_name(script_client_path, source_assets):
    # Every directory containing an __init__.py file is a module. So, we need to scan backwards
    # to see what this script's module name is. Example: the KI's PFM `xKI` module will import
    # the `ki` module (ki/__init__.py) which has submodules `ki.xKIChat`, etc.
    base_py_path = Path("Python")
    if script_client_path.stem == "__init__":
        working_path = script_client_path.parent
        if working_path == base_py_path:
            logging.error("Root-level __init__.py will not be in packed python!")
            return
    else:
        working_path = script_client_path

    module_name = working_path.stem
    while working_path != base_py_path:
        working_path = working_path.parent
        working_init_path = working_path.joinpath("__init__.py")
        if working_init_path not in source_assets:
            break
        module_name = f"{working_path.stem}.{module_name}"
    return module_name

def _compyle_file(py_exe, py_tools_path, py_file_path, py_glue_path, module_name, is_pfm):
    proc = subprocess.Popen((str(py_exe), str(py_tools_path)),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=False)

    # Danger: do not try to pickle a bytes object and send it to Python <2.6. Apparently this chokes
    # because some module does not have an `encode` function. And here I am likw WTF, mate. A Python 3
    # bytes object is just an immutable Python 2 str... And I explicitly asked for a backwards compat
    # pickling protocol...
    command = {}
    command["cmd"] = "compyle"
    command["py_file_path"] = str(py_file_path)
    command["py_glue_path"] = str(py_glue_path)
    command["module_name"] = module_name
    command["force_append_glue"] = is_pfm
    # NOTE: binary protocols can lead to unexpected EOFs in Python 2...
    buf = pickle.dumps(command, 0)

    # Whoosh... off it goes...
    stdout, stderr = proc.communicate(buf)
    if stderr:
        logging.error(stderr)
    if stdout:
        return pickle.loads(stdout, encoding="bytes")
    return {}

def _compyle_all(source_assets, staged_assets, py_exe=None, py_version=(2, 7), ncpus=None):
    logging.info("Compyling Python...")

    def iter_python_sources():
        for client_path, source_asset in source_assets.items():
            if "python" in source_asset.categories and client_path.suffix.lower() == ".py":
                yield client_path, source_asset

    def on_compyle(client_path, source_path, module_name, was_pfm, future):
        with lock:
            logging.debug(f"== Compyle '{client_path}' ==")
            result = future.result()
            returncode, pfm_glue = result["returncode"], result.get("pfm")

            if returncode == PyToolsResultCodes.success:
                module_code[module_name] = result["code"]
                if was_pfm and pfm_glue == PfmGlue.indeed:
                    logging.debug(f"{module_name} is a PythonFileMod(TM)!")
                elif not was_pfm and pfm_glue == PfmGlue.indeed:
                    logging.debug(f"{module_name} can be a PythonFileMod(TM)!")
                elif not was_pfm and pfm_glue != PfmGlue.indeed:
                    logging.debug(f"{module_name} is a PoS... plain old script file.")
                elif was_pfm and pfm_glue != PfmGlue.indeed:
                    logging.error(f"FORCING {module_name} AS A PythonFileMod(TM)")

                if was_pfm and pfm_glue == PfmGlue.not_a_modifier:
                    logging.error(f"Python class '{client_path.stem}' does not seem to derive from a Plasma type!")
                elif was_pfm and pfm_glue == PfmGlue.no_class:
                    logging.error(f"Python class '{client_path.stem}' was not found???")
                elif was_pfm and pfm_glue == PfmGlue.ast_crashed:
                    logging.debug(f"Hmmm... The AST parse/visit crashed in '{source_path}' -- maybe this is A-OK then...")
            elif returncode == PyToolsResultCodes.file_not_found:
                logging.error(f"Py2 Compyler could not load '{source_path}'")
            else:
                logging.critical(f"Py2 Compyler Traceback in file '{source_path}'")
                for i in result.get("traceback", []):
                    try:
                        logging.critical(i.decode("utf-8").strip())
                    except UnicodeError:
                        pass

    # Find python2-compatible schtuff
    if py_exe and py_exe.is_file() and utils.check_python_version(py_exe, py_version):
        logging.debug(f"Using configured Python executable: {py_exe}")
    else:
        py_exe = utils.find_python_exe(py_version)
        if not utils.check_python_version(py_exe, py_version):
            py_exe = None
    if not py_exe:
        logging.critical(f"Could not find Python {py_version[0]}.{py_version[1]}")
        return
    py_tools_path = utils.find_python2_tools()
    if not py_tools_path.exists():
        logging.critical("Could not find Python2 helper module")
        return

    # H-uru Python.paks can have submodules, so prepare a dict of those.
    module_lut = { i: _build_module_name(i, source_assets) for i, _ in iter_python_sources() }
    c = Counter(module_lut.values())
    module_code = {}

    # As of now, all PythonFileMods must have a set of glue code appended to the end of the real code
    # to facillitate use in the engine. Someday, the glue will be rewritten in C++, so it's not a fatal
    # error if the glue code is missing.
    glue_client_path = Path("Python", "plasma", "glue.py")
    try:
        glue_asset = source_assets[glue_client_path]
    except LookupError:
        logging.error("Plasma Python glue not available... This might be bad news...")
        glue_path = None
    else:
        glue_path = glue_asset.source_path

    # Due to the dynamic nature of python scripts, we cannot make any assumptions that only scripts
    # listed in the PRPs and imported at the module level are the only ones needed. While Cyan never
    # did this, there is nothing stopping an enterprising programmer (eg me) from importing arbitrary
    # modules by string name somewhere deep inside the code. So, long story short, it's best if we
    # compile and package all.
    with concurrent.futures.ThreadPoolExecutor(max_workers=ncpus) as executor:
        # Logfile sanity
        lock = Lock()
        for client_path, source_asset in iter_python_sources():
            module_name = module_lut.get(client_path)
            if not module_name:
                logging.error(f"Skipping '{client_path}' due to empty module name!")
                continue
            if c.get(module_name) != 1:
                logging.error(f"Skipping '{client_path}' due to conflicting module name '{module_name}'!")
                continue
            is_pfm = bool(staged_assets[client_path].flags & ManifestFlags.python_file_mod)

            future = executor.submit(_compyle_file, py_exe, py_tools_path, source_asset.source_path,
                                     glue_path, module_name, is_pfm)
            future.add_done_callback(functools.partial(on_compyle, client_path, source_asset.source_path,
                                                       module_name, is_pfm))
    logging.debug(f"Compyled {len(module_code)} python files.")
    return module_code

def _package(source_assets, staged_assets, module_code, output_path, droid_key):
    # Python.pak format:
    # uint32_t numFiles
    #     - safeStr filename
    #     - uint32_t offset
    # ~~~~~
    # uint32_t filesz
    # uint8_t data[filesz]

    logging.info("Building Python.pak...")
    if not module_code:
        logging.error("No marshalled python code available for packaging!")
        return

    pak_client_path = Path("Python", "Python.pak")
    pak_source_path = output_path.joinpath(pak_client_path)
    pak_source_path.parent.mkdir(parents=True, exist_ok=True)

    # We are using a plEncryptedStream, which doesn't seek very well at all.
    # Therefore, we will go ahead and calculate the size of the index block so
    # there is no need to seek around to write offset values
    base_offset = 4 # uint32_t numFiles
    data_offset = 0
    pyc_info = [] # sad, but makes life easier...
    for module_name, compyled_code in module_code.items():
        pyc_info.append((module_name, data_offset, compyled_code))

        # index offset overall
        base_offset += 2 # writeSafeStr length
        # NOTE: This assumes that libHSPlasma's hsStream::writeSafeStr converts
        #       the Python unicode/string object to UTF-8. Currently, this is true.
        base_offset += len(module_name.encode("utf-8")) # writeSafeStr
        base_offset += 4

        # current file data offset
        data_offset += 4  # uint32_t filesz
        data_offset += len(compyled_code)

    stream = plEncryptedStream(pvMoul)
    stream.setKey(droid_key)
    with stream.open(pak_source_path, fmCreate, plEncryptedStream.kEncDroid):
        stream.writeInt(len(pyc_info))
        for module_name, data_offset, compyled_code in pyc_info:
            stream.writeSafeStr(module_name)
            # offset of data == index size (base_offset) + offset to data blob (data_offset)
            stream.writeInt(base_offset + data_offset)
        for module_name, data_offset, compyled_code in pyc_info:
            stream.writeInt(len(compyled_code))
            stream.write(compyled_code)

    source_assets[pak_client_path] = Asset(None, pak_source_path, pak_client_path, set(("python",)))
    staged_asset = staged_assets[pak_client_path]
    staged_asset.file_name = pak_client_path
    # Prevent a spurious warning about naughty encryption
    staged_asset.flags |= ManifestFlags.dont_encrypt

def process(source_assets, staged_assets, output_path, droid_key, py_exe=None, py_version=(2,7)):
    logging.info("Processing client python...")

    def iter_python_paks():
        for client_path, source_asset in source_assets.items():
            if "python" in source_asset.categories and client_path.suffix.lower() == ".pak":
                yield client_path, source_asset

    # Check for any Python paks -- if they exist, bail.
    if any(iter_python_paks()):
        logging.warning("Using prebuilt Python packages -- this is not recommended!")
        for client_path, source_asset in iter_python_paks():
            logging.trace(f"Prebuilt Python: '{client_path.name}'")
            staged_assets[client_path].file_name = client_path
        return

    # The compyler was written assuming a minimum of Python 2.3
    if py_version[0] == 2 and py_version[1] < 3:
        logging.critical(f"Python {'.'.join(py_version)} is not supported by the compyler.")
        logging.critical("No Python.pak will be generated!")
        return

    module_code = _compyle_all(source_assets, staged_assets, py_exe, py_version)
    if module_code:
        _package(source_assets, staged_assets, module_code, output_path, droid_key)
