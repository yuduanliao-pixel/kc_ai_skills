# Windows 驗證繞過 — Frida 腳本範本與 Patch 流程

> SKILL.md 的技術展開版。包含可直接使用的 Frida 腳本和 binary patch 流程。

---

## Frida 腳本範本

### 被動偵察（觀察不修改）

> 第一次動態分析時用。只記錄，不修改任何東西。

```javascript
// === 網路層 ===

// Hook connect — 記錄目標 server
var connectPtr = Module.findExportByName("ws2_32.dll", "connect");
if (connectPtr) {
    Interceptor.attach(connectPtr, {
        onEnter: function(args) {
            var sa = args[1];
            var port = (Memory.readU8(sa.add(2)) << 8) | Memory.readU8(sa.add(3));
            var ip = Memory.readU8(sa.add(4)) + "." + Memory.readU8(sa.add(5)) + "." +
                     Memory.readU8(sa.add(6)) + "." + Memory.readU8(sa.add(7));
            console.log("[CONNECT] " + ip + ":" + port + " | RetAddr=" + this.returnAddress);
        }
    });
}

// Hook send — 記錄封包（不修改！）
Interceptor.attach(Module.findExportByName("ws2_32.dll", "send"), {
    onEnter: function(args) {
        var len = args[2].toInt32();
        console.log("[SEND] " + len + " bytes | RetAddr=" + this.returnAddress);
        if (len > 0 && len < 2048) console.log(hexdump(args[1], { length: Math.min(len, 128) }));
    }
});

// Hook recv — 記錄回應（不修改！）
Interceptor.attach(Module.findExportByName("ws2_32.dll", "recv"), {
    onEnter: function(args) { this.buf = args[1]; },
    onLeave: function(retval) {
        var n = retval.toInt32();
        if (n > 0) {
            console.log("[RECV] " + n + " bytes | RetAddr=" + this.returnAddress);
            console.log(hexdump(this.buf, { length: Math.min(n, 128) }));
        }
    }
});

// === UI 層（最重要）===

// Hook CreateWindowExA — 主視窗建立 = bypass 成功指標
// backtrace 裡的 return address 是找決策點的關鍵線索
Interceptor.attach(Module.findExportByName("user32.dll", "CreateWindowExA"), {
    onEnter: function(args) {
        var cls = args[1];
        var title = args[2];
        var clsName = cls.isNull() ? "(null)" : (cls.toInt32() < 0xFFFF ? "#" + cls.toInt32() : Memory.readCString(cls));
        var titleStr = title.isNull() ? "(null)" : Memory.readCString(title);
        console.log("[CreateWindowExA] class=\"" + clsName + "\" title=\"" + titleStr + "\"");
        console.log("  Backtrace:\n" +
            Thread.backtrace(this.context, Backtracer.ACCURATE)
                .map(DebugSymbol.fromAddress).join('\n'));
    }
});

// Hook CreateWindowExW（有些程式用 Unicode 版）
Interceptor.attach(Module.findExportByName("user32.dll", "CreateWindowExW"), {
    onEnter: function(args) {
        var title = args[2];
        var titleStr = title.isNull() ? "(null)" : Memory.readUtf16String(title);
        console.log("[CreateWindowExW] title=\"" + titleStr + "\"");
        console.log("  Backtrace:\n" +
            Thread.backtrace(this.context, Backtracer.ACCURATE)
                .map(DebugSymbol.fromAddress).join('\n'));
    }
});

// Hook MessageBoxA — 錯誤訊息 + backtrace
Interceptor.attach(Module.findExportByName("user32.dll", "MessageBoxA"), {
    onEnter: function(args) {
        var text = args[1].isNull() ? "(null)" : Memory.readCString(args[1]);
        var title = args[2].isNull() ? "(null)" : Memory.readCString(args[2]);
        console.log("[MessageBoxA] title=\"" + title + "\" text=\"" + text + "\"");
        console.log("  Backtrace:\n" +
            Thread.backtrace(this.context, Backtracer.ACCURATE)
                .map(DebugSymbol.fromAddress).join('\n'));
    }
});
```

### 計時器凍結（對抗 VMP timestamp 檢查）

> 當 VMP 偵測到 single-step / hook 延遲時使用。凍結所有計時 API。

