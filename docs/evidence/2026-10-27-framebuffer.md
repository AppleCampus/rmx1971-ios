# 2026-10-27 — Framebuffer: alive timer, stalled swaps

**Symptom.** Pull down Control Center and it works. Drag AssistiveTouch and it
follows. But leave settings, or go back to the home screen, and the middle of the
display stays black — sometimes for minutes — before the UI reappears. Brightness
also does nothing.

## The path

`SweetFramebuffer.cpp` provides the IOSurface and IOMobileFramebuffer services.
CoreAnimation composites in software; each presented surface is copied by the CPU
into the framebuffer ABL left at `0x9C000000`:

- canvas 1125×2436 (what iOS thinks the panel is), squashed into 1080×2340;
- one full frame = 1125 × 2436 × 4 ≈ **10.9 MiB**;
- a vsync timer runs at 60 Hz, but a frame is only flushed every `fFlushTicks`,
  and `sweetfb_hz` defaults to **10 Hz** → ≈ 110 MB/s of `memcpy`.

## What the heartbeat proved

Patch `0087` prints, every 600 vsyncs, the vsync count, the number of swaps that
actually happened and whether the framebuffer was dirty:

```
SWEET FB: heartbeat vsync 33600 swaps 994 dirty 1 poweroff 0
SWEET FB: heartbeat vsync 34200 swaps 994 dirty 1 poweroff 0
```

The vsync counter climbs while the swap counter is frozen. **The timer is alive;
the compositing/copy side is stalled.** That single line eliminated the timer, the
interrupt path and the display power state (patch `0079`) from suspicion, and
moved the investigation into `swap()`.

Two things were also wrong with the instrumentation itself:

- `swap()` took `IOLockSleep` while flushing, so a slow copy blocked the vsync
  thread and made the stall worse. `0087` switches to `IOLockTryLock` — if the
  lock is held, the frame is dropped instead of queued. Dropping is correct here:
  the next vsync will present a full frame anyway.
- An `else` sat on the wrong branch, so "swap record allocation failed" was
  printed on every non-flushing vsync. `0089` replaces the per-frame
  `IOMallocData` with one static record buffer, which removes the spam and the
  allocation from the hot path.

## Telling "the app drew black" from "we copied the wrong surface"

`0089` classifies every skipped layer and prints the colour of the centre pixel
of the surface it is about to present:

```
SWEET FB: src 0x... centre 0x00000000
SWEET FB: skip: no surface
SWEET FB: skip: default surface
SWEET FB: skip: out of bounds 0x...,0x...
```

Reading it:

| Observation | Meaning |
| --- | --- |
| swaps climbing, screen black, `centre 0x00000000` | SpringBoard/backboardd genuinely rendered black — app-side, look at the app |
| swaps climbing, screen black, `centre` non-zero | the wrong surface or the wrong offset is being copied |
| swaps frozen, `dirty 1` | `swap()` never returns — blocked lock, or the copy is stuck |
| repeated `skip:` lines for one surface | layer geometry/bounds bug in the compositor side |

The earlier "eight allocation failures per second" were this instrumentation, not
the driver: worth remembering before reading a log as a fault.

## Brightness

The panel is AMOLED and its real brightness is set with a **DSI command**. This
display path has no DSI channel at all — the kernel only owns the ABL
framebuffer — so the brightness slider moving without effect is expected, not a
bug in the copy path. Fixing it means writing a DSI command interface
(`sweet_dsi`), which is a separate bring-up from compositing.

## Next

- Dirty-rectangle tracking instead of a full-frame copy: the order-of-magnitude
  win, and the one that would make the UI usable at 30 Hz.
- `sweetfb_hz=30` is already possible by rebuilding the boot image, but it costs
  ~330 MB/s of CPU copy — worth measuring only after dirty rects land.
- Move the copy off the vsync thread so a slow frame cannot delay the next tick.
