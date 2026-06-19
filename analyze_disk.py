#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Visual Analysis Tool for demofs Disk Images with raw byte annotations.
# Copyright (C) 2026 Gemini CLI

import sys
import os
import struct
import stat

# Constants
DEMOFS_MAGIC = 0xdeeb00ff
BLOCK_SIZE = 4096
MAX_INODES = 32
N_BLOCKS = 12

# ANSI colors
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

def format_mode(mode):
    """Translates numeric file mode to standard drwxr-xr-x string."""
    if stat.S_ISDIR(mode):
        type_char = 'd'
    elif stat.S_ISREG(mode):
        type_char = '-'
    else:
        type_char = '?'
    
    perms = []
    # User
    perms.append('r' if mode & stat.S_IRUSR else '-')
    perms.append('w' if mode & stat.S_IWUSR else '-')
    perms.append('x' if mode & stat.S_IXUSR else '-')
    # Group
    perms.append('r' if mode & stat.S_IRGRP else '-')
    perms.append('w' if mode & stat.S_IWGRP else '-')
    perms.append('x' if mode & stat.S_IXGRP else '-')
    # Others
    perms.append('r' if mode & stat.S_IROTH else '-')
    perms.append('w' if mode & stat.S_IWOTH else '-')
    perms.append('x' if mode & stat.S_IXOTH else '-')
    
    return type_char + "".join(perms)

