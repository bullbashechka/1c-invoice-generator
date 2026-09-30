#include "component.h"

#include <algorithm>
#include <cstring>
#include <cwctype>

#ifdef _WINDOWS
#include <windows.h>
#endif

namespace syntax_help {

// ============================================================================
//  Name tables
// ============================================================================

const wchar_t* SyntaxHelpReaderComponent::method_names_en_[] = {
    L"Initialize",
    L"Search",
    L"GetTopic",
    L"Version",
};

const wchar_t* SyntaxHelpReaderComponent::method_names_ru_[] = {
    L"Инициализировать",
    L"Поиск",
    L"ПолучитьТему",
    L"Версия",
};

// ============================================================================
//  IInitDoneBase
// ============================================================================

bool SyntaxHelpReaderComponent::Init(void* disp) {
    addin_base_ = static_cast<IAddInDefBase*>(disp);
    return addin_base_ != nullptr;
}

bool SyntaxHelpReaderComponent::setMemManager(void* mem) {
    mem_manager_ = static_cast<IMemoryManager*>(mem);
    return mem_manager_ != nullptr;
}

void SyntaxHelpReaderComponent::Done() {
    addin_base_  = nullptr;
    mem_manager_ = nullptr;
}

// ============================================================================
//  RegisterExtensionAs
// ============================================================================

bool SyntaxHelpReaderComponent::RegisterExtensionAs(WCHAR_T** wsExtensionName) {
    return AllocWStr(wsExtensionName, L"SyntaxHelpReader");
}

// ============================================================================
//  Methods
// ============================================================================

long SyntaxHelpReaderComponent::GetNMethods() { return eMethodLast; }

long SyntaxHelpReaderComponent::FindMethod(const WCHAR_T* wsMethodName) {
    std::wstring name = WcharTToWstr(wsMethodName);
    auto lower = [](std::wstring s) {
        std::transform(s.begin(), s.end(), s.begin(), ::towlower);
        return s;
    };
    std::wstring lower_name = lower(name);
    for (long i = 0; i < eMethodLast; ++i) {
        if (lower(method_names_en_[i]) == lower_name ||
            lower(method_names_ru_[i]) == lower_name) {
            return i;
        }
    }
    return -1;
}

const WCHAR_T* SyntaxHelpReaderComponent::GetMethodName(const long lMethodNum,
                                                          const long lMethodAlias) {
    if (lMethodNum < 0 || lMethodNum >= eMethodLast) return nullptr;
    const wchar_t* name = (lMethodAlias == 0) ? method_names_en_[lMethodNum]
                                               : method_names_ru_[lMethodNum];
    WCHAR_T* result = nullptr;
    if (AllocWStr(&result, std::wstring(name))) return result;
    return nullptr;
}

long SyntaxHelpReaderComponent::GetNParams(const long lMethodNum) {
    switch (lMethodNum) {
        case eMethodInitialize: return 1;
        case eMethodSearch:     return 2;
        case eMethodGetTopic:   return 1;
        case eMethodVersion:    return 0;
        default:                return 0;
    }
}

bool SyntaxHelpReaderComponent::HasRetVal(const long lMethodNum) {
    return lMethodNum == eMethodInitialize ||
           lMethodNum == eMethodSearch     ||
           lMethodNum == eMethodGetTopic   ||
           lMethodNum == eMethodVersion;
}

bool SyntaxHelpReaderComponent::CallAsFunc(const long lMethodNum,
                                            tVariant* pvarRetValue,
                                            tVariant* paParams,
                                            const long lSizeArray) {
    if (!pvarRetValue) return false;

    switch (lMethodNum) {

        case eMethodInitialize: {
            if (lSizeArray < 1) return false;
            std::string dir = GetStringFromVariant(&paParams[0]);
            int result = index_.Initialize(dir);
            return SetIntToVariant(pvarRetValue, result);
        }

        case eMethodSearch: {
            if (lSizeArray < 2) return false;
            std::string keywords_json = GetStringFromVariant(&paParams[0]);
            bool match_all = GetBoolFromVariant(&paParams[1]);
            auto keywords = ParseJsonStringArray(keywords_json);
            std::string json = index_.Search(keywords, match_all);
            return SetStringToVariant(pvarRetValue, json);
        }

        case eMethodGetTopic: {
            if (lSizeArray < 1) return false;
            std::string breadcrumb = GetStringFromVariant(&paParams[0]);
            std::string json = index_.GetTopic(breadcrumb);
            return SetStringToVariant(pvarRetValue, json);
        }

        case eMethodVersion: {
            return SetStringToVariant(pvarRetValue, "1.0.0");
        }

        default:
            return false;
    }
}

// ============================================================================
//  Helpers: 1C variant
// ============================================================================

bool SyntaxHelpReaderComponent::SetStringToVariant(tVariant* var, const std::string& utf8) {
    if (!var || !mem_manager_) return false;
    std::wstring ws = Utf8ToWstr(utf8);
    TV_VT(var) = VTYPE_PWSTR;
    size_t byte_count = (ws.size() + 1) * sizeof(WCHAR_T);
    if (!mem_manager_->AllocMemory(reinterpret_cast<void**>(&var->pwstrVal),
                                    static_cast<unsigned long>(byte_count)))
        return false;
#ifdef _WINDOWS
    memcpy(var->pwstrVal, ws.c_str(), byte_count);
#else
    for (size_t i = 0; i <= ws.size(); ++i)
        var->pwstrVal[i] = static_cast<WCHAR_T>(ws[i]);
#endif
    var->wstrLen = static_cast<uint32_t>(ws.size());
    return true;
}

bool SyntaxHelpReaderComponent::SetIntToVariant(tVariant* var, int32_t value) {
    if (!var) return false;
    TV_VT(var) = VTYPE_I4;
    var->lVal   = value;
    return true;
}

std::string SyntaxHelpReaderComponent::GetStringFromVariant(const tVariant* var) {
    if (!var) return "";
    if (TV_VT(var) == VTYPE_PWSTR && var->pwstrVal) {
#ifdef _WINDOWS
        std::wstring ws(var->pwstrVal, var->wstrLen);
        return WstrToUtf8(ws);
#else
        std::wstring ws;
        ws.reserve(var->wstrLen);
        for (uint32_t i = 0; i < var->wstrLen; ++i)
            ws += static_cast<wchar_t>(var->pwstrVal[i]);
        return WstrToUtf8(ws);
#endif
    }
    if (TV_VT(var) == VTYPE_PSTR && var->pstrVal) {
        return std::string(var->pstrVal, var->strLen);
    }
    return "";
}

bool SyntaxHelpReaderComponent::GetBoolFromVariant(const tVariant* var) {
    if (!var) return false;
    if (TV_VT(var) == VTYPE_BOOL) return var->bVal;
    if (TV_VT(var) == VTYPE_I4)   return var->lVal != 0;
    return false;
}

bool SyntaxHelpReaderComponent::AllocWStr(WCHAR_T** dest, const std::wstring& src) {
    if (!dest || !mem_manager_) return false;
    size_t byte_count = (src.size() + 1) * sizeof(WCHAR_T);
    if (!mem_manager_->AllocMemory(reinterpret_cast<void**>(dest),
                                    static_cast<unsigned long>(byte_count)))
        return false;
#ifdef _WINDOWS
    memcpy(*dest, src.c_str(), byte_count);
#else
    for (size_t i = 0; i <= src.size(); ++i)
        (*dest)[i] = static_cast<WCHAR_T>(src[i]);
#endif
    return true;
}

// ============================================================================
//  UTF-8 ↔ wstring (Windows only: wchar_t = UTF-16)
// ============================================================================

std::string SyntaxHelpReaderComponent::WstrToUtf8(const std::wstring& ws) {
#ifdef _WINDOWS
    if (ws.empty()) return "";
    int sz = WideCharToMultiByte(CP_UTF8, 0, ws.c_str(),
                                  static_cast<int>(ws.size()),
                                  nullptr, 0, nullptr, nullptr);
    if (sz <= 0) return "";
    std::string out(static_cast<size_t>(sz), '\0');
    WideCharToMultiByte(CP_UTF8, 0, ws.c_str(), static_cast<int>(ws.size()),
                        out.data(), sz, nullptr, nullptr);
    return out;
#else
    // Basic: assume wchar_t is UCS-4/UTF-32
    std::string out;
    for (wchar_t wc : ws) {
        uint32_t cp = static_cast<uint32_t>(wc);
        if (cp < 0x80) { out += static_cast<char>(cp); }
        else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }
    return out;
#endif
}

std::wstring SyntaxHelpReaderComponent::Utf8ToWstr(const std::string& s) {
#ifdef _WINDOWS
    if (s.empty()) return L"";
    int sz = MultiByteToWideChar(CP_UTF8, 0, s.c_str(),
                                  static_cast<int>(s.size()),
                                  nullptr, 0);
    if (sz <= 0) return L"";
    std::wstring out(static_cast<size_t>(sz), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()),
                        out.data(), sz);
    return out;
#else
    // Basic UTF-8 decode
    std::wstring out;
    size_t i = 0;
    const size_t n = s.size();
    while (i < n) {
        uint8_t b0 = (uint8_t)s[i];
        uint32_t cp = 0; size_t seq = 1;
        if (b0 < 0x80) { cp = b0; seq = 1; }
        else if ((b0 & 0xE0) == 0xC0 && i+1 < n) { cp = ((b0&0x1F)<<6)|((uint8_t)s[i+1]&0x3F); seq=2; }
        else if ((b0 & 0xF0) == 0xE0 && i+2 < n) { cp = ((b0&0x0F)<<12)|(((uint8_t)s[i+1]&0x3F)<<6)|((uint8_t)s[i+2]&0x3F); seq=3; }
        else if ((b0 & 0xF8) == 0xF0 && i+3 < n) { cp = ((b0&0x07)<<18)|(((uint8_t)s[i+1]&0x3F)<<12)|(((uint8_t)s[i+2]&0x3F)<<6)|((uint8_t)s[i+3]&0x3F); seq=4; }
        else { cp = b0; seq = 1; }
        out += static_cast<wchar_t>(cp);
        i += seq;
    }
    return out;
#endif
}

