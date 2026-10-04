# cTrader Open API

ทางเลือกแทน MT5 สำหรับคนที่ไม่มี Windows หรือไม่อยากเปิดโปรแกรมค้าง บอทคุยกับ gateway ของ Spotware
(`demo.ctraderapi.com` / `live.ctraderapi.com` port 5035) ผ่าน TLS ตรง ไม่ต้องมีโปรแกรม cTrader เปิดอยู่
ไฟล์เดียวที่รู้เรื่องนี้คือ `broker_ctrader.py` ส่วนอื่นของบอทไม่เปลี่ยน

## ต้องมี

1. **บัญชีที่โบรกที่มี cTrader** เช่น IC Markets, Pepperstone, FxPro, Fusion Markets, BlackBull (XM ไม่มี cTrader)
   เปิด demo ก่อน ตอนสมัครเลือกแพลตฟอร์ม cTrader จะได้ **cTrader ID** (อีเมล + รหัส) มาด้วย
2. **app ใน Open API**: ไปที่ https://openapi.ctrader.com login ด้วย cTrader ID -> Applications -> Add new app
   - Redirect URI ต้องเป็น `http://localhost:8765/callback` ตรงตัว (ถ้าใส่ไม่ตรง จะ login แล้วเด้งไม่กลับ)
   - กด Activate แล้วจดค่า **Client ID** กับ **Client Secret**
3. Python 3.10+ (Mac มักมี `python3` อยู่แล้ว)

## ตั้งค่า

```
sh start.sh        # Mac / Linux   (Windows: start.bat)
```

รอบแรก wizard ถามว่าเทรดที่ไหน ตอบ `2` (cTrader) จากนั้น

1. มันขอ Client ID / Client Secret แล้วเปิด browser ไปหน้า login ของ cTrader ID
2. login แล้วหน้าเว็บจะเด้งกลับมาที่ `localhost:8765` เอง (ถ้าไม่เด้ง ก๊อป URL ทั้งเส้นที่ browser ไปถึงมาวางใน terminal)
3. มันลิสต์บัญชีเทรดของ cTrader ID นั้น เลือกเลข (เลือก demo ก่อน) แล้วเขียน `CTRADER_*` ลง `.env`
4. ต่อด้วยคำถามเดิม: symbol (มันลิสต์ทองที่โบรกให้เลือก), lot, timeframe, ขาดทุนต่อวัน, AI, Telegram

อยาก login ใหม่หรือเปลี่ยนบัญชี: `python ctrader_auth.py` อย่างเดียวก็ได้ มันแก้เฉพาะบรรทัด `CTRADER_*` ใน `.env`

`MODE` ยังเป็น `dry` เหมือนเดิม: paper trading บนราคาจริงของ cTrader ไม่มีออเดอร์ออกจนกว่าจะแก้เป็น `demo`
(ส่งจริงเฉพาะเมื่อบัญชีที่เลือกเป็น demo) หรือ `live` ด้วยมือ

## ที่ต่างจาก MT5 (รู้ไว้ ไม่ต้องทำอะไร)

| เรื่อง | MT5 | cTrader |
| --- | --- | --- |
| ต้องเปิดโปรแกรมค้าง | ใช่ (terminal) | ไม่ |
| OS | Windows | ทุก OS |
| ติดป้ายไม้ของบอท | magic number | label ของออเดอร์ (ใช้ค่า `MAGIC` เดิมเป็นข้อความ) |
| SL/TP ตอนส่ง | ราคาตรงๆ | ระยะจากราคาเข้า (relativeStopLoss/TakeProfit) ราคาจริงจึงขยับตาม slippage นิดหน่อย |
| รู้ว่าปิดด้วย SL หรือ TP | ดีลบอก | ดีลไม่บอก บอทเทียบราคาปิดกับ SL/TP ในสมุด (ชิดข้างไหนภายใน 1/5 ของช่วง) ไม่งั้นลงเป็น `closed` |
| spread ในแท่งย้อนหลัง | มีทุกแท่ง | ไม่มี ใช้ spread ปัจจุบันกับทุกแท่ง (มีผลกับ paper fill และ replay นิดเดียว) |
| timeframe | ครบ | M1 M2 M3 M4 M5 M10 M15 M30 H1 H4 H12 D1 W1 MN1 (ไม่มี M6 M12 M20 H2 H3 H6 H8) |
| วันใหม่ของ MAX_DAILY_LOSS | เที่ยงคืนเวลา server โบรก | เที่ยงคืน UTC |
| `FILLING` | ต้องตรงกับโบรก | ไม่ใช้ |
| ประวัติแท่งที่ดึงได้ครั้งเดียว | ตามที่ terminal โหลดไว้ | M1-M5 ย้อนได้ 5 สัปดาห์, M10-H1 35 สัปดาห์, H4-D1 1 ปี (`train.sh` 90 วันบน M15 พอ) |

