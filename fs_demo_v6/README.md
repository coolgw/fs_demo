# demov6 Verification Summary (Directory-Supporting Block-Backed Filesystem)

This document describes the design, implementation, and verification steps of the `demov6` filesystem, which introduces full support for subdirectory creation (`mkdir`) and deletion (`rmdir`).

Before starting verification, compile the user-space `mkfs` tool inside `fs_demo_v6`:

```bash
gcc -Wall -o mkfs.demofs mkfs.demofs.c
```

---

## Part 1: Architecture of Subdirectory Support in demov6

Subdirectories in `demov6` are implemented natively using block-backed inodes and standard UNIX directory layout conventions:

### 1. Inode Identification & Type
- Subdirectories are assigned the `S_IFDIR` bitwise flag in their inode mode, and set with operations targeting `demofs_dir_inode_operations` and `demofs_dir_operations`.
- The in-memory link count (`i_nlink`) of a newly created subdirectory is set to `2` to account for:
  1. The parent directory's link pointing to this directory.
  2. The subdirectory's internal `.` directory entry pointing to itself.

### 2. Internal Subdirectory Block Initializer
When a subdirectory is created, `demofs_mkdir` dynamically allocates:
- **1 Inode** from the Inode Bitmap.
- **1 Block** from the Block Bitmap.

The allocated block is initialized immediately with exactly two directory entries:
- **`.`**: Pointing to the new subdirectory's allocated inode ID.
- **`..`**: Pointing to the parent directory's inode ID (`dir->i_ino`).

Additionally, the parent directory's link count is incremented (`inc_nlink(dir)`) to reflect the new `..` back-link.

### 3. Subdirectory Deletion (rmdir)
Subdirectory deletion checks that the directory is empty (only containing `.` and `..` via `simple_empty`) and deletes the parent directory entry. It then decrements links counts appropriately on both the child and parent inodes.

---

## Part 2: Step-by-Step Verification Sequence

You can run our end-to-end automated verification test:

```bash
sudo ./verify_v6_correct.sh
```

This script performs the following sequence:

1. Compiles the user-space formatting utility.
2. Compiles the `demofsv6.ko` kernel module.
3. Formats a 1MB loopback disk image `/tmp/disk_v6.img`.
4. Inserts the `demofsv6.ko` kernel module.
5. Mounts the disk image at `/tmp/test_mount_v6`.
6. Creates a file `/tmp/test_mount_v6/diskfile.txt` (written with text).
7. Creates a new subdirectory `/tmp/test_mount_v6/test_dir`.
8. Creates a nested file inside the subdirectory `/tmp/test_mount_v6/test_dir/nested_file.txt`.
9. Performs an unmount and remount sequence to prove disk persistence.
10. Validates the existence and exact contents of both root files and subdirectory nested files.
11. Verifies deletion by removing the nested file and executing `rmdir /tmp/test_mount_v6/test_dir`.
12. Confirms that directory deletion was fully completed.
