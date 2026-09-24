# Stick Cleaner — step-by-step calibration

This is a **host-side preview**. It does not write Marius / GameSir firmware and it does not change what a game receives. Rate numbers on this page are **measured from report timestamps**, not the 8 kHz sticker on the box.

Do the pad’s own calibration first when you can. Then do this rest pass so hysteresis matches *this* stick, *this* USB port, *this* host.

---

## Before you start

You need:

- The physical pad (Marius board, Tarantula 8K, or GameSir 8K)
- A **direct motherboard USB** port — not a case front port, not a hub, not a KVM
- RcmTool running (`python gamepad_signal_lab.py` or the Update-and-Start bat)
- Two minutes with your thumbs off the sticks

Do **not** calibrate if:

- The pad is resting on its face or a stick is touching the desk
- You are bumping the table
- Record Session is not running (no live reports)

---

## Part A — Pad firmware first (do this once per pad)

These boards already have inner/outer deadzone and a curve **on the device**. Calibrate that first so this tool is not fighting a second curve.

### Battle Beaver / Project Marius

1. Chrome or Edge (not Firefox / Safari).
2. Open https://setup.mariusheier.com
3. Hold the PS / Guide button and plug USB-C in.
4. Confirm **XInput** if you have that firmware (12-bit sticks).
5. Set **Polling Rate** to 8 kHz.
6. Inner deadzone: start at **2%**. Raise only until rest drift stops. Do not go “just in case.”
7. Outer deadzone: **0–5%**. Raise until a full physical throw reads 1.0.
8. Leave the response curve at whatever you already play with. Write it down.
9. Save to the board.

BBC factory ballpark: inner 2%, outer 0–5%, CER target 6–9%, bitrate 12-bit.

### GameSir Tarantula 8K / G7 Pro 8K

1. Open **GameSir Connect**.
2. Set polling to **8000 Hz** (wired).
3. Stick deadzone: start near **2%**.
4. If Connect has a curve, leave it. You will keep “Apply power curve here” **off** in RcmTool.
5. Save the profile.

---

## Part B — RcmTool rest calibration (2 minutes)

### 1. Connect for an honest rate

1. Plug USB-C into a rear I/O port.
2. Launch RcmTool.
3. Open **Controller Lab**.
4. Click **Refresh devices**.
5. Under Input Source pick the **named Raw HID** device, not Automatic / XInput, if the pad enumerates as HID.
6. Confirm sticks move when you touch them.

Why Raw HID: XInput/SDL/WinMM timestamps include Windows polling. The rate card will say ESTIMATE. Raw HID is MEASURED host-observed (still not a USB analyzer, but it is the real report stream this PC is seeing).

### 2. Start the live stream

1. Click **Record Session** on the top bar.
2. Open **Dashboard**.
3. Watch **Measured rate**.
4. Give it 2–3 seconds.

What you should see:

| What the card says | Meaning |
|---|---|
| ~8000 Hz | Host is actually getting 8 k reports/s |
| ~4000 / ~1000 / ~250 Hz | The pad or the OS fell back. Fix USB / firmware before calibrating hysteresis |
| Unavailable | No reports. Record Session is off or the device is wrong |

**Instant window** (Stick Cleaner page) is the same math on the last 250 ms. If Measured rate is 8 k and Instant window drops to 1 k, the stream is stuttering.

Preset target staying at 8000 does **not** mean you measured 8000. That card is CONFIG only.

### 3. Open Stick Cleaner

1. Left nav → **Stick Cleaner**.
2. Hardware dropdown — pick the pad you are holding:

   - Marius + MIDAS 5-pin Hall → **Marius MIDAS**
   - Marius + Magneto TMR → **Marius TMR**
   - Marius + ALPS pot → **Marius ALPS**
   - Tarantula 8K → **Tarantula 8K**
   - G7 Pro 8K / other GameSir 8K TMR → **GameSir 8K**

3. **Apply power curve here** — leave **unchecked** unless you turned the board curve off in Part A.
4. **Show cleaned overlay** — leave checked.

### 4. Hands-off rest capture

1. Set the pad flat on the desk.
2. Take both thumbs **completely off** both sticks.
3. Do not rest a palm on the shell hard enough to tilt a stick.
4. Click **Calibrate rest 2 s**.
5. Status should read `REST CAPTURE — thumbs off both sticks for 2 seconds.`
6. Do not touch anything until status changes to `Rest applied...`

If you bump a stick, click Calibrate rest again. One nudge inflates peak-to-peak and hysteresis goes too wide.

### 5. Read the result

After a good pass you should see something like:

```text
L center (+0.0120, -0.0070)  pp 0.0022/0.0018  n=16000
R center (-0.0040, +0.0090)  pp 0.0014/0.0016  n=16000
L hyst 0.0042   R hyst 0.0031
```

| Field | What it means | Healthy 12-bit Hall/TMR |
|---|---|---|
| center | Mean rest point. The cleaner subtracts this so (0,0) is the real spring center | Usually < 0.03 per axis |
| pp | Peak-to-peak chatter during the 2 s | Typically a few thousandths |
| n | Samples used | At 8 kHz, 2 s ≈ 16,000. At 1 kHz ≈ 2,000. If n is tiny, Record Session was not running |
| hyst | Circular hold radius = 3 × rest radius, clamped 0.0004–0.012 | Hall/TMR often 0.001–0.005. ALPS pots often higher |

Then:

1. Keep thumbs off. Raw LX/LY/RX/RY should sit near the center you just measured.
2. Clean values should read **0.0000** on all four axes.
3. Nudge a stick ~1 mm and let go. Clean should return to 0 and stay there (no LSB flicker).
4. Flick to the edge and back. Clean should follow the flick, not smear it.

### 6. Save the rest file

Click **Export rest CSV**. Keep it with the pad’s serial / shell color. Next time you can compare pp and center instead of guessing the stick got noisier.

CSV header: `lx,ly,rx,ry` in normalized −1…1.

---

## Part C — What to do when it looks wrong

**Clean is not zero at rest**
You touched a stick during the 2 s, or inner deadzone on the board is smaller than rest offset. Recapture. If center is > ~0.05, do Marius/GameSir center cal first.

**Clean feels mushy on flicks**
Apply power curve is ON *and* the board already has a curve. Turn the checkbox off. Also check Instant window — if it is 250–1000 Hz you are not at 8 kHz.

**Clean ignores small aim**
Hysteresis is too big (a spike during rest). Recapture. Do not raise inner deadzone to hide this.

**Measured rate is not ~8000**
Wrong USB port, hub, XInput path, or firmware still at 1 kHz. Fix that before you trust hysteresis. Hysteresis from a 250 Hz stream is still valid for noise, but dt for the EMA will be the slow interval.

**n is a few hundred after 2 s**
Record Session was off, or the selected device is not the one sending reports. Go back to Controller Lab.

**Left and right hysteresis differ a lot**
Normal if one mech is quieter. Recapture once. If one side pp is 5–10× the other, that mech is worn or the magnet is off-center.

---

## Part D — Order of operations (cheat sheet)

```text
1. Firmware curve + inner/outer DZ on the pad
2. Rear USB, Raw HID, Record Session
3. Confirm Measured rate is the rate you think you bought
4. Pick the matching preset
5. Power-curve checkbox OFF if the pad already curves
6. Thumbs off → Calibrate rest 2 s
7. Confirm Clean = 0.0000 at rest
8. Flick test
9. Export rest CSV
```

That is the whole calibration. Everything after that is playback of the same stream.