def hex_dump(data, start_offset):
    """Generates a standard canonical hex dump format (hexdump -C style)."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        # Format hex representations (8 bytes separated by double space)
        hex_part_1 = " ".join(f"{b:02x}" for b in chunk[:8])
        hex_part_2 = " ".join(f"{b:02x}" for b in chunk[8:])
        hex_str = hex_part_1
        if len(chunk) > 8:
            hex_str += "  " + hex_part_2
            
        # Standard padding to keep ASCII column aligned
        padding_len = 49 - len(hex_str)
        if padding_len > 0:
            hex_str += " " * padding_len
            
        # ASCII representation
        ascii_chars = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
        lines.append(f"  0x{start_offset + i:04x}:  {hex_str}  |{ascii_chars}|")
    return "\n".join(lines)

def analyze(disk_path):
    if not os.path.exists(disk_path):
        print(f"{RED}{BOLD}Error:{RESET} Disk image file '{disk_path}' does not exist.")
        sys.exit(1)
        
    file_size = os.path.getsize(disk_path)
    total_blocks = file_size // BLOCK_SIZE
    
    print(f"{BOLD}{BLUE}======================================================================{RESET}")
    print(f"      {BOLD}{GREEN}demofs Disk Image Visual Analyzer{RESET}")
    print(f"{BOLD}{BLUE}======================================================================{RESET}")
    print(f"Analyzing File:     {YELLOW}{disk_path}{RESET}")
    print(f"Total File Size:    {YELLOW}{file_size} bytes{RESET}")
    print(f"Block Size:         {YELLOW}{BLOCK_SIZE} bytes{RESET}")
    print(f"Calculated Blocks:  {YELLOW}{total_blocks}{RESET}")
    print()

    with open(disk_path, "rb") as f:
        # First, read and unpack the basic superblock fields to enable dynamic parsing
        f.seek(0)
        sb_data = f.read(24)
        if len(sb_data) < 24:
            print(f"{RED}Error: Cannot read superblock (file too small).{RESET}")
            return
            
        magic, block_size, blocks_count, inodes_count, free_blocks, free_inodes = struct.unpack("<6I", sb_data)
        
        if magic != DEMOFS_MAGIC:
            print(f"{RED}Error: Superblock magic mismatch. This is not a valid demofs volume.{RESET}")
            return

        # --- 0. Raw Disk Metadata Blocks Dump (Hex View with dynamic mapping) ---
        print(f"{BOLD}{CYAN}[0] Raw Disk Metadata Blocks Dump with Field Mapping (Hex View){RESET}")
        print()
        
        # Superblock (Block 0) - first 64 bytes
        f.seek(0)
        sb_bytes = f.read(64)
        magic_hex = " ".join(f"{b:02x}" for b in sb_bytes[0:4])
        bs_hex = " ".join(f"{b:02x}" for b in sb_bytes[4:8])
        bc_hex = " ".join(f"{b:02x}" for b in sb_bytes[8:12])
        ic_hex = " ".join(f"{b:02x}" for b in sb_bytes[12:16])
        fb_hex = " ".join(f"{b:02x}" for b in sb_bytes[16:20])
        fi_hex = " ".join(f"{b:02x}" for b in sb_bytes[20:24])
        
        print(f"  {BOLD}{YELLOW}Block 0 (Superblock) | Byte Offset 0x0000 (first 64 bytes):{RESET}")
        print(hex_dump(sb_bytes, 0))
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
        print(f"    ├─ {BOLD}0x0000-0x0003{RESET} [{magic_hex}]: {CYAN}magic number (0x{magic:08x}){RESET}")
        print(f"    ├─ {BOLD}0x0004-0x0007{RESET} [{bs_hex}]: {CYAN}block_size ({block_size} bytes){RESET}")
        print(f"    ├─ {BOLD}0x0008-0x000b{RESET} [{bc_hex}]: {CYAN}blocks_count ({blocks_count} blocks){RESET}")
        print(f"    ├─ {BOLD}0x000c-0x000f{RESET} [{ic_hex}]: {CYAN}inodes_count ({inodes_count} inodes){RESET}")
        print(f"    ├─ {BOLD}0x0010-0x0013{RESET} [{fb_hex}]: {CYAN}free_blocks ({free_blocks} blocks){RESET}")
        print(f"    └─ {BOLD}0x0014-0x0017{RESET} [{fi_hex}]: {CYAN}free_inodes ({free_inodes} inodes){RESET}")
        print()
        
        # Block Bitmap (Block 1) - first 16 bytes
        f.seek(1 * BLOCK_SIZE)
        bb_bytes = f.read(16)
        b1_byte = bb_bytes[0]
        allocated_blocks = []
        for b in range(8):
            if b1_byte & (1 << b):
                allocated_blocks.append(str(b))
        blocks_str = ", ".join(allocated_blocks)
        
        print(f"  {BOLD}{YELLOW}Block 1 (Block Bitmap) | Byte Offset 0x1000 (first 16 bytes):{RESET}")
        print(hex_dump(bb_bytes, 1 * BLOCK_SIZE))
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
        print(f"    └─ {BOLD}0x1000{RESET}           [{b1_byte:02x}]: {CYAN}block allocation mask (0b{b1_byte:08b} in binary){RESET}")
        print(f"                           -> Physical block(s) {blocks_str} are currently allocated.")
        print()
        
        # Inode Bitmap (Block 2) - first 16 bytes
        f.seek(2 * BLOCK_SIZE)
        ib_bytes = f.read(16)
        b2_byte = ib_bytes[0]
        allocated_inodes_list = []
        for b in range(8):
            if b2_byte & (1 << b):
                if b == 0:
                    allocated_inodes_list.append("0 (Reserved)")
                elif b == 1:
                    allocated_inodes_list.append("1 (Root Dir)")
                else:
                    allocated_inodes_list.append(str(b))
        inodes_str = ", ".join(allocated_inodes_list)
        
        print(f"  {BOLD}{YELLOW}Block 2 (Inode Bitmap) | Byte Offset 0x2000 (first 16 bytes):{RESET}")
        print(hex_dump(ib_bytes, 2 * BLOCK_SIZE))
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
        print(f"    └─ {BOLD}0x2000{RESET}           [{b2_byte:02x}]: {CYAN}inode allocation mask (0b{b2_byte:08b} in binary){RESET}")
        print(f"                           -> Inode slot(s) {inodes_str} are currently allocated.")
        print()
        
        # Inode Table (Block 3) - first 192 bytes (covers Inodes 0, 1, and 2)
        f.seek(3 * BLOCK_SIZE)
        it_bytes = f.read(192)
        print(f"  {BOLD}{YELLOW}Block 3 (Inode Table) | Byte Offset 0x3000 (first 192 bytes):{RESET}")
        print(hex_dump(it_bytes, 3 * BLOCK_SIZE))
        
        # Parse Inode 1
        ino1_bytes = it_bytes[64:128]
        mode1, uid1, gid1, lc1, size1, blocks1, *bp1 = struct.unpack("<4H2I12I", ino1_bytes)
        mode1_hex = " ".join(f"{b:02x}" for b in ino1_bytes[0:2])
        size1_hex = " ".join(f"{b:02x}" for b in ino1_bytes[8:12])
        blocks1_hex = " ".join(f"{b:02x}" for b in ino1_bytes[12:16])
        bp1_hex = " ".join(f"{b:02x}" for b in ino1_bytes[16:20])
        
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation (for Inode 1 [Root Directory] starting at 0x3040):{RESET}")
        print(f"    ├─ {BOLD}0x3040-0x3041{RESET} [{mode1_hex}]:       {CYAN}mode (0x{mode1:04x} -> {format_mode(mode1)} directory){RESET}")
        print(f"    ├─ {BOLD}0x3048-0x304b{RESET} [{size1_hex}]: {CYAN}size ({size1} bytes){RESET}")
        print(f"    ├─ {BOLD}0x304c-0x304f{RESET} [{blocks1_hex}]: {CYAN}blocks ({blocks1} blocks allocated){RESET}")
        print(f"    └─ {BOLD}0x3050-0x3053{RESET} [{bp1_hex}]: {CYAN}block[0] (Direct Pointer 0 -> Block {bp1[0]} holds contents){RESET}")
        
        # Determine active inodes
        allocated_inodes_set = []
        for i in range(MAX_INODES):
            byte_idx = i // 8
            bit_idx = i % 8
            if (ib_bytes[byte_idx] & (1 << bit_idx)) != 0:
                allocated_inodes_set.append(i)
                
        # Parse Inode 2 dynamically if allocated
        if 2 in allocated_inodes_set:
            ino2_bytes = it_bytes[128:192]
            mode2, uid2, gid2, lc2, size2, blocks2, *bp2 = struct.unpack("<4H2I12I", ino2_bytes)
            mode2_hex = " ".join(f"{b:02x}" for b in ino2_bytes[0:2])
            size2_hex = " ".join(f"{b:02x}" for b in ino2_bytes[8:12])
            blocks2_hex = " ".join(f"{b:02x}" for b in ino2_bytes[12:16])
            bp2_hex = " ".join(f"{b:02x}" for b in ino2_bytes[16:20])
            
            print(f"  {BOLD}{MAGENTA}Field Mapping Annotation (for Inode 2 starting at 0x3080):{RESET}")
            print(f"    ├─ {BOLD}0x3080-0x3081{RESET} [{mode2_hex}]:       {CYAN}mode (0x{mode2:04x} -> {format_mode(mode2)} regular file){RESET}")
            print(f"    ├─ {BOLD}0x3088-0x308b{RESET} [{size2_hex}]: {CYAN}size ({size2} bytes){RESET}")
            print(f"    ├─ {BOLD}0x308c-0x308f{RESET} [{blocks2_hex}]: {CYAN}blocks ({blocks2} blocks allocated){RESET}")
            print(f"    └─ {BOLD}0x3090-0x3093{RESET} [{bp2_hex}]: {CYAN}block[0] (Direct Pointer 0 -> Block {bp2[0]} holds file contents){RESET}")
        print()
        
        # Root Directory Data (Block 4) - first 192 bytes (covers entries ., .., and new file)
        f.seek(4 * BLOCK_SIZE)
        dir_bytes = f.read(192)
        print(f"  {BOLD}{YELLOW}Block 4 (Root Dir Data Block) | Byte Offset 0x4000 (first 192 bytes):{RESET}")
        print(hex_dump(dir_bytes, 4 * BLOCK_SIZE))
        
        # Parse Entry 0
        e0_ino, e0_name_b = struct.unpack("<I60s", dir_bytes[0:64])
        e0_name = e0_name_b.decode('utf-8', errors='ignore').split('\x00')[0]
        e0_ino_hex = " ".join(f"{b:02x}" for b in dir_bytes[0:4])
        e0_name_hex = " ".join(f"{b:02x}" for b in dir_bytes[4:8])
        
        # Parse Entry 1
        e1_ino, e1_name_b = struct.unpack("<I60s", dir_bytes[64:128])
        e1_name = e1_name_b.decode('utf-8', errors='ignore').split('\x00')[0]
        e1_ino_hex = " ".join(f"{b:02x}" for b in dir_bytes[64:68])
        e1_name_hex = " ".join(f"{b:02x}" for b in dir_bytes[68:72])
        
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
        print(f"    ├─ {BOLD}Entry 0 ('{e0_name}' | Offset 0x4000-0x403f):{RESET}")
        print(f"    │  ├─ {BOLD}0x4000-0x4003{RESET} [{e0_ino_hex}]: {CYAN}inode number (Inode {e0_ino}){RESET}")
        print(f"    │  └─ {BOLD}0x4004-0x4007{RESET} [{e0_name_hex}...]: {CYAN}entry name (\"{e0_name}\"){RESET}")
        print(f"    ├─ {BOLD}Entry 1 ('{e1_name}' | Offset 0x4040-0x407f):{RESET}")
        print(f"    │  ├─ {BOLD}0x4040-0x4043{RESET} [{e1_ino_hex}]: {CYAN}inode number (Inode {e1_ino}){RESET}")
        print(f"    │  └─ {BOLD}0x4044-0x4047{RESET} [{e1_name_hex}...]: {CYAN}entry name (\"{e1_name}\"){RESET}")
        
        # Check if Entry 2 is active dynamically
        e2_ino, e2_name_b = struct.unpack("<I60s", dir_bytes[128:192])
        if e2_ino > 0:
            e2_name = e2_name_b.decode('utf-8', errors='ignore').split('\x00')[0]
            e2_ino_hex = " ".join(f"{b:02x}" for b in dir_bytes[128:132])
            e2_name_hex = " ".join(f"{b:02x}" for b in dir_bytes[132:136])
            print(f"    └─ {BOLD}Entry 2 ('{e2_name}' | Offset 0x4080-0x40bf):{RESET}")
            print(f"       ├─ {BOLD}0x4080-0x4083{RESET} [{e2_ino_hex}]: {CYAN}inode number (Inode {e2_ino}){RESET}")
            print(f"       └─ {BOLD}0x4084-0x4087{RESET} [{e2_name_hex}...]: {CYAN}entry name (\"{e2_name}\"){RESET}")
        print()
        
        # Block 5 (File Data block) - first 16 bytes (read dynamically based on actual file size)
        if file_size >= 6 * BLOCK_SIZE:
            f.seek(5 * BLOCK_SIZE)
            b5_data = f.read(16)
            
            # Find the printable ascii string length in b5_data dynamically
            content_len = 0
            for b in b5_data:
                if b == 0 or b < 32 or b > 126:
                    break
                content_len += 1
            
            hex_bytes_str = " ".join(f"{b:02x}" for b in b5_data[:content_len]) if content_len > 0 else "00"
            ascii_str = b5_data[:content_len].decode('utf-8', errors='ignore') if content_len > 0 else ""
            
            print(f"  {BOLD}{YELLOW}Block 5 (File Data block) | Byte Offset 0x5000 (first 16 bytes):{RESET}")
            print(hex_dump(b5_data, 5 * BLOCK_SIZE))
            print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
            if content_len > 0:
                print(f"    └─ {BOLD}0x5000-0x{0x5000 + content_len - 1:04x}{RESET} [{hex_bytes_str}]: {CYAN}raw file content data ('{ascii_str}' ASCII string){RESET}")
            else:
                print(f"    └─ {BOLD}0x5000{RESET}: {CYAN}empty file content block or non-printable ASCII content{RESET}")
            print()
            
        print("-" * 70)
        print()

        # --- 1. Read Superblock (Block 0) ---
        f.seek(0)
        sb_data = f.read(24) # Read first 24 bytes
        magic, block_size, blocks_count, inodes_count, free_blocks, free_inodes = struct.unpack("<6I", sb_data)
        
        print(f"{BOLD}{CYAN}[1] Superblock Information (Block 0 | Offset 0x0000){RESET}")
        print(f"  Magic Number:     {GREEN}0x{magic:08x}{RESET} " + (f"({GREEN}VALID{RESET})" if magic == DEMOFS_MAGIC else f"({RED}INVALID{RESET})"))
        print(f"  Block Size:       {GREEN}{block_size} bytes{RESET}")
        print(f"  Blocks Count:     {GREEN}{blocks_count}{RESET}")
        print(f"  Inodes Count:     {GREEN}{inodes_count}{RESET}")
        print(f"  Free Blocks:      {GREEN}{free_blocks}{RESET}")
        print(f"  Free Inodes:      {GREEN}{free_inodes}{RESET}")
        print()

        # --- 2. Read Block Bitmap (Block 1) ---
        f.seek(1 * BLOCK_SIZE)
        bb_data = f.read(BLOCK_SIZE)
        
        # Render Visual Block Grid
        print(f"{BOLD}{CYAN}[2] Block Allocation Grid (Block 1 | Offset 0x1000){RESET}")
        print(f"  Each character represents a 4KB block. Total blocks: {blocks_count}")
        print("  Legend: " + f"{RED}■{RESET} Metadata/Reserved  " + f"{YELLOW}■{RESET} Allocated Data  " + f"{GREEN}·{RESET} Free Space")
        print("  " + "-" * 40)
        
        grid_rows = 16
        grid_cols = 16
        allocated_blocks_list = []
        
        for r in range(grid_rows):
            line_chars = []
            for c in range(grid_cols):
                b_num = r * grid_cols + c
                if b_num >= blocks_count:
                    break
                    
                byte_idx = b_num // 8
                bit_idx = b_num % 8
                is_allocated = (bb_data[byte_idx] & (1 << bit_idx)) != 0
                
                if is_allocated:
                    allocated_blocks_list.append(b_num)
                    if b_num <= 3:
                        line_chars.append(f"{RED}■{RESET}") # Metadata
                    elif b_num == 4:
                        line_chars.append(f"{RED}■{RESET}") # Root dir block
                    else:
                        line_chars.append(f"{YELLOW}■{RESET}") # Allocated data block
                else:
                    line_chars.append(f"{GREEN}·{RESET}")
            
            if line_chars:
                offset_lbl = f"  Blocks {r*grid_cols:03d}-{min((r+1)*grid_cols-1, blocks_count-1):03d}: "
                print(offset_lbl + " ".join(line_chars))
        print()

        # --- 3. Read Inode Bitmap (Block 2) ---
        f.seek(2 * BLOCK_SIZE)
        ib_data = f.read(BLOCK_SIZE)
        allocated_inodes = []
        for i in range(MAX_INODES):
            byte_idx = i // 8
            bit_idx = i % 8
            if (ib_data[byte_idx] & (1 << bit_idx)) != 0:
                allocated_inodes.append(i)
                
        print(f"{BOLD}{CYAN}[3] Inode Allocation Bitmap (Block 2 | Offset 0x2000){RESET}")
        inode_chars = []
        for i in range(MAX_INODES):
            if i in allocated_inodes:
                if i == 0:
                    inode_chars.append(f"{RED}0{RESET}") # Reserved
                elif i == 1:
                    inode_chars.append(f"{MAGENTA}R{RESET}") # Root Directory
                else:
                    inode_chars.append(f"{YELLOW}{i}{RESET}")
            else:
                inode_chars.append(f"{GREEN}·{RESET}")
        print("  Inode Slot Grid: " + " ".join(inode_chars))
        print(f"  Active Inode IDs: {YELLOW}{[x for x in allocated_inodes if x > 0]}{RESET}")
        print()

        # --- 4. Read Inode Table (Block 3) & directory items ---
        f.seek(3 * BLOCK_SIZE)
        it_data = f.read(BLOCK_SIZE)
        
        print(f"{BOLD}{CYAN}[4] Decoded Inode Table (Block 3 | Offset 0x3000){RESET}")
        
        decoded_inodes = {}
        active_user_inodes = [x for x in allocated_inodes if x > 0]
        
        for ino in active_user_inodes:
            ino_offset = ino * 64
            ino_bytes = it_data[ino_offset : ino_offset + 64]
            
            mode, uid, gid, links_count, size, blocks, *block_pointers = struct.unpack("<4H2I12I", ino_bytes)
            
            decoded_inodes[ino] = {
                'mode': mode,
                'uid': uid,
                'gid': gid,
                'links_count': links_count,
                'size': size,
                'blocks': blocks,
                'block_pointers': block_pointers
            }
            
            type_str = "Directory" if stat.S_ISDIR(mode) else "Regular File"
            print(f"  {BOLD}Inode {ino} ({type_str}){RESET}:")
            print(f"    Mode/Permissions: {YELLOW}{format_mode(mode)} (0o{mode:o}){RESET}")
            print(f"    Owner UID/GID:    {YELLOW}{uid}/{gid}{RESET}")
            print(f"    Links Count:      {YELLOW}{links_count}{RESET}")
            print(f"    File Size:        {YELLOW}{size} bytes{RESET}")
            print(f"    Allocated Blocks: {YELLOW}{blocks} blocks{RESET}")
            
            # Print non-zero block pointers
            used_ptrs = [b for b in block_pointers if b > 0]
            print(f"    Direct Blocks:    {YELLOW}{used_ptrs}{RESET}")
            print()

        # --- 5. Decode Directories ---
        print(f"{BOLD}{CYAN}[5] Visual Directory Hierarchy Decoding{RESET}")
        
        def decode_directory(ino, prefix=""):
            info = decoded_inodes.get(ino)
            if not info or not stat.S_ISDIR(info['mode']):
                return
                
            block0 = info['block_pointers'][0]
            if block0 == 0:
                print(f"  {prefix}Directory Inode {ino} has no data block.")
                return
                
            f.seek(block0 * BLOCK_SIZE)
            dir_block = f.read(BLOCK_SIZE)
            
            entry_sz = 64
            num_entries = BLOCK_SIZE // entry_sz
            
            for i in range(num_entries):
                entry_data = dir_block[i*entry_sz : (i+1)*entry_sz]
                target_ino, name_bytes = struct.unpack("<I60s", entry_data)
                
                if target_ino > 0:
                    # Strip null characters from string
                    name = name_bytes.decode('utf-8', errors='ignore').split('\x00')[0]
                    
                    if name in [".", ".."]:
                        print(f"  {prefix}├── {BOLD}{name}{RESET} ──► Inode {target_ino}")
                        continue
                        
                    target_info = decoded_inodes.get(target_ino)
                    if target_info:
                        if stat.S_ISDIR(target_info['mode']):
                            print(f"  {prefix}├── {BOLD}{BLUE}{name}/{RESET} ──► Inode {target_ino} (Dir, Size: {target_info['size']}B)")
                            decode_directory(target_ino, prefix + "  │  ")
                        else:
                            # Read file content snippet
                            f_block0 = target_info['block_pointers'][0]
                            snippet = "(empty)"
                            if f_block0 > 0:
                                f.seek(f_block0 * BLOCK_SIZE)
                                snippet_bytes = f.read(min(30, target_info['size']))
                                snippet = snippet_bytes.decode('utf-8', errors='ignore').replace('\n', '\\n')
                                if target_info['size'] > 30:
                                    snippet += "..."
                                    
                            print(f"  {prefix}└── {BOLD}{GREEN}{name}{RESET} ──► Inode {target_ino} (File, Size: {target_info['size']}B) [Content: '{snippet}']")
                    else:
                        print(f"  {prefix}└── {RED}{name} (BROKEN REFERENCE ──► Inode {target_ino}){RESET}")

        # Start recursive decode from Root (Inode 1)
        if 1 in decoded_inodes:
            print(f"  {BOLD}{BLUE}/{RESET} (Root Directory ──► Inode 1)")
            decode_directory(1, "  ")
        else:
            print(f"  {RED}Root Inode 1 not found.{RESET}")
            
    print(f"{BOLD}{BLUE}======================================================================{RESET}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default fallback test paths
        default_paths = ["/tmp/disk_v4.img", "/tmp/disk_v3.img", "/tmp/disk_v2.img", "/tmp/disk_v1.img"]
        chosen_path = None
        for p in default_paths:
            if os.path.exists(p):
                chosen_path = p
                break
        if not chosen_path:
            print(f"Usage: {sys.argv[0]} <demofs_disk_image>")
            print("Or format a disk image first using mkfs.demofs and pass it here.")
            sys.exit(1)
        analyze(chosen_path)
    else:
        analyze(sys.argv[1])
