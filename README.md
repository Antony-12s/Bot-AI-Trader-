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
| `start.bat` `train.bat` `settings.bat` | ดับเบิลคลิกบน Windows: รันบอท / ฝึก AI / แก้ค่า (`setup.bat` เตรียมสภาพแวดล้อมให้) |
| `dashboard.bat` `dashboard.py` | หน้าต่างควบคุม: สถานะ MT5, ราคา, ปุ่ม Start/Stop/Pause, สถิติ, log (สั่งบอทผ่าน `pause.flag` / `stop.flag`) |
| `wizard.py` | ถาม-ตอบรอบแรก อ่าน symbol, contract size, filling จาก MT5 แล้วเขียน `.env` ให้ |
| `run_forever.bat` `install_autostart.bat` | สำหรับ VPS: watchdog รีสตาร์ทบอทเองตอน crash และตั้งให้รันตอน login |
| `DECISIONS.md` | ทำไมถึงเลือกทางนี้ |

## เริ่มใช้ 3 ขั้น (Windows)

1. ลง **Python 3.10+** จาก python.org ติ๊ก "Add to PATH"
2. ลง **MT5 ของโบรก** (ต้องเป็น MT5 ไม่ใช่ MT4) เปิดแล้ว login ค้างไว้ บัญชี demo ก็ได้
3. ดับเบิลคลิก **`start.bat`**

รอบแรกมันถาม 3-4 คำถามในหน้าต่างเดียว (symbol เลือกจากรายการที่มันหาเจอใน MT5, lot, timeframe, ขาดทุนต่อวันสูงสุด) ที่เหลือเช่น contract size, จำนวนทศนิยม, filling mode มันอ่านจากโบรกเอง ถามท้ายว่าจะใช้ AI ไหม (ต้องมี API key) และ Telegram ไหม (ข้ามได้) แล้วบอทเริ่มทำงานเลย อยากเปลี่ยนค่าทีหลังจิ้ม `settings.bat`

ค่าเริ่มต้นคือ `MODE=dry`: paper trading ไม่ส่งอะไรให้โบรก ไม่มีเงินจริงขยับ จนกว่ามุงจะแก้ `MODE` ใน `.env` เอง

- **`dashboard.bat`** หน้าต่างเดียวจบ: ไฟเขียว/แดงว่าต่อ MT5 ติดไหม, ราคาสด, ปุ่ม Start / Stop / Pause / Report / Train / Settings, สถิติ, playbook, log ของบอท MT5 ย่อทิ้งไว้ได้เลย
- **`train.bat`** ฝึก AI ครบลูป: ดึงแท่งจาก MT5, เทียบกลยุทธ์ฟรี, replay บนกระดาษจนครบงบ, โชว์รายงาน
- ทุก .bat สร้าง `.venv` และลง library ให้เอง ปิดหน้าต่างหรือ Ctrl+C เพื่อหยุด

แบบพิมพ์เองถ้าชอบ:

```
pip install -r requirements.txt
python wizard.py            # หรือ copy .env.example .env แล้วแก้เอง
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
- `mr_zscore` สวนกลับแบบ MR-Intraday ของทีม (`docs/mr-intraday-v1.md`) ตัดเหลือ timeframe เดียว: ราคาปิดห่าง EMA20 เกิน 2 ATR ในตลาดที่ timeframe ใหญ่กว่า 2 เท่าไม่มีเทรนด์ (ADX < 20) และความผันผวนไม่พุ่ง แล้วแท่งล่าสุดกลับตัว ใช้คู่กับ `SL_ATR=1.0 TP_ATR=2.0` เป้าถึงจะอยู่แถวค่าเฉลี่ย ไม่มีตัวกรองข่าว/เวลา NY/trigger M1 ของสเปคเต็ม อันนั้นอยู่ใน engine วิจัย `mr_intraday.py`

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

## ทำงานเป็นทีม

- คนที่มีสิทธิ์ write ใน repo: clone มา สร้าง branch ของตัวเอง แก้ แล้วเปิด Pull Request เข้า `main` ทุก push รัน test อัตโนมัติ (แท็บ Actions) ทั้งบน Linux และ Windows
- รัน test ในเครื่องที่ไม่ใช่ Windows: `PYTHONPATH=tests_support python -m unittest` (โฟลเดอร์ `tests_support` มีตัวแทน MetaTrader5 ให้ import ได้)
- `.env` (มี API key, Telegram token) และ `journal.db` เป็นของใครของมัน อยู่ใน `.gitignore` ห้าม commit เด็ดขาด
- อยากให้ AI ของเพื่อนร่วมทีมเริ่มจากประสบการณ์เดียวกัน: ก๊อป `journal.db` ให้กันตรงๆ ได้ มันคือไฟล์เดียว
- push แล้วโดน 403 หรือ "Permission denied to <ชื่อคนอื่น>": เครื่องจำ login GitHub บัญชีอื่นไว้ Windows ดับเบิลคลิก `fix_github_login.bat` / Mac เปิด Terminal แล้ว `sh fix_github_login.sh` มันล้าง login เก่าแล้วให้ login ใหม่ด้วยบัญชีที่ถูกเชิญ (404 = URL ผิด สังเกตขีดท้ายชื่อ `Bot-AI-Trader-` หรือยังไม่กดรับคำเชิญ)

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
