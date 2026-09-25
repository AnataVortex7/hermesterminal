FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    nginx \
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


# Install gotty for web-based browser terminal
RUN curl -sL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 -o /usr/local/bin/ttyd && chmod +x /usr/local/bin/ttyd

WORKDIR /app
COPY . /app/
RUN cp /app/nginx.conf /etc/nginx/nginx.conf
RUN chmod +x /app/terminal_entry.sh /app/start.sh /app/server.py

ENV PORT=10000
EXPOSE 10000

CMD ["/app/start.sh"]