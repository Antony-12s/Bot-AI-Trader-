# DECISIONS

บันทึกสั้นๆ ว่าทำไมเลือกทางนี้

## 2026-10-03

- ใช้ Python + แพ็กเกจ MetaTrader5 ทางการ
  - เหตุผล: ตลาดที่เลือกคือ Forex/ทอง และเครื่องเป็น Windows มี Python 3.12 อยู่แล้ว
  - ข้อแลก: ต้องเปิดโปรแกรม MT5 ค้างไว้ และรันได้เฉพาะ Windows
- เกาะ terminal ที่ login ไว้แล้ว ไม่เก็บรหัสผ่านโบรกในไฟล์
- Telegram ใช้ urllib ของ stdlib ไม่เพิ่ม library
- มี 3 โหมด: dry (ค่าเริ่มต้น) -> demo -> live
  - demo จะเช็คว่าเป็นบัญชี demo จริงทุกครั้งก่อนส่งออเดอร์
  - live ต้องแก้ .env ด้วยมือเท่านั้น
- ขีดจำกัดขาดทุนต่อวันนับทุกดีลในบัญชี รวมที่เทรดมือด้วย
  - เหตุผล: ไม่แน่ใจว่าดีลที่ปิดด้วย SL/TP จะติด magic number ของ bot ทุกโบรกหรือไม่
  - นับทั้งบัญชีจึงปลอดภัยกว่า และเป้าหมายคือกันบัญชีพัง
- วันใหม่ตัดที่เที่ยงคืนตามเวลา server ของโบรก ไม่ใช่เวลาเครื่อง
- ไม่เทรดแท่งที่ปิดก่อน bot เริ่มรัน กันเข้าไม้ช้า
- ออเดอร์ fail แล้วไม่ยิงซ้ำ รอสัญญาณรอบถัดไป
- กลยุทธ์ MA cross ใน strategy.py เป็นแค่ตัวอย่างทดสอบระบบ ยังไม่เคย backtest
- อินดิเคเตอร์ (sma, ema, rsi, macd, bollinger) เขียนเองด้วย Python ล้วนใน indicators.py
  - เหตุผล: ตัวละไม่กี่บรรทัด ส่วน TA-Lib ต้อง build C บน Windows ซึ่งยุ่งเกินเหตุ
  - ข้อแลก: ค่า MACD signal อาจต่างจากกราฟ MT5 เล็กน้อย เพราะ MT5 ใช้วิธี smooth คนละแบบ
  - ยังไม่มี ATR / Stochastic เพราะต้องใช้ high/low ตอนนี้ bot ส่งให้กลยุทธ์แค่ราคาปิด
- ดู repo HKUDS/AI-Trader แล้ว ไม่ได้นำมาใช้
  - เหตุผล: เป็นเว็บแพลตฟอร์มชุมชน/paper trading (ai4trade.ai) ไม่มีโค้ดอินดิเคเตอร์ กลยุทธ์ backtest หรือ MT5
- เพิ่มสมอง AI (BRAIN=ai) ให้ Claude ตัดสินใจ buy/sell/hold ผ่าน API ของผู้ใช้เอง
  - เหตุผล: ผู้ใช้ต้องการให้ AI เป็นคนตัดสินใจเทรดแทน ไม่ใช่เขียนกฎเอง
  - AI เลือกได้แค่ทิศทาง ส่วน lot, SL/TP, ขีดจำกัดขาดทุน ล็อกไว้ในโค้ด AI แก้ไม่ได้
  - ทุกความผิดพลาดของ AI (key ผิด, เน็ตหลุด, ตอบแปลก) = hold ไม่เทรด
  - เช็คเงื่อนไขห้ามเทรดก่อนเรียก AI แท่งที่ถูกบล็อกจึงไม่เสียค่า API
  - ใช้โมเดล claude-opus-5-5 effort medium เป็นค่าตั้งต้น แก้ได้ใน ai_strategy.py
  - เปิด fallback ของ Anthropic ไว้: ถ้าโมเดลหลักปฏิเสธคำขอ ระบบจะลองโมเดลสำรองให้เอง
  - ข้อแลก: มีค่าใช้จ่ายต่อแท่งเทียน, ผลไม่ซ้ำเดิมทุกครั้ง, backtest ยาก, ไม่มีหลักฐานว่าทำกำไรได้
  - ค่าเริ่มต้นยังเป็น BRAIN=rules และ MODE=dry
