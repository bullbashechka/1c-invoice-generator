#pragma once

#include <cstdint>
#include <cstdio>
#include <string>
#include "utf_utils.h"

namespace mcp {

inline bool IsValidUtf8(const std::string& s) {
    size_t i = 0;
    while (i < s.size()) {
        unsigned char b = static_cast<unsigned char>(s[i]);
        int extra;
        uint32_t cp;
        if (b < 0x80) {
            cp = b; extra = 0;
        } else if (b >= 0xC2 && b <= 0xDF) {
            cp = b & 0x1F; extra = 1;
        } else if (b >= 0xE0 && b <= 0xEF) {
            cp = b & 0x0F; extra = 2;
        } else if (b >= 0xF0 && b <= 0xF4) {
            cp = b & 0x07; extra = 3;
        } else {
            return false;
        }
        for (int j = 1; j <= extra; ++j) {
            if (i + j >= s.size()) return false;
            unsigned char cont = static_cast<unsigned char>(s[i + j]);
            if (cont < 0x80 || cont > 0xBF) return false;
            cp = (cp << 6) | (cont & 0x3F);
        }
        i += 1 + extra;
        if (extra == 1 && cp < 0x80)    return false;
        if (extra == 2 && cp < 0x800)   return false;
        if (extra == 3 && cp < 0x10000) return false;
        if (cp >= 0xD800 && cp <= 0xDFFF) return false;
        if (cp > 0x10FFFF) return false;
    }
    return true;
}

// CP1251 high-half lookup table (0x80–0xFF → Unicode codepoints)
static const uint16_t kCp1251[128] = {
    0x0402,0x0403,0x201A,0x0453,0x201E,0x2026,0x2020,0x2021,
    0x20AC,0x2030,0x0409,0x2039,0x040A,0x040C,0x040B,0x040F,
    0x0452,0x2018,0x2019,0x201C,0x201D,0x2022,0x2013,0x2014,
    0x0098,0x2122,0x0459,0x203A,0x045A,0x045C,0x045B,0x045F,
    0x00A0,0x040E,0x045E,0x0408,0x00A4,0x0490,0x00A6,0x00A7,
    0x0401,0x00A9,0x0404,0x00AB,0x00AC,0x00AD,0x00AE,0x0407,
    0x00B0,0x00B1,0x0406,0x0456,0x0491,0x00B5,0x00B6,0x00B7,
    0x0451,0x2116,0x0454,0x00BB,0x0458,0x0405,0x0455,0x0457,
    0x0410,0x0411,0x0412,0x0413,0x0414,0x0415,0x0416,0x0417,
    0x0418,0x0419,0x041A,0x041B,0x041C,0x041D,0x041E,0x041F,
    0x0420,0x0421,0x0422,0x0423,0x0424,0x0425,0x0426,0x0427,
    0x0428,0x0429,0x042A,0x042B,0x042C,0x042D,0x042E,0x042F,
    0x0430,0x0431,0x0432,0x0433,0x0434,0x0435,0x0436,0x0437,
    0x0438,0x0439,0x043A,0x043B,0x043C,0x043D,0x043E,0x043F,
    0x0440,0x0441,0x0442,0x0443,0x0444,0x0445,0x0446,0x0447,
    0x0448,0x0449,0x044A,0x044B,0x044C,0x044D,0x044E,0x044F
};

// CP866 high-half lookup table (0x80–0xFF → Unicode codepoints)
static const uint16_t kCp866[128] = {
    // 0x80-0x8F: А-П
    0x0410,0x0411,0x0412,0x0413,0x0414,0x0415,0x0416,0x0417,
    0x0418,0x0419,0x041A,0x041B,0x041C,0x041D,0x041E,0x041F,
    // 0x90-0x9F: Р-Я
    0x0420,0x0421,0x0422,0x0423,0x0424,0x0425,0x0426,0x0427,
    0x0428,0x0429,0x042A,0x042B,0x042C,0x042D,0x042E,0x042F,
    // 0xA0-0xAF: а-п
    0x0430,0x0431,0x0432,0x0433,0x0434,0x0435,0x0436,0x0437,
    0x0438,0x0439,0x043A,0x043B,0x043C,0x043D,0x043E,0x043F,
    // 0xB0-0xBF: box drawing
    0x2591,0x2592,0x2593,0x2502,0x2524,0x2561,0x2562,0x2556,
    0x2555,0x2563,0x2551,0x2557,0x255D,0x255C,0x255B,0x2510,
    // 0xC0-0xCF: box drawing + misc
    0x2514,0x2534,0x252C,0x251C,0x2500,0x253C,0x255E,0x255F,
    0x255A,0x2554,0x2569,0x2566,0x2560,0x2550,0x256C,0x00A4,
    // 0xD0-0xDF: Ё, ё, misc
    0x0401,0x0451,0x221A,0x2248,0x2264,0x2265,0x00A0,0x2219,
    0x00B0,0x00B2,0x00B7,0x00F7,0x2116,0x00A4,0x25A0,0x00A0,
    // 0xE0-0xEF: р-я
    0x0440,0x0441,0x0442,0x0443,0x0444,0x0445,0x0446,0x0447,
    0x0448,0x0449,0x044A,0x044B,0x044C,0x044D,0x044E,0x044F,
    // 0xF0-0xFF: misc
    0x0401,0x0451,0x0404,0x046C,0x0406,0x0409,0x212A,0x2030,
    0x20AC,0x2126,0x040E,0x0490,0x0491,0x25A0,0x25A0,0x00A0
};

inline std::wstring DecodeCP1x(const std::string& s, int codepage) {
    const uint16_t* table = (codepage == 866) ? kCp866 : kCp1251;
    std::wstring result;
    result.reserve(s.size());
    for (unsigned char b : s) {
        if (b < 0x80) {
            result.push_back(static_cast<wchar_t>(b));
        } else {
            result.push_back(static_cast<wchar_t>(table[b - 0x80]));
        }
    }
    return result;
}

inline int CyrillicScore(const std::wstring& s) {
    int score = 0;
    for (wchar_t c : s) {
        if ((c >= 0x0410 && c <= 0x044F) || c == 0x0401 || c == 0x0451)
            score += 2;   // Russian Cyrillic
        else if (c >= 0x2500 && c <= 0x25FF)
            score -= 15;  // Box drawing (CP866 artifact)
        else if (c == 0x2219 || c == 0x221A)
            score -= 5;   // · √ (CP866 artifact)
    }
    return score;
}

inline std::string JsonEscape(const std::string& s) {
    std::string result;
    result.reserve(s.size() + 16);
    for (char c : s) {
        switch (c) {
            case '"':  result += "\\\""; break;
            case '\\': result += "\\\\"; break;
            case '\n': result += "\\n";  break;
            case '\r': result += "\\r";  break;
            case '\t': result += "\\t";  break;
            case '\b': result += "\\b";  break;
            case '\f': result += "\\f";  break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", static_cast<unsigned char>(c));
                    result += buf;
                } else {
                    result += c;
                }
                break;
        }
    }
    return result;
}

