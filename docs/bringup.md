# Bring-up order

The order the board actually came up in, and what each stage needs from
`board.toml`. Reversing this order wastes days: nothing can be observed before
the console works, and nothing can be debugged before touch or the buttons work.

## 0. Console

No UART exists on this board (the debug pads are wired as SPI). The console is
drawn into the framebuffer ABL leaves at `0x9C000000` and mirrored into a
persistent ring in `userdata` so the log survives a hang. Everything after this
depends on being able to read it.

Requires: `[display] fb_base`, `fb_size`; `patches` 0083 (synchronous flush).

## 1. Memory

`/chosen` needs `dram-base`/`dram-size` above every low-bank carve-out, plus a
panic ring at the top of the window. Get this wrong and the kernel hangs before
it can print anything: `check_for_panic_log()` returns 0, `debug_log_init()`
bails twice, and the boot stops right after `kdp_core zlib memory`.

Requires: `[memory]` whole section; the guard in `configs/rmx1971.adt.py`
refuses a window that overlaps a carve-out.

## 2. CPU

Eight `cpu@N` nodes with `compatible = "qcom,kryo470"`. That string is an
IONameMatch key in the SWEET CPU driver — change it and the driver never binds,
leaving the kernel spinning in `ml_wait_max_cpus` after `bootstrap done`.

Requires: `[adt] timebase_frequency`, `virtual_timer_intid`, GIC ranges.

## 3. Storage

UFS host at `0x01D84000` (internal `ufs_mem`, no card slot on this variant).
Root is a journaled HFS+ volume, read/write, built by ios-sweet's
`tools/mkiosroot.sh` and written to a user partition.

Requires: `[storage] ufs_base`, `ufs_size`; boot partition only for flashing.

## 4. Display

IOSurface + IOMobileFramebuffer services, CoreAnimation on the CPU, each frame
copied into the ABL framebuffer. iOS is told it is an iPhone X (1125×2436) and
the copy squashes to the 1080×2340 panel; per-frame cost is what makes the UI
feel like 10 fps.

Requires: `[display]` canvas and framebuffer, `[identity]` panel-facing values.

## 5. Input

Three separate mechanisms, three separate drivers:

| Input | Path | Note |
| --- | --- | --- |
| Touch | Himax HX83112A over GENI SPI, INT gpio | 64 KiB firmware download on first probe, 56-byte event frames |
| Volume | TLMM gpios 24 and 92, active-low | One step per press after 3-frame debounce |
| Power | PM660 PON over SPMI, PPID `0x08` | **Not a gpio.** `PON_RT_STS` bit 0 reads 1 while held |

Requires: `[touch]`, `[buttons]`.

## 6. USB

Last because it is the only subsystem that has to talk to a host to be verified,
and because it needs working storage first (gadget functions, adb). DWC3 is left
in OTG by ABL and refuses the device role; forcing it exposes an endpoint-0
setup timeout. Currently the one open item. See
`docs/evidence/2026-10-27-usb-dwc3.md`.
