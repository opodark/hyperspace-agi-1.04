# SPDX-License-Identifier: Apache-2.0
# Security Lab: Kali Linux con gli strumenti di pentest di rete, su rete host.
# Il confine di sicurezza NON è questo container (gira con socket raw): è
# KALI_TARGET_ALLOWLIST, verificata in hostctl prima di ogni esecuzione.
FROM kalilinux/kali-rolling

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nmap nikto gobuster dnsutils iproute2 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Resta vivo così l'host-agent può entrarci con `docker exec`.
ENTRYPOINT ["sleep", "infinity"]