```javascript
var tick0 = null;
Interceptor.attach(Module.findExportByName("kernel32.dll", "GetTickCount"), {
    onLeave: function(retval) {
        if (!tick0) tick0 = retval.toInt32();
        retval.replace(ptr(tick0));
    }
});

var tick64_0 = null;
var gtc64 = Module.findExportByName("kernel32.dll", "GetTickCount64");
if (gtc64) {
    Interceptor.attach(gtc64, {
        onLeave: function(retval) {
            if (!tick64_0) tick64_0 = retval;
            retval.replace(tick64_0);
        }
    });
}

var qpc0 = null;
Interceptor.attach(Module.findExportByName("kernel32.dll", "QueryPerformanceCounter"), {
    onEnter: function(args) { this.pCounter = args[0]; },
    onLeave: function(retval) {
        if (!qpc0) qpc0 = Memory.readS64(this.pCounter);
        else Memory.writeS64(this.pCounter, qpc0);
    }
});
```

### 反偵測 bypass

> 隱藏 debugger 和 Frida 的存在。

```javascript
// IsDebuggerPresent → 永遠回 0
Interceptor.attach(Module.findExportByName("kernel32.dll", "IsDebuggerPresent"), {
    onLeave: function(retval) { retval.replace(0); }
});

// NtQueryInformationProcess → 隱藏 debug 資訊
Interceptor.attach(Module.findExportByName("ntdll.dll", "NtQueryInformationProcess"), {
    onEnter: function(args) {
        this.cls = args[1].toInt32();
        this.pInfo = args[2];
    },
    onLeave: function(retval) {
        if (this.cls === 0x7) Memory.writeInt(this.pInfo, 0);        // DebugPort
        else if (this.cls === 0x1F) Memory.writeInt(this.pInfo, 1);  // DebugFlags (1=no debugger)
        else if (this.cls === 0x1E) {                                // DebugObjectHandle
            Memory.writeInt(this.pInfo, 0);
            retval.replace(0xC0000353); // STATUS_PORT_NOT_SET
        }
    }
});

// 隱藏 Frida 字串（偵測 strstr 搜尋 "frida"）
Interceptor.attach(Module.findExportByName("msvcrt.dll", "strstr"), {
    onEnter: function(args) {
        try { this.needle = Memory.readCString(args[1]); } catch(e) {}
    },
    onLeave: function(retval) {
        if (this.needle && this.needle.toLowerCase().indexOf("frida") >= 0)
            retval.replace(ptr(0));
    }
});
```

### Runtime 記憶體 Patch

> 找到決策點後，用這個模板 patch。先讀 → 確認 → patch → 驗證。

```javascript
// 找到要改的地址後：
var target = ptr("0x0XXXXXXX");  // 從 backtrace 得到的地址

// Step 1: 讀取當前 bytes，確認是預期的指令
console.log("Before:", hexdump(target, { length: 16 }));

// Step 2: Patch
// 例：NOP 掉 6 byte 的 JNE near (0F 85 XX XX XX XX)
Memory.patchCode(target, 6, function(code) {
    var w = new X86Writer(code, { pc: target });
    w.putNop(); w.putNop(); w.putNop();
    w.putNop(); w.putNop(); w.putNop();
    w.flush();
});

// Step 3: 驗證 patch 結果
console.log("After:", hexdump(target, { length: 16 }));
```

### 記憶體掃描（找驗證結果字串）

```javascript
// 搜尋特定 byte pattern（例如「登陆成功」的 GBK bytes）
function scanMemory(pattern, label) {
    var found = [];
    Process.enumerateRanges('rw-').forEach(function(range) {
        try {
            Memory.scan(range.base, range.size, pattern, {
                onMatch: function(address) {
                    console.log("[FOUND " + label + "] at " + address);
                    console.log(hexdump(address.sub(16), { length: 64 }));
                    found.push(address);
                },
                onComplete: function() {}
            });
        } catch(e) {}
    });
    return found;
}

// 用法
scanMemory("B5 C7 C2 BD B3 C9 B9 A6", "登陆成功");
scanMemory("B4 ED CE F3", "错误");
scanMemory("66 6C 61 67 7B", "flag{");
```

---

## Frida Gadget Mode（隱身模式）

當一般 Frida 注入被偵測時，用 DLL sideload 方式載入：

