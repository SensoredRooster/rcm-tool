# RCM Tool

Windows controller integrity prototype. It **reads** stick axes and HID timing. It does **not** write to the controller, inject into a game, or flash firmware.

Repository: https://github.com/SensoredRooster/rcm-tool

## Honest Before / After

Same PC, same protocol, same hands. Only the **hardware path** changes.

1. **Before** — pad straight to the PC. No pass-through box.
2. Unplug. Put the suspected device in the cable path.
3. **After** — same Neutral hold or same Guided moves.

Pair-delta plus HID signal metrics compare rest HF RMS, sample rate, inter-sample jitter, and how many distinct axis levels showed up. That is a **change detector**, not a cheat verdict. An idle box can look identical at rest.

**After noise** and **Injected** protocols are tool self-tests only. Files tagged `INJECTED_` are not a pad screen.

## 1. Install once

```powershell
git clone https://github.com/SensoredRooster/rcm-tool.git
cd rcm-tool
python -m pip install -r requirements.txt
python apply_all.py
python -m unittest discover -v
python rcm_tool.py
```

Later: **RCM Tool - Update and Start.bat**.

If apply says `could not find block`:

```powershell
git checkout -- controller_integrity.py
python apply_all.py
```

## 2. UI

Portal navy (`#070B14`) with cyan labels and blue primary (`#3B82F6`), same language as Tester Share. Theme is `rcm_theme.py`. ttk still owns native combo chrome.

## 3. Session order

1. Tool check: Injected HF sine / slow sine, thumbs off. `INJECTED_` files.
2. Neutral Before + After with hardware out, then hardware in.
3. Guided pair the same way.

## 4. Pair delta

`unchanged` / `increased_at_rest` / `increased_high_freq` / `unsupported`.
Reports also include `hid_signal_metrics` and `hidSignalDelta` (rate, timing jitter, unique levels).

## 5. RC filter

`tau = 0.05 s`. HF RMS = RMS(raw minus trend).

## 6. After a run

JSON writes itself. **Submit Reports.bat**, type YES. Do not submit `INJECTED_` files as a pad screen.

## Support and Tester Share

- Support worker: `https://rcm-tool-support.sensoredrooster-com.workers.dev`
- Tester Share: `https://rcm-tool-share.sensoredrooster-com.workers.dev`

See [docs/SUPPORT.md](docs/SUPPORT.md) and [docs/TESTER_SHARE.md](docs/TESTER_SHARE.md).