- ยังไม่ทำ: backtest, /closeall, trailing stop, หลาย symbol, lot ตาม % ทุน, รันบน VPS

## 2026-10-03 (ค่ำ) ให้ AI เทรดเองทั้งหมดและเรียนรู้จากไม้ของตัวเอง

- เป้าหมายใหม่: Claude เป็นคนเทรดคนเดียว ไม่มีมือคนยุ่ง และต้อง "ฝึก" ก่อนเพื่อสะสมประสบการณ์
- Claude เทรนน้ำหนักโมเดลผ่าน API ไม่ได้ จึงเรียนรู้ผ่านสมุดบันทึก (journal.py, SQLite ใน stdlib)
  - ทุกการตัดสินใจและทุกไม้ลง journal.db พร้อมผลลัพธ์จริง (โดน SL/TP, กำไร)
  - ทุกครั้งที่ตัดสินใจ AI ได้อ่านสถิติของตัวเอง, playbook, และบทเรียน 5 ไม้ล่าสุด
  - ปิดไม้แล้ว AI เขียนบทเรียน 1 ข้อ (reflect, effort low เพราะสั้น) ทุก 10 ไม้เขียน playbook ใหม่ (distill)
  - journal ใบเดียวใช้ร่วมกันทั้ง replay, dry, demo, live ประสบการณ์เลยติดตัวไปทุกโหมด
- MODE=dry กลายเป็น paper trading จริง: เปิดไม้เสมือนแล้วเช็ค high/low ของทุกแท่งว่าโดน SL/TP ไหม (fills.py)
  - เหตุผล: dry แบบเดิมไม่มีผลลัพธ์ให้เรียนรู้เลย และฝึกบน paper ปลอดภัยกว่า demo
  - ขาดทุนบน paper นับเข้า MAX_DAILY_LOSS ด้วย ไม่งั้นช่วงฝึกจะไม่เคารพกฎ
  - จำลองแบบระวังตัว: แท่งเดียวโดนทั้ง SL และ TP ถือว่าโดน SL, ราคา gap ถือว่าออกที่ SL พอดี (ของจริงแย่กว่า)
- demo/live: ตามไม้ที่ปิดจาก deal ใน MT5 (settle_mt5) ใช้ position id กำไรรวม commission/swap
- replay.py ฝึกเร็วจาก CSV ประวัติ (export_history.py ดึงจาก MT5 บน Windows)
  - เหตุผลที่ใช้ CSV: MetaTrader5 ลงได้แค่ Windows ส่วน replay ควรรันที่ไหนก็ได้
  - เปิดไม้ที่ราคาเปิดแท่งถัดไป + spread ของแท่งนั้น เหมือนบอทจริงที่ยิงออเดอร์หลังแท่งปิด
  - ข้อควรระวัง: โมเดลอาจรู้จักช่วงเวลาที่ replay แล้ว prompt จึงไม่ใส่วันที่ แต่ผล replay ก็ยังเชื่อถือได้น้อยกว่า paper/demo
- งบ AI (AI_BUDGET_USD): ต่อวันใน bot.py (ตัดวันตามเวลา server) ต่อ run ใน replay.py ถึงงบแล้ว hold
  - ราคาต่อ token ของ Opus 5.5 ฝังในโค้ด (ai_strategy.PRICES) เป็นค่าประมาณ ถ้า fallback เสิร์ฟโมเดลอื่นบิลจริงต่างเล็กน้อย
  - prompt สั้น (ไม่ถึงพันกว่า token) จึงไม่ใส่ prompt caching เพราะไม่ติด cache อยู่ดี
