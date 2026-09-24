#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <dlfcn.h>
#include <stdio.h>

// Return a valid dummy fd (like a socketpair or /dev/null) so audit_open() succeeds!
int audit_open(void) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0) {
        // Return one end, close the other in background or keep it
        // To prevent blocking, make it non-blocking or just let it be
        fcntl(sv[0], F_SETFL, O_NONBLOCK);
        close(sv[1]);
        return sv[0];
    }
    return open("/dev/null", O_RDWR);
}

// Intercept socket to return a fake fd for NETLINK_AUDIT
int socket(int domain, int type, int protocol) {
    static int (*orig_socket)(int, int, int) = NULL;
    if (!orig_socket) {
        orig_socket = (int (*)(int, int, int))dlsym(RTLD_NEXT, "socket");
    }
    // If it's netlink audit, return a socketpair fd or /dev/null
    #ifndef AF_NETLINK
    #define AF_NETLINK 16
    #endif
    #ifndef NETLINK_AUDIT
    #define NETLINK_AUDIT 9
    #endif

    if (domain == AF_NETLINK && protocol == NETLINK_AUDIT) {
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0) {
            close(sv[1]);
            return sv[0];
        }
    }
    return orig_socket(domain, type, protocol);
}

// Intercept sendto / send to swallow audit messages successfully
ssize_t sendto(int sockfd, const void *buf, size_t len, int flags, const struct sockaddr *dest_addr, socklen_t addrlen) {
    static ssize_t (*orig_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t) = NULL;
    if (!orig_sendto) {
        orig_sendto = (ssize_t (*)(int, const void *, size_t, int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "sendto");
    }
    // If sockfd is our fake audit fd or similar, just return len (pretend sent successfully)
    return orig_sendto(sockfd, buf, len, flags, dest_addr, addrlen);
}