#!/usr/bin/env python3
"""
pe_info.py — PE 靜態偵察腳本

用途：不依賴外部工具，用純 Python 解析 PE header，
      輸出 bypass 流程需要的所有靜態資訊。

使用方式：
    python pe_info.py target.exe
    python pe_info.py target.exe --json             # JSON 輸出（給其他腳本用）
    python pe_info.py target.exe --strings          # 額外做字串搜尋
    python pe_info.py target.exe --gbk "关键字"      # 搜尋 GBK 編碼（簡體中文）
    python pe_info.py target.exe --big5 "關鍵字"     # 搜尋 Big5 編碼（繁體中文）
    python pe_info.py target.exe --utf8 "關鍵字"     # 搜尋 UTF-8 編碼
    python pe_info.py target.exe --encoding euc-kr "키워드"  # 任意編碼

輸出：
    - 基本資訊（arch, ImageBase, EP, ASLR, DEP, manifest）
    - Section 列表 + entropy + 保護殼判斷
    - Import 分析 + 攻擊面提示
    - 可疑字串（IP, URL, 配置檔路徑, 視窗標題）

不需要安裝任何第三方套件。
"""

import struct
import sys
import math
import json
import os
import re
from collections import OrderedDict


# =============================================================
# PE Parser（純 stdlib，不依賴 pefile）
# =============================================================

def read_pe(data):
    """解析 PE header，回傳結構化資訊"""
    info = OrderedDict()

    # DOS header
    if data[:2] != b"MZ":
        return {"error": "Not a PE file (no MZ header)"}

    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]

    if data[pe_offset:pe_offset+4] != b"PE\x00\x00":
        return {"error": "Not a PE file (no PE signature)"}

    # COFF header
    coff = pe_offset + 4
    machine = struct.unpack_from("<H", data, coff)[0]
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    timestamp = struct.unpack_from("<I", data, coff + 4)[0]
    optional_size = struct.unpack_from("<H", data, coff + 16)[0]
    characteristics = struct.unpack_from("<H", data, coff + 18)[0]

    machines = {0x14c: "x86 (32-bit)", 0x8664: "x64 (64-bit)", 0x1c0: "ARM", 0xaa64: "ARM64"}
    info["architecture"] = machines.get(machine, f"Unknown (0x{machine:04x})")
    info["is_32bit"] = machine == 0x14c
    info["num_sections"] = num_sections
    info["is_dll"] = bool(characteristics & 0x2000)

    # Optional header
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    is_pe32plus = magic == 0x20b

    if is_pe32plus:
        info["image_base"] = f"0x{struct.unpack_from('<Q', data, opt + 24)[0]:016x}"
        entry_rva = struct.unpack_from("<I", data, opt + 16)[0]
        dll_chars = struct.unpack_from("<H", data, opt + 70)[0]
        subsystem = struct.unpack_from("<H", data, opt + 68)[0]
        num_data_dirs = struct.unpack_from("<I", data, opt + 108)[0]
        data_dirs_offset = opt + 112
    else:
        info["image_base"] = f"0x{struct.unpack_from('<I', data, opt + 28)[0]:08x}"
        entry_rva = struct.unpack_from("<I", data, opt + 16)[0]
        dll_chars = struct.unpack_from("<H", data, opt + 70)[0]
        subsystem = struct.unpack_from("<H", data, opt + 68)[0]
        num_data_dirs = struct.unpack_from("<I", data, opt + 96)[0]
        data_dirs_offset = opt + 96 + 4

    info["entry_point_rva"] = f"0x{entry_rva:08x}"

    subsystems = {1: "Native", 2: "GUI", 3: "CLI (Console)", 9: "WinCE"}
    info["subsystem"] = subsystems.get(subsystem, f"Unknown ({subsystem})")

    info["aslr"] = bool(dll_chars & 0x0040)
    info["dep"] = bool(dll_chars & 0x0100)
    info["high_entropy_aslr"] = bool(dll_chars & 0x0020)

    # Sections
    section_table = opt + optional_size
    sections = []
    ep_section = None

    for i in range(num_sections):
        s_off = section_table + i * 40
        name_bytes = data[s_off:s_off+8]
        name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
        virtual_size = struct.unpack_from("<I", data, s_off + 8)[0]
        virtual_addr = struct.unpack_from("<I", data, s_off + 12)[0]
        raw_size = struct.unpack_from("<I", data, s_off + 16)[0]
        raw_offset = struct.unpack_from("<I", data, s_off + 20)[0]
        chars = struct.unpack_from("<I", data, s_off + 36)[0]

        # Entropy
        ent = 0.0
        if raw_size > 0 and raw_offset + raw_size <= len(data):
            ent = calc_entropy(data[raw_offset:raw_offset + raw_size])

        # Flags
        flags = []
        if chars & 0x20000000: flags.append("X")  # execute
        if chars & 0x40000000: flags.append("R")  # read
        if chars & 0x80000000: flags.append("W")  # write

        section = OrderedDict([
            ("name", name),
            ("virtual_addr", f"0x{virtual_addr:08x}"),
            ("virtual_size", f"0x{virtual_size:08x}"),
            ("raw_offset", f"0x{raw_offset:08x}"),
            ("raw_size", f"0x{raw_size:08x}"),
            ("entropy", round(ent, 4)),
            ("flags", "".join(flags)),
        ])

        # 判斷保護殼
        packer_hint = detect_packer_section(name, ent, raw_size, virtual_size)
        if packer_hint:
            section["packer_hint"] = packer_hint

        sections.append(section)

        # EP 落在哪個 section
        if virtual_addr <= entry_rva < virtual_addr + virtual_size:
            ep_section = name

    info["entry_point_section"] = ep_section or "(unknown)"
    if ep_section and ep_section != ".text":
        info["ep_warning"] = f"Entry point not in .text (in {ep_section}) — likely packed"

    info["sections"] = sections

    # Imports
    imports = parse_imports(data, data_dirs_offset, num_data_dirs,
                           sections, is_pe32plus)
    if imports:
        info["imports"] = imports

    # Manifest（搜尋 requireAdministrator）
    manifest = extract_manifest_hint(data)
    if manifest:
        info["manifest"] = manifest

    return info


