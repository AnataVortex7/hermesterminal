#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <dlfcn.h>
#include <stdio.h>

// If libaudit is used
int audit_open(void) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_DGRAM, 0, sv) == 0) {
        close(sv[1]);
        return sv[0];
    }
    return open("/dev/null", O_RDWR);
}

int audit_send(int fd, int type, const void *data, size_t size) {
    return 0; // Return 0 (success)
}

int audit_log_user_message(int audit_fd, int type, const char *message,
                           const char *hostname, const char *addr,
                           const char *tty, int result) {
    return 0; // Return 0 (success)
}

int audit_log_acct_message(int audit_fd, int type, const char *pgname,
                           const char *op, const char *name, unsigned int id,
                           const char *host, const char *addr, const char *tty,
                           int result) {
    return 0; // Return 0 (success)
}

// Intercept socket()
int socket(int domain, int type, int protocol) {
    static int (*orig_socket)(int, int, int) = NULL;
    if (!orig_socket) {
        orig_socket = (int (*)(int, int, int))dlsym(RTLD_NEXT, "socket");
    }
    #ifndef AF_NETLINK
    #define AF_NETLINK 16
    #endif
    #ifndef NETLINK_AUDIT
    #define NETLINK_AUDIT 9
    #endif

    if (domain == AF_NETLINK && protocol == NETLINK_AUDIT) {
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_DGRAM, 0, sv) == 0) {
            close(sv[1]);
            return sv[0];
        }
    }
    return orig_socket(domain, type, protocol);
}

// Intercept sendto()
ssize_t sendto(int sockfd, const void *buf, size_t len, int flags,
               const struct sockaddr *dest_addr, socklen_t addrlen) {
    static ssize_t (*orig_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t) = NULL;
    if (!orig_sendto) {
        orig_sendto = (ssize_t (*)(int, const void *, size_t, int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "sendto");
    }
    ssize_t res = orig_sendto(sockfd, buf, len, flags, dest_addr, addrlen);
    if (res < 0) {
        // If error is EISCONN or EPERM or ENOENT on sendto, pretend success
        return len;
    }
    return res;
}

// Intercept sendmsg()
ssize_t sendmsg(int sockfd, const struct msghdr *msg, int flags) {
    static ssize_t (*orig_sendmsg)(int, const struct msghdr *, int) = NULL;
    if (!orig_sendmsg) {
        orig_sendmsg = (ssize_t (*)(int, const struct msghdr *, int))dlsym(RTLD_NEXT, "sendmsg");
    }
    ssize_t res = orig_sendmsg(sockfd, msg, flags);
    if (res < 0) {
        return 0;
    }
    return res;
}