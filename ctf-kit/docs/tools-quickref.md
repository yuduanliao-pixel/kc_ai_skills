# 工具使用速查

> Capstone、Unicorn、Frida Stalker 的實戰用法。
> 不是完整文檔，只收錄 bypass 場景最常用的 pattern。

---

## Frida Stalker

> **為什麼用 Stalker 而不是 INT3 斷點？**
> Stalker 用 dynamic binary instrumentation（動態改寫 code 到 slab 執行），
> 不插入 0xCC、不設 debug register、不觸發 exception。
> 對抗 code integrity check 和反調試比 INT3 穩定得多。
>
> **代價：** 效能 overhead 比 Interceptor 大。不要 follow 太多線程。

### 基本概念

```
Stalker.follow(threadId, options)   — 開始追蹤指定線程
Stalker.unfollow(threadId)          — 停止追蹤
Stalker.flush()                     — 強制輸出緩衝區

options.events                      — 要記錄的事件類型
options.transform                   — 逐 block 改寫指令（最強大的功能）
options.onReceive / onCallSummary   — 接收事件資料的 callback
```

### 場景 1：監看特定地址的暫存器值

> 最常用。等於「在某地址設斷點看 register」但不用 INT3。

```javascript
var moduleBase = Module.findBaseAddress("target.exe");
var watchAddr = moduleBase.add(0x1234);  // 要觀察的地址

Stalker.follow(Process.getCurrentThreadId(), {
    transform: function(iterator) {
        var instruction;
        while ((instruction = iterator.next()) !== null) {
            // 在目標地址插入 callback
            if (instruction.address.equals(watchAddr)) {
                iterator.putCallout(function(context) {
                    console.log("[HIT] addr=" + watchAddr);
                    console.log("  EAX=" + context.eax);
                    console.log("  EBX=" + context.ebx);
                    console.log("  ECX=" + context.ecx);
                    console.log("  EDX=" + context.edx);
                    console.log("  [ESP]=" + Memory.readPointer(context.esp));
                    // 讀記憶體
                    // var val = Memory.readU32(context.eax);
                });
            }
            iterator.keep();  // 保留原始指令
        }
    }
});
```

### 場景 2：監看特定地址範圍（縮小 overhead）

> **關鍵技巧：** 只在目標範圍內做 transform，其他地址直接 keep。
> 這是避免 Stalker hang 的核心——限制 instrumentation 範圍。

```javascript
var rangeStart = moduleBase.add(0x1000);
var rangeEnd   = moduleBase.add(0x2000);

Stalker.follow(targetThreadId, {
    transform: function(iterator) {
        var instruction;
        var blockStart = null;

        while ((instruction = iterator.next()) !== null) {
            if (!blockStart) blockStart = instruction.address;

            // 只在目標範圍內插入 callout
            if (instruction.address.compare(rangeStart) >= 0 &&
                instruction.address.compare(rangeEnd) < 0) {

                // 只攔截條件跳轉（找決策點）
                if (instruction.mnemonic === "je" || instruction.mnemonic === "jne" ||
                    instruction.mnemonic === "jz" || instruction.mnemonic === "jnz") {
                    var addr = instruction.address;
                    iterator.putCallout(function(context) {
                        // EFLAGS 的 ZF bit（bit 6）
                        var zf = (context.eflags >>> 6) & 1;
                        console.log("[BRANCH] " + addr + " ZF=" + zf);
                    });
                }
            }
            iterator.keep();
        }
    }
});
```

### 場景 3：追蹤 API call 序列（輕量版 differential trace）

> 比 Interceptor 多一個好處：能看到 call 指令的**來源地址**和目標。

```javascript
var logFile = new File("C:\\CTF\\stalker_trace.log", "w");
var targetModule = Process.findModuleByName("target.exe");

Stalker.follow(targetThreadId, {
    events: { call: true },   // 只記錄 call 事件
    onReceive: function(events) {
        var parsed = Stalker.parse(events, { stringify: false, annotate: false });
        for (var i = 0; i < parsed.length; i++) {
            var ev = parsed[i];
            // ev = [event_type, from_addr, to_addr, ...]
            var from = ptr(ev[1]);
            var to = ptr(ev[2]);

            // 只記錄從目標模組出發的 call
            if (targetModule.base.compare(from) <= 0 &&
                from.compare(targetModule.base.add(targetModule.size)) < 0) {
                var sym = DebugSymbol.fromAddress(to);
                logFile.write(from + " -> " + to + " " + sym + "\n");
            }
        }
        logFile.flush();
    }
});
```

### 場景 4：修改執行流程（不改 binary）

> 在 transform 裡可以**替換指令**，等於 runtime patch 但不改原始 byte。
> 比 Memory.patchCode 更隱蔽——原始 code 完全不動。