token ที่ได้มีอายุราวเดือน บอท refresh เองเมื่อหมดแล้วเขียนกลับ `.env` ถ้า gateway ตัดการเชื่อมต่อ บอทต่อใหม่เองและ subscribe ราคาซ้ำ แจ้ง Telegram ครั้งเดียวเหมือนตอน MT5 หลุด

## ยังไม่ชัวร์ (เพราะยังไม่เคยวิ่งกับ gateway จริง)

เครื่องที่พัฒนาสายนี้ออกอินเทอร์เน็ตไปหา `ctraderapi.com` ไม่ได้ test ทั้ง 27 ตัวใน `test_broker_ctrader.py` ใช้
gateway จำลองที่ตอบด้วย protobuf จริงจาก package `ctrader-open-api` รอบแรกที่รันกับ demo จริง ให้ดู 4 จุดนี้

1. **เครื่องหมายค่าคอม/swap**: โค้ดบวกค่าที่ gateway ส่งมาตรงๆ โดยเชื่อว่าค่าใช้จ่ายเป็นลบ หลังปิดไม้ demo ไม้แรก
   เทียบ `profit` ใน `python report.py` กับ Net P&L ที่ cTrader โชว์ ถ้าต่างกันเท่าค่าคอมสองเท่า แปลว่าเครื่องหมายกลับ แก้ที่ `deal_result()` ใน `broker_ctrader.py`
2. **หน่วยของ `slDistance`** (ระยะ SL ต่ำสุดของโบรก): ถือว่าเป็น point = 10^-digits ถ้าออเดอร์โดน `TRADING_BAD_STOPS` หรือ `PROTECTION_IS_TOO_CLOSE_TO_MARKET` ให้เพิ่ม `SL_ATR` หรือ `SL_POINTS`
3. **แท่งที่กำลังก่อตัว**: ถ้า gateway ส่งมาให้แล้ว บอทจะไม่เติมซ้ำ (เช็คที่เวลาเริ่มแท่ง) ถ้า log บอทขึ้น "skipped: candle closed ... min ago" ทั้งที่ตลาดเปิดอยู่ ให้แจ้ง
4. **คำสั่ง `/status` แสดง `open_positions`** นับจาก label ถ้าเปิดไม้มือใน cTrader มันจะไม่นับ (ถูกต้อง) แต่ `pnl_today` นับทุกดีลในบัญชี (ตั้งใจ เหมือน MT5)

ถ้าออเดอร์ส่งแล้วไม่มีคำตอบใน 20 วินาที บอทจะลงว่า fail และไม่ส่งซ้ำ ถ้า cTrader โชว์ว่าไม้เปิดจริง บอทยังนับไม้นั้นผ่าน label (ไม่เปิดเพิ่ม) แต่สมุดจะไม่มีแถวของมัน ปิดมือได้

## รันบน VPS

สายนี้ไม่ต้องใช้ Windows: VPS Linux ราคาถูกสุด (RAM 1 GB พอ) หรือ Mac mini ที่บ้าน ก็รัน `sh start.sh` ใน `tmux`/`screen`
หรือทำเป็น systemd service ที่รัน `.venv/bin/python bot.py` ใน `WorkingDirectory` ของ repo แล้ว `Restart=always` แทน `run_forever.bat`
