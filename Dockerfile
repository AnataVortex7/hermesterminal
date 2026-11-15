FROM debian:bookworm-slim

# Tailscale static binaries (no apt repo / install script needed)
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale  /usr/local/bin/tailscale

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash tmux git curl ca-certificates openssh-server python3-minimal procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY start.sh health.py /app/
RUN chmod +x /app/start.sh

ENV PORT=10000 TZ=Asia/Kolkata
EXPOSE 10000
CMD ["/app/start.sh"]
