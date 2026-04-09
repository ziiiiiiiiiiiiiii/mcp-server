#!/bin/bash
apt-get update -qq && apt-get install -y openssh-server -qq
mkdir -p /run/sshd
echo 'root:admin' | chpasswd
sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config
/usr/sbin/sshd
/sbin/tini -- /usr/lib/frr/docker-start
