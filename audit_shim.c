#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/types.h>
#include <errno.h>
#include <dlfcn.h>
#include <stdio.h>

// Intercept linux_audit_write_entry directly (OpenSSH audit function)
int linux_audit_write_entry(int type, int success) {
    return 0; // Success, do nothing
}

int audit_open(void) {
    return -1; // Indicate audit not available
}

int socket(int domain, int type, int protocol) {
    static int (*orig_socket)(int, int, int) = NULL;
    if (!orig_socket) {
        orig_socket = (int (*)(int, int, int))dlsym(RTLD_NEXT, "socket");
    }
    if (domain == 16 /* AF_NETLINK */ && protocol == 9 /* NETLINK_AUDIT */) {
        errno = EPERM;
        return -1;
    }
    return orig_socket(domain, type, protocol);
}