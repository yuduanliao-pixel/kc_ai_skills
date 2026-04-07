# VMProtect 專區

> 本文件收錄 VMProtect 保護殼特有的行為、陷阱和對策。
> 從 67+ 次實戰失敗中歸納。其他保護殼不一定適用。

---

## VMP 核心特性

| 特性 | 影響 |
|------|------|
| Code virtualization | 原始 x86 指令被轉成 VMP bytecode，無法直接反組譯 |
| Direct syscall | 用 sysenter/syscall 繞過 user-mode hook（ExitProcess、NtQueryInformationProcess 等）|
| Code integrity check | 偵測 .svmp section 的任何修改（INT3、patch）|
| Timestamp check | 在加密路徑上檢查經過時間，偵測 single-step |
| SEH-based DR clearing | 通過 exception handler 清除硬體斷點暫存器 |
| VM state randomization | 每次執行虛擬暫存器和堆疊佈局不同 |
| Section encryption | 部分 section 只在認證成功後才解密 |

---

## 對 VMP 程式的攻擊原則

### 1. 不要脫殼

完整反虛擬化（devirtualization）對 VMP 3.x 不實際。在 API 邊界攻擊就夠了。

### 2. 不要在 VMP code 裡設斷點

VMP 對自己的 bytecode section 做 integrity check。任何 INT3 或 byte 修改都會被偵測。
**只在 system DLL 的 API entry point 設斷點**（那些 VMP 不校驗）。

### 3. 不要做記憶體 dump 比較

VMP 的虛擬機器狀態每次執行都不同。成功/失敗的 diff 全是 VM 內部隨機狀態的噪音。
**改用 API call 序列比較**（differential trace）。

### 4. 不要在 recv 路徑 single-step

VMP 在 recv → 解密 → 判斷的路徑上有 timestamp check。Single-step 太慢會觸發超時。
**凍結計時 API 或直接 hook 決策結果端**。

### 5. User-mode hook 擋不住 VMP 的反調試

VMP 3.x 用 direct syscall 呼叫 NtTerminateProcess、NtQueryInformationProcess。
ScyllaHide 等 user-mode 方案無效。需要 **TitanHide kernel driver** 或 **Frida**（不觸發 debug flag）。

---

## VMP 反調試繞過方案

| 問題 | 方案 | 備註 |
|------|------|------|
| INT3 被偵測 | 硬體斷點（DR0-DR3）或 Frida Interceptor | 不修改 code byte |
| 硬體斷點被 SEH 清除 | Frida（不用 DR）| 或在 SEH handler 裡重設 DR |
| 硬體斷點無限迴圈 | 處理後清 DR6（`context.Dr6 = 0`）| |
| ScyllaHide 被繞過 | TitanHide kernel driver | kernel 層攔截 syscall |
| Frida 被偵測 | Gadget mode（DLL sideload）| 改名成 version.dll |
| Timestamp 偵測 | 凍結 GetTickCount / QPC | 見 bypass-auth.md 腳本 |
| ExitProcess hook 無效 | 不攔截退出，攔截退出的**原因** | hook 決策點而非結果 |
| 顯示隱藏視窗觸發退出 | 不要 ShowWindow | VMP 監控視窗狀態 |

---

## VMP 程式上已證實無效的方法

> 以下方法在 VMP 3.x 上反覆測試失敗。不要嘗試。

| # | 方法 | 死因 |
|---|------|------|
| 1 | 全記憶體 dump 比對 | VM 狀態隨機化，82 個 diff block 全是噪音 |
| 2 | recv 後 single-step | 封包 timestamp 偵測 |
| 3 | Hook ExitProcess/NtTerminateProcess | Direct syscall 繞過 |
| 4 | Replay/偽造加密封包 | Session-bound key |
| 5 | VMP context transplant | 跨 session 指標不匹配 |
| 6 | NUL 掉本地錯誤字串 | 訊息來自 server |
| 7 | Stalker follow 全線程 | Overhead 太大，程式 hang |
| 8 | Stalker 只 follow 主線程 | 認證可能不在主線程 |
| 9 | INT3 在 .svmp section | Code integrity check |
| 10 | 顯示隱藏視窗 | 觸發退出機制 |
| 11 | .svmp section 靜態 patch | Bytecode 加密/隨機化 |
| 12 | S-box 搜尋 | S-box 在 virtual registers 裡 |
| 13 | VMP State Clone / Live Patch | 寫入執行中 bytecode → crash |
| 14 | 跨環境記憶體注入 | 指標跨機器不匹配 |
| 15 | 改 result code 期望自動解密 | Section 解密有多重 state check |
| 16 | NtTerminateProcess 時掃記憶體 | 記憶體已釋放，太晚 |
| 17 | angr / symbolic execution | VMP control flow 導致狀態爆炸 |

---

## VMP 程式的推薦攻擊流程

```
Phase 1（靜態）
  識別 .svmp section → 確認 VMP 版本
  分析 import（在 VMP 外的 API call）
  搜尋字串（驗證結果字串用 GBK hex 搜尋）
  上網搜尋 VMP 版本 + 破解案例

Phase 2（動態）
  Frida hook Win32 API（不 hook VMP 內部）
  重點：CreateWindowExA backtrace
  如果被偵測：Gadget mode → TitanHide
  凍結計時 API（如需要）

Phase 3（繞過）
  從 backtrace 找決策點 → patch 條件跳轉
  如果是網路驗證：不攻擊加密層，攻擊決策層
  Runtime patch 驗證 → Binary patch 固化
```

---

## Frida 計時凍結腳本

```javascript
var tick0 = null;
Interceptor.attach(Module.findExportByName("kernel32.dll", "GetTickCount"), {
    onLeave: function(retval) {
        if (!tick0) tick0 = retval.toInt32();
        retval.replace(ptr(tick0));
    }
});
// 同理 hook GetTickCount64, QueryPerformanceCounter
// 完整腳本見 docs/bypass-auth.md
```

---

## 延伸閱讀

- [failure-patterns.md](failure-patterns.md) — 第二節「保護殼反調試」和第三節「加密協議層」有更多 VMP 相關模式
- [bypass-auth.md](bypass-auth.md) — Frida 反偵測腳本、Gadget mode 設定、Differential trace
