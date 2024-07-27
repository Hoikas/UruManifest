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

cmake_minimum_required(VERSION 3.15)
project(urumanifest LANGUAGES CXX)

find_package(Python3 REQUIRED COMPONENTS Development Interpreter)

include(FetchContent)
FetchContent_Declare(pybind11
    GIT_REPOSITORY "https://github.com/pybind/pybind11.git"
    GIT_TAG "v2.8.0"
)
FetchContent_MakeAvailable(pybind11)

set(urumanifest_SOURCES
    module.cpp
)
set(urumanifest_HEADERS
)

pybind11_add_module(urumanifest ${urumanifest_SOURCES} ${urumanifest_HEADERS})
set_target_properties(urumanifest PROPERTIES OUTPUT_NAME _urumanifest)

# Avoid dependency on C++20
if(CMAKE_VERSION VERSION_GREATER_EQUAL 3.20)
    if(CMAKE_CXX_BYTE_ORDER STREQUAL BIG_ENDIAN)
        set(BIG_ENDIAN TRUE)
    endif()
else()
    include(TestBigEndian)
    test_big_endian(BIG_ENDIAN)
endif()
if(BIG_ENDIAN)
    target_compile_definitions(urumanifest PRIVATE URUMANIFEST_BIG_ENDIAN)
endif()
