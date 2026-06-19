#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Script to reproduce the demofs_v2 Transient Inode Bug (drop_inode returns 1) on the VM.
# Run this script as root on the target VM.

set -e

MOUNT_DIR="/tmp/test_mount_v2"
DISK_IMG="/tmp/disk_v2.img"
SRC_DIR=$(dirname $(readlink -f "$0"))
MODULE_NAME="demofsv2"

echo "=== Step 1: Cleaning up any old test files and mounts ==="
# Unmount if mounted
if mountpoint -q "$MOUNT_DIR" 2>/dev/null || grep -q "$MOUNT_DIR" /proc/mounts; then
    echo "Mount point busy or active, performing unmount..."
    sudo umount -l "$MOUNT_DIR" || true
fi

# Remove module if loaded
if lsmod | grep -q "$MODULE_NAME"; then
    echo "Unloading old module..."
    sudo rmmod "$MODULE_NAME" || true
fi

# Delete files and clean build directory
echo "Removing old files..."
rm -f "$DISK_IMG"
rm -f "$SRC_DIR/mkfs.demofs"
if [ -d "$SRC_DIR" ]; then
    (cd "$SRC_DIR" && make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" clean || true)
fi

echo "=== Step 2: Compiling userspace mkfs utility ==="
gcc -Wall -o "$SRC_DIR/mkfs.demofs" "$SRC_DIR/mkfs.demofs.c"
echo "mkfs.demofs compiled successfully."

echo "=== Step 3: Compiling kernel module (Version 2 with drop_inode=1 bug) ==="
make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" modules
echo "demofsv2.ko compiled successfully."

echo "=== Step 4: Creating and Formatting disk image ==="
dd if=/dev/zero of="$DISK_IMG" bs=4096 count=256
"$SRC_DIR/mkfs.demofs" "$DISK_IMG"

echo "=== Step 5: Loading the buggy module ==="
sudo insmod "$SRC_DIR/demofsv2.ko"

echo "=== Step 6: Mounting the block device ==="
mkdir -p "$MOUNT_DIR"
sudo mount -o loop -t demofs_v2 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 7: Writing data to a file ==="
echo "Transient Inode Bug!" > "$MOUNT_DIR/diskfile.txt"
echo "File created and written successfully. Directory details before remount:"
ls -la "$MOUNT_DIR"
echo "File content before remount:"
cat "$MOUNT_DIR/diskfile.txt"

echo "=== Step 8: Unmounting and Remounting ==="
echo "Unmounting $MOUNT_DIR..."
sudo umount "$MOUNT_DIR"

echo "Mounting $MOUNT_DIR again..."
sudo mount -o loop -t demofs_v2 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 9: Verifying the Transient Inode Bug (Size 0) ==="
echo "Checking directory details after remount:"
ls -la "$MOUNT_DIR"

echo "File content (should be empty/lost):"
cat "$MOUNT_DIR/diskfile.txt"

echo "=== REPRODUCTION COMPLETE ==="
if [ ! -s "$MOUNT_DIR/diskfile.txt" ]; then
    echo "SUCCESS: Transient Inode Bug successfully reproduced (file is 0 bytes on remount)."
else
    echo "FAILURE: File is not 0 bytes. Check if VM kernel behaves differently."
fi

echo "=== Step 10: Running Visual Disk Analyzer ==="
echo "Unmounting $MOUNT_DIR to safely analyze the raw disk image..."
sudo umount "$MOUNT_DIR" || true
"$SRC_DIR/../analyze_disk.py" "$DISK_IMG"

