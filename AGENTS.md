# AGENTS.md

Working notes for anyone (human or model) touching this repository. Read
`README.md`, `board.toml` and `docs/evidence/` before changing a number.

## What lives where

- `board.toml` is the single source of truth for addresses, the DRAM window and
  the boot header. Scripts read it; nothing hardcodes an address twice.
- `patches/xnu/series` only lists patches. The patches themselves live in
  `ios-sweet/xnu/patches/`, because that is what its CI applies.
- `docs/evidence/` is measured data, not prose: register values, log excerpts,
  the command that produced them. New findings go there before they go into
  `board.toml`.

## Hard rules

1. **No Apple artifacts, ever.** `d22ap.adt`, kernel caches, `ios16-root.img`,
   anything `.im4p`/`.ipsw` stays out of git. `.gitignore` covers them; do not
   "just this once" commit one.
2. **No device dumps.** `backup/`, `dtb/`, `persist/` carry IMEI and serials.
3. **No tokens.** A GitHub PAT does not belong in a working tree, a script or a
   commit. Use `GH_TOKEN` in the environment.
4. **Do not touch `recovery`, `modem`, `persist`, `vbmeta`.** Only `boot` is
   ours, and it is written with `fastboot flash boot`: this ABL has no `boot`
   command, so `fastboot boot` returns `unknown command` after the transfer.
5. **Patch numbering continues from upstream.** The next free slot is `0090`.
   Never renumber an existing patch: CI builds are keyed on the series.

## The build loop

```
ios-sweet CI (xnu.yml, macOS runner)  →  kernel.development.sweet
   ↓
scripts/build-boot-image.py           →  out/boot.img
   ↓
scripts/flash-boot.sh                 →  handset
   ↓
screen console / TWRP log ring        →  docs/evidence/
```

`check-board.py --sweet ../ios-sweet` is cheap; run it before every build.

## Traps that have cost time here

- **Hunk counts.** ios-sweet applies patches with `git apply`, so a hand-written
  hunk with a wrong line count is silently skipped. Generate hunks from the real
  file (`old` = count of `' '` + `'-'`, `new` = count of `' '` + `'+'`), or take
  a file from the CI `xnu-buildtree` artifact and diff against it.
- **Line endings.** `sweet_usb.c` is LF. Editing it on Windows and diffing
  against a CRLF copy produces a patch that applies to nothing.
- **`dtb_idx=3`.** ABL takes the base DTB from the FDTs appended after the
  kernel, not from the boot header. A shim image without
  `firmware/stock-appended-dtbs.bin` boots back into fastboot.
- **`_end` padding.** Image size must be `_end`, not the `.bin` length, or
  `.bss` and the stacks do not exist in memory.
- **Absolute relocations.** `start.S` stores the image size as an absolute
  value; C objects must have none, or the shim only works at the address it was
  linked for. The build fails loudly on purpose.
- **There is no `fastboot boot` on this board.** OPPO/realme's ABL keeps
  `download` and `flash` only; `boot` answers `FAILED (remote: 'unknown
  command')`. Every test is a flash of `boot`, which costs one `fastboot flash`
  to undo -- so keep the last known-good image next to the new one.
- **Windows adb.** `adb push` needs `MSYS_NO_PATHCONV=1` under Git Bash, and
  `adb shell "cat > file"` goes through a pty that eats binary data — use
  `adb push` to a temp path then `dd`.
- **CI queued.** A run stuck in `queued` is cancelled and re-dispatched, not
  waited on.

## Boot-args switches

`configs/cmdline.txt` turns subsystems on and off at runtime, which is how each
one was isolated:

| Arg | Effect |
| --- | --- |
| `sweetusb=2` | DWC3 bring-up with verbose logging (`0` = off) |
| `sweetkeys=0` | TLMM volume-key driver off when it fights the touch driver |
| `sweettouch=0` | Himax driver off |
| `sweethimax=1`, `sweetmaxdelay=N` | Himax enable + post-reset settle time |
| `sweetsample=SpringBoard,backboardd,launchd` | Per-process sampler |
| `cpus=8` | Use all eight cores |
| `wdt=-1` | Watchdog off while debugging |

## Open items

1. **DWC3 device mode.** Forced device role now sticks, but `ep0` setup still
   times out. See `docs/evidence/2026-10-27-usb-dwc3.md` for what has been
   ruled out.
2. **Compositor stall.** Going back to an app can leave the screen black for
   minutes; the vsync timer keeps running while the swap counter stops. The
   in-kernel source diag (`0089`) prints the centre pixel to tell "the app drew
   black" from "the wrong surface was copied".
3. **3.09 GiB window.** Built, flashed once, not verified. Watch for a hang in
   `ml_static_mfree` or in the UFS driver's DMA path, both of which would mean a
   carve-out was missed.
4. **Brightness.** Needs a DSI command channel that this display path does not
   have.
