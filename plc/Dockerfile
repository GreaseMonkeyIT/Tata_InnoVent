# OpenPLC v3 runtime (IEC 61131-3) — real Modbus TCP slave on :502, web UI on :8080.
# Build is source-based (their install.sh) and SLOW (~10-15 min, once); runtime is light.
# BOX-VERIFY (LOG-033): this image + the headless program upload in entrypoint.sh are the
# unproven steps of 2F — prove them early; until then the sim runs open-loop by design.
FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl sudo build-essential pkg-config autoconf automake libtool \
        python3 python3-pip sqlite3 cmake bison flex ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/thiagoralves/OpenPLC_v3.git /OpenPLC_v3
WORKDIR /OpenPLC_v3
RUN ./install.sh docker
COPY program.st /program.st
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 502 8080
ENTRYPOINT ["/entrypoint.sh"]