1. 下載對應架構的 frida-gadget：`frida-gadget-XX.X.X-windows-x86.dll`
2. 改名成程式會載入的 DLL（例如 `version.dll`、`winmm.dll`）
3. 放到目標程式旁邊
4. 建立同名 config：`version.dll.config`
   ```json
   {
       "interaction": {
           "type": "script",
           "path": "hook.js"
       }
   }
   ```
5. 目標程式啟動時自動載入 gadget → 執行 hook.js

**找到正確的 DLL 名稱：**
```bash
# 列出程式 import 的 DLL
python3 -c "
import pefile
pe = pefile.PE('target.exe')
for entry in pe.DIRECTORY_ENTRY_IMPORT:
    print(entry.dll.decode())
"
# 挑一個程式有 import 但不影響功能的 DLL（version.dll 通常安全）
```

---

## Binary Patch 流程

### 從 Frida runtime address 算回 file offset

```python
# 已知資訊（從 PE header 和 Frida 取得）
runtime_va = 0x00451234      # Frida backtrace 拿到的地址（替換成你的實際值）
image_base = 0x00400000      # PE header

rva = runtime_va - image_base  # = 0x00051234

# 找到 RVA 落在哪個 section
# 例如 .text: VA=0x00001000, file_offset=0x00000400
section_va = 0x00001000
section_file = 0x00000400

file_offset = rva - section_va + section_file
```

### Python patch 腳本

```python
import shutil
import struct

original = "target.exe"
patched = "target_patched.exe"

# 備份
shutil.copy2(original, original + ".bak")

data = bytearray(open(original, "rb").read())

# 確認目標 bytes 正確（防止改錯位置）
expected = bytes([0x0F, 0x85])  # JNE near
actual = data[offset:offset+2]
assert actual == expected, f"Expected {expected.hex()} but got {actual.hex()} at offset {offset:#x}"

# Patch: JNE near → NOP x6
data[offset:offset+6] = b'\x90' * 6

open(patched, "wb").write(data)
print(f"Patched {patched} ({len(data)} bytes)")
print(f"  Offset: {offset:#x}")
print(f"  Original: {expected.hex()}")
print(f"  Patched: {'90' * 6}")
```

### 進階：用 LIEF 改 PE 結構

```python
import lief

binary = lief.parse("target.exe")

# 改 import table — 讓程式載入我們的 DLL
binary.add_library("myhook.dll")

# 加新 section — 塞 hook code
section = lief.PE.Section(".hook")
section.content = list(hook_shellcode)
section.characteristics = (lief.PE.Section.CHARACTERISTICS.MEM_READ |
                           lief.PE.Section.CHARACTERISTICS.MEM_EXECUTE)
binary.add_section(section)

# 改 entry point — 先跑我們的 code
binary.optional_header.addressof_entrypoint = new_entry_rva

binary.write("target_hooked.exe")
```

適用場景：需要永久注入自訂 code、改 import table 載入 hook DLL、或加新 section 時。
簡單改幾個 byte 用上面的 Python 腳本就好，不需要 LIEF。

---

## Differential Trace（API 比較法）

> 比較成功/失敗兩次的 API call 序列，找到分歧點。
> 這是替代「記憶體 dump 比較」的正確方法。

```javascript
// 記錄所有 Win32 API 呼叫到檔案
var logFile = new File("C:\\CTF\\api_trace.log", "w");
var callCount = 0;

function traceAPI(dll, func) {
    var p = Module.findExportByName(dll, func);
    if (!p) return;
    Interceptor.attach(p, {
        onEnter: function(args) {
            callCount++;
            var line = callCount + "|" + func + "|" + this.returnAddress + "\n";
            logFile.write(line);
        }
    });
}

// Hook 關鍵 API
["CreateWindowExA", "CreateWindowExW", "ShowWindow", "UpdateWindow",
 "MessageBoxA", "MessageBoxW", "DestroyWindow"].forEach(function(f) {
    traceAPI("user32.dll", f);
});
["connect", "send", "recv", "closesocket"].forEach(function(f) {
    traceAPI("ws2_32.dll", f);
});

// 程式結束時 flush
logFile.flush();
```

跑兩次（成功/失敗），然後 diff 兩份 log：
```bash
diff api_trace_success.log api_trace_fail.log
```

分歧點的 RetAddr 就是決策位置附近。