- แก้บั๊ก: แท่งที่ปิดนานเกิน 2 แท่ง (เช่นแท่งสุดท้ายวันศุกร์ที่เพิ่งเห็นเช้าวันจันทร์) ไม่เทรด กัน gap
- replay หยุดเองถ้า AI ล้มเหลวติดกัน 3 ครั้ง (key ผิด, เน็ตล่ม) ไม่วิ่งเปล่าหลายพันแท่ง
- MODE=live ยังต้องสวิตช์ด้วยมือเหมือนเดิม: บอทเทรดเองทุกอย่าง แต่สิทธิ์ใช้เงินจริงยังเป็นของเจ้าของ
- แยก config.py / risk.py ออกจาก bot.py ให้ replay และ test ใช้ได้โดยไม่ต้องมี MetaTrader5
- start.bat / train.bat ให้กดจิ้มเดียวจบบน Windows: สร้าง .venv, ลง library, รอบแรกเปิด .env ให้กรอก
  - เลือก .bat ไม่ใช่ GUI เพราะเครื่องเป้าหมายคือ Windows ที่เปิด MT5 อยู่แล้ว และไม่เพิ่ม dependency
  - บันทึกเป็น CRLF (.gitattributes) เพราะ cmd อ่าน label หลัง goto พลาดได้ถ้าไฟล์เป็น LF
- ยังไม่ทำ: trailing stop, /closeall, หลาย symbol, lot ตาม % ทุน, VPS, กรองแท่งนิ่งๆ ก่อนเรียก AI เพื่อประหยัดงบ

## 2026-10-04 กลยุทธ์: กฎหา setup, AI ตัดสิน, stop ตาม ATR

- สมองเดิมมีแค่ MA cross ตัวอย่าง กับ AI ที่เดาจากราคา 30 ตัว ไม่มีกลยุทธ์จริง
- strategies.py รวมกฎ 4 แบบ (ma_cross, trend_pullback, bollinger_breakout, rsi_reversion) เลือกด้วย STRATEGY
  - ทุกกฎรับแท่งเต็ม (OHLC) ไม่ใช่แค่ราคาปิด เพื่อให้ใช้ high/low และ ATR ได้
  - ค่าตั้งต้น trend_pullback เพราะตามเทรนด์เสียหายน้อยกว่าเวลาผิด ไม่ใช่เพราะพิสูจน์แล้วว่าดี
  - ไม่มีตัวไหนผ่าน backtest บนข้อมูลจริงเลย ต้องรัน replay --compare บนข้อมูลของผู้ใช้ก่อน
- BRAIN=hybrid: กฎเสนอ setup, Claude เลือกเอา/ไม่เอา
  - เหตุผล: LLM ไม่เก่งทายทิศจากตัวเลข แต่เก่งกรองด้วยบริบทและจำบทเรียน ให้มันทำสิ่งที่เก่ง
  - โค้ดบังคับว่า AI เทรดทิศที่กฎไม่เสนอไม่ได้ (นับเป็น hold) เพื่อให้วินัยคงที่และบทเรียนไม่มั่ว
  - แท่งที่ไม่มี setup ไม่เรียก API เลย ประหยัดงบไปมาก
- SL/TP ตาม ATR (SL_ATR 1.5, TP_ATR 3.0) แทน points ตายตัว เพราะความผันผวนของทองเปลี่ยนวันต่อวัน
  - stop ไม่แคบกว่า 2 spread และไม่แคบกว่า trade_stops_level ของโบรก
  - ตั้ง 0 กลับไปใช้ SL_POINTS/TP_POINTS ได้ และใช้ points เมื่อยังไม่มี ATR
- ข้อมูลที่ AI เห็นเพิ่ม: OHLC 10 แท่งล่าสุด, ATR, EMA20 กับเทรนด์ของ timeframe ใหญ่ขึ้น 4 เท่า (resample จากแท่งเดิม), วันและชั่วโมงตามเวลา server (ไม่มีวันที่ กัน look-ahead)
  - ต้องการ 300 แท่งเพื่อให้ EMA20 ของ timeframe ใหญ่เซ็ตตัว
- journal เพิ่มคอลัมน์ brain: ประสบการณ์ที่ป้อน AI นับเฉพาะไม้ที่ ai/hybrid ตัดสิน ไม้ของกฎล้วนไม่ปน
  - replay --compare ใช้ journal ชั่วคราวในหน่วยความจำ ไม่เขียนลง journal.db
