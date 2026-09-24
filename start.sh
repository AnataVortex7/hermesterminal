#!/bin/bash
/usr/sbin/sshd &
ttyd -w -p 10000 /bin/bash
