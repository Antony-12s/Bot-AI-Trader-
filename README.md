# Bot AI Trader

บอทเทรด Forex/ทอง บน MT5 ที่ให้ Claude เป็นคนตัดสินใจเองทั้งหมด และเรียนรู้จากไม้ที่ตัวเองเทรด

```
export_history.py  ->  history.csv  ->  replay.py  ---+
                                                      |      journal.db
bot.py (dry = paper, demo, live)  --------------------+-->  ทุกการตัดสินใจ ทุกไม้ ผลลัพธ์ บทเรียน playbook
                                                      |
report.py  <------------------------------------------+
```

## ไฟล์

| ไฟล์ | หน้าที่ |
| --- | --- |
| `bot.py` | ลูปหลัก: อ่านแท่งจาก MT5, ถามสมอง, ส่ง/จำลองออเดอร์, คุยกับ Telegram |
| `strategies.py` | กลยุทธ์กฎ 4 แบบ (ma_cross, trend_pullback, bollinger_breakout, rsi_reversion) เลือกด้วย `STRATEGY=` |
| `ai_strategy.py` | สมอง AI: ตัดสินใจจากตลาด + ประสบการณ์ตัวเอง, สะท้อนบทเรียนหลังปิดไม้, เขียน playbook |
| `brain.py` | เลือกสมอง: `rules` กฎเทรดเอง, `ai` Claude เทรดเอง, `hybrid` กฎเสนอ Claude ตัดสิน |
| `journal.py` | สมุดบันทึก SQLite ที่ AI อ่านประสบการณ์ตัวเองกลับมา |
| `fills.py` | จำลองการโดน SL/TP จาก high/low ของแท่ง ใช้ทั้ง dry mode และ replay |
| `risk.py` | กฎที่สมองแตะไม่ได้: ห้ามเทรดเมื่อไร, SL/TP ตาม ATR อยู่ตรงไหน |
| `replay.py` | ป้อนกราฟย้อนหลังทีละแท่งให้สมองเทรดบนกระดาษ ฝึกได้เร็ว |
| `export_history.py` | ดึงแท่งจาก MT5 เป็น CSV (รันบน Windows ที่เปิด MT5) |
| `report.py` | สรุปผล: win rate, กำไร, drawdown, ค่า API, playbook, บทเรียนล่าสุด |
| `config.py` `indicators.py` | ตั้งค่าจาก .env และอินดิเคเตอร์ Python ล้วน |
| `start.bat` `train.bat` | ดับเบิลคลิกบน Windows: รันบอท / ฝึก AI (`setup.bat` เตรียมสภาพแวดล้อมให้ทั้งคู่) |
| `run_forever.bat` `install_autostart.bat` | สำหรับ VPS: watchdog รีสตาร์ทบอทเองตอน crash และตั้งให้รันตอน login |
| `DECISIONS.md` | ทำไมถึงเลือกทางนี้ |

## เริ่มใช้แบบกดจิ้ม (Windows ที่มี MT5 เปิดและ login แล้ว)

ต้องมี Python 3.10 ขึ้นไปจาก python.org (ติ๊ก "Add to PATH" ตอนลง) แค่นั้น

- ดับเบิลคลิก **`start.bat`** รันบอท รอบแรกมันจะสร้าง `.env` แล้วเปิด Notepad ให้กรอก พอเซฟปิดบอทก็เริ่มเอง
- ดับเบิลคลิก **`train.bat`** ฝึก AI ครบลูป: ดึงแท่งจาก MT5, replay บนกระดาษจนครบงบ, แล้วโชว์รายงาน

ทั้งสองไฟล์สร้าง `.venv` และลง library ให้เอง ปิดหน้าต่างหรือกด Ctrl+C เพื่อหยุด

ค่าเริ่มต้นคือ `MODE=dry` และ `BRAIN=rules`: ไม่ส่งอะไรให้โบรก ไม่เสียค่า API

แบบพิมพ์เองถ้าชอบ:

