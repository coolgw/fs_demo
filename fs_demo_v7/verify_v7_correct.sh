#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Script to verify multi-block file support, persistence, and disk layout on demofs_v7.
# Run this script as root on the VM.

set -e

MOUNT_DIR="/tmp/test_mount_v7"
DISK_IMG="/tmp/disk_v7.img"
SRC_DIR=$(dirname $(readlink -f "$0"))
MODULE_NAME="demofsv7"

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

# Delete files
echo "Removing old files..."
rm -f "$DISK_IMG"

echo "=== Step 2: Compiling userspace mkfs utility ==="
gcc -Wall -o "$SRC_DIR/mkfs.demofs" "$SRC_DIR/mkfs.demofs.c"
echo "mkfs.demofs compiled successfully."

echo "=== Step 3: Compiling kernel module (Version 7 - Multi-block File Support) ==="
make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" modules
echo "demofsv7.ko compiled successfully."

echo "=== Step 4: Creating and Formatting disk image ==="
dd if=/dev/zero of="$DISK_IMG" bs=4096 count=256
"$SRC_DIR/mkfs.demofs" "$DISK_IMG"

echo "=== Step 5: Loading the v7 module ==="
sudo insmod "$SRC_DIR/demofsv7.ko"

echo "=== Step 6: Mounting the block device ==="
mkdir -p "$MOUNT_DIR"
sudo mount -o loop -t demofs_v7 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 7: Creating a large file (6000 bytes -> spanning 2 blocks) ==="
# Write exactly 6000 'A' characters (6000 bytes)
python3 -c "print('A' * 6000, end='')" > "$MOUNT_DIR/big_file.txt"

echo "Checking file details on mounted filesystem:"
ls -la "$MOUNT_DIR"
FILE_SIZE=$(stat -c%s "$MOUNT_DIR/big_file.txt")
echo "Created file size: $FILE_SIZE bytes (should be exactly 6000)"

if [ "$FILE_SIZE" -ne 6000 ]; then
    echo "FAILURE: File size is not 6000 bytes!"
    exit 1
fi

echo "=== Step 8: Performing Unmount & Remount to verify persistence ==="
echo "Unmounting $MOUNT_DIR..."
sudo umount "$MOUNT_DIR"

echo "Mounting $MOUNT_DIR again..."
sudo mount -o loop -t demofs_v7 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 9: Verifying correctness of persistent Demofs v7 ==="
echo "Checking file details after remount:"
ls -la "$MOUNT_DIR"

# Verify content preservation
READ_SIZE=$(stat -c%s "$MOUNT_DIR/big_file.txt")
echo "Persisted file size: $READ_SIZE bytes"

if [ "$READ_SIZE" -ne 6000 ]; then
    echo "FAILURE: Persisted file size is not 6000 bytes!"
    exit 1
fi

# Assert content is composed entirely of 'A'
NUM_AS=$(tr -cd 'A' < "$MOUNT_DIR/big_file.txt" | wc -c)
echo "Count of 'A' characters inside big_file.txt: $NUM_AS"

if [ "$NUM_AS" -ne 6000 ]; then
    echo "FAILURE: File content was corrupted or altered!"
    exit 1
fi

echo "SUCCESS: File content is perfectly preserved across remount!"

echo "=== Step 10: Unmounting and Running Visual Disk Analyzer ==="
echo "Unmounting $MOUNT_DIR to safely analyze the raw disk image..."
sudo umount "$MOUNT_DIR" || true

# Execute the parent-level analyze_disk.py to verify metadata counts are in sync!
"$SRC_DIR/../analyze_disk.py" "$DISK_IMG"

echo "=== VERIFICATION COMPLETE: SUCCESS ==="