def calc_entropy(data):
    """計算 Shannon entropy (0.0 - 8.0)"""
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    length = len(data)
    entropy = 0.0
    for f in freq:
        if f > 0:
            p = f / length
            entropy -= p * math.log2(p)
    return entropy


def detect_packer_section(name, entropy, raw_size, virtual_size):
    """根據 section 名稱和特徵判斷保護殼"""
    name_lower = name.lower()

    if name_lower.startswith(".vmp") or name_lower.startswith(".svmp"):
        return "VMProtect"
    if name_lower in (".upx0", ".upx1", "upx0", "upx1"):
        return "UPX (try: upx -d)"
    if name_lower == ".themida" or name_lower == ".winlice":
        return "Themida / WinLicense"
    if name_lower == ".aspack":
        return "ASPack"
    if name_lower == ".adata":
        return "ASProtect (possible)"
    if name_lower in (".nsp0", ".nsp1", ".nsp2"):
        return "NsPack"
    if name_lower == ".perplex":
        return "Perplex PE Protector"

    # 高 entropy + 可執行 = 可能加殼
    if entropy > 7.5 and raw_size > 0x1000:
        return f"High entropy ({entropy:.2f}) — possibly packed/encrypted"

    # .text raw_size = 0 = 殼搬移了 code
    if name_lower == ".text" and raw_size == 0 and virtual_size > 0:
        return "Empty .text section — code relocated by packer"

    return None


def parse_imports(data, data_dirs_offset, num_data_dirs, sections, is_pe32plus):
    """解析 import table"""
    if num_data_dirs < 2:
        return None

    entry_size = 8  # each data dir entry = 8 bytes (RVA + Size)
    import_rva = struct.unpack_from("<I", data, data_dirs_offset + entry_size)[0]
    import_size = struct.unpack_from("<I", data, data_dirs_offset + entry_size + 4)[0]

    if import_rva == 0:
        return None

    import_offset = rva_to_offset(import_rva, sections)
    if import_offset is None:
        return None

    imports = OrderedDict()
    pos = import_offset

    while pos + 20 <= len(data):
        ilt_rva = struct.unpack_from("<I", data, pos)[0]
        name_rva = struct.unpack_from("<I", data, pos + 12)[0]

        if name_rva == 0:
            break

        name_offset = rva_to_offset(name_rva, sections)
        if name_offset and name_offset < len(data):
            dll_name = read_cstring(data, name_offset)
        else:
            dll_name = f"(RVA 0x{name_rva:08x})"

        # 解析函數名
        funcs = []
        if ilt_rva != 0:
            ilt_offset = rva_to_offset(ilt_rva, sections)
            if ilt_offset:
                entry_sz = 8 if is_pe32plus else 4
                fpos = ilt_offset
                count = 0
                while fpos + entry_sz <= len(data) and count < 200:
                    if is_pe32plus:
                        entry_val = struct.unpack_from("<Q", data, fpos)[0]
                        ordinal_flag = 1 << 63
                    else:
                        entry_val = struct.unpack_from("<I", data, fpos)[0]
                        ordinal_flag = 1 << 31

                    if entry_val == 0:
                        break

                    if entry_val & ordinal_flag:
                        funcs.append(f"Ordinal #{entry_val & 0xFFFF}")
                    else:
                        hint_offset = rva_to_offset(entry_val & 0x7FFFFFFF, sections)
                        if hint_offset and hint_offset + 2 < len(data):
                            func_name = read_cstring(data, hint_offset + 2)
                            funcs.append(func_name)

                    fpos += entry_sz
                    count += 1

        attack_surface = analyze_import_surface(dll_name, funcs)

        entry = OrderedDict([("functions", funcs)])
        if attack_surface:
            entry["attack_surface"] = attack_surface
        imports[dll_name] = entry

        pos += 20

    return imports


