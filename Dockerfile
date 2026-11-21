FROM debian:bookworm-slim

# Tailscale static binaries (no apt repo / install script needed)
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale  /usr/local/bin/tailscale

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash tmux git curl ca-certificates openssh-server python3 procps rclone zstd \
    && apt-mark showmanual | sort > /usr/share/hermes-base-manual.txt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY start.sh health.py oomguard.py cgshim.py fsd.py backup.py /app/
# container-aware free/nproc/uptime/top/df/lscpu: /usr/local/bin is ahead of /usr/bin in PATH
RUN chmod +x /app/start.sh /app/cgshim.py \
 && for c in free nproc uptime top df lscpu; do ln -sf /app/cgshim.py /usr/local/bin/$c; done

ENV PORT=10000 TZ=Asia/Kolkata
EXPOSE 10000
CMD ["/app/start.sh"]
