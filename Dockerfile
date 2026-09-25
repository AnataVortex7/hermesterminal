FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    openssh-server \
    curl \
    bash \
    git \
    tmux \
    ca-certificates \
    build-essential \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Compile robust audit shim to prevent any linux_audit crash
COPY audit_shim.c /app/audit_shim.c
RUN gcc -shared -fPIC -ldl /app/audit_shim.c -o /usr/local/lib/audit_shim.so && \
    apt-get purge -y build-essential && apt-get autoremove -y

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