def analyze_import_surface(dll_name, funcs):
    """根據 import 判斷攻擊面"""
    dll_lower = dll_name.lower()
    hints = []

    if "ws2_32" in dll_lower or "wsock32" in dll_lower:
        hints.append("Network verification — hook connect/send/recv")
    if "winhttp" in dll_lower or "wininet" in dll_lower:
        hints.append("HTTP-based verification — hook at HTTP layer")

    func_set = set(f.lower() for f in funcs)
    if func_set & {"lstrcmpa", "lstrcmpw", "comparestringa", "comparestringw"}:
        hints.append("String comparison — possible local key check")
    if func_set & {"cryptdecrypt", "cryptencrypt", "bcryptdecrypt", "bcryptencrypt"}:
        hints.append("Crypto API — hook to see plaintext")
    if func_set & {"getprocaddress"}:
        hints.append("Dynamic resolve — static IAT hook may be insufficient")
    if func_set & {"createprocessa", "createprocessw"}:
        hints.append("Spawns subprocess — check if loader")
    if func_set & {"messageboxw", "messageboxa"}:
        hints.append("MessageBox — hook for error messages + backtrace")
    if func_set & {"createwindowexa", "createwindowexw"}:
        hints.append("CreateWindowEx — KEY hook target (main window = bypass success)")
    if func_set & {"regopenkeyexa", "regopenkeyexw"}:
        hints.append("Registry access — may store license info")
    if func_set & {"getsystemtime", "getlocaltime"}:
        hints.append("Time API — possible time-based license check")

    return hints if hints else None


def rva_to_offset(rva, sections):
    """RVA → file offset"""
    for s in sections:
        va = int(s["virtual_addr"], 16)
        vs = int(s["virtual_size"], 16)
        ro = int(s["raw_offset"], 16)
        rs = int(s["raw_size"], 16)
        if va <= rva < va + max(vs, rs):
            return rva - va + ro
    return None


def read_cstring(data, offset, max_len=256):
    """讀取 null-terminated string"""
    end = data.find(b"\x00", offset, offset + max_len)
    if end == -1:
        end = offset + max_len
    return data[offset:end].decode("ascii", errors="replace")


def extract_manifest_hint(data):
    """搜尋 manifest 中的執行等級"""
    hints = {}
    # requireAdministrator
    if b"requireAdministrator" in data:
        hints["execution_level"] = "requireAdministrator"
    elif b"highestAvailable" in data:
        hints["execution_level"] = "highestAvailable"
    elif b"asInvoker" in data:
        hints["execution_level"] = "asInvoker"
    return hints if hints else None


# =============================================================
# 字串搜尋
# =============================================================

