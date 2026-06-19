# FS Demo (demofs)

This repository contains a demonstration of a custom Linux Filesystem (`demofs`) across multiple stages of development and debugging.

## Testing `fs_demo_v1` (Unmount Hang Bug)

To clone the repository and run the automated reproduction script for the buggy version `fs_demo_v1` (which hangs on unmount):

```bash
git clone https://github.com/coolgw/fs_demo.git
cd fs_demo/fs_demo_v1/
./reproduce_v1_hang.sh
```
