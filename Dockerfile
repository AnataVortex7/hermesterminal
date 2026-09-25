FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    openssh-server \
    curl \
    bash \
    git \
    tmux \
    ca-certificates \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/
RUN chmod +x /app/start.sh /app/server.py

ENV PORT=10000
EXPOSE 10000

CMD ["/app/start.sh"]