def search_strings(data, min_len=6):
    """搜尋可疑字串"""
    results = OrderedDict()

    # ASCII strings
    ascii_strings = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        s = m.group().decode("ascii")
        offset = m.start()
        ascii_strings.append((offset, s))

    # IP addresses
    ips = [(off, s) for off, s in ascii_strings
           if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", s)]
    if ips:
        results["ip_addresses"] = [{"offset": f"0x{o:08x}", "value": s} for o, s in ips[:20]]

    # URLs
    urls = [(off, s) for off, s in ascii_strings
            if re.search(r"https?://|ftp://|wss?://", s, re.IGNORECASE)]
    if urls:
        results["urls"] = [{"offset": f"0x{o:08x}", "value": s} for o, s in urls[:20]]

    # Config file paths
    configs = [(off, s) for off, s in ascii_strings
               if re.search(r"\.(ini|cfg|json|xml|conf|dat|key|lic)\b", s, re.IGNORECASE)]
    if configs:
        results["config_files"] = [{"offset": f"0x{o:08x}", "value": s} for o, s in configs[:20]]

    # Window class / registration
    wnd = [(off, s) for off, s in ascii_strings
           if any(k in s for k in ("Window", "Dialog", "MainWnd", "FormClass"))]
    if wnd:
        results["window_hints"] = [{"offset": f"0x{o:08x}", "value": s} for o, s in wnd[:10]]

    return results if results else None


def search_encoded_string(data, text, encoding):
    """搜尋指定編碼的字串（GBK、Big5、UTF-8 等）"""
    try:
        pattern = text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return []

    results = []
    start = 0
    while True:
        idx = data.find(pattern, start)
        if idx == -1:
            break
        results.append(f"0x{idx:08x}")
        start = idx + 1
    return results


# =============================================================
# Main
# =============================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python pe_info.py <target.exe> [--json] [--strings]")
        print("       [--gbk <text>] [--big5 <text>] [--utf8 <text>] [--encoding <enc> <text>]")
        sys.exit(1)

    target = sys.argv[1]
    use_json = "--json" in sys.argv
    do_strings = "--strings" in sys.argv

    # 收集所有編碼搜尋請求：[(encoding, text), ...]
    encoded_queries = []
    encoding_flags = {"--gbk": "gbk", "--big5": "big5", "--utf8": "utf-8"}
    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in encoding_flags and i + 1 < len(sys.argv):
            encoded_queries.append((encoding_flags[arg], sys.argv[i + 1]))
            i += 2
        elif arg == "--encoding" and i + 2 < len(sys.argv):
            encoded_queries.append((sys.argv[i + 1], sys.argv[i + 2]))
            i += 3
        else:
            i += 1

    if not os.path.isfile(target):
        print(f"Error: {target} not found")
        sys.exit(1)

    with open(target, "rb") as f:
        data = f.read()

    info = read_pe(data)
    info["file"] = target
    info["file_size"] = f"0x{len(data):x} ({len(data):,} bytes)"

    if do_strings:
        strings = search_strings(data)
        if strings:
            info["strings"] = strings

    if encoded_queries:
        enc_results = OrderedDict()
        for encoding, q in encoded_queries:
            hits = search_encoded_string(data, q, encoding)
            key = f"[{encoding}] {q}"
            enc_results[key] = hits if hits else ["(not found)"]
        info["encoded_search"] = enc_results

    if use_json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print_report(info)


def print_report(info):
    """人類可讀的報告"""
    print("=" * 60)
    print(f"  PE Analysis: {info.get('file', '?')}")
    print(f"  Size: {info.get('file_size', '?')}")
    print("=" * 60)

    if "error" in info:
        print(f"\n  ERROR: {info['error']}")
        return

    print(f"\n  Architecture:  {info['architecture']}")
    print(f"  ImageBase:     {info['image_base']}")
    print(f"  Entry Point:   {info['entry_point_rva']} (in {info['entry_point_section']})")
    print(f"  Subsystem:     {info['subsystem']}")
    print(f"  ASLR:          {'Yes' if info['aslr'] else 'No'}")
    print(f"  DEP:           {'Yes' if info['dep'] else 'No'}")
    print(f"  DLL:           {'Yes' if info['is_dll'] else 'No'}")

    if "ep_warning" in info:
        print(f"  ** WARNING:    {info['ep_warning']}")

    if "manifest" in info:
        for k, v in info["manifest"].items():
            print(f"  Manifest:      {k} = {v}")

    # Sections
    print(f"\n  Sections ({info['num_sections']}):")
    print(f"  {'Name':<12} {'VirtAddr':<12} {'VirtSize':<12} {'RawSize':<12} {'Entropy':<10} {'Flags':<6} Hint")
    print("  " + "-" * 80)
    for s in info.get("sections", []):
        hint = s.get("packer_hint", "")
        print(f"  {s['name']:<12} {s['virtual_addr']:<12} {s['virtual_size']:<12} "
              f"{s['raw_size']:<12} {s['entropy']:<10} {s['flags']:<6} {hint}")

    # Imports
    if "imports" in info:
        print(f"\n  Imports:")
        for dll, entry in info["imports"].items():
            funcs = entry.get("functions", [])
            surface = entry.get("attack_surface", [])
            print(f"\n  {dll} ({len(funcs)} functions)")
            if surface:
                for hint in surface:
                    print(f"    >> {hint}")
            for f in funcs[:15]:  # 只顯示前 15 個
                print(f"    - {f}")
            if len(funcs) > 15:
                print(f"    ... and {len(funcs) - 15} more")

    # Strings
    if "strings" in info:
        print(f"\n  Suspicious Strings:")
        for category, items in info["strings"].items():
            print(f"\n  [{category}]")
            for item in items:
                print(f"    {item['offset']}  {item['value'][:80]}")

    # Encoded string search
    if "encoded_search" in info:
        print(f"\n  Encoded String Search:")
        for query, hits in info["encoded_search"].items():
            print(f"    {query}: {', '.join(hits)}")

    print()


if __name__ == "__main__":
    main()