std::wstring SyntaxHelpReaderComponent::WcharTToWstr(const WCHAR_T* s) {
    if (!s) return {};
#ifdef _WINDOWS
    return std::wstring(s);
#else
    std::wstring out;
    for (const WCHAR_T* p = s; *p; ++p)
        out += static_cast<wchar_t>(*p);
    return out;
#endif
}

// ============================================================================
//  JSON string array parser
// ============================================================================

bool SyntaxHelpReaderComponent::SkipWs(const std::string& s, size_t& pos) {
    while (pos < s.size() && (uint8_t)s[pos] <= 0x20) ++pos;
    return pos < s.size();
}

bool SyntaxHelpReaderComponent::ParseJsonStr(const std::string& s, size_t& pos,
                                              std::string& out) {
    if (pos >= s.size() || s[pos] != '"') return false;
    ++pos;
    out.clear();
    while (pos < s.size()) {
        char c = s[pos++];
        if (c == '"') return true;
        if (c == '\\' && pos < s.size()) {
            char esc = s[pos++];
            switch (esc) {
                case '"':  out += '"';  break;
                case '\\': out += '\\'; break;
                case '/':  out += '/';  break;
                case 'n':  out += '\n'; break;
                case 'r':  out += '\r'; break;
                case 't':  out += '\t'; break;
                case 'u': {
                    if (pos + 4 <= s.size()) {
                        unsigned long cp = std::stoul(s.substr(pos, 4), nullptr, 16);
                        pos += 4;
                        // encode to UTF-8
                        if (cp < 0x80) { out += static_cast<char>(cp); }
                        else if (cp < 0x800) {
                            out += static_cast<char>(0xC0|(cp>>6));
                            out += static_cast<char>(0x80|(cp&0x3F));
                        } else {
                            out += static_cast<char>(0xE0|(cp>>12));
                            out += static_cast<char>(0x80|((cp>>6)&0x3F));
                            out += static_cast<char>(0x80|(cp&0x3F));
                        }
                    }
                    break;
                }
                default: out += esc; break;
            }
        } else {
            out += c;
        }
    }
    return false;
}

std::vector<std::string> SyntaxHelpReaderComponent::ParseJsonStringArray(
        const std::string& json) {
    std::vector<std::string> result;
    size_t pos = 0;
    if (!SkipWs(json, pos) || json[pos] != '[') return result;
    ++pos;
    while (pos < json.size()) {
        SkipWs(json, pos);
        if (pos >= json.size()) break;
        if (json[pos] == ']') break;
        if (json[pos] == ',') { ++pos; continue; }
        if (json[pos] == '"') {
            std::string val;
            ParseJsonStr(json, pos, val);
            result.push_back(val);
        } else {
            ++pos;
        }
    }
    return result;
}

} // namespace syntax_help
