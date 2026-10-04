# MR-Intraday v1 (Price-only) — GBPUSD & XAUUSD CFD

- วันที่: 2026-10-04
- สถานะ: **กลยุทธ์ที่กำหนดกติกาครบแล้ว แต่ยังไม่ได้ backtest** — ไม่มีผลทดสอบในเอกสารนี้
- ต่อยอดจาก: `claude/strategy-spec-mr-intraday-gbpusd-xauusd-v0.md`
- ข้อมูลที่ใช้: ราคาเท่านั้น (M1 bid OHLC + spread) — ไม่ใช้ IV, OI และไม่พึ่ง volume

---

## 1. ข้อมูลและการเตรียม

- M1 bid OHLC + spread ต่อแท่ง (ask = bid + spread)
- แปลงเวลาทั้งหมดเป็นเวลา New York (America/New_York) เพื่อรองรับ DST; ตรวจ timezone ของ server โบรกเกอร์ก่อน
- สร้าง M15 / M30 จาก M1 ชุดเดียวกัน (align ตามนาฬิกา :00 :15 :30 :45)
- ทุก indicator ใช้เฉพาะแท่งที่ปิดแล้ว

## 2. Indicators

| หน้าที่ | Indicator | TF | ค่า |
|---|---|---|---|
| Anchor (ค่าเฉลี่ย) | EMA(20) ของ close | M15 | 20 |
| สเกลความผันผวน | ATR(14) แบบ Wilder | M15 | 14 |
| ส่วนเบี่ยงเบน | z = (Close − EMA20) ÷ ATR14 | M15 | — |
| Regime: แนวโน้ม | ADX(14) แบบ Wilder | M30 | < 20 |
| Regime: volatility ขยาย | Percentile rank ของ ATR(14) ใน 100 แท่งล่าสุด | M30 | < 80 |
| Trigger | M1 close > High ของแท่ง M1 ก่อนหน้า (Long) | M1 | — |

## 3. Filters (ต้องผ่านทั้งหมด ณ เวลาปิดแท่ง M15 ที่เป็น setup)

1. เวลา: แท่ง setup ปิดระหว่าง 03:00–15:00 NY, จันทร์–ศุกร์
2. Regime: ADX(14) M30 < 20 และ ATR-percentile M30 < 80 (ใช้แท่ง M30 ล่าสุดที่ปิดแล้ว)
3. ข่าว: ไม่อยู่ในช่วง −30 ถึง +30 นาทีของข่าว high-impact (USD ทั้งสองสัญลักษณ์; GBP เพิ่มสำหรับ GBPUSD)
4. Spread: spread ปัจจุบัน ≤ 1.5 × median spread ของช่วงเวลา 15 นาทีเดียวกันใน 20 วันทำการก่อนหน้า
5. ไม่มีสถานะเปิดในสัญลักษณ์นั้น
6. Excursion reset: หลังจากเข้าเทรด Long แล้ว จะไม่รับ setup Long ใหม่จนกว่า z จะปิด M15 เหนือ −1.0 (Short กลับทิศ)

## 4. Entry (Long; Short กลับทิศทุกข้อ)

1. Setup: แท่ง M15 ปิดด้วย z ≤ −2.0 และผ่าน filters
2. Trigger window: 15 แท่ง M1 ถัดไป
3. Trigger: แท่ง M1 แรกที่ close > high ของแท่ง M1 ก่อนหน้า
4. เข้า: ราคาเปิดของแท่ง M1 ถัดไป ที่ ask
5. RR check: ถ้า (TP − Entry) < 1.0 × (Entry − SL) → ไม่เข้า
6. ถ้าหมด window โดยไม่เกิด trigger → ยกเลิก (แท่ง M15 ถัดไปเปิด setup ใหม่ได้ถ้าเงื่อนไขครบ)

## 5. Exit (ข้อใดเกิดก่อน)

| Exit | กติกา |
|---|---|
| Take Profit | ค่า EMA20 (M15) ณ แท่ง setup — ล็อกตอนเข้า, limit fill |
| Stop Loss | Entry − 1.0 × ATR14 (M15 ณ แท่ง setup); ถ้าราคาเปิด gap ผ่าน SL ให้ fill ที่ราคาเปิด |
| Time stop | ปิดเมื่อครบ 8 แท่ง M15 หลังเข้า (≈ 2 ชั่วโมง) ที่ราคาเปิด M1 ถัดไป |
| ก่อนข่าว | ปิด 5 นาทีก่อนข่าว high-impact |
| สิ้นวัน | ปิดทั้งหมด 16:50 NY (ก่อน rollover — ไม่มี swap) |

