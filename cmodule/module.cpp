//    This file is part of UruManifest
//
//    UruManifest is free software: you can redistribute it and/or modify
//    it under the terms of the GNU General Public License as published by
//    the Free Software Foundation, either version 3 of the License, or
//    (at your option) any later version.
//
//    UruManifest is distributed in the hope that it will be useful,
//    but WITHOUT ANY WARRANTY; without even the implied warranty of
//    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
//    GNU General Public License for more details.
//
//    You should have received a copy of the GNU General Public License
//    along with UruManifest.  If not, see <http://www.gnu.org/licenses/>.

#include <array>
#include <tuple>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// ==============================================================================

using block_t = std::tuple<uint32_t, uint32_t>;
using block_array_t = std::array<uint32_t, 2>;
using crypt_key_t = std::array<uint32_t, 4>;

using io_t = std::function<pybind11::bytes(size_t)>;

// ==============================================================================

namespace
{
    inline uint32_t byteswap(uint32_t value)
    {
#ifdef _MSC_VER
        return _byteswap_ulong(value);
#else
        return __builtin_bswap32(value);
#endif
    }

    inline uint32_t swap_le(uint32_t value)
    {
#ifdef URUMANIFEST_BIG_ENDIAN
        value = byteswap(value);
#endif
        return value;
    }


    block_t xtea_decipher(const crypt_key_t& ekey, const block_t& buf)
    {
        uint32_t first, second;
        std::tie(first, second) = buf;
        uint32_t key = 0xC6EF3720;

        for (int i = 0; i < 32; i++) {
            second -= (((first >> 5) ^ (first << 4)) + first)
                    ^ (ekey[(key >> 11) & 3] + key);
            key += 0x61C88647;
            first -= (((second >> 5) ^ (second << 4)) + second)
                   ^ (ekey[key & 3] + key);
        }

        return std::make_tuple(swap_le(first), swap_le(second));
    }

    block_t xtea_encipher(const crypt_key_t& key, const block_t& buf)
    {
        uint32_t first = swap_le(std::get<0>(buf));
        uint32_t second = swap_le(std::get<1>(buf));
        uint32_t delta = 0;

        for (int i = 0; i < 32; i++) {
            first += (((second >> 5) ^ (second << 4)) + second)
                   ^ (key[delta & 3] + delta);
            delta -= 0x61C88647;
            second += (((first >> 5) ^ (first << 4)) + first)
                    ^ (key[(delta >> 11) & 3] + delta);
        }

        return std::make_tuple(first, second);
    }

    block_array_t btea_decipher(const crypt_key_t& ekey, const block_t& input)
    {
        block_array_t buf{ std::get<0>(input), std::get<1>(input) };
        constexpr uint32_t num = buf.size();

        uint32_t key = ((52 / num) + 6) * 0x9E3779B9;
        while (key != 0) {
            const uint32_t xorkey = (key >> 2) & 3;
            uint32_t numloop = num - 1;
            while (numloop != 0) {
                buf[numloop] -=
                    (((buf[numloop - 1] << 4) ^ (buf[numloop - 1] >> 3)) +
                        ((buf[numloop - 1] >> 5) ^ (buf[numloop - 1] << 2))) ^
                    ((ekey[(numloop & 3) ^ xorkey] ^ buf[numloop - 1]) +
                        (key ^ buf[numloop - 1]));
                numloop--;
            }
            buf[0] -=
                (((buf[num - 1] << 4) ^ (buf[num - 1] >> 3)) +
                    ((buf[num - 1] >> 5) ^ (buf[num - 1] << 2))) ^
                ((ekey[(numloop & 3) ^ xorkey] ^ buf[num - 1]) +
                    (key ^ buf[num - 1]));
            key += 0x61C88647;
        }

        return buf;
    }

    block_array_t btea_encipher(const crypt_key_t& ekey, const block_t& input)
    {
        block_array_t buf{ std::get<0>(input), std::get<1>(input) };
        constexpr uint32_t num = buf.size();

        uint32_t key = 0;
        uint32_t count = (52 / num) + 6;
        while (count != 0) {
            key -= 0x61C88647;
            const uint32_t xorkey = (key >> 2) & 3;
            uint32_t numloop = 0;
            while (numloop != num - 1) {
                buf[numloop] +=
                    (((buf[numloop + 1] << 4) ^ (buf[numloop + 1] >> 3)) +
                        ((buf[numloop + 1] >> 5) ^ (buf[numloop + 1] << 2))) ^
                    ((ekey[(numloop & 3) ^ xorkey] ^ buf[numloop + 1]) +
                        (key ^ buf[numloop + 1]));
                numloop++;
            }
            buf[num - 1] +=
                (((buf[0] << 4) ^ (buf[0] >> 3)) +
                    ((buf[0] >> 5) ^ (buf[0] << 2))) ^
                ((ekey[(numloop & 3) ^ xorkey] ^ buf[0]) +
                    (key ^ buf[0]));
            count--;
        }

        return buf;
    }
};

// ==============================================================================

PYBIND11_MODULE(_urumanifest, m)
{
    m.doc() = "UruManifest";

    m.def(
        "xtea_decipher",
        xtea_decipher,
        pybind11::arg("key"),
        pybind11::arg("buf")
    );
    m.def(
        "xtea_encipher",
        xtea_encipher,
        pybind11::arg("key"),
        pybind11::arg("buf")
    );

    m.def(
        "btea_decipher",
        btea_decipher,
        pybind11::arg("key"),
        pybind11::arg("buf")
    );
    m.def(
        "btea_encipher",
        btea_encipher,
        pybind11::arg("key"),
        pybind11::arg("buf")
    );
}
