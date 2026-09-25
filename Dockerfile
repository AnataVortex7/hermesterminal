FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    openssh-server \
    curl \
    bash \
    git \
    tmux \
    ca-certificates \
    gcc \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Debian chya sshd madhe Linux audit-write call asतो, jo container मधे
# CAP_AUDIT_WRITE nasल्यामुळे "Operation not permitted" ने fatal होतो आणि
# login झाल्या-झाल्या session band करतो. Ha ek chhota shim (LD_PRELOAD)
# audit_open() ला -1 return karवतो, sshd samजते audit available nahi,
# आणि नॉर्मल पुढे जातं (fatal होत नाही).
RUN echo 'int audit_open(void) { return -1; }' > /tmp/fakeaudit.c && \
    gcc -shared -fPIC -o /usr/local/lib/libfakeaudit.so /tmp/fakeaudit.c && \
    rm /tmp/fakeaudit.c && \
    apt-get purge -y gcc && apt-get autoremove -y

# SSH setup
RUN mkdir -p /var/run/sshd && \
    sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i 's/UsePAM yes/UsePAM no/' /etc/ssh/sshd_config && \
    echo "PubkeyAuthentication yes" >> /etc/ssh/sshd_config && \
    echo "AuthorizedKeysFile .ssh/authorized_keys" >> /etc/ssh/sshd_config && \
    echo "ClientAliveInterval 60" >> /etc/ssh/sshd_config && \
    echo "ClientAliveCountMax 10" >> /etc/ssh/sshd_config

WORKDIR /app
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV PORT=10000
EXPOSE 10000

CMD ["/app/start.sh"]
