#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Script to reproduce the demofs_v1 Unmount Hang (Folio Lock Leak) on the VM.
# Run this script as root on the target VM.

set -e

MOUNT_DIR="/tmp/test_mount_v1"
DISK_IMG="/tmp/disk_v1.img"
SRC_DIR=$(dirname $(readlink -f "$0"))
MODULE_NAME="demofsv1"

echo "=== Step 1: Cleaning up any old test files and mounts ==="
# Unmount if mounted (use lazy umount to detach stuck mounts)
if mountpoint -q "$MOUNT_DIR" 2>/dev/null || grep -q "$MOUNT_DIR" /proc/mounts; then
    echo "Mount point busy or active, performing lazy unmount..."
    sudo umount -l "$MOUNT_DIR" || true
fi

# Remove module if loaded
if lsmod | grep -q "$MODULE_NAME"; then
    echo "Unloading old module..."
    sudo rmmod "$MODULE_NAME" || true
fi

# Delete files and clean build directory on VM
echo "Removing old files..."
rm -f "$DISK_IMG"
rm -f "$SRC_DIR/mkfs.demofs"
if [ -d "$SRC_DIR" ]; then
    (cd "$SRC_DIR" && make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" clean || true)
fi

echo "=== Step 2: Compiling userspace mkfs utility ==="
gcc -Wall -o "$SRC_DIR/mkfs.demofs" "$SRC_DIR/mkfs.demofs.c"
echo "mkfs.demofs compiled successfully."

echo "=== Step 3: Compiling kernel module (Version 1 with leak) ==="
make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" modules
echo "demofsv1.ko compiled successfully."

echo "=== Step 4: Creating and Formatting disk image ==="
dd if=/dev/zero of="$DISK_IMG" bs=4096 count=256
"$SRC_DIR/mkfs.demofs" "$DISK_IMG"

echo "=== Step 5: Loading the buggy module ==="
sudo insmod "$SRC_DIR/demofsv1.ko"

echo "=== Step 6: Mounting the block device ==="
mkdir -p "$MOUNT_DIR"
sudo mount -o loop -t demofs_v1 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 7: Writing data to leak the folio lock ==="
echo "Deliberate Folio Lock Leak!" > "$MOUNT_DIR/diskfile.txt"
echo "File created and written successfully. Active files:"
ls -la "$MOUNT_DIR"

echo "=== Step 8: Triggering the Unmount Hang ==="
echo "Running umount in the background (will hang)..."
sudo umount "$MOUNT_DIR" &
sleep 2

echo "Checking umount process state (D means Uninterruptible Sleep):"
ps -aux | grep -E "umount|$MOUNT_DIR" | grep -v grep || true

echo "=== Step 9: Triggering SysRq Blocked State dump ==="
echo "Dumping blocked tasks' stacks to kernel logs..."
echo w | sudo tee /proc/sysrq-trigger > /dev/null

echo "=== REPRODUCTION COMPLETE ==="
echo "To view the exact deadlock call stack, execute:"
echo "  dmesg | tail -n 80"