```javascript
var patchAddr = moduleBase.add(0x5678);

Stalker.follow(targetThreadId, {
    transform: function(iterator) {
        var instruction;
        while ((instruction = iterator.next()) !== null) {
            if (instruction.address.equals(patchAddr)) {
                // 原始指令是 JNE（跳過成功路徑）
                // 替換成 NOP，讓它 fall through 到成功路徑
                iterator.putNop();
                // 不 keep() → 原始指令被丟棄
            } else {
                iterator.keep();
            }
        }
    }
});
```

### Stalker 使用注意事項

| 問題 | 對策 |
|------|------|
| Follow 太多線程 → 程式 hang | **只 follow 1-2 個目標線程**，用 `Process.enumerateThreads()` 找到正確的 |
| 長時間 follow → 記憶體暴漲 | 拿到需要的資料後立刻 `Stalker.unfollow()` |
| Transform callback 太重 → 程式變慢 | 限制 callout 只在目標地址範圍內觸發 |
| Follow 主線程但看不到認證邏輯 | 認證可能在 worker thread，試 follow 其他線程 |
| Stalker + 網路驗證 → 超時 | 網路封包處理路徑不要 follow，或凍結計時 API |
| `iterator.keep()` 忘了寫 → crash | 每個 instruction 都要 keep()（除非刻意替換）|

### 找到正確的線程

```javascript
// 列出所有線程
Process.enumerateThreads().forEach(function(t) {
    console.log("Thread " + t.id + " state=" + t.state);
});

// 通常策略：
// 1. hook 目標 API（如 recv），在 onEnter 記錄 Process.getCurrentThreadId()
// 2. 用記錄到的 threadId 做 Stalker.follow
```

### Stalker vs Interceptor vs INT3 — 選擇指南

| | Stalker | Interceptor | INT3 (debugger) |
|---|---------|-------------|-----------------|
| 原理 | 動態改寫 code 到 slab | Hook 函數入口（改前幾 byte）| 插入 0xCC 觸發 exception |
| 觸發反調試？ | 不觸發 debug flag | 可能觸發 code integrity | 觸發 INT3 偵測 |
| 效能 | 中-重（取決於範圍）| 輕 | 輕 |
| 粒度 | **逐指令** | 函數入口/出口 | 任意地址 |
| 適合場景 | 監看暫存器值、追蹤分支走向 | Hook API、看參數/返回值 | 無反調試的程式 |
| 修改能力 | 替換指令 | 改參數/返回值 | 改 context |
| VMP 相容性 | ✅ 高（不改原始 byte）| ⚠️ 中（改函數入口可能被偵測）| ❌ 低 |

**經驗法則：**
- 想看 API 參數 → Interceptor
- 想看特定地址的暫存器/分支走向 → Stalker
- 想 patch 但不動原始 byte → Stalker transform
- 無反調試 → 隨便，INT3 最直觀

---

## Capstone

> 在 Frida 或 Python 裡做即時反組譯。用來確認地址附近是什麼指令。

### Python 用法

```python
from capstone import Cs, CS_ARCH_X86, CS_MODE_32  # 或 CS_MODE_64

cs = Cs(CS_ARCH_X86, CS_MODE_32)
cs.detail = True   # 啟用指令細節（operand、group 等）

# 從 binary 讀取 bytes
with open("target.exe", "rb") as f:
    f.seek(file_offset)
    code = f.read(64)

# 反組譯
for insn in cs.disasm(code, runtime_va):
    print(f"0x{insn.address:08x}  {insn.mnemonic:8s} {insn.op_str}")
```

### 場景 1：找目標地址附近的條件跳轉

```python
from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_GRP_JUMP

cs = Cs(CS_ARCH_X86, CS_MODE_32)
cs.detail = True

# code = 從 binary 或 memory dump 讀取的 bytes
# base_addr = 這段 code 的起始 VA

for insn in cs.disasm(code, base_addr):
    # 找條件跳轉
    if insn.group(CS_GRP_JUMP) and insn.mnemonic != "jmp":
        print(f"[COND JUMP] 0x{insn.address:08x}  {insn.mnemonic} {insn.op_str}")
        print(f"  bytes: {insn.bytes.hex()}")
        print(f"  offset in file: 計算 file_offset...")
```

### 場景 2：遞迴追蹤 CALL 鏈

