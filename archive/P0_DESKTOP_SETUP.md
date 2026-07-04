# P0 desktop setup — Windows 11 → dual-boot Ubuntu Server 24.04

> **LOG-019 amendment (live):** actual install = **Xubuntu 26.04 LTS minimal** (Server ISO hung at the i915 handoff; box needed a CMOS clear after a BIOS lockout — see BUILD_LOG). Deltas: `sudo apt install -y openssh-server` is the FIRST post-install command; WoL arm = `sudo nmcli connection modify "<con-name>" 802-3-ethernet.wake-on-lan magic` instead of the netplan edit; skip Part 4's xubuntu-core/lightdm lines (native). After any CMOS clear: BIOS is at defaults — re-apply one per reboot: AC BACK=Always On, ErP=Disabled, Fast Boot=Disabled (never "Ultra Fast"). Everything else below applies unchanged.

Target: home desktop (i7-10700 / 16 GB), dual-boot, Ubuntu as the **default** boot OS,
reachable from AIC over Tailscale. Ends where BUILD_GUIDE P0 step 2 begins.
Time: ~90 min hands-on. You need: an 8 GB+ USB stick (will be wiped), ethernet to the router.

---

## Part 1 — Prep inside Windows (20 min)

1. **Back up anything on C: you care about.** Partition shrinking is safe but not sacred.
2. **BitLocker:** Settings → Privacy & security → Device encryption (or `manage-bde -status` in admin PowerShell). If ON: note the recovery key (Microsoft account → Devices → BitLocker keys), then **Suspend protection** before partitioning. If it was on, your first Windows boot after GRUB appears will demand the recovery key — that's expected, have it on your phone.
3. **Disable Fast Startup** (it half-hibernates and locks the disk + fights the clock):
   Control Panel → Power Options → "Choose what the power buttons do" → uncheck *Turn on fast startup*. In admin PowerShell: `powercfg /h off`.
4. **Shrink C:** Win+X → Disk Management → right-click C: → Shrink Volume → free **150 GB minimum** (200 if the disk allows; images + PVCs + Prometheus + Loki grow). Leave it as *unallocated* — the Ubuntu installer takes it from there.
   If Windows refuses to shrink enough: disable pagefile + System Restore temporarily, retry, re-enable later.
5. **Flash the USB:** download Ubuntu **Server** 24.04.x LTS ISO (ubuntu.com/download/server) and [Rufus](https://rufus.ie) → select ISO + USB → GPT / UEFI (non-CSM) → write (default ISO mode is fine).
6. **Router:** log into the router admin page and set a **DHCP reservation** for the desktop's ethernet MAC (Disk Management's neighbor, `ipconfig /all` → Physical Address). A fixed LAN IP makes everything later boring, which is the goal.

## Part 2 — BIOS (5 min)

Reboot → BIOS (usually Del/F2 on desktop boards):

- Boot order: USB first (one-time boot menu F11/F12 also works).
- **"Restore AC Power Loss" / "Power On After Power Failure" → ON** — a headless box that stays down after a blink-out while you're in Bengaluru is dead weight.
- Leave Secure Boot ON (Ubuntu 24.04 is signed; no need to weaken the box).
- If ethernet supports it: enable **Wake-on-LAN** (often under Power/APM). Optional but nice.

## Part 3 — Install Ubuntu Server (25 min, keyboard + monitor on the box)

Boot the USB → "Try or Install Ubuntu Server":

1. Language/keyboard → defaults. Network: confirm the ethernet shows the reserved IP. Skip proxy.
2. **Storage: choose "Custom storage layout"** (safer than "Install alongside" wording variance):
   - Select the **free space** on the Windows disk → Add GPT partition → size: all of it, format ext4, mount `/`.
   - The installer will reuse the existing EFI partition automatically (shows as "existing"). Do **not** format the EFI partition or any ntfs partition.
   - Summary screen must show: ntfs partitions untouched, one new ext4 `/`, existing EFI reused. If it says anything about erasing the disk, stop and re-read.
3. Profile: user `soumyadip`, hostname `forge` (or your pick — it becomes the Tailscale name).
4. **"Install OpenSSH server" → YES.** Import SSH key from GitHub if your key lives there — passwordless from minute one.
5. Skip all featured snaps (we install our own things). Install, reboot, pull the USB.

