# SPDX-License-Identifier: GPL-2.0-only
obj-m += demofsv5.o
demofsv5-objs := demofs_v5.o

all: mkfs.demofs
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules

mkfs.demofs: mkfs.demofs.c
	gcc -Wall -o mkfs.demofs mkfs.demofs.c

clean:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean
	rm -f mkfs.demofs