- ข้อความล้มเหลวจาก API ขึ้นต้น "AI error" เพื่อให้ replay แยก "หยุดเพราะ API พัง" กับ "AI เลือก hold" ได้

## 2026-10-04 (เช้า) รันบน VPS โดยไม่มีคนเฝ้า

- เลือก Windows VPS แทน web/.exe เพราะ MetaTrader5 package รันได้แค่ Windows เครื่องเดียวกับ MT5 web บน cloud ทั่วไปทำไม่ได้ และ .exe ไม่ได้อะไรเพิ่มเมื่อเครื่องมี Python อยู่แล้ว
- run_forever.bat เป็น watchdog ธรรมดา รีสตาร์ททุก 30 วินาทีหลัง crash ไม่ใช้ service/NSSM เพื่อให้เห็นหน้าต่าง log และไม่เพิ่มเครื่องมือ
- /stop เขียนไฟล์ stop.flag ให้ watchdog รู้ว่าเจ้าของสั่งปิด ไม่ใช่ crash
- บอทเช็ค terminal_info ทุกรอบ ถ้า MT5 หาย แจ้ง Telegram ครั้งเดียวแล้ว initialize ใหม่เรื่อยๆ ไม่ crash ไม่ spam
- Task Scheduler แบบ onlogon + auto-logon ของ Windows: ง่ายสุดที่รีบูตแล้วกลับมาเองได้ทั้ง MT5 และบอท
- wizard.py แทนการเปิด Notepad แก้ .env: ถาม 3-4 ข้อ ที่เหลือ (symbol ที่มี, contract size, digits, filling mode จาก filling_mode flags, lot ต่ำสุด) อ่านจาก MT5
  - เหตุผล: ผู้ใช้บอกว่าใช้งานยาก และค่าที่ต้องเดา (ชื่อ symbol ของ XM เป็น GOLD, filling mode) คือจุดพังบ่อยสุด
  - เช็ค API key ด้วย models.list ซึ่งฟรี ปฏิเสธก็รู้ทันที ไม่ต้องรอ replay ล้ม 3 ครั้ง
  - หา Telegram chat id เองจาก getUpdates หลังผู้ใช้ทักบอท ไม่ต้องไปอ่าน console
- ทีม: tests_support/MetaTrader5.py เป็นตัวแทน package สำหรับรัน test นอก Windows และ GitHub Actions รัน test ทุก push ทั้ง Linux (ตัวแทน) และ Windows (package จริง)
  - เหตุผล: มี collaborator ใน repo แล้ว ต้องมีอะไรบอกว่า push นั้นพังไหมโดยไม่ต้องรอใครรันเอง

## 2026-10-04 (สาย) รับงานทีม: MR-Intraday

- เพื่อนร่วมทีม (branch strategy/mr-intraday-v1) ส่งสเปค mean reversion บน M1 + engine backtest แยก ไม่แตะบอท
  - merge กับ branch หลักได้สะอาด ไม่มีไฟล์ชน test ทั้งสองชุดผ่านพร้อมกัน
- mr_zscore ใน strategies.py คือเวอร์ชันย่อของสเปคนั้นบน timeframe เดียว ให้บอทและ replay --compare ใช้ได้ทันที
  - เก็บ: z = (close - EMA20)/ATR14 ≥ 2, ADX14 ของ timeframe ใหญ่ 2 เท่า < 20, ATR percentile < 80, excursion ต้องเริ่มใหม่ (เคยอยู่ใน 1 ATR ของค่าเฉลี่ยไม่นานมานี้), trigger = แท่งล่าสุดปิดทะลุ high/low แท่งก่อน
  - ตัด: ข่าว (ไม่มีปฏิทิน), ชั่วโมง NY (ไม่รู้ timezone server), trigger M1 (บอทมี timeframe เดียว), time stop, lot ตาม % equity
  - exit ของสเปค (TP = EMA20, SL = 1 ATR) แทนด้วย SL_ATR=1.0 TP_ATR=2.0 ซึ่งใกล้เคียงเมื่อ z ≈ 2
  - ADX เพิ่มใน indicators.py แบบ Wilder ตามสเปค
  - สเปคเต็มยังอยู่ในสาย engine ของเพื่อน รอข้อมูล M1 กับปฏิทินข่าว

