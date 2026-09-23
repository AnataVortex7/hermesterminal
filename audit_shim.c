#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/types.h>
#include <errno.h>
#include <dlfcn.h>
#include <stdio.h>

#ifndef NETLINK_AUDIT
#define NETLINK_AUDIT 9
#endif

// Intercept socket() to block NETLINK_AUDIT
int socket(int domain, int type, int protocol) {
    static int (*orig_socket)(int, int, int) = NULL;
    if (!orig_socket) {
        orig_socket = (int (*)(int, int, int))dlsym(RTLD_NEXT, "socket");
    }
    if (domain == AF_NETLINK && protocol == NETLINK_AUDIT) {
        errno = EPERM;
        return -1;
    }
    return orig_socket(domain, type, protocol);
}

// Also intercept audit_open if libaudit is present
int audit_open(void) {
    errno = EPERM;
    return -1;
}