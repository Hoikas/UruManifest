#    This file is part of HuruDist
#
#    HuruDist is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    HuruDist is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with HuruDist.  If not, see <http://www.gnu.org/licenses/>.

import logging
from pathlib import Path
import subprocess
import sys
from typing import Optional

def check_python_version(py_exe, py_version=(2,7)):
    logging.debug(f"Checking Python interpreter version: {py_exe}")
    if not py_exe or not py_exe.is_file():
        logging.debug("Non-file input")
        return False

    args = (str(py_exe), "-V")
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="ascii")
    if result.returncode == 0:
        logging.trace(f"{py_exe}: {result.stdout}")

        expected_version = f"Python {py_version[0]}.{py_version[1]}"
        result_version = result.stdout.strip()
        if not result_version.startswith(expected_version):
            logging.error(f"Python interpreter '{py_exe}' is wrong version--expected '{expected_version}' got '{result_version}'")
            return False
        return True
    else:
        logging.debug("Nonzero returncode")
        return False

def find_python_exe(py_version=(2, 7)) -> Optional[Path]:
    def _find_python_reg(py_version):
        import winreg
        subkey_name = "Software\\Python\\PythonCore\\{}.{}\\InstallPath".format(*py_version)
        for reg_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                python_dir = winreg.QueryValue(reg_key, subkey_name)
            except FileNotFoundError:
                continue
            else:
                return Path(python_dir, "python.exe")
        return None

    # Maybe, someday, this will be true...
    # NOTE: venv python.exe will cause bad things to happen!
    if sys.version_info[:2] == py_version:
        if sys.prefix == sys.base_prefix:
            return Path(sys.executable)
        else:
            logging.debug("The current python executable is a venv. It cannot be used for py2tools!")
    major, minor = py_version

    # If we're on Windows, we can try looking in the registry...
    if sys.platform == "win32":
        py_exe = None
        for i in range(minor, 0, -1):
            py_exe = _find_python_reg((major, i))
            if py_exe:
                logging.debug(f"Found Python {major}.{i}: {py_exe}")
                return py_exe

    # Ok, now we try using some posix junk...
    cmd = f"command -v python{major}.{minor}"
    encoding = sys.stdout.encoding
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, encoding=encoding)
    if result.returncode == 0:
        logging.debug(f"Found Python {major}.{minor}: {result.stdout}")
        return Path(result.stdout.strip())

    cmd = f"command -v python{major}"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, encoding=encoding)
    if result.returncode == 0:
        logging.debug(f"Found Python {major}: {result.stdout}")
        return Path(result.stdout.strip())

    # You win, I give up.
    return None

def find_python2_tools():
    tools_path = Path(__file__).parent.joinpath("py2tools.py")
    return tools_path

def get_droid_key(droid_key):
    if isinstance(droid_key, str):
        droid_key = int(droid_key, 16)
    assert isinstance(droid_key, int)
    try:
        buf = droid_key.to_bytes(length=16, byteorder="big")
    except OverflowError:
        raise AssetError("The droid key should be a 128-byte integer")
    else:
        key = []
        key.append(int.from_bytes(buf[0:4], byteorder="big"))
        key.append(int.from_bytes(buf[4:8], byteorder="big"))
        key.append(int.from_bytes(buf[8:12], byteorder="big"))
        key.append(int.from_bytes(buf[12:16], byteorder="big"))
    return key

def is_path_relative_to(parent_path: Path, child_path: Path) -> bool:
    # Path.is_relative_to() was added in Python 3.9, but we need to
    # work all the way back to Python 3.6, so use this shim.
    try:
        child_path.relative_to(parent_path)
    except ValueError:
        return False
    else:
        return True