```
pip install -r requirements.txt
copy .env.example .env      # แก้ SYMBOL, LOT, SL/TP, CONTRACT_SIZE, Telegram, API key
python -m unittest          # ไม่ต้องมี MT5 ก็รันได้บน Windows
python bot.py
```

## กลยุทธ์: ใครหาจุดเข้า ใครตัดสิน

| `BRAIN=` | ใครหา setup | ใครตัดสิน | ค่า API |
| --- | --- | --- | --- |
| `rules` | กฎใน `strategies.py` | กฎ | ฟรี |
| `ai` | Claude ดูตลาดเอง | Claude | ทุกแท่ง |
| `hybrid` | กฎเสนอ | Claude เลือกเอา/ไม่เอา เทรดทิศที่กฎไม่เสนอไม่ได้ | เฉพาะแท่งที่มี setup |

กฎที่มี (ตั้งใน `STRATEGY=` เลือกตัวเดียว, หลายตัวคั่น comma, หรือ `all`):

- `trend_pullback` ตามเทรนด์: EMA50 บอกทิศ รอราคาย่อแตะ EMA20 แล้วปิดกลับทางเทรนด์ RSI ไม่ตึง
- `bollinger_breakout` Bollinger บีบตัวเหลือครึ่งของช่วงกว้างสุดใน 20 แท่ง แล้วปิดทะลุแบนด์
- `rsi_reversion` สวนกลับ: RSI หลุด 30/70 ขณะราคาอยู่นอกแบนด์ แล้วเริ่มกลับตัว
- `ma_cross` SMA10 ตัด SMA30 ตัวอย่างดั้งเดิม

SL/TP เป็นตัวคูณ ATR (`SL_ATR=1.5 TP_ATR=3.0`) ตลาดแรง stop กว้างขึ้นเอง ตลาดนิ่ง stop แคบลง ไม่เคยแคบกว่า 2 spread ตั้งเป็น 0 ถ้าอยากใช้ points ตายตัว

**ไม่มีกลยุทธ์ไหนพิสูจน์แล้วว่าได้กำไร** เทียบบนข้อมูลของมุงเองก่อน (ฟรี ไม่เรียก AI):

```
python replay.py history.csv --compare
```

## ให้ AI ฝึกก่อนใช้เงินจริง

`train.bat` ทำข้อ 1 ถึง 4 ให้ในคลิกเดียว หรือทำเองทีละขั้น:

1. ดึงประวัติ: `python export_history.py --days 90` (ได้ `history.csv` และบอก contract size ของโบรกให้ตรวจ `CONTRACT_SIZE`)
2. เทียบกลยุทธ์กฎฟรีๆ: `python replay.py history.csv --compare` เลือกตัวที่รอดไปใส่ `STRATEGY=`
3. ฝึกเร็วบนกระดาษ: `python replay.py history.csv --brain hybrid --budget 5` หยุดเองเมื่อครบงบ (หรือ `--brain ai` ให้ Claude หาเอง แพงกว่า)
4. ดูผล: `python report.py` หรือ `python report.py --source replay`
5. ฝึกต่อกับตลาดจริงแบบไม่เสี่ยง: ตั้ง `BRAIN=hybrid` แล้วรัน `python bot.py` ใน `MODE=dry` ทุกสัญญาณเปิดไม้เสมือน ตามผลจากแท่งจริง
6. พอใจแล้วค่อย `MODE=demo` (บัญชี demo เท่านั้น) และ `MODE=live` ต้องแก้ `.env` ด้วยมือเท่านั้น

ทุกขั้นเขียน `journal.db` ใบเดียวกัน บทเรียนจากข้อ 3 ติดตัว AI ไปข้อ 5 และ 6 ไม้ที่กฎเทรดเอง (`rules`) ไม่ปนเข้าประสบการณ์ของ AI

## คำสั่ง Telegram