GRUB now appears at every boot with Ubuntu default. Verify Windows still boots once (pick *Windows Boot Manager*; supply BitLocker key if asked; re-enable BitLocker protection if you suspended it), then boot back into Ubuntu.

## Part 4 — First-boot hardening for remote life (20 min, can be done over SSH from the laptop)

```bash
ssh soumyadip@<reserved-ip>

sudo apt update && sudo apt full-upgrade -y
sudo apt install -y chrony git make curl htop
sudo systemctl enable --now chrony          # lag math runs on this clock

# GRUB: default Ubuntu, short menu, and remote one-time-boot into Windows when ever needed
sudo sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
sudo sed -i 's/^#\?GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/' /etc/default/grub
sudo update-grub
# from now on, to reboot into Windows ONCE from SSH (it returns to Ubuntu after):
#   sudo grub-reboot "$(grep -o 'Windows Boot Manager[^"]*' /boot/grub/grub.cfg | head -1)" && sudo reboot

# Stop the dual-boot clock fight (Windows keeps RTC in local time):
sudo timedatectl set-local-rtc 1 --adjust-system-clock

# Tailscale (D-008): the box's identity from AIC
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up        # login link once; then note: tailscale ip -4

# No sleep, ever:
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Optional on-demand GUI (LOG-010): `sudo apt install --no-install-recommends xubuntu-core xrdp && sudo systemctl set-default multi-user.target && sudo systemctl disable xrdp lightdm`.

### Part 4b — additions settled later on 2026-06-12 (WoL, shared box, sync loop, toolchain)

- **BIOS trio (B460M D3H):** ErP → Disabled (else WoL is dead in S5), AC BACK → Always On, Fast Boot → Disabled. (Windows Fast Startup is separate and already off per Part 1.)
- **Wake-on-LAN arm (Intel NIC, e1000e):** `wakeonlan: true` under the interface in `/etc/netplan/50-cloud-init.yaml`, `netplan apply`; verify `ethtool <if> | grep Wake-on` shows `g`. Enable "Wake on Magic Packet" in the Windows driver too (dual-boot: WoL obeys the OS last shut down from). Magic packets are L2 broadcast — they cross neither internet nor Tailscale; remote wake needs a LAN relay (router WoL button, or any always-on home device on the tailnet). WoL = recovery path; the box's operating mode is 24/7 + AC BACK for blackouts. Drill before leaving: poweroff → `wakeonlan <MAC>` from laptop on LAN, once from each OS's shutdown.
- **K3s install gains** `--tls-san <tailscale-ip>,<hostname>` (D-008) — kubectl from AIC.
- **Toolchain:** `docker.io golang-go python3-venv python3-pip` + `usermod -aG docker` + helm snap.
- **Sync loop:** `syncthing` + `systemctl enable --now syncthing@$USER`; pair with the laptop over an SSH tunnel to :8384; share ABB_Accelerator → `~/ABB_Accelerator`. Git remains history; Syncthing moves the working copy.
- **Claude Code:** `npm install -g @anthropic-ai/claude-code`, login, run inside the repo.
- **Shared-box arrangement (dad):** standard non-sudo user; `xubuntu-core` + xrdp with `graphical.target` + lightdm **enabled** (supersedes the headless-default line above for this box); OnlyOffice + Firefox snaps. GUI session stays off during rehearsals/RAM measurements.

## Part 5 — Handoff gate

Run BUILD_GUIDE P0 step 2 verbatim:

```bash
uname -r                         # ≥ 5.15 (24.04 ships 6.8+)
ls /sys/kernel/btf/vmlinux       # exists
stat -fc %T /sys/fs/cgroup       # cgroup2fs
cat /proc/pressure/cpu           # some/full lines present
timedatectl | grep synchronized  # yes
```

All five green → continue at BUILD_GUIDE P0 step 3 (K3s install with the PSI gate, plus
`--tls-san <tailscale-ip>,forge` so kubectl works from AIC). Then `git clone` the repo and P1 begins.

**Failure notes:** no `/proc/pressure` → kernel booted with `psi=0` somewhere — check `cat /proc/cmdline`, remove, `update-grub`, reboot (24.04 default is ON). GRUB shows no Windows entry → `sudo os-prober && sudo update-grub` (if os-prober is disabled: `echo GRUB_DISABLE_OS_PROBER=false | sudo tee -a /etc/default/grub && sudo update-grub`). Windows clock wrong after dual-boot → the `set-local-rtc 1` step above fixes it from the Linux side.
