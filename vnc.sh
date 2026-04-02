sh << 'EOF'
#!/bin/sh
# Alpine VNC Auto Install
P="yiwan123"
D="/dev/vda"
R="http://mirrors.aliyun.com/alpine/v3.23"
setup-interfaces -a -r
sleep 2
cat>/tmp/a<<E
KEYMAPOPTS="us us"
HOSTNAMEOPTS="-n alpine"
INTERFACESOPTS="auto lo
iface lo inet loopback
auto eth0
iface eth0 inet dhcp"
DNSOPTS="-d 223.5.5.5 8.8.8.8"
TIMEZONEOPTS="-z PRC"
PROXYOPTS="none"
APKREPOSOPTS="$R/main"
SSHDOPTS="-c openssh"
NTPOPTS="-c chrony"
USEROPTS="-a -k none"
DISKOPTS="-m sys -s 0 $D"
E
export ERASE_DISKS="$D"
echo|setup-alpine -f /tmp/a -e
mkdir -p /mnt
mount ${D}2 /mnt
mount ${D}1 /mnt/boot
echo "root:$P"|chroot /mnt chpasswd
sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /mnt/etc/ssh/sshd_config
printf "$R/main\n$R/community\n">/mnt/etc/apk/repositories
mount --bind /dev /mnt/dev
mount --bind /proc /mnt/proc
mount --bind /sys /mnt/sys
chroot /mnt apk update
chroot /mnt apk add util-linux grub grub-bios curl vim bash parted e2fsprogs
umount /mnt/boot
swapoff -a 2>/dev/null
dd if=/dev/zero of=$D bs=1K seek=32 count=992 conv=notrunc
dd if=/dev/zero of=$D bs=512 seek=1 count=33 conv=notrunc
B=$(basename $D)
S=$(cat /sys/block/$B/size)
dd if=/dev/zero of=$D bs=512 seek=$((S-33)) count=33 conv=notrunc
sync
mount ${D}1 /mnt/boot
chroot /mnt grub-install --recheck $D
chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg
chroot /mnt apk del syslinux 2>/dev/null
sync
umount /mnt/boot /mnt/dev /mnt/proc /mnt/sys /mnt 2>/dev/null
reboot
EOF