`/status` สถานะ + สถิติ, `/pause` `/resume` หยุด/เปิดเข้าไม้ใหม่, `/stop` ปิดบอทจริงๆ (watchdog ไม่รีสตาร์ท), `/playbook` กฎที่ AI เขียนให้ตัวเอง, `/lessons` บทเรียน 3 ไม้ล่าสุด

## รัน 24 ชั่วโมงบน Windows VPS ไม่ต้องมีคนเฝ้า

บอทคุยกับ MT5 ผ่าน package ที่รันได้แค่บน Windows เครื่องเดียวกับ MT5 ดังนั้น "รันตลอดโดยไม่ต้องเปิดคอม" = เช่า Windows VPS (ค้นว่า forex VPS, RAM 2 GB พอ) แล้วทำตามนี้บน VPS:

1. ลง Python 3.10+ (ติ๊ก Add to PATH) และ MT5 ของโบรก login ค้างไว้
2. ก๊อป repo นี้ไปวาง ดับเบิลคลิก `start.bat` รอบแรกเพื่อสร้าง `.env` และกรอกค่า ปิดได้เมื่อเห็นว่าบอทเริ่มทำงาน
3. ดับเบิลคลิก `install_autostart.bat` ให้ `run_forever.bat` รันเองทุกครั้งที่ login (ถ้าสร้าง task ไม่ได้ คลิกขวา Run as administrator)
4. ให้ MT5 เปิดเองตอน login: Win+R พิมพ์ `shell:startup` แล้วลาก shortcut ของ MT5 ใส่
5. ตั้ง auto-logon ของ Windows (`netplwiz` หรือเครื่องมือ Autologon ของ Sysinternals) เพื่อให้รีบูตแล้วกลับมาเองโดยไม่ต้อง RDP เข้าไปกด
6. รีบูต 1 ครั้งเพื่อทดสอบ ดูว่า Telegram ได้ข้อความ "bot started"

`run_forever.bat` รีสตาร์ทบอทใน 30 วินาทีหลัง crash หรือหลัง MT5 ยังไม่ขึ้นตอนบูต ถ้า MT5 หลุดกลางทางบอทจะแจ้งใน Telegram ครั้งเดียวแล้วต่อใหม่เอง สั่ง `/stop` จาก Telegram เมื่ออยากปิดจริง ถ้าออเดอร์ fail ด้วย retcode 10027 ให้กดปุ่ม Algo Trading บน MT5 ให้เขียว

## ต้องรู้ก่อน พูดตรงๆ

- Claude ผ่าน API เทรนน้ำหนักโมเดลไม่ได้ "การเรียนรู้" ในนี้คือการอ่านสถิติ, playbook และบทเรียนของตัวเองกลับเข้า prompt ทุกครั้ง
- ค่าใช้จ่าย: `ai` ทุกแท่งที่เข้าเงื่อนไขเทรดได้ = 1 call, `hybrid` เฉพาะแท่งที่มี setup, ทุกไม้ที่ปิด = 1 call สั้นๆ, ทุก 10 ไม้ = 1 call เขียน playbook ตั้ง `AI_BUDGET_USD` ไว้เสมอ ตัวเลขค่าใช้จ่ายในรายงานเป็นค่าประมาณจาก token
- LLM ไม่เก่งเรื่องทายทิศแท่งถัดไปจากตัวเลขล้วนๆ `hybrid` จึงให้กฎหาจุดเข้าและให้ Claude เป็นคนกรองด้วยบริบทและบทเรียน ไม่ใช่เดาจากอากาศ
- replay อาจดูดีเกินจริง เพราะโมเดลอาจเคยเห็นช่วงเวลานั้นมาแล้ว prompt ไม่มีวันที่ แต่ก็ควรเชื่อผลจาก dry/demo มากกว่า
- ไม่มีอะไรรับประกันว่าทำกำไรได้ กฎ SL/TP, ขีดจำกัดขาดทุนต่อวัน และงบ AI อยู่นอกมือของ AI ด้วยเหตุนี้
