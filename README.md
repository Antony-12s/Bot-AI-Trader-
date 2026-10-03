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
| `strategy.py` | สมองแบบกฎ (BRAIN=rules) ตัวอย่าง MA cross แก้ได้ตามใจ |
| `ai_strategy.py` | สมอง AI (BRAIN=ai): ตัดสินใจ, สะท้อนบทเรียนหลังปิดไม้, เขียน playbook |
| `journal.py` | สมุดบันทึก SQLite ที่ AI อ่านประสบการณ์ตัวเองกลับมา |
| `fills.py` | จำลองการโดน SL/TP จาก high/low ของแท่ง ใช้ทั้ง dry mode และ replay |
| `risk.py` | กฎที่สมองแตะไม่ได้: ห้ามเทรดเมื่อไร, SL/TP อยู่ตรงไหน |
| `replay.py` | ป้อนกราฟย้อนหลังทีละแท่งให้สมองเทรดบนกระดาษ ฝึกได้เร็ว |
| `export_history.py` | ดึงแท่งจาก MT5 เป็น CSV (รันบน Windows ที่เปิด MT5) |
| `report.py` | สรุปผล: win rate, กำไร, drawdown, ค่า API, playbook, บทเรียนล่าสุด |
| `config.py` `indicators.py` | ตั้งค่าจาก .env และอินดิเคเตอร์ Python ล้วน |
| `DECISIONS.md` | ทำไมถึงเลือกทางนี้ |

## เริ่มใช้ (Windows ที่มี MT5 เปิดและ login แล้ว)

```
pip install -r requirements.txt
copy .env.example .env      # แก้ SYMBOL, LOT, SL/TP, CONTRACT_SIZE, Telegram, API key
python -m unittest          # ไม่ต้องมี MT5 ก็รันได้บน Windows
python bot.py
```

ค่าเริ่มต้นคือ `MODE=dry` และ `BRAIN=rules`: ไม่ส่งอะไรให้โบรก ไม่เสียค่า API

## ให้ AI ฝึกก่อนใช้เงินจริง

1. ดึงประวัติ: `python export_history.py --days 90` (ได้ `history.csv` และบอก contract size ของโบรกให้ตรวจ `CONTRACT_SIZE`)
2. ฝึกเร็วบนกระดาษ: `python replay.py history.csv --brain ai --budget 5` หยุดเองเมื่อครบงบ
3. ดูผล: `python report.py` หรือ `python report.py --source replay`
4. ฝึกต่อกับตลาดจริงแบบไม่เสี่ยง: ตั้ง `BRAIN=ai` แล้วรัน `python bot.py` ใน `MODE=dry` ทุกสัญญาณเปิดไม้เสมือน ตามผลจากแท่งจริง
5. พอใจแล้วค่อย `MODE=demo` (บัญชี demo เท่านั้น) และ `MODE=live` ต้องแก้ `.env` ด้วยมือเท่านั้น

ทุกขั้นเขียน `journal.db` ใบเดียวกัน บทเรียนจากข้อ 2 ติดตัว AI ไปข้อ 4 และ 5

## คำสั่ง Telegram

`/status` สถานะ + สถิติ, `/pause` `/resume` หยุด/เปิดเข้าไม้ใหม่, `/playbook` กฎที่ AI เขียนให้ตัวเอง, `/lessons` บทเรียน 3 ไม้ล่าสุด

## ต้องรู้ก่อน พูดตรงๆ

- Claude ผ่าน API เทรนน้ำหนักโมเดลไม่ได้ "การเรียนรู้" ในนี้คือการอ่านสถิติ, playbook และบทเรียนของตัวเองกลับเข้า prompt ทุกครั้ง
- ค่าใช้จ่าย: ทุกแท่งที่เข้าเงื่อนไขเทรดได้ = 1 call, ทุกไม้ที่ปิด = 1 call สั้นๆ, ทุก 10 ไม้ = 1 call เขียน playbook ตั้ง `AI_BUDGET_USD` ไว้เสมอ ตัวเลขค่าใช้จ่ายในรายงานเป็นค่าประมาณจาก token
- replay อาจดูดีเกินจริง เพราะโมเดลอาจเคยเห็นช่วงเวลานั้นมาแล้ว prompt ไม่มีวันที่ แต่ก็ควรเชื่อผลจาก dry/demo มากกว่า
- ไม่มีอะไรรับประกันว่าทำกำไรได้ กฎ SL/TP, ขีดจำกัดขาดทุนต่อวัน และงบ AI อยู่นอกมือของ AI ด้วยเหตุนี้