## 2026-10-06 แก้จาก review งานทีม

- งบ AI รายวัน (AI_BUDGET_USD) ใช้กับ hybrid ด้วย ไม่ใช่แค่ ai
  - เหตุผล: hybrid ก็เรียก Claude เดิมเช็คงบเฉพาะ BRAIN=ai ทำให้ replay แบบ hybrid ใน train.bat ไม่หยุดที่งบตามที่บอกไว้
  - แก้ที่ risk.block_reason จุดเดียว เพราะทั้ง bot.py และ replay.py เช็คผ่านฟังก์ชันนี้
- เพิ่ม tzdata ใน requirements.txt: Windows ไม่มีฐานข้อมูล time zone ทำให้ mr_intraday.py import ไม่ได้
- เพิ่ม tests/__init__.py ให้ unittest หาเทสต์ใน tests/ เจอ
  - เดิมบันทึกไว้ว่า "test ทั้งสองชุดผ่านพร้อมกัน" แต่ tests/test_mr.py ไม่เคยถูกรันทั้งในเครื่องและใน CI
- settings.bat เลิกใช้บล็อกวงเล็บรอบคำถาม y/N เพราะ ")" ใน "(y/N)" ปิดบล็อกก่อนเวลา ตอนไม่มี .env จึงไม่ทำอะไรเลย

## 2026-10-07 สมอง AI ย้ายจาก Opus 5.5 เป็น Sonnet 5.5

- ผู้ใช้เลือกเอง: ราคาครึ่งเดียว ($2/$10 ต่อล้าน token เทียบ $4/$20) งบ AI_BUDGET_USD เท่าเดิมจึงได้จำนวนครั้งเรียกราวสองเท่า
- ยังไม่มีหลักฐานว่า Opus ตัดสินใจเทรดดีกว่า Sonnet: งานของ AI ใน hybrid คือกรอง setup ไม่ใช่ทายทิศราคา
- คง effort medium ไว้ (Sonnet 5.5 ค่าเริ่มต้นคือ high) ยังไม่ได้วัดว่า low พอไหม
- ถ้าผลบน paper/demo แย่ลง เปลี่ยน MODEL และ PRICES ใน ai_strategy.py กลับได้ในสองบรรทัด
## 2026-10-06 ตัวติดตั้ง TradeBot-Setup.exe

- แบบเดียวกับ AutoBotSignal แต่เล็กกว่ามาก: PyInstaller ทำ TradeBot.exe แล้ว Inno Setup ห่อเป็น setup
  - TradeBot.exe ทำตัวแทน python.exe (launcher.py) ไฟล์ .bat เดิมใช้ต่อได้ แก้แค่ setup.bat
  - ติดตั้งแบบต่อ user ลง AppData ไม่ต้องใช้ admin เพราะบอทเขียน .env, journal.db ข้างตัวเอง
  - config.py ใช้โฟลเดอร์ของ exe ตอน frozen ไม่งั้นไฟล์ไปอยู่ใน _internal
  - ไม่ใช้ Tauri/MSI เพราะยังไม่มี UI ให้ห่อ

## 2026-10-06 หน้า UI (Dashboard + Settings)

- ได้แรงบันดาลใจจาก AutoBotSignal (dark + ส้ม, การ์ดตัวเลข, equity curve, ฟีด decisions)
  - ui.py ใช้ http.server ของ stdlib + ui.html ไฟล์เดียว กราฟวาด SVG เอง ไม่เพิ่ม library
  - ฟังแค่ 127.0.0.1 และเช็ก Host/Origin ทุก request กันเว็บอื่นแอบแก้ .env (เช่นสลับ MODE=live)
  - API key กับ Telegram token ไม่ส่งกลับไปหน้าเว็บ เว้นว่างแปลว่าใช้ค่าเดิม
  - Settings ตรวจด้วย load_config ตัวเดียวกับที่บอทใช้ตอนเริ่ม
  - ยังไม่ทำปุ่ม start/stop บอทจากหน้าเว็บ รอบหน้าค่อยว่ากัน

