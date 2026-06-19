#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Script to verify the fully correct and synchronized demofs_v5 filesystem.
# Run this script as root on the VM.

set -e

MOUNT_DIR="/tmp/test_mount_v5"
DISK_IMG="/tmp/disk_v5.img"
SRC_DIR=$(dirname $(readlink -f "$0"))
MODULE_NAME="demofsv5"

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

echo "=== Step 3: Compiling kernel module (Version 5 - Superblock Synchronized) ==="
make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" modules
echo "demofsv5.ko compiled successfully."

echo "=== Step 4: Creating and Formatting disk image ==="
dd if=/dev/zero of="$DISK_IMG" bs=4096 count=256
"$SRC_DIR/mkfs.demofs" "$DISK_IMG"

echo "=== Step 5: Loading the corrected module ==="
sudo insmod "$SRC_DIR/demofsv5.ko"

echo "=== Step 6: Mounting the block device ==="
mkdir -p "$MOUNT_DIR"
sudo mount -o loop -t demofs_v5 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 7: Writing data to a file ==="
echo "Demofs Version 5 is Perfectly Synchronized!" > "$MOUNT_DIR/diskfile.txt"
echo "File created and written successfully. Directory details before remount:"
ls -la "$MOUNT_DIR"
echo "File content before remount:"
cat "$MOUNT_DIR/diskfile.txt"

echo "=== Step 8: Unmounting and Remounting ==="
echo "Unmounting $MOUNT_DIR (flushing dirty pages and metadata safely)..."
sudo umount "$MOUNT_DIR"

echo "Mounting $MOUNT_DIR again..."
sudo mount -o loop -t demofs_v5 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 9: Verifying correctness of Demofs v5 ==="
echo "Checking directory details after remount:"
ls -la "$MOUNT_DIR"

echo "File content (should be preserved perfectly):"
cat "$MOUNT_DIR/diskfile.txt"

echo "=== VERIFICATION COMPLETE ==="
if [ -s "$MOUNT_DIR/diskfile.txt" ] && [ "$(cat "$MOUNT_DIR/diskfile.txt")" = "Demofs Version 5 is Perfectly Synchronized!" ]; then
    echo "SUCCESS: Demofs v5 is fully functional, correct, and persistent!"
else
    echo "FAILURE: Demofs v5 did not preserve written data."
fi

echo "=== Step 10: Running Visual Disk Analyzer ==="
echo "Unmounting $MOUNT_DIR to safely analyze the raw disk image..."
sudo umount "$MOUNT_DIR" || true

# Execute the parent-level analyze_disk.py to verify metadata counts are in sync!
"$SRC_DIR/../analyze_disk.py" "$DISK_IMG"
