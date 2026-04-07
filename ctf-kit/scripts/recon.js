/**
 * recon.js — Frida 被動偵察腳本
 *
 * 用途：第一次動態分析時掛上去，觀察目標程式的行為。
 *       只記錄，不修改任何東西。
 *
 * 使用方式：
 *   frida -l recon.js -f target.exe          # spawn（程式啟動前 hook）
 *   frida -l recon.js -p <PID>               # attach（程式已在跑）
 *   frida -l recon.js -f target.exe --no-pause  # spawn + 自動 resume
 *
 * 輸出：
 *   所有 hook 結果印到 console。
 *   如需寫檔，取消註解底部的 File output 區塊。
 *
 * 自訂：
 *   根據靜態分析結果，啟用/停用對應的 hook 區塊。
 *   每個區塊開頭都有 ENABLE 開關。
 */

"use strict";

// ============================================================
// 設定區 — 根據目標程式調整
// ============================================================

var ENABLE_NETWORK    = true;   // WS2_32: connect, send, recv
var ENABLE_WINDOW     = true;   // CreateWindowExA/W（最重要：找決策點）
var ENABLE_MESSAGEBOX = true;   // MessageBoxA/W（錯誤訊息 + backtrace）
var ENABLE_STRCMP     = true;   // lstrcmpA, CompareStringA（本地驗證）
var ENABLE_CRYPTO     = false;  // CryptDecrypt, BCryptDecrypt
var ENABLE_FILE       = false;  // CreateFileA/W, ReadFile（配置檔讀取）
var ENABLE_PROCESS    = false;  // CreateProcessA/W（loader 偵測）
var ENABLE_REGISTRY   = false;  // RegOpenKeyExA/W, RegQueryValueExA/W
var ENABLE_TIME       = false;  // GetSystemTime, GetLocalTime（時間驗證偵測）
var ENABLE_MEMORY_SCAN = false; // 定時掃描記憶體找特定 pattern

// 記憶體掃描 pattern — 根據目標程式的靜態分析結果填入
// 用 pe_info.py --strings 或 --gbk 找到關鍵字後，把 hex 填在這裡
// GBK 轉換：python3 -c "print(' '.join(f'{b:02X}' for b in '關鍵字'.encode('gbk')))"
var SCAN_PATTERNS = [
    // { pattern: "xx xx xx xx", label: "驗證成功訊息" },
    // { pattern: "xx xx xx xx", label: "錯誤訊息" },
    // { pattern: "66 6C 61 67 7B", label: "flag{" },
];

// ============================================================
// 工具函數
// ============================================================

function safeReadCString(p) {
    if (p.isNull()) return "(null)";
    try { return Memory.readCString(p); } catch (e) { return "(unreadable)"; }
}

function safeReadUtf16(p) {
    if (p.isNull()) return "(null)";
    try { return Memory.readUtf16String(p); } catch (e) { return "(unreadable)"; }
}

function backtrace(ctx) {
    return "  Backtrace:\n" +
        Thread.backtrace(ctx, Backtracer.ACCURATE)
            .map(DebugSymbol.fromAddress).join("\n");
}

function tryHook(dll, func, callbacks) {
    var p = Module.findExportByName(dll, func);
    if (p) {
        Interceptor.attach(p, callbacks);
        return true;
    }
    return false;
}

// ============================================================
// 網路層 — connect / send / recv
// ============================================================

if (ENABLE_NETWORK) {
    tryHook("ws2_32.dll", "connect", {
        onEnter: function (args) {
            var sa = args[1];
            var family = Memory.readU16(sa);
            if (family === 2) { // AF_INET
                var port = (Memory.readU8(sa.add(2)) << 8) | Memory.readU8(sa.add(3));
                var ip = Memory.readU8(sa.add(4)) + "." + Memory.readU8(sa.add(5)) + "." +
                         Memory.readU8(sa.add(6)) + "." + Memory.readU8(sa.add(7));
                console.log("[CONNECT] " + ip + ":" + port + " | RetAddr=" + this.returnAddress);
            }
        }
    });

    tryHook("ws2_32.dll", "send", {
        onEnter: function (args) {
            var len = args[2].toInt32();
            console.log("[SEND] " + len + " bytes | RetAddr=" + this.returnAddress);
            if (len > 0 && len < 2048) {
                console.log(hexdump(args[1], { length: Math.min(len, 128) }));
            }
        }
    });

    tryHook("ws2_32.dll", "recv", {
        onEnter: function (args) {
            this.buf = args[1];
        },
        onLeave: function (retval) {
            var n = retval.toInt32();
            if (n > 0) {
                console.log("[RECV] " + n + " bytes | RetAddr=" + this.returnAddress);
                console.log(hexdump(this.buf, { length: Math.min(n, 128) }));
            }
        }
    });

    // WSAConnect（某些程式用這個而非 connect）
    tryHook("ws2_32.dll", "WSAConnect", {
        onEnter: function (args) {
            var sa = args[1];
            var family = Memory.readU16(sa);
            if (family === 2) {
                var port = (Memory.readU8(sa.add(2)) << 8) | Memory.readU8(sa.add(3));
                var ip = Memory.readU8(sa.add(4)) + "." + Memory.readU8(sa.add(5)) + "." +
                         Memory.readU8(sa.add(6)) + "." + Memory.readU8(sa.add(7));
                console.log("[WSA_CONNECT] " + ip + ":" + port + " | RetAddr=" + this.returnAddress);
            }
        }
    });
}

