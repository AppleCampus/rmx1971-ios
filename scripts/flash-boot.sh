#!/usr/bin/env bash
# Put a boot image on the RMX1971.
#
#   ./flash-boot.sh out/boot-gz.img          # fastboot flash boot (default)
#   ./flash-boot.sh out/boot-gz.img --boot   # fastboot boot, if the ABL allows it
#
# This handset's ABL (OPPO/realme) has no `boot` command: it accepts the
# transfer and then answers `FAILED (remote: 'unknown command')`. Flashing is
# therefore the only path here; `--boot` is kept for boards that do support it.
# Only /dev/block/sde10 is touched; recovery, modem and persist are not.
set -euo pipefail

img="${1:-out/boot.img}"
mode=flash
[ "${2:-}" = "--boot" ] && mode=boot

[ -f "$img" ] || { echo "no such image: $img" >&2; exit 1; }
command -v fastboot >/dev/null || { echo "fastboot not in PATH" >&2; exit 1; }

if ! fastboot devices | grep -q fastboot; then
	echo "no fastboot device. Hold power+vol-down, or 'adb reboot bootloader'." >&2
	exit 1
fi

# A wrong board means a wrong boot header. Ask the bootloader what it is.
product=$(fastboot getvar product 2>&1 | sed -n 's/^product: *//p' | head -1 || true)
if [ -n "$product" ] && [ "${product#RMX1971}" = "$product" ]; then
	echo "warning: bootloader reports '$product', not RMX1971" >&2
fi

size=$(stat -c %s "$img" 2>/dev/null || stat -f %z "$img")
echo "$img: $size bytes"

if [ "$mode" = boot ]; then
	fastboot boot "$img"
else
	echo "This overwrites the boot partition (/dev/block/sde10)."
	read -r -p "Type 'yes' to continue: " answer
	[ "$answer" = yes ] || { echo "aborted"; exit 1; }
	fastboot flash boot "$img"
	fastboot reboot
fi

echo
echo "watch the screen: the shim draws its console into the ABL framebuffer."
echo "if it hangs, hold power+vol-down for 10s and pull the console ring from TWRP."