// Full encoding detection stack without WinAPI — mirrors HttpTransport::AutoDecodeToUTF8
inline std::string AutoDecodeUtf8Free(const std::string& raw, const std::string& ct) {
    if (raw.empty()) return raw;

    // 1. Explicit charset from Content-Type
    if (!ct.empty()) {
        std::string lower = ct;
        for (auto& c : lower) c = static_cast<char>(
            (c >= 'A' && c <= 'Z') ? c + 32 : c);
        auto pos = lower.find("charset=");
        if (pos != std::string::npos) {
            pos += 8;
            while (pos < lower.size() && (lower[pos] == ' ' || lower[pos] == '"' || lower[pos] == '\''))
                ++pos;
            std::string charset;
            while (pos < lower.size() && lower[pos] != ';' && lower[pos] != ' '
                   && lower[pos] != '"' && lower[pos] != '\'')
                charset += lower[pos++];
            if (!charset.empty() && charset != "utf-8" && charset != "utf8") {
                int cp = (charset == "windows-1251" || charset == "cp1251") ? 1251 :
                         (charset == "cp866"        || charset == "ibm866") ? 866  : 0;
                if (cp != 0) {
                    std::wstring wide = DecodeCP1x(raw, cp);
                    if (!wide.empty()) return Utf16UnitsToUtf8(wide);
                }
            }
        }
    }

    // 2. UTF-8 fast path
    if (IsValidUtf8(raw)) return raw;

    // 3. CP1251 vs CP866 scoring
    std::wstring w1251 = DecodeCP1x(raw, 1251);
    std::wstring w866  = DecodeCP1x(raw, 866);
    std::wstring best  = (CyrillicScore(w1251) >= CyrillicScore(w866)) ? w1251 : w866;
    return Utf16UnitsToUtf8(best);
}

}  // namespace mcp