// ============================================================
// UI 層 — CreateWindowEx / MessageBox
// ============================================================

if (ENABLE_WINDOW) {
    tryHook("user32.dll", "CreateWindowExA", {
        onEnter: function (args) {
            var cls = args[1];
            var clsName = cls.isNull() ? "(null)" :
                          (cls.toInt32() > 0 && cls.toInt32() < 0xFFFF) ? ("#" + cls.toInt32()) :
                          safeReadCString(cls);
            var title = safeReadCString(args[2]);
            console.log("[CreateWindowExA] class=\"" + clsName + "\" title=\"" + title + "\"");
            console.log(backtrace(this.context));
        }
    });

    tryHook("user32.dll", "CreateWindowExW", {
        onEnter: function (args) {
            var title = safeReadUtf16(args[2]);
            console.log("[CreateWindowExW] title=\"" + title + "\"");
            console.log(backtrace(this.context));
        }
    });

    tryHook("user32.dll", "ShowWindow", {
        onEnter: function (args) {
            var hwnd = args[0];
            var cmd = args[1].toInt32();
            // SW_SHOW=5, SW_HIDE=0, SW_MAXIMIZE=3
            console.log("[ShowWindow] hwnd=" + hwnd + " cmd=" + cmd + " | RetAddr=" + this.returnAddress);
        }
    });
}

if (ENABLE_MESSAGEBOX) {
    tryHook("user32.dll", "MessageBoxA", {
        onEnter: function (args) {
            var text = safeReadCString(args[1]);
            var title = safeReadCString(args[2]);
            console.log("[MessageBoxA] title=\"" + title + "\" text=\"" + text + "\"");
            console.log(backtrace(this.context));
        }
    });

    tryHook("user32.dll", "MessageBoxW", {
        onEnter: function (args) {
            var text = safeReadUtf16(args[1]);
            var title = safeReadUtf16(args[2]);
            console.log("[MessageBoxW] title=\"" + title + "\" text=\"" + text + "\"");
            console.log(backtrace(this.context));
        }
    });
}

// ============================================================
// 字串比較 — lstrcmpA / CompareStringA（本地驗證）
// ============================================================

if (ENABLE_STRCMP) {
    tryHook("kernel32.dll", "lstrcmpA", {
        onEnter: function (args) {
            var s1 = safeReadCString(args[0]);
            var s2 = safeReadCString(args[1]);
            console.log("[lstrcmpA] \"" + s1 + "\" vs \"" + s2 + "\" | RetAddr=" + this.returnAddress);
        }
    });

    tryHook("kernel32.dll", "lstrcmpiA", {
        onEnter: function (args) {
            var s1 = safeReadCString(args[0]);
            var s2 = safeReadCString(args[1]);
            console.log("[lstrcmpiA] \"" + s1 + "\" vs \"" + s2 + "\" | RetAddr=" + this.returnAddress);
        }
    });

    tryHook("kernel32.dll", "CompareStringA", {
        onEnter: function (args) {
            var s1 = safeReadCString(args[2]);
            var s2 = safeReadCString(args[4]);
            console.log("[CompareStringA] \"" + s1 + "\" vs \"" + s2 + "\" | RetAddr=" + this.returnAddress);
        }
    });
}

// ============================================================
// 加密 API
// ============================================================

if (ENABLE_CRYPTO) {
    // WinCrypt
    tryHook("advapi32.dll", "CryptDecrypt", {
        onEnter: function (args) {
            this.pbData = args[3];
            this.pdwDataLen = args[4];
        },
        onLeave: function (retval) {
            if (retval.toInt32() !== 0) {
                var len = Memory.readU32(this.pdwDataLen);
                console.log("[CryptDecrypt] " + len + " bytes | RetAddr=" + this.returnAddress);
                if (len > 0 && len < 2048) {
                    console.log(hexdump(this.pbData, { length: Math.min(len, 128) }));
                }
            }
        }
    });

    // BCrypt
    tryHook("bcrypt.dll", "BCryptDecrypt", {
        onEnter: function (args) {
            this.pbOutput = args[4];
            this.pcbResult = args[6];
            console.log("[BCryptDecrypt] called | RetAddr=" + this.returnAddress);
        },
        onLeave: function (retval) {
            if (retval.toInt32() === 0 && !this.pcbResult.isNull()) {
                var len = Memory.readU32(this.pcbResult);
                console.log("[BCryptDecrypt] output " + len + " bytes");
                if (len > 0 && len < 2048) {
                    console.log(hexdump(this.pbOutput, { length: Math.min(len, 128) }));
                }
            }
        }
    });
}

