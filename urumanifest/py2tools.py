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

try:
    import cPickle as pickle
except ImportError:
    import pickle
import marshal
import os.path
import sys

from py2constants import *

def _format_exc_unicode():
    import traceback

    exc = []
    for i in traceback.format_exception(*sys.exc_info()):
        try:
            exc.append(i.encode("utf-8"))
        except UnicodeError:
            exc.append(u"<<<UnicodeError in traceback>>>")
    return exc

def _use_compiler_module(legacy=False):
    """The compiler module was deprecated in Python 2.6 and removed in Python 3.0 in favor of using
       the built-in `compile` function and the `ast` module. This function helps us determine if we
       should use it."""
    major, minor = sys.version_info[:2]
    if major == 2:
        return minor < 6 or legacy
    else:
        return False

def _read_py_source(py_file_path):
    try:
        if sys.version_info[0] == 2:
            fp = open(py_file_path, "r")
        else:
            fp = open(py_file_path, "r", encoding="utf-8")

        # Carriage returns are illegal in Py2 source code...
        return fp.read().replace('\r\n', '\n').replace('\r', '\n')
    finally:
        fp.close()

def _is_pfm(py_file_path, legacy=False):
    """Determine if a python file can be used as a PythonFileMod"""

    compiler_module =  _use_compiler_module(legacy)
    if compiler_module:
        from compiler import parse
        class NodeVisitor:
            pass
    else:
        from ast import NodeVisitor, parse
    try:
        ast_node = parse(_read_py_source(py_file_path))
    except:
        return PFM_AST_CRASHED

    klass_name = os.path.basename(py_file_path)
    ext_idx = klass_name.find('.')
    if ext_idx != -1:
        klass_name = klass_name[:ext_idx]

    class PfmVisitor(NodeVisitor):
        def __init__(self):
            self.result = PFM_NO_CLASS

        def _check_class(self, node, name_attr):
            if node.name == klass_name:
                for i in node.bases:
                    if getattr(i, name_attr, None) in PFM_BASES:
                        self.result = PFM_INDEED
                        break
                else:
                    self.result = PFM_NOT_A_MODIFIER

        def visitClass(self, node):
            # Old compiler.ast
            self._check_class(node, "name")

        def visit_ClassDef(self, node):
            # Python 2.6+ AST
            self._check_class(node, "id")

    v = PfmVisitor()
    if compiler_module:
        from compiler import walk
        walk(ast_node, v)
    else:
        v.visit(ast_node)
    return v.result

def compyle(py_file_path, py_glue_path=None, module_name=u"<string>", force_append_glue=False):
    result = {}

    is_pfm = _is_pfm(py_file_path)
    result[u"pfm"] = is_pfm
    append_glue = is_pfm == PFM_INDEED or force_append_glue

    # Grumble about stupid unpickling here...
    encoding = sys.getfilesystemencoding()
    py_file_path = py_file_path.encode(encoding)
    if py_glue_path:
        py_glue_path = py_glue_path.encode(encoding)
    module_name = module_name.encode("utf-8")

    try:
        py_source_code = _read_py_source(py_file_path)
    except:
        return { u"returncode": TOOLS_FILE_NOT_FOUND }
    if append_glue and py_glue_path:
        py_source_code += "\n\n"
        try:
            py_source_code += _read_py_source(py_glue_path)
        except:
            return { u"returncode": TOOLS_FILE_NOT_FOUND }
        result[u"glue_appended"] = True
    else:
        result[u"glue_appended"] = False

    try:
        py_code_object = compile(py_source_code, module_name, "exec")
        result[u"code"] = marshal.dumps(py_code_object)
    except:
        result[u"returncode"] = TOOLS_MODULE_TRACEBACK
        result[u"traceback"] = _format_exc_unicode()
    return result

def get_python_lib():
    result = {}

    if sys.version_info >= (3, 2):
        from sysconfig import get_path
        result[u"python_lib"] = get_path("stdlib").encode("utf-8")
    else:
        from distutils.sysconfig import get_python_lib
        result[u"python_lib"] = get_python_lib(plat_specific=False, standard_lib=True)

    return result

def exit():
    sys.exit(TOOLS_SUCCESS)

_commands = {
    u"compyle": compyle,
    u"get_python_lib": get_python_lib,
    u"quit": quit,
    u"exit": exit,
}

def _handle_command():
    # Python tries to be "helpful" on Windows by converting \n to \r\n.
    # Therefore we must change the mode of stdout.
    if sys.platform == "win32":
        import os, msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    if sys.version_info[0] == 2:
        stdin, stdout = sys.stdin, sys.stdout
    else:
        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    code = pickle.load(stdin)

    try:
        command = _commands.get(code.pop(u"cmd"))
        if command:
            args = code.pop(u"args", [])

            # For some stupid ass reason, we can't unpickle Py3 bytes objects in Python 2.3, so
            # everything is a flipping unicode object now. SIGH.
            kwargs = {}
            if sys.version_info[0] == 2:
                for key, value in code.iteritems():
                    kwargs[key.encode("utf-8")] = value
            else:
                kwargs = code
            result = command(*args, **kwargs)
        else:
            result = { u"returncode": TOOLS_INVALID_COMMAND }
    except:
        result = {}
        result[u"returncode"] = TOOLS_CRASHED
        result[u"traceback"] = _format_exc_unicode()

    if u"returncode" not in result:
        result[u"returncode"] = TOOLS_SUCCESS
    pickle.dump(result, stdout, 0)

if __name__ == "__main__":
    _handle_command()
    exit()
