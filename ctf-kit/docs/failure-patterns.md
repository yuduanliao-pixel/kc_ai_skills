# 失敗模式資料庫

> 遇到瓶頸時查這裡，避免在已知死路上浪費時間。
> 每次遇到新的失敗模式就加進來。
>
> **每次提出新方案前，掃一遍這份文件。** 如果新方案跟以下任何模式有相似邏輯，必須先回答：「這次跟上次的差異在哪裡？為什麼這次會成功？」

---

## 一、泛用失敗模式（適用所有 Windows 驗證繞過）

### 1.1 在加密層死磕

**症狀：** 花大量時間分析加密演算法、搜尋 key、嘗試解密，但完全不需要理解加密就能 bypass。

**正確做法：** 加密是手段不是目的。如果目標是 bypass 認證，找到「通過/不通過」的**判斷點**然後改掉就好。先嘗試決策層攻擊，打不通再考慮加密層。

### 1.2 提方案時不檢查先決條件

**症狀：** 提出方案，但先決條件（如「需要有效 key」「需要 server 在線」）跟當前目標矛盾。

**正確做法：** 提方案前問：(1) 先決條件都滿足嗎？(2) 這達成了目標嗎？

### 1.3 一個方法反覆重試

**症狀：** 方法 A 失敗 → 微調 A 再試 → 再微調... 花 2 小時在同一條死路上。

**正確做法：** 一個方法**最多試 3 個變體**。都失敗就記錄原因，切換到完全不同的路線。

### 1.4 沒搜尋就下結論

**症狀：** 「這個殼不能破」「這個加密很強」——但沒上網查過。

**正確做法：** 任何關於保護殼、加密、驗證框架的假設，先搜尋：
- 中文：吾爱破解、看雪、52pojie
- 英文：GitHub、Stack Overflow、r/ReverseEngineering

### 1.5 NUL 掉本地錯誤字串無效

**症狀：** 把 binary 裡的「錯誤」字串 NUL 掉，程式還是顯示錯誤訊息。

**原因：** 網路驗證程式的錯誤訊息幾乎都是 server 回傳的，不是本地字串。

### 1.6 Patch binary 後 crash

**可能原因：** 改到了 PE header / 破壞了 code integrity check / file offset 算錯。

**預防：** 先用 Frida runtime patch 驗證正確性，確認有效再固化到 binary。

### 1.7 angr / symbolic execution 跑不出結果

**判斷時機：** 10 分鐘沒結果就放棄。angr 只適合無殼、邏輯簡單的 flag checker。加殼/重度混淆的 control flow 讓 symbolic execution 狀態爆炸。

---

## 二、保護殼反調試（通用 + 殼特定）

### 2.1 軟體斷點被偵測（INT3）`[通用]`

**症狀：** 設 breakpoint 後程式彈錯誤或退出。

**原因：** 保護殼掃描 code section 找 0xCC 或做 CRC 校驗。

**正確做法：** 硬體斷點（DR0-DR3）或 Frida Interceptor（不修改 code byte）。

### 2.2 硬體斷點觸發後無限迴圈 `[通用]`

**症狀：** 硬體斷點觸發一次後不斷重複觸發。

**原因：** DR6 暫存器未清除。處理後必須寫 `context.Dr6 = 0`。

### 2.3 Hook ExitProcess/NtTerminateProcess 無效 `[VMP/Themida]`

**症狀：** hook 了退出函數，程式還是能自己結束。

**原因：** 保護殼用 direct syscall 繞過 user-mode hook。

**正確做法：** 不攔截退出，攔截退出的**原因**。或用 TitanHide kernel driver。

### 2.4 x32dbg + ScyllaHide 被偵測 `[VMP 3.x]`

**原因：** VMP 3.x 用 direct syscall，ScyllaHide user-mode hook 無效。

**正確做法：** TitanHide 或 Frida。

### 2.5 認證過程中 attach 工具 `[通用]`

**症狀：** 認證過程中 attach Frida/debugger，程式偵測到退出。

**正確做法：** 在程式啟動後、使用者操作前 attach。

### 2.6 顯示隱藏視窗觸發退出 `[VMP]`

**原因：** 保護殼監控視窗狀態，外部 ShowWindow = 干預 = 退出。

### 2.7 硬體斷點被 SEH 清除 `[VMP]`

**原因：** 通過 exception handler 清除 DR 暫存器。用 Frida 替代。

更多 VMP 特有問題見 [docs/vmp-guide.md](vmp-guide.md)。

---

## 三、加密與協議層

### 3.1 Replay 成功封包到新 session `[通用]`

**症狀：** 重播舊 session 的封包，程式報錯。

**判斷方式：** 比較兩次 session 同一 round 封包——無共同 byte → per-session key。

**正確做法：** 不攻擊加密層，攻擊決策層。

### 3.2 偽造 recv 回應 `[通用]`

**原因：** 程式解密假資料得到亂碼，完整性校驗失敗。

### 3.3 假伺服器回覆正確大小但錯誤內容 `[通用]`

**原因：** 不只看大小，也看內容。加密回應有完整性校驗。

### 3.4 單 byte XOR / 已知明文攻擊 `[通用]`

**判斷方式：** 多組同一 round 封包，body 長度同但內容不同 → per-session key + 串流加密。

### 3.5 S-box 搜尋找加密 key `[VMP]`

**原因：** VMP 把 S-box 放在 virtual registers 裡，不暴露到一般記憶體。

---

## 四、記憶體分析

### 4.1 全記憶體 dump 比對 `[VMP]`

**原因：** VMP 虛擬機器狀態隨機化，diff 全是噪音。

**正確做法：** 比較 API call 序列（differential trace）。

### 4.2 跨環境記憶體注入 `[通用]`

**原因：** 記憶體中的指標是特定環境的虛擬地址，跨機器/跨 session 不匹配。

### 4.3 改 result code 期望自動解密 `[VMP]`

**原因：** Section 解密有多重 state check，改一個 flag 不夠。

---

## 五、工具使用陷阱

### 5.1 Frida Stalker follow 全線程 `[通用]`

**症狀：** 程式 hang → 網路超時。Stalker overhead 太大。

**正確做法：** 用 Interceptor 精準 hook 目標 API，不做全面 trace。

### 5.2 EnumWindows 找不到視窗 `[通用]`

**原因：** 管理員權限程式的視窗，一般權限看不到。用 `tasklist` 找 PID。

### 5.3 Shell redirect 覆蓋輸出 `[通用]`

**正確做法：** 腳本內部用 Python `open()` / Frida `File` API 寫檔。

---

## 六、環境與操作

### 6.1 Host 抓包導致系統 hang `[通用]`

**後果：** 遠端連入無法操作，需系統還原，所有環境設定回滾。

**正確做法：** 網路抓包在 VM 裡做。Host 最多用 `netstat`。

### 6.2 系統還原後環境消失 `[通用]`

**正確做法：** 記錄環境設定步驟。或用 VM snapshot。