```python
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

cs = Cs(CS_ARCH_X86, CS_MODE_32)

def find_calls(code, base_addr):
    """找出這段 code 裡的所有 CALL 目標"""
    calls = []
    for insn in cs.disasm(code, base_addr):
        if insn.mnemonic == "call":
            # 直接 call（非 register call）
            try:
                target = int(insn.op_str, 16)
                calls.append((insn.address, target))
            except ValueError:
                # register call (call eax 等)，記錄但無法靜態解析
                calls.append((insn.address, f"REG:{insn.op_str}"))
    return calls

# 用法
calls = find_calls(code_bytes, 0x00401000)
for src, dst in calls:
    print(f"  0x{src:08x} -> {dst if isinstance(dst, str) else f'0x{dst:08x}'}")
```

### 場景 3：在 Frida 裡用 Capstone（透過 Instruction 物件）

```javascript
// Frida 內建 Instruction.parse()，不需要額外安裝 Capstone
var addr = ptr("0x00401234");
for (var i = 0; i < 20; i++) {
    var insn = Instruction.parse(addr);
    console.log(addr + "  " + insn.mnemonic + " " + insn.opStr);
    addr = insn.next;  // 下一條指令的地址
}
```

### 安裝

```bash
pip install capstone
# 或
pip install capstone-engine
```

---

## Unicorn Engine

> CPU 模擬器。載入 binary code 到虛擬記憶體，逐步執行，完全控制 register 和 memory。
> 用途：模擬保護殼的解密邏輯、在沒有 Windows 環境時跑小段 x86 code。

### Python 用法

```python
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import *

# 建立模擬器
mu = Uc(UC_ARCH_X86, UC_MODE_32)

# 映射記憶體（必須 page-aligned，0x1000 的倍數）
CODE_ADDR = 0x00400000
STACK_ADDR = 0x00100000
mu.mem_map(CODE_ADDR, 0x10000)    # code
mu.mem_map(STACK_ADDR, 0x10000)   # stack

# 寫入 code
code = b"\x33\xc0\x40\xc3"  # xor eax,eax; inc eax; ret
mu.mem_write(CODE_ADDR, code)

# 設定暫存器
mu.reg_write(UC_X86_REG_ESP, STACK_ADDR + 0x8000)
mu.reg_write(UC_X86_REG_EBP, STACK_ADDR + 0x8000)

# 執行
mu.emu_start(CODE_ADDR, CODE_ADDR + len(code))

# 讀取結果
eax = mu.reg_read(UC_X86_REG_EAX)
print(f"EAX = {eax}")  # → 1
```

### 場景 1：模擬解密函數

```python
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import *

mu = Uc(UC_ARCH_X86, UC_MODE_32)

# 從 binary dump 載入 sections
# 通常需要：.text, .rdata, .data（至少包含目標函數和它引用的資料）
with open("target.exe", "rb") as f:
    # 假設已知 section 的 file offset 和 VA
    sections = [
        # (file_offset, va, size)
        (0x1000, 0x00401000, 0x5000),   # .text
        (0x6000, 0x00406000, 0x2000),   # .rdata
    ]
    for foff, va, size in sections:
        mu.mem_map(va & ~0xFFF, (size + 0xFFF) & ~0xFFF)
        f.seek(foff)
        mu.mem_write(va, f.read(size))

# Stack
mu.mem_map(0x00100000, 0x10000)
mu.reg_write(UC_X86_REG_ESP, 0x00108000)

# 設定函數參數（cdecl：推到 stack）
# decrypt(buffer, length, key)
BUF_ADDR = 0x00200000
mu.mem_map(BUF_ADDR, 0x1000)
mu.mem_write(BUF_ADDR, encrypted_data)

esp = 0x00108000
mu.mem_write(esp + 4, BUF_ADDR.to_bytes(4, 'little'))    # arg1: buffer
mu.mem_write(esp + 8, len(encrypted_data).to_bytes(4, 'little'))  # arg2: length
mu.mem_write(esp + 12, key_value.to_bytes(4, 'little'))   # arg3: key

# 執行
FUNC_ADDR = 0x00401234
mu.emu_start(FUNC_ADDR, 0x00401234 + 0x100, timeout=5000000)  # 5 秒 timeout

# 讀取解密結果
result = mu.mem_read(BUF_ADDR, len(encrypted_data))
print(result)
```

### 場景 2：逐步執行 + trace

```python
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import *
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

cs = Cs(CS_ARCH_X86, CS_MODE_32)

def hook_code(mu, address, size, user_data):
    """每條指令執行前觸發"""
    code = mu.mem_read(address, size)
    for insn in cs.disasm(bytes(code), address):
        eax = mu.reg_read(UC_X86_REG_EAX)
        print(f"0x{address:08x}  {insn.mnemonic:8s} {insn.op_str:20s} EAX=0x{eax:08x}")

mu = Uc(UC_ARCH_X86, UC_MODE_32)
# ... 載入 code 和設定 ...

# 加 hook（可限制範圍）
mu.hook_add(UC_HOOK_CODE, hook_code, begin=0x00401000, end=0x00402000)

mu.emu_start(start_addr, end_addr)
```

