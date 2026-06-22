#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Script to verify directory creation, persistence, and correctness on demofs_v6.
# Run this script as root on the VM.

set -e

MOUNT_DIR="/tmp/test_mount_v6"
DISK_IMG="/tmp/disk_v6.img"
SRC_DIR=$(dirname $(readlink -f "$0"))
MODULE_NAME="demofsv6"

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

echo "=== Step 3: Compiling kernel module (Version 6 - Directory Supported) ==="
make -C /lib/modules/$(uname -r)/build M="$SRC_DIR" modules
echo "demofsv6.ko compiled successfully."

echo "=== Step 4: Creating and Formatting disk image ==="
dd if=/dev/zero of="$DISK_IMG" bs=4096 count=256
"$SRC_DIR/mkfs.demofs" "$DISK_IMG"

echo "=== Step 5: Loading the v6 module ==="
sudo insmod "$SRC_DIR/demofsv6.ko"

echo "=== Step 6: Mounting the block device ==="
mkdir -p "$MOUNT_DIR"
sudo mount -o loop -t demofs_v6 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 7: Performing write, mkdir, and nested write ==="
echo "Demofs Version 6 is Rock Solid!" > "$MOUNT_DIR/diskfile.txt"

echo "Creating new directory 'test_dir'..."
mkdir "$MOUNT_DIR/test_dir"

echo "Creating nested file inside 'test_dir'..."
echo "Nested file content in Demofs v6" > "$MOUNT_DIR/test_dir/nested_file.txt"

echo "Directory details before remount:"
ls -la "$MOUNT_DIR"
ls -la "$MOUNT_DIR/test_dir"

echo "=== Step 8: Performing Unmount & Remount to verify persistence ==="
echo "Unmounting $MOUNT_DIR..."
sudo umount "$MOUNT_DIR"

echo "Mounting $MOUNT_DIR again..."
sudo mount -o loop -t demofs_v6 "$DISK_IMG" "$MOUNT_DIR"

echo "=== Step 9: Verifying correctness of persistent Demofs v6 ==="
echo "Checking directory details after remount:"
ls -la "$MOUNT_DIR"
ls -la "$MOUNT_DIR/test_dir"

echo "Verifying file content preservation:"
cat "$MOUNT_DIR/diskfile.txt"
cat "$MOUNT_DIR/test_dir/nested_file.txt"

# Perform assertions
FILE1_CONTENT=$(cat "$MOUNT_DIR/diskfile.txt")
FILE2_CONTENT=$(cat "$MOUNT_DIR/test_dir/nested_file.txt")

if [ "$FILE1_CONTENT" != "Demofs Version 6 is Rock Solid!" ]; then
    echo "FAILURE: Main file content mismatch!"
    exit 1
fi

if [ "$FILE2_CONTENT" != "Nested file content in Demofs v6" ]; then
    echo "FAILURE: Nested file content mismatch!"
    exit 1
fi

echo "=== Step 10: Unmounting and Running Visual Disk Analyzer ==="
echo "Unmounting $MOUNT_DIR to safely analyze the raw disk image..."
sudo umount "$MOUNT_DIR" || true

# Execute the parent-level analyze_disk.py to verify metadata counts are in sync!
"$SRC_DIR/../analyze_disk.py" "$DISK_IMG"

echo "=== VERIFICATION COMPLETE: SUCCESS ==="
