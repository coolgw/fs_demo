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
        # 1. Read Superblock
        f.seek(0)
        sb_data = f.read(24)
        if len(sb_data) < 24:
            print(f"{RED}Error: Cannot read superblock (file too small).{RESET}")
            return
        magic, block_size, blocks_count, inodes_count, free_blocks, free_inodes = struct.unpack("<6I", sb_data)
        if magic != DEMOFS_MAGIC:
            print(f"{RED}Error: Superblock magic mismatch. This is not a valid demofs volume.{RESET}")
            return

        # 2. Read Block Bitmap (Block 1)
        f.seek(1 * BLOCK_SIZE)
        bb_bytes = f.read(16)
        allocated_blocks_set = []
        for b in range(total_blocks):
            byte_idx = b // 8
            bit_idx = b % 8
            if byte_idx < len(bb_bytes) and (bb_bytes[byte_idx] & (1 << bit_idx)):
                allocated_blocks_set.append(b)

        # 3. Read Inode Bitmap (Block 2)
        f.seek(2 * BLOCK_SIZE)
        ib_bytes = f.read(16)
        allocated_inodes_set = []
        for i in range(MAX_INODES):
            byte_idx = i // 8
            bit_idx = i % 8
            if byte_idx < len(ib_bytes) and (ib_bytes[byte_idx] & (1 << bit_idx)):
                allocated_inodes_set.append(i)

        # 4. Read and Decode Inode Table (Block 3)
        f.seek(3 * BLOCK_SIZE)
        it_bytes = f.read(2048) # 32 inodes * 64 bytes
        decoded_inodes = {}
        for ino in allocated_inodes_set:
            if ino == 0:
                continue
            ino_offset = ino * 64
            ino_bytes = it_bytes[ino_offset : ino_offset + 64]
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

        # --- 0. Raw Disk Metadata Blocks Dump (Hex View) ---
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
        b1_byte = bb_bytes[0]
        allocated_blocks_list = [str(b) for b in allocated_blocks_set if b < 16]
        blocks_str = ", ".join(allocated_blocks_list)
        
        print(f"  {BOLD}{YELLOW}Block 1 (Block Bitmap) | Byte Offset 0x1000 (first 16 bytes):{RESET}")
        print(hex_dump(bb_bytes, 1 * BLOCK_SIZE))
        print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
        print(f"    └─ {BOLD}0x1000{RESET}           [{b1_byte:02x}]: {CYAN}block allocation mask (0b{b1_byte:08b} in binary){RESET}")
        print(f"                           -> Physical block(s) {blocks_str} are currently allocated.")
        print()
        
        # Inode Bitmap (Block 2) - first 16 bytes
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
        
        # Inode Table (Block 3) - Dump active inodes dynamically (starts at 3 * 4096 = 12288 = 0x3000)
        max_active_ino = max(allocated_inodes_set) if allocated_inodes_set else 2
        dump_size = (max_active_ino + 1) * 64
        print(f"  {BOLD}{YELLOW}Block 3 (Inode Table) | Byte Offset 0x3000 (first {dump_size} bytes):{RESET}")
        print(hex_dump(it_bytes[:dump_size], 3 * BLOCK_SIZE))
        
        # Print field mapping annotations dynamically for each active inode
        for ino in sorted(allocated_inodes_set):
            if ino == 0:
                continue
            ino_bytes = it_bytes[ino * 64 : (ino + 1) * 64]
            mode, uid, gid, lc, size, blocks, *bp = struct.unpack("<4H2I12I", ino_bytes)
            mode_hex = " ".join(f"{b:02x}" for b in ino_bytes[0:2])
            size_hex = " ".join(f"{b:02x}" for b in ino_bytes[8:12])
            blocks_hex = " ".join(f"{b:02x}" for b in ino_bytes[12:16])
            
            # Use exact physical byte offset of 3 * BLOCK_SIZE (0x3000)
            ino_start_offset = 3 * BLOCK_SIZE + ino * 64
            
            type_str = "directory" if stat.S_ISDIR(mode) else "regular file"
            print(f"  {BOLD}{MAGENTA}Field Mapping Annotation (for Inode {ino} starting at 0x{ino_start_offset:04x}):{RESET}")
            print(f"    ├─ {BOLD}0x{ino_start_offset:04x}-0x{ino_start_offset + 1:04x}{RESET} [{mode_hex}]:       {CYAN}mode (0x{mode:04x} -> {format_mode(mode)} {type_str}){RESET}")
            print(f"    ├─ {BOLD}0x{ino_start_offset + 8:04x}-0x{ino_start_offset + 11:04x}{RESET} [{size_hex}]: {CYAN}size ({size} bytes){RESET}")
            print(f"    ├─ {BOLD}0x{ino_start_offset + 12:04x}-0x{ino_start_offset + 15:04x}{RESET} [{blocks_hex}]: {CYAN}blocks ({blocks} blocks allocated){RESET}")
            
            # DYNAMIC ALLOCATED BLOCK POINTERS ANNOTATION LOOP
            if blocks > 0:
                for b_idx in range(min(12, blocks)):
                    bp_offset = ino_start_offset + 16 + b_idx * 4
                    bp_val = bp[b_idx]
                    bp_val_bytes = ino_bytes[16 + b_idx * 4 : 20 + b_idx * 4]
                    bp_val_hex = " ".join(f"{b:02x}" for b in bp_val_bytes)
                    connector = "└─" if b_idx == (blocks - 1) or b_idx == 11 else "├─"
                    print(f"    {connector} {BOLD}0x{bp_offset:04x}-0x{bp_offset + 3:04x}{RESET} [{bp_val_hex}]: {CYAN}block[{b_idx}] (Direct Pointer {b_idx} -> Block {bp_val} holds contents){RESET}")
            else:
                bp_hex_unassigned = " ".join(f"{b:02x}" for b in ino_bytes[16:20])
                print(f"    └─ {BOLD}0x{ino_start_offset + 16:04x}-0x{ino_start_offset + 19:04x}{RESET} [{bp_hex_unassigned}]: {CYAN}block[0] (Direct Pointer 0 -> unassigned){RESET}")
        print()
        
        # Dump all allocated data blocks dynamically
        for b_num in sorted(allocated_blocks_set):
            if b_num < 4:
                continue
            f.seek(b_num * BLOCK_SIZE)
            block_data = f.read(BLOCK_SIZE)
            
            # Determine if this block belongs to a directory or file
            belongs_to_dir = False
            dir_ino = 1
            for ino, ino_info in decoded_inodes.items():
                if stat.S_ISDIR(ino_info['mode']) and b_num in ino_info['block_pointers']:
                    belongs_to_dir = True
                    dir_ino = ino
                    break
                    
            if belongs_to_dir:
                # Directory Data Block Dump - Print up to 320 bytes to show up to 5 entries
                num_dump_bytes = 320
                print(f"  {BOLD}{YELLOW}Block {b_num} (Directory Data Block for Inode {dir_ino}) | Byte Offset 0x{b_num * BLOCK_SIZE:04x} (first {num_dump_bytes} bytes):{RESET}")
                print(hex_dump(block_data[:num_dump_bytes], b_num * BLOCK_SIZE))
                
                print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
                for entry_idx in range(num_dump_bytes // 64):
                    entry_data = block_data[entry_idx * 64 : (entry_idx + 1) * 64]
                    entry_ino, entry_name_b = struct.unpack("<I60s", entry_data)
                    if entry_ino > 0:
                        entry_name = entry_name_b.decode('utf-8', errors='ignore').split('\x00')[0]
                        ino_hex = " ".join(f"{b:02x}" for b in entry_data[0:4])
                        name_hex = " ".join(f"{b:02x}" for b in entry_data[4:8])
                        
                        # Generic connector detection: check if the NEXT entry is valid
                        next_ino = 0
                        if (entry_idx + 1) < (num_dump_bytes // 64):
                            next_entry_data = block_data[(entry_idx + 1) * 64 : (entry_idx + 2) * 64]
                            next_ino = struct.unpack("<I", next_entry_data[0:4])[0]
                            
                        connector = "└─" if next_ino == 0 else "├─"
                        print(f"    {connector} {BOLD}Entry {entry_idx} ('{entry_name}' | Offset 0x{b_num * BLOCK_SIZE + entry_idx * 64:04x}-0x{b_num * BLOCK_SIZE + entry_idx * 64 + 63:04x}):{RESET}")
                        print(f"       ├─ {BOLD}0x{b_num * BLOCK_SIZE + entry_idx * 64:04x}-0x{b_num * BLOCK_SIZE + entry_idx * 64 + 3:04x}{RESET} [{ino_hex}]: {CYAN}inode number (Inode {entry_ino}){RESET}")
                        print(f"       └─ {BOLD}0x{b_num * BLOCK_SIZE + entry_idx * 64 + 4:04x}-0x{b_num * BLOCK_SIZE + entry_idx * 64 + 7:04x}{RESET} [{name_hex}...]: {CYAN}entry name (\"{entry_name}\"){RESET}")
            else:
                # Regular File Data Block Dump (first 16 bytes)
                b_data_snippet = block_data[:16]
                content_len = 0
                for b in b_data_snippet:
                    if b == 0 or b < 32 or b > 126:
                        break
                    content_len += 1
                
                print(f"  {BOLD}{YELLOW}Block {b_num} (File Data Block) | Byte Offset 0x{b_num * BLOCK_SIZE:04x} (first 16 bytes):{RESET}")
                print(hex_dump(b_data_snippet, b_num * BLOCK_SIZE))
                
                print(f"  {BOLD}{MAGENTA}Field Mapping Annotation:{RESET}")
                if content_len > 0:
                    hex_bytes_str = " ".join(f"{b:02x}" for b in b_data_snippet[:content_len])
                    ascii_str = b_data_snippet[:content_len].decode('utf-8', errors='ignore')
                    print(f"    └─ {BOLD}0x{b_num * BLOCK_SIZE:04x}-0x{b_num * BLOCK_SIZE + content_len - 1:04x}{RESET} [{hex_bytes_str}]: {CYAN}raw file content data ('{ascii_str}' ASCII string){RESET}")
                else:
                    print(f"    └─ {BOLD}0x{b_num * BLOCK_SIZE:04x}{RESET}: {CYAN}empty file content block or non-printable ASCII content{RESET}")
            print()
            
        print("-" * 70)
        print()

        # --- 1. Read Superblock Information ---
        print(f"{BOLD}{CYAN}[1] Superblock Information (Block 0 | Offset 0x0000){RESET}")
        print(f"  Magic Number:     {GREEN}0x{magic:08x}{RESET} " + (f"({GREEN}VALID{RESET})" if magic == DEMOFS_MAGIC else f"({RED}INVALID{RESET})"))
        print(f"  Block Size:       {GREEN}{block_size} bytes{RESET}")
        print(f"  Blocks Count:     {GREEN}{blocks_count}{RESET}")
        print(f"  Inodes Count:     {GREEN}{inodes_count}{RESET}")
        print(f"  Free Blocks:      {GREEN}{free_blocks}{RESET}")
        print(f"  Free Inodes:      {GREEN}{free_inodes}{RESET}")
        print()

        # --- 2. Read Block Bitmap ---
        f.seek(1 * BLOCK_SIZE)
        bb_data = f.read(BLOCK_SIZE)
        print(f"{BOLD}{CYAN}[2] Block Allocation Grid (Block 1 | Offset 0x1000){RESET}")
        print(f"  Each character represents a 4KB block. Total blocks: {blocks_count}")
        print("  Legend: " + f"{RED}■{RESET} Metadata/Reserved  " + f"{YELLOW}■{RESET} Allocated Data  " + f"{GREEN}·{RESET} Free Space")
        print("  " + "-" * 40)
        
        grid_rows = 16
        grid_cols = 16
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
                    if b_num <= 3:
                        line_chars.append(f"{RED}■{RESET}")
                    else:
                        # Check if directory block dynamically
                        is_dir_blk = False
                        for i_info in decoded_inodes.values():
                            if stat.S_ISDIR(i_info['mode']) and b_num in i_info['block_pointers']:
                                is_dir_blk = True
                                break
                        if is_dir_blk:
                            line_chars.append(f"{RED}■{RESET}") # Treat directories as red/metadata-ish
                        else:
                            line_chars.append(f"{YELLOW}■{RESET}")
                else:
                    line_chars.append(f"{GREEN}·{RESET}")
            if line_chars:
                offset_lbl = f"  Blocks {r*grid_cols:03d}-{min((r+1)*grid_cols-1, blocks_count-1):03d}: "
                print(offset_lbl + " ".join(line_chars))
        print()

        # --- 3. Read Inode Bitmap ---
        print(f"{BOLD}{CYAN}[3] Inode Allocation Bitmap (Block 2 | Offset 0x2000){RESET}")
        inode_chars = []
        for i in range(MAX_INODES):
            if i in allocated_inodes_set:
                if i == 0:
                    inode_chars.append(f"{RED}0{RESET}")
                elif i == 1:
                    inode_chars.append(f"{MAGENTA}R{RESET}")
                else:
                    inode_chars.append(f"{YELLOW}{i}{RESET}")
            else:
                inode_chars.append(f"{GREEN}·{RESET}")
        print("  Inode Slot Grid: " + " ".join(inode_chars))
        print(f"  Active Inode IDs: {YELLOW}{[x for x in allocated_inodes_set if x > 0]}{RESET}")
        print()

        # --- 4. Read Decoded Inode Table ---
        print(f"{BOLD}{CYAN}[4] Decoded Inode Table (Block 3 | Offset 0x3000){RESET}")
        for ino in sorted(allocated_inodes_set):
            if ino == 0:
                continue
            info = decoded_inodes[ino]
            type_str = "Directory" if stat.S_ISDIR(info['mode']) else "Regular File"
            print(f"  {BOLD}Inode {ino} ({type_str}){RESET}:")
            print(f"    Mode/Permissions: {YELLOW}{format_mode(info['mode'])} (0o{info['mode']:o}){RESET}")
            print(f"    Owner UID/GID:    {YELLOW}{info['uid']}/{info['gid']}{RESET}")
            print(f"    Links Count:      {YELLOW}{info['links_count']}{RESET}")
            print(f"    File Size:        {YELLOW}{info['size']} bytes{RESET}")
            print(f"    Allocated Blocks: {YELLOW}{info['blocks']} blocks{RESET}")
            used_ptrs = [b for b in info['block_pointers'] if b > 0]
            print(f"    Direct Blocks:    {YELLOW}{used_ptrs}{RESET}")
            print()

        # --- 5. Visual Directory Hierarchy Decoding ---
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

        if 1 in decoded_inodes:
            print(f"  {BOLD}{BLUE}/{RESET} (Root Directory ──► Inode 1)")
            decode_directory(1, "  ")
        else:
            print(f"  {RED}Root Inode 1 not found.{RESET}")
            
    print(f"{BOLD}{BLUE}======================================================================{RESET}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        default_paths = ["/tmp/disk_v7.img", "/tmp/disk_v6.img", "/tmp/disk_v5.img"]
        chosen_path = None
        for p in default_paths:
            if os.path.exists(p):
                chosen_path = p
                break
        if not chosen_path:
            print(f"Usage: {sys.argv[0]} <demofs_disk_image>")
            sys.exit(1)
        analyze(chosen_path)
    else:
        analyze(sys.argv[1])