### 場景 3：從記憶體 dump 載入

> 如果你有 Frida dump 出來的記憶體區域，可以直接載入 Unicorn 模擬。

```python
import json
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32

mu = Uc(UC_ARCH_X86, UC_MODE_32)

# 假設 Frida dump 出來的格式是 [{base, size, file}, ...]
dump_regions = json.load(open("dump_regions.json"))
for region in dump_regions:
    base = region["base"]
    size = region["size"]
    aligned_base = base & ~0xFFF
    aligned_size = ((base + size + 0xFFF) & ~0xFFF) - aligned_base
    try:
        mu.mem_map(aligned_base, aligned_size)
    except Exception:
        pass  # 可能已映射（regions 重疊）
    data = open(region["file"], "rb").read()
    mu.mem_write(base, data)
```

### Unicorn 常見問題

| 問題 | 對策 |
|------|------|
| `UC_ERR_FETCH_UNMAPPED` | Code 訪問了未映射的記憶體。加 `mu.mem_map()` |
| `UC_ERR_READ_UNMAPPED` / `WRITE` | 資料讀寫未映射區域。檢查指標和 stack |
| 模擬 Windows API call | Unicorn 不模擬 OS。需要自己 hook API 地址回傳假值 |
| 超時 | 可能進了無限迴圈。加 instruction count 限制 |
| VMP bytecode 模擬 | 需要載入所有 .svmp section + 正確的初始 register state |

### Hook Windows API（Unicorn 不提供 OS 模擬）

```python
# 在 API 的 IAT 地址設 hook，模擬返回值
def hook_api(mu, address, size, user_data):
    if address == 0x004B0020:  # recv 的 IAT 地址（替換成你的實際值）
        # 模擬 recv 返回 100 bytes
        mu.reg_write(UC_X86_REG_EAX, 100)
        # 跳過 call，直接到下一條指令
        ret_addr = mu.mem_read(mu.reg_read(UC_X86_REG_ESP), 4)
        mu.reg_write(UC_X86_REG_EIP, int.from_bytes(ret_addr, 'little'))
        mu.reg_write(UC_X86_REG_ESP, mu.reg_read(UC_X86_REG_ESP) + 4)

mu.hook_add(UC_HOOK_CODE, hook_api, begin=0x004B0000, end=0x004B0040)
```

### 安裝

```bash
pip install unicorn
```

---

## 三工具組合使用場景

### 流程：Frida Stalker 找決策點 → Capstone 確認指令 → Unicorn 驗證 patch

```
1. Frida Stalker follow 目標線程
   → 在可疑區域加 callout，記錄條件跳轉的 ZF 值
   → 找到「成功走 A 分支、失敗走 B 分支」的地址

2. Capstone 反組譯該地址附近
   → 確認是 JNE/JE + 跳轉目標
   → 計算需要 patch 的 bytes

3.（可選）Unicorn 模擬驗證
   → 載入函數 code，設定 patch 後的 bytes
   → 確認 patch 後 control flow 走向正確路徑

4. Frida Stalker transform 做 runtime patch
   → 不改原始 byte，在 slab 裡替換指令
   → 驗證成功 → 固化成 binary patch
```

### 範例：完整的「找決策點 + patch」腳本

```javascript
// Step 1: Stalker 找條件跳轉
var target = Process.findModuleByName("target.exe");
var suspect = target.base.add(0x1234);  // 從 backtrace 得到的可疑區域
var found = {};

Stalker.follow(threadId, {
    transform: function(iterator) {
        var insn;
        while ((insn = iterator.next()) !== null) {
            // 監看可疑區域 ±0x100 範圍的條件跳轉
            var offset = insn.address.sub(suspect).toInt32();
            if (Math.abs(offset) < 0x100) {
                if (insn.mnemonic === "je" || insn.mnemonic === "jne" ||
                    insn.mnemonic === "jz" || insn.mnemonic === "jnz") {
                    var a = insn.address;
                    var m = insn.mnemonic;
                    iterator.putCallout(function(ctx) {
                        var zf = (ctx.eflags >>> 6) & 1;
                        console.log("[BRANCH] " + a + " " + m + " ZF=" + zf +
                                    " EAX=" + ctx.eax);
                        found[a.toString()] = { mnemonic: m, zf: zf };
                    });
                }
            }
            iterator.keep();
        }
    }
});

// Step 2: 使用者觸發認證（成功或失敗）
// Step 3: 分析 found{} 裡哪個分支決定了成功/失敗
// Step 4: 用 Stalker transform 的 iterator.putNop() 做 runtime patch 驗證
```