## 2026-10-06 ปุ่ม Start/Stop + หน้า History

- Stop ใช้ stop.flag ตัวเดิม: บอทเช็กไฟล์นี้ทุกรอบ (should_stop) แล้วปิดเองแบบเรียบร้อย
  - ไม่ kill process: journal ปิดครบ MT5 shutdown ครบ ไม้ที่เปิดอยู่ยังมี SL/TP
  - หยุดได้ทุกบอท ไม่ว่าเปิดจาก start.bat, run_forever หรือหน้าเว็บ และ watchdog ก็ไม่ restart
- บอทแตะ bot.alive ทุกรอบ หน้าเว็บใช้ดูว่ามีบอทรันอยู่ กัน Start ซ้อนสองตัวบนบัญชีเดียว
  - เกิน 120 วินาทีไม่แตะ ถือว่าตาย (ถ้า AI call นานกว่านั้นจะโชว์ผิดเป็น stopped)
- launcher.py ตั้ง line buffering เอง เพราะ exe จาก PyInstaller ไม่สน PYTHONUNBUFFERED ทำให้ bot.log ว่าง

## 2026-10-07 ทำให้เหมือน AutoBotSignal (เฉพาะส่วนที่ถูกกฎ)

- แกะ AutoBotSignal ดูแค่ระดับโครงสร้าง ไม่ถอดโค้ด: Tauri UI + engine Python (PyInstaller) + Chrome
  - ไม่ลอก: undetected chromedriver/stealth, จ้างแก้ captcha, API โบรกเกอร์ที่คนแกะเอง (ผิด ToS เสี่ยงบัญชีโดนแบน)
  - ไม่ลอกโค้ด/โลโก้/ชื่อ เอาแค่ไอเดีย UX
- หน้าต่างแอป: pywebview (WebView2 ที่มากับ Windows) แทน Tauri, installer ยังเล็ก (~23MB)
  - ไม่ทำ tray: บอทเป็น process แยก ปิดหน้าต่างแล้วบอทยังรัน
- MAX_TRADES_PER_DAY ค่าเริ่ม 0 = ไม่จำกัด เพื่อไม่เปลี่ยนพฤติกรรมบอทเดิม
- Agents = strategy แต่ละตัว จับคู่เทรดจาก reason "name: ..." (AI brain เขียน reason เอง เลยไม่นับเข้า agent ไหน)
- Signals: TradingView ส่ง webhook ได้แค่ URL สาธารณะ เราไม่มีเซิร์ฟเวอร์ → ใช้ ntfy.sh (ฟรี ไม่ต้องสมัคร)
  - topic สุ่มยาวคือ secret ตัวเดียว, บอท poll ทุกรอบ 5 วิ ด้วย urllib ไม่ต้องลง lib
  - สัญญาณบอกแค่ buy/sell, lot/SL/TP/กฎความเสี่ยงยังเป็นของบอททั้งหมด (current_block ตัวเดียวกับ strategies)
  - สัญญาณที่ส่งมาตอนบอทปิดจะถูกข้าม ไม่เทรดย้อนหลัง
  - Telegram /buy /sell ใช้บอท Telegram ของเราเอง ไม่ล็อกอินด้วยเบอร์แบบ AutoBotSignal

## 2026-10-07 แอปใช้งานเดี่ยวได้ (standalone) + หน้า Setup

- ไม่ยัดไฟล์ MT5 ลงในตัวติดตั้ง: MT5 เป็นของ MetaQuotes การแจกต่อเองเสี่ยงผิด license
  - แทนด้วยปุ่มเดียวในหน้า Setup: ดาวน์โหลดตัวติดตั้งทางการจาก download.mql5.com แล้วเปิดให้กด
  - MT5 ของโบรกเกอร์ไหนก็ใช้ได้ (หาจาก uninstall list ใน registry)
- หน้า Setup 5 ขั้น: MT5 → ล็อกอิน → โหมด → symbol → Start, เปิดเองตอนตั้งค่ายังไม่ครบ
  - โหมดมาก่อน symbol: การเลือก symbol สร้าง .env ซึ่งจะทำให้ขั้นโหมดถูกติ๊กเองถ้าอยู่หลัง
