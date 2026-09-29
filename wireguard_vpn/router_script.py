"""Skriptet për CLI-në e Teltonika RUT142 (RutOS / OpenWrt UCI).

Skripti i lidhjes:
  * gjeneron private key TE ROUTER-i me `wg genkey` (ose ripërdor atë ekzistues —
    ekzekutimi i dytë s'e ndërron çelësin), që s'largohet kurrë nga router-i
  * krijon interface-in `okvpn` + peer-in drejt hub-it (Floating IP)
  * firewall: nga tuneli pranohet vetëm trafiku që vjen nga hub-i (10.50.0.1)
  * opsionale: port-forward drejt pajisjeve në LAN (shërbimet e router-it te Frappe,
    p.sh. 2222 → 192.168.1.50:22, 5900 → 192.168.1.60:5900)
  * në fund shfaq PUBLIC KEY-in e router-it — ai ngjitet te Frappe → Aktivizo
"""
from __future__ import annotations

import re

IFACE = "okvpn"
_SAFE_COMMENT = re.compile(r"[^\w .,:()/+-]", re.UNICODE)


def _comment(text: str) -> str:
    return _SAFE_COMMENT.sub("", str(text or ""))[:80]


def _clear_firewall() -> list[str]:
    return [
        f"for s in $(uci -q show firewall | sed -n 's/^firewall\\.\\({IFACE}_[a-z0-9_]*\\)=.*/\\1/p'); do",
        '  uci -q delete "firewall.$s" || true',
        "done",
    ]


def connect_script(*, title: str, tunnel_ip: str, hub_tunnel_ip: str, hub_public_key: str,
                   endpoint_host: str, endpoint_port: int, forwards: list[dict],
                   device_lan_ip: str | None = None, keepalive: int = 25) -> str:
    hub = hub_tunnel_ip
    lines = [
        f"# WireGuard VPN — {_comment(title)} — {tunnel_ip}",
        "command -v wg >/dev/null 2>&1 || { echo 'GABIM: ky router nuk e ka komandën wg (WireGuard). "
        "Përdor WebUI (Services -> VPN -> WireGuard) ose përditëso RutOS.'; exit 1; }",
        f"PK=$(uci -q get network.{IFACE}.private_key || true)",
        '[ -n "$PK" ] || PK=$(wg genkey)',
        "set -e",
        f"uci -q delete network.{IFACE} || true",
        f"uci -q delete network.{IFACE}_hub || true",
        f"uci set network.{IFACE}=interface",
        f"uci set network.{IFACE}.proto='wireguard'",
        f'uci set network.{IFACE}.private_key="$PK"',
        f"uci add_list network.{IFACE}.addresses='{tunnel_ip}/32'",
        f"uci set network.{IFACE}.disabled='0'",
        f"uci set network.{IFACE}_hub=wireguard_{IFACE}",
        f"uci set network.{IFACE}_hub.description='okvpn-hub'",
        f"uci set network.{IFACE}_hub.public_key='{hub_public_key}'",
        f"uci set network.{IFACE}_hub.endpoint_host='{endpoint_host}'",
        f"uci set network.{IFACE}_hub.endpoint_port='{int(endpoint_port)}'",
        f"uci add_list network.{IFACE}_hub.allowed_ips='{hub}/32'",
        f"uci set network.{IFACE}_hub.persistent_keepalive='{int(keepalive)}'",
        f"uci set network.{IFACE}_hub.route_allowed_ips='1'",
        f"uci set network.{IFACE}_hub.tunlink='any'",
        f"uci set network.{IFACE}_hub.force_tunlink='0'",
        *_clear_firewall(),
        f"uci set firewall.{IFACE}_zone=zone",
        f"uci set firewall.{IFACE}_zone.name='{IFACE}'",
        f"uci add_list firewall.{IFACE}_zone.network='{IFACE}'",
        f"uci set firewall.{IFACE}_zone.input='REJECT'",
        f"uci set firewall.{IFACE}_zone.output='ACCEPT'",
        f"uci set firewall.{IFACE}_zone.forward='REJECT'",
        f"uci set firewall.{IFACE}_in=rule",
        f"uci set firewall.{IFACE}_in.name='{IFACE}-from-hub'",
        f"uci set firewall.{IFACE}_in.src='{IFACE}'",
        f"uci set firewall.{IFACE}_in.src_ip='{hub}'",
        f"uci set firewall.{IFACE}_in.proto='all'",
        f"uci set firewall.{IFACE}_in.target='ACCEPT'",
    ]
    for fwd in forwards:
        lan_ip = fwd.get("lan_ip") or device_lan_ip
        if lan_ip:
            sec = f"firewall.{IFACE}_fwd_{fwd['name']}"
            lines += [
                f"uci set {sec}=redirect",
                f"uci set {sec}.name='{IFACE}-{fwd['name']}'",
                f"uci set {sec}.src='{IFACE}'",
                f"uci set {sec}.src_ip='{hub}'",
                f"uci set {sec}.src_dport='{int(fwd['ext_port'])}'",
                f"uci set {sec}.dest='lan'",
                f"uci set {sec}.dest_ip='{lan_ip}'",
                f"uci set {sec}.dest_port='{int(fwd['int_port'])}'",
                f"uci set {sec}.proto='{fwd['proto']}'",
                f"uci set {sec}.target='DNAT'",
            ]
    lines += [
        "uci commit network",
        "uci commit firewall",
        "/etc/init.d/network reload",
        "/etc/init.d/firewall reload",
        "set +e",
        "i=0",
        "while [ $i -lt 20 ]; do",
        f"  if ubus call network.interface.{IFACE} status 2>/dev/null | grep -q '\"up\": true'; then",
        f"    echo 'VPN: interface-i {IFACE} është UP'; break",
        "  fi",
        "  i=$((i+1)); sleep 1",
        "done",
        "echo",
        "echo '========== KOPJO KËTË TE FRAPPE (Router Public Key) =========='",
        'echo "$PK" | wg pubkey',
        "echo '=============================================================='",
    ]
    return _wrap("\n".join(lines) + "\n")


def remove_script(*, title: str) -> str:
    lines = [
        f"# WireGuard VPN — heqja nga router-i — {_comment(title)}",
        "set -e",
        f"uci -q delete network.{IFACE} || true",
        f"uci -q delete network.{IFACE}_hub || true",
        *_clear_firewall(),
        "uci commit network",
        "uci commit firewall",
        "/etc/init.d/network reload",
        "/etc/init.d/firewall reload",
        "echo 'VPN: konfigurimi okvpn u hoq nga router-i'",
    ]
    return _wrap("\n".join(lines) + "\n")


def _wrap(script: str) -> str:
    """Ngjitet si një bllok në terminalin e router-it (heredoc me thonjëza = pa zgjerim)."""
    return ("cat > /tmp/okvpn.sh <<'OKVPN_EOF'\n" + script + "OKVPN_EOF\n"
            "sh /tmp/okvpn.sh; rm -f /tmp/okvpn.sh\n")
