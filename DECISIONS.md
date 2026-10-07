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