// ============================================================
// 檔案操作（配置檔偵測）
// ============================================================

if (ENABLE_FILE) {
    tryHook("kernel32.dll", "CreateFileA", {
        onEnter: function (args) {
            var path = safeReadCString(args[0]);
            var access = args[1].toInt32();
            var rw = (access & 0x80000000) ? "R" : "";
            rw += (access & 0x40000000) ? "W" : "";
            console.log("[CreateFileA] " + rw + " \"" + path + "\" | RetAddr=" + this.returnAddress);
        }
    });

    tryHook("kernel32.dll", "CreateFileW", {
        onEnter: function (args) {
            var path = safeReadUtf16(args[0]);
            console.log("[CreateFileW] \"" + path + "\" | RetAddr=" + this.returnAddress);
        }
    });
}

// ============================================================
// 子進程（loader 偵測）
// ============================================================

if (ENABLE_PROCESS) {
    tryHook("kernel32.dll", "CreateProcessA", {
        onEnter: function (args) {
            var app = safeReadCString(args[0]);
            var cmd = safeReadCString(args[1]);
            console.log("[CreateProcessA] app=\"" + app + "\" cmd=\"" + cmd + "\"");
            console.log(backtrace(this.context));
        }
    });

    tryHook("kernel32.dll", "CreateProcessW", {
        onEnter: function (args) {
            var app = safeReadUtf16(args[0]);
            var cmd = safeReadUtf16(args[1]);
            console.log("[CreateProcessW] app=\"" + app + "\" cmd=\"" + cmd + "\"");
        }
    });
}

// ============================================================
// Registry（授權資訊偵測）
// ============================================================

if (ENABLE_REGISTRY) {
    tryHook("advapi32.dll", "RegOpenKeyExA", {
        onEnter: function (args) {
            var subkey = safeReadCString(args[1]);
            console.log("[RegOpenKeyExA] \"" + subkey + "\" | RetAddr=" + this.returnAddress);
        }
    });

    tryHook("advapi32.dll", "RegQueryValueExA", {
        onEnter: function (args) {
            var name = safeReadCString(args[1]);
            console.log("[RegQueryValueExA] \"" + name + "\" | RetAddr=" + this.returnAddress);
        }
    });
}

// ============================================================
// 時間 API（時間驗證偵測）
// ============================================================

if (ENABLE_TIME) {
    tryHook("kernel32.dll", "GetSystemTime", {
        onEnter: function (args) {
            console.log("[GetSystemTime] called | RetAddr=" + this.returnAddress);
        }
    });

    tryHook("kernel32.dll", "GetLocalTime", {
        onEnter: function (args) {
            console.log("[GetLocalTime] called | RetAddr=" + this.returnAddress);
        }
    });
}

// ============================================================
// 記憶體掃描（定時）
// ============================================================

if (ENABLE_MEMORY_SCAN && SCAN_PATTERNS.length > 0) {
    setInterval(function () {
        SCAN_PATTERNS.forEach(function (entry) {
            Process.enumerateRanges("rw-").forEach(function (range) {
                try {
                    Memory.scan(range.base, range.size, entry.pattern, {
                        onMatch: function (address) {
                            console.log("[SCAN] Found \"" + entry.label + "\" at " + address);
                            console.log(hexdump(address.sub(16), { length: 64 }));
                        },
                        onComplete: function () {}
                    });
                } catch (e) {}
            });
        });
    }, 3000); // 每 3 秒掃一次
}

// ============================================================
// File output（取消註解以寫檔）
// ============================================================

// var logFile = new File("C:\\CTF\\recon_log.txt", "w");
// 在各 hook 的 console.log 後加上：
// logFile.write("[CONNECT] " + ip + ":" + port + "\n");
// logFile.flush();

console.log("[recon.js] Loaded. Hooks active:");
console.log("  Network=" + ENABLE_NETWORK + " Window=" + ENABLE_WINDOW +
            " MessageBox=" + ENABLE_MESSAGEBOX + " Strcmp=" + ENABLE_STRCMP);
console.log("  Crypto=" + ENABLE_CRYPTO + " File=" + ENABLE_FILE +
            " Process=" + ENABLE_PROCESS + " Registry=" + ENABLE_REGISTRY +
            " Time=" + ENABLE_TIME + " MemScan=" + ENABLE_MEMORY_SCAN);