- รหัสผ่านโบรกเกอร์ส่งตรงเข้า mt5.login ไม่เก็บไม่ log, ห้ามสลับบัญชีตอนบอทรัน
- pywebview ถูกบังคับใช้ WebView2 (gui=edgechromium) ถ้าไม่มีให้เปิดเบราว์เซอร์แทน ไม่ถอยไปใช้ IE engine
- build_installer.bat ล้าง PYTHONPATH และตรวจว่า MetaTrader5 ตัวจริง (_core.pyd) อยู่ในตัวแพ็ก
  - เคยหลุด: PYTHONPATH=tests_support ทำให้ installer รุ่น 13:33 แพ็ก MT5 ตัวปลอมไป เทรดไม่ได้เลย

## 2026-10-07 แก้ตามผลทดสอบของ sub agent

- สัญญาณจากข้างนอกติดเวลาส่ง เก่ากว่า 60 วิข้าม (MT5 หลุดแล้วกลับมา ต้องไม่เทรดย้อนหลัง)
- ข้อความแบบ exit/close/flat ไม่เทรด เพราะ SL/TP ของบอทปิดไม้เอง; คู่มือเปลี่ยนเป็น {{strategy.market_position}}
- บอทจองพอร์ต 47821 ตอนรัน ตัวที่สองเปิดไม่ขึ้น (OS คืนพอร์ตเองตอน process ตาย แม้ crash)
  - bot.alive ยังใช้แสดงสถานะในหน้าเว็บ แต่ไม่ใช่ตัวกันซ้อนอีกต่อไป
- save_settings/arm/switch_source อ่าน-รวม-เขียนใน RLock เดียว กันกดเร็วแล้วค่าหาย
- เช็ก Sec-Fetch-Site กัน <img> ข้ามเว็บสั่ง MT5 เปิด; error ทุกตัวตอบเป็น JSON ไม่ตัดการเชื่อมต่อ
- ถอนการติดตั้งลบ .env (มี key) แต่เก็บ journal.db
- ไม่แก้: AI ช้า >60 วิ กับสัญญาณช่วงตลาดปิดในโหมด paper (ต่ำ ต้องรู้ offset เวลา server)
- ไม่บังคับยืนยัน MODE=live ฝั่ง server: ใครเรียก API ได้ก็แก้ .env ตรงๆ ได้อยู่แล้ว หน้าเว็บถามยืนยันแล้ว

## 2026-10-07 เลือก AI ได้ 3 เจ้า: Claude, GPT, Gemini

- AI_PROVIDER = claude | openai | gemini, key ของใครของมัน (ANTHROPIC/OPENAI/GEMINI_API_KEY), AI_MODEL ว่าง = ค่าเริ่ม
- GPT กับ Gemini เรียกผ่าน HTTPS ด้วย urllib ไม่ลง SDK เพิ่ม; ทุกเจ้าผ่าน ask() ตัวเดียว ตอบ JSON schema เดียวกัน
  - GPT: Responses API, text.format json_schema strict, รุ่นเริ่ม gpt-6.1-sol ($2 / $0.10 cached / $10)
  - Gemini: generateContent, responseSchema (ตัว type พิมพ์ใหญ่ ไม่มี additionalProperties), รุ่นเริ่ม gemini-3.8-flash
  - ราคา Gemini ใช้ $1.50/$7.50 (ราคาหลังโปรหมด 2026-12-31) ให้งบรายวันประเมินเกินไว้ก่อน ปลอดภัยกว่า
- ทุก error ของทุกเจ้า = hold พร้อมบอกเหตุผล ไม่มีวันเทรดเพราะ AI พัง
- ยังไม่ได้ยิง GPT/Gemini จริง (ไม่มี key) ทดสอบด้วยการจำลองคำตอบตามเอกสารทางการเดือนตุลาคม 2026
- wizard แบบ console ยังถามแค่ key ของ Claude; เลือกเจ้าอื่นในหน้า Settings

## 2026-10-07 desktop เต็มตัว: windowed exe, tray, watchdog

