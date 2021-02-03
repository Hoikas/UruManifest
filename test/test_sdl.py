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

from pathlib import Path
import unittest

from urumanifest import plasma_sdl

class SDLTest(unittest.TestCase):
    def test_sdl(self):
        dir = Path(__file__).parent

        mgr = plasma_sdl.Manager()
        with dir.joinpath("avatar.sdl").open("r") as stream:
            mgr.read_descriptors(stream)

        self._check_standardStage(mgr.find_descriptor("standardStage"))
        self._check_genericBrain(mgr.find_descriptor("genericBrain"))

    def _check_standardStage(self, desc):
        self.assertEqual(desc.version, 3)

        # General test
        name_var = next((i for i in desc.variables if i.name == "name"), None)
        self.assertEqual(name_var.type, plasma_sdl.VariableType.String)
        self.assertEqual(name_var.range, 1)

        # Be sure the last variable in the statedesc is present.
        last_var = next((i for i in desc.variables if i.name == "isAttached"), None)
        self.assertEqual(last_var.type, plasma_sdl.VariableType.Bool)
        self.assertEqual(last_var.range, 1)

    def _check_genericBrain(self, desc):
        self.assertEqual(desc.version, 3)

        # Test variable length and embedded sdrs
        stages_var = next((i for i in desc.variables if i.name == "stages"), None)
        self.assertEqual(stages_var.type, plasma_sdl.VariableType.Descriptor)
        self.assertEqual(stages_var.descriptor, "standardStage")
        self.assertIs(stages_var.range, None)

