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

from urumanifest import plasmoul

class AgeInfoTest(unittest.TestCase):
    def test_ageInfoRead(self):
        dir = Path(__file__).parent
        age = plasmoul.plAge(dir.joinpath("Garden.age"))

        # Known values
        prefix = 1
        common_pages = ["BuiltIn", "Textures"]
        age_pages = ["ItinerantBugCloud", "kemoGarden", "kemoStorm"]

        self.assertEqual(age.prefix, prefix)
        self.assertEqual(list(age.common_pages), common_pages)
        self.assertEqual(list(age.pages), age_pages)