- exe เป็น --windowed (ไม่มี console) เครื่องมือ .bat ใช้ AttachConsole ยืม console ของ cmd
- ปิดหน้าต่าง = ซ่อนไป tray (pystray), AUTO_START_BOT + watchdog ฟื้นบอทที่ตายเองหลัง 30 วิ
  ยอมแพ้หลังตายเร็วติดกัน 6 ครั้ง (กันวนเพราะตั้งค่าผิด)
- ThreadingHTTPServer ของ stdlib ตั้ง SO_REUSEADDR: บน Windows แอปที่สองจองพอร์ตซ้ำได้ → ใช้ SO_EXCLUSIVEADDRUSE
- **เปิดเองตอนเปิดเครื่อง ให้ตัวติดตั้งทำ (Startup shortcut) ห้ามแอปเขียน Run key เอง**
  - ครั้งแรกให้แอปเขียน HKCU\...\Run เอง → Defender ตีเป็น Behavior:Win32/Persistence.A!ml
    แล้วบล็อก exe ตัวนั้นทุกที่ (false positive แต่พฤติกรรมเหมือนมัลแวร์จริง: exe ไม่เซ็น + ฝังตัวเอง)
  - test_desktop เช็กว่าในโค้ดไม่มีการเขียน Run key อีก
- การทดสอบ installer ซ้อนกับของผู้ใช้ทำให้รายการ Apps & features และ Start Menu หาย (AppId เดียวกัน)
  ซ่อมแล้วด้วยการลงทับ; ต่อไปทดสอบจาก dist\TradeBot ตรงๆ

## 2026-10-07: Demo / Live ตามบัญชีที่ MT5 ล็อกอินอยู่ (ผู้ใช้เลือกข้อ 3)
- แอปเช็คทุก 15 วิ ถ้าบัญชีใน MT5 ไม่ตรงกับ MODE ก็สลับให้ และจดเลขบัญชีลงช่อง Demo หรือ Live
- บัญชี Demo สลับให้ทันที ไม่ต้องถาม
- บัญชีเงินจริงต้องให้ผู้ใช้กดยืนยันก่อน ถามครั้งเดียวต่อเลขบัญชี และถามเฉพาะตอนหน้าต่างแอปเปิดอยู่
- ถ้าผู้ใช้ตอบไม่ MODE ยังเป็น demo ต่อ และ risk.account_error ทำให้บอทไม่ยอมเทรดบัญชีนั้น
- ไม่เลือกแบบสลับเองล้วนๆ (ข้อ 2) เพราะแค่ล็อกอินบัญชีจริงใน MT5 เล่นๆ บอทก็จะเทรดเงินจริงเงียบๆ

## 2026-10-08: รวมหน้า Strategies เข้า AI agents + ตัววิเคราะห์ในตัว (ผู้ใช้เลือก 1,1)
- บอทหลักเลิกเทรดตาม STRATEGY เอง (ลบ check_market) ออเดอร์จากกฎทุกตัวมาจาก agent ทางเดียว
- ย้ายครั้งเดียวตอนเปิดแอป: สูตรที่เคยเปิดไว้กลายเป็น agent โหมด auto บน SYMBOL/TIMEFRAME เดิม
  - จำว่าทำแล้วด้วยไฟล์ strategies.moved; ถ้ายังไม่มี .env (ติดตั้งใหม่) จะไม่สร้าง agent ให้
  - BRAIN=ai/hybrid ย้ายเป็น analyst "saved" (AI ตัดสินจาก setup ของสูตร)
- ตัววิเคราะห์ในตัว (plainrules.py) อ่านกฎภาษาอังกฤษด้วย regex ไม่ใช้ AI
  - ประโยคที่อ่านไม่ออก ฝั่งนั้นไม่เทรด (ไม่เดา) และโชว์ในหน้าสร้าง agent
  - สูตรสำเร็จรูปที่ไม่แก้ข้อความ รันโค้ดเดิมใน strategies.py เป๊ะ เพราะบางสูตร (mr_zscore) อธิบายเป็นคำไม่ครบ
- ผลข้างเคียง: AI brain ที่เรียนรู้จากเทรดของตัวเอง (BRAIN=ai, reflect/playbook) ไม่ถูกเรียกจากบอทแล้ว เหลือใช้ใน replay.py