- Long ออกที่ bid / Short ออกที่ ask
- ถ้า SL และ TP อยู่ในแท่ง M1 เดียวกัน ให้ถือว่าโดน SL ก่อน (สมมติฐานแบบระมัดระวัง)
- เพดาน 2 วันของผู้ใช้ไม่ถูกใช้เพราะปิดภายในวัน

## 6. Position sizing และต้นทุน

- เสี่ยง 0.5% ของ Equity ต่อเทรด (ทดลองจริงเริ่ม 0.25%); ความเสี่ยงเปิดรวมสองสัญลักษณ์ ≤ 1%
- Lots = เงินที่เสี่ยง ÷ (ระยะ SL เป็นราคา × contract size) — ทั้งสองสัญลักษณ์ quote เป็น USD
- GBPUSD contract 100,000; XAUUSD contract size ตามโบรกเกอร์ (ต้องตรวจ)
- ปัดลงตาม lot step; ต่ำกว่า min lot → ไม่เข้า
- ต้นทุน: spread จริงจากข้อมูล + commission ตามโบรกเกอร์ + slippage บน SL/time stop
- ถ้าไม่มี spread จริง ใช้ grid: GBPUSD 0.8 / 1.2 / 1.8 pips; XAUUSD 0.20 / 0.35 / 0.50 USD (ไปกลับ)

## 7. เหตุผลของค่าที่เลือก

| ค่า | ประเภท | เหตุผล |
|---|---|---|
| ถือสั้น + time stop 2 ชม. + ปิดก่อน 16:50 NY | อิงหลักฐาน | Safari & Schmidhuber (2025): reversion ที่สเกลนาที, trend ตั้งแต่ไม่กี่ชั่วโมง |
| ต้องมี SL | อิงหลักฐาน/กลไก | บัญชี margin มี Stop Out; Connors เตือน DD ใหญ่เมื่อไม่ใช้ stop (คอร์สบทที่ 6) |
| M1 ใช้เป็น trigger เท่านั้น | อิงหลักฐาน + คณิตศาสตร์ต้นทุน | ต้นทุนต่อ R ของ M1 สูง |
| Anchor EMA20 M15, k = 2, ADX < 20, ATR pct < 80, window 03:00–15:00 NY | สมมติฐานของผู้ออกแบบ | ค่าตั้งต้นที่ใช้กันทั่วไป — ต้องทดสอบ |
| Spread filter, ข่าว ±30 นาที | สมมติฐาน | ลดต้นทุนและความเสี่ยงกระโดดของราคา |

## 8. Break-even (คณิตศาสตร์)

- c = ต้นทุนไปกลับ ÷ ระยะ SL (หน่วย R)
- ถ้า TP ≈ 2R และ SL = 1R: E = 3p − 1 − c → ต้องการ p > (1 + c) ÷ 3
- Time stop จะทำให้ RR จริงต่ำกว่านี้ — ต้องวัดจาก backtest

## 9. Parameter grid (ล็อกล่วงหน้า)

k {1.5, 2.0, 2.5} × ADX threshold {20, 25} × Time stop {8, 16 แท่ง M15} = 12 ชุดต่อสินทรัพย์
ค่าอื่นล็อกตามข้อ 2–6 ไม่ optimize; Variant ที่จะเทียบภายหลัง: Anchor = Session TWAP (reset 17:00 NY) → รวม 24 ชุด

## 10. เงื่อนไขหักล้าง

1. Walk-forward OOS net expectancy ≤ 0 ที่ต้นทุนฐาน
2. ต้นทุน × 1.5 แล้วติดลบ
3. ไม่ชนะ baseline สุ่มเวลาเข้า (ช่วงเวลาเดียวกัน, exit เดียวกัน)
4. ตัด regime filter แล้วผลไม่ต่าง
5. พารามิเตอร์ข้างเคียงผลกลับขั้ว
6. จำนวนเทรด OOS < 200

## 11. Pseudo-code

```
for each M15 bar t closed:
    update EMA20, ATR14, z on M15; ADX14, ATRpct on last closed M30
    if position open or not filters_ok(t): continue
    if z[t] <= -k and long_reset_ok:  open_window(LONG,  t, TP=EMA20[t], ATR=ATR14[t])
    if z[t] >= +k and short_reset_ok: open_window(SHORT, t, TP=EMA20[t], ATR=ATR14[t])

for each M1 bar i inside an open window (max 15 bars):
    if LONG  and close[i] > high[i-1]: entry = ask_open[i+1]; SL = entry - 1.0*ATR
    if SHORT and close[i] < low[i-1]:  entry = bid_open[i+1]; SL = entry + 1.0*ATR
    if |TP - entry| < 1.0*|entry - SL|: cancel
    else: size = 0.005*equity / (|entry-SL| * contract_size); open; set reset flag

for each M1 bar while position open:
    check SL (SL first if both), TP, time stop (8 M15 bars), news-5min, 16:50 NY
```