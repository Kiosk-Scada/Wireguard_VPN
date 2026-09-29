"""Qasja nga browser-i te router-at dhe pajisjet pas tyre (SSH / VNC / RDP) përmes Apache Guacamole.

Rrjedha (asnjë fjalëkalim s'kalon te browser-i i punëtorit):
    1. Punëtori klikon "Lidhu" te Frappe → connect(router, service)
    2. Frappe kontrollon rolin, ndërton JSON-in e lidhjes, e nënshkruan (HMAC-SHA256) dhe e enkripton
       (AES-128-CBC) me çelësin e përbashkët — formati i extension-it guacamole-auth-json
    3. Frappe ia dërgon Guacamole-s direkt (adresa e brendshme) dhe merr një authToken
    4. Browser-i hap  https://remote.../#/client/<id>?token=<authToken>
    5. Guacamole (i lidhur në VPN) → hub → tuneli → router 10.50.x.y → pajisja

Të dhënat JSON janë "singleUse" (s'mund të ripërdoren) dhe skadojnë pas 'Kohëzgjatja maksimale'.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

import frappe
from frappe import _

from wireguard_vpn.vpn import DOCTYPE, SETTINGS, log_event

REMOTE_ROLES = ("System Manager", "VPN Manager", "VPN Remote Access")
DEFAULT_PORTS = {"SSH": 22, "VNC": 5900, "RDP": 3389}
EXT_PORT_BASE = {"SSH": 2222, "VNC": 5900, "RDP": 3389}
RESERVED_EXT_PORTS = {22, 53, 80, 443, 4444, 51820}
LAN_TARGET = "Pajisje në LAN"
SECRET_RE = re.compile(r"^[0-9a-fA-F]{32}$")


# --------------------------------------------------------------------------- konfigurimi
def remote_settings_from_doc(d) -> dict:
    d = d or frappe._dict()
    raw = (d.get("guac_secret_key") or "").strip()
    secret = raw
    if raw and set(raw) == {"*"} and getattr(d, "get_password", None):
        try:
            secret = d.get_password("guac_secret_key", raise_exception=False) or ""
        except Exception:  # noqa: BLE001
            secret = ""
    return {
        "enabled": bool(d.get("remote_enabled")),
        "public_url": (d.get("guac_public_url") or "").strip().rstrip("/"),
        "internal_url": (d.get("guac_internal_url") or "http://127.0.0.1:8085").strip().rstrip("/"),
        "secret": (secret or "").strip(),
        "hours": max(int(d.get("remote_session_hours") or 8), 1),
        "recording": bool(d.get("remote_recording")),
        "verify_tls": bool(d.get("guac_verify_tls")),
    }


def remote_settings(require: bool = True) -> dict:
    try:
        d = frappe.get_cached_doc(SETTINGS)
    except frappe.DoesNotExistError:
        d = None
    s = remote_settings_from_doc(d)
    if d is not None and s["secret"] and set(s["secret"]) == {"*"}:
        s["secret"] = ""
    if d is not None and not s["secret"] and d.get("guac_secret_key"):
        try:
            s["secret"] = (d.get_password("guac_secret_key", raise_exception=False) or "").strip()
        except Exception:  # noqa: BLE001
            pass
    if require:
        validate_remote_settings(s)
    return s


def validate_remote_settings(s: dict):
    if not s["enabled"]:
        frappe.throw(_("Qasja nga browser-i është e fikur (WireGuard VPN Settings → Qasja nga browser-i)."))
    for key, label in (("public_url", "Adresa e Guacamole për punëtorët"),
                       ("internal_url", "Adresa e Guacamole nga serveri Frappe")):
        if not re.match(r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._\-/]*)?$", s[key]):
            frappe.throw(_("Guacamole: '{0}' duhet të jetë URL e plotë, p.sh. https://remote.pikapetrol.com").format(label))
    if not SECRET_RE.match(s["secret"]):
        frappe.throw(_("Guacamole: JSON Secret Key duhet të jetë 32 karaktere hex (nga output-i i guacamole.sh)."))


def has_remote_role(user: str | None = None) -> bool:
    return bool(set(frappe.get_roles(user)) & set(REMOTE_ROLES))


def require_remote_role():
    frappe.only_for(list(REMOTE_ROLES))


# --------------------------------------------------------------------------- shërbimet e router-it
def validate_services(doc):
    """Thirret nga VPNRouter.validate: plotëson portet dhe ndan portet në tunel (pa përplasje)."""
    from wireguard_vpn.vpn import LAN_IP_RE

    if doc.device_lan_ip and not any(
            r.target == LAN_TARGET and (r.lan_ip or "").strip() == doc.device_lan_ip for r in doc.services):
        doc.append("services", {"service_name": "SSH pajisja", "protocol": "SSH", "target": LAN_TARGET,
                                "lan_ip": doc.device_lan_ip, "port": 22, "enabled": 1})
    names, used = set(), set()
    for r in doc.services:
        r.service_name = (r.service_name or "").strip()
        if not r.service_name:
            frappe.throw(_("Shërbimi në rreshtin {0} s'ka emër.").format(r.idx))
        if r.service_name.lower() in names:
            frappe.throw(_("Emri i shërbimit '{0}' përsëritet.").format(r.service_name))
        names.add(r.service_name.lower())
        if r.protocol not in DEFAULT_PORTS:
            frappe.throw(_("Protokoll i panjohur: {0}").format(r.protocol))
        r.port = int(r.port or DEFAULT_PORTS[r.protocol])
        if not 1 <= r.port <= 65535:
            frappe.throw(_("Porti i shërbimit '{0}' duhet 1-65535.").format(r.service_name))
        if r.target == LAN_TARGET:
            r.lan_ip = (r.lan_ip or "").strip()
            if not LAN_IP_RE.match(r.lan_ip):
                frappe.throw(_("Shërbimi '{0}': IP LAN duhet të jetë IPv4 private, p.sh. 192.168.1.50")
                             .format(r.service_name))
        else:
            r.target, r.lan_ip = "Router", None
            r.ext_port = r.port                 # direkt te Tunnel IP e router-it
    # portet në tunel: ruaj ato ekzistuese, ndaj të rinjtë
    for r in doc.services:
        if r.target == LAN_TARGET and r.ext_port:
            if r.ext_port in used or r.ext_port in RESERVED_EXT_PORTS or r.ext_port < 1024:
                r.ext_port = None
            else:
                used.add(r.ext_port)
    for r in doc.services:
        if r.target == LAN_TARGET and not r.ext_port:
            port = EXT_PORT_BASE[r.protocol]
            while port in used or port in RESERVED_EXT_PORTS:
                port += 1
            r.ext_port = port
            used.add(port)


def lan_forwards(doc) -> list[dict]:
    """Port-forward-et që duhet t'i hapë skripti në router (vetëm shërbimet aktive në LAN)."""
    out = []
    for r in doc.get("services") or []:
        if r.target == LAN_TARGET and r.enabled and r.lan_ip and r.ext_port:
            out.append({"name": f"p{int(r.ext_port)}", "ext_port": int(r.ext_port), "int_port": int(r.port),
                        "proto": "tcp", "lan_ip": r.lan_ip, "service": r.service_name})
    return out


def forwards_signature(doc) -> str:
    return json.dumps([(f["ext_port"], f["lan_ip"], f["int_port"]) for f in lan_forwards(doc)])


# --------------------------------------------------------------------------- guacamole-auth-json
def encrypt_payload(secret_hex: str, payload: dict) -> str:
    """Formati i guacamole-auth-json: base64( AES-128-CBC(IV=0, key)( HMAC-SHA256(key, json) + json ) )."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = bytes.fromhex(secret_hex)
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signed = hmac.new(key, data, hashlib.sha256).digest() + data
    padder = padding.PKCS7(128).padder()
    padded = padder.update(signed) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor()
    return base64.b64encode(enc.update(padded) + enc.finalize()).decode("ascii")


def client_identifier(connection_id: str, data_source: str = "json") -> str:
    raw = "\0".join((connection_id, "c", data_source)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _post_token(s: dict, data: str) -> dict:
    import requests

    resp = requests.post(f"{s['internal_url']}/api/tokens", data={"data": data}, timeout=15,
                         verify=s["verify_tls"])
    if resp.status_code == 403:
        raise frappe.ValidationError(_("Guacamole e refuzoi hyrjen — kontrollo që JSON Secret Key te Settings "
                                       "është i njëjtë me atë të serverit Guacamole."))
    if not resp.ok:
        raise frappe.ValidationError(_("Guacamole: HTTP {0} nga {1}").format(resp.status_code, s["internal_url"]))
    body = resp.json()
    if not body.get("authToken"):
        raise frappe.ValidationError(_("Guacamole s'ktheu authToken."))
    return body


def _connection(s: dict, router, svc) -> tuple[str, dict]:
    host = router.tunnel_ip
    port = int(svc.port if svc.target != LAN_TARGET else svc.ext_port)
    params = {"hostname": host, "port": str(port)}
    if svc.username:
        params["username"] = svc.username
    password = svc.get_password("password", raise_exception=False) if svc.get("password") else None
    if password:
        params["password"] = password
    if svc.protocol == "SSH":
        params.update({"server-alive-interval": "30", "enable-sftp": "true", "scrollback": "3000"})
        protocol = "ssh"
    elif svc.protocol == "VNC":
        params.update({"cursor": "remote"})
        protocol = "vnc"
    else:
        params.update({"security": svc.rdp_security or "any",
                       "ignore-cert": "true" if svc.ignore_cert else "false",
                       "resize-method": "display-update"})
        protocol = "rdp"
    if s["recording"]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        who = re.sub(r"[^A-Za-z0-9._-]", "_", frappe.session.user)[:40]
        params.update({"recording-path": "/recordings", "create-recording-path": "true",
                       "recording-name": f"{stamp}-{router.name}-{re.sub(r'[^A-Za-z0-9]', '', svc.service_name)[:20]}-{who}"})
    return protocol, params


@frappe.whitelist()
def connect(router: str, service: str):
    """Butoni "Lidhu": kthen URL-në e Guacamole për këtë shërbim (hapet në tab të ri)."""
    require_remote_role()
    s = remote_settings()
    doc = frappe.get_doc(DOCTYPE, router)
    doc.check_permission("read")
    if doc.status in ("Pending", "Revoked") or not doc.tunnel_ip:
        frappe.throw(_("Router-i {0} s'është i lidhur me VPN (statusi: {1}).").format(doc.site_name, doc.status))
    svc = next((r for r in doc.services if r.name == service or r.service_name == service), None)
    if not svc or not svc.enabled:
        frappe.throw(_("Shërbimi s'u gjet ose është i fikur."))

    protocol, params = _connection(s, doc, svc)
    conn_id = f"{doc.site_name} — {svc.service_name}"[:120]
    payload = {
        "username": frappe.session.user,
        "expires": int((time.time() + s["hours"] * 3600) * 1000),
        "singleUse": True,
        "connections": {conn_id: {"protocol": protocol, "parameters": params}},
    }
    token = _post_token(s, encrypt_payload(s["secret"], payload))["authToken"]
    target = f"{doc.tunnel_ip}:{params['port']}"
    if svc.target == LAN_TARGET:
        target += f" → {svc.lan_ip}:{svc.port}"
    log_event(doc.name, "Remote Session", f"{svc.protocol} · {svc.service_name} · {target}")
    frappe.db.commit()
    return {"url": f"{s['public_url']}/#/client/{client_identifier(conn_id)}?token={token}",
            "title": conn_id, "offline": doc.status == "Offline"}


@frappe.whitelist()
def router_services(router: str):
    """Shërbimet aktive të një router-i (për Dashboard)."""
    require_remote_role()
    doc = frappe.get_doc(DOCTYPE, router)
    doc.check_permission("read")
    return [{"name": r.name, "service_name": r.service_name, "protocol": r.protocol,
             "target": r.target, "lan_ip": r.lan_ip} for r in doc.services if r.enabled]


@frappe.whitelist()
def test_guacamole():
    """Butoni te Settings: provon çelësin dhe lidhjen me Guacamole (pa hapur asnjë sesion)."""
    frappe.only_for("System Manager")
    import requests

    s = remote_settings()
    payload = {"username": "frappe-test", "expires": int((time.time() + 60) * 1000), "singleUse": True,
               "connections": {}}
    body = _post_token(s, encrypt_payload(s["secret"], payload))
    try:
        requests.delete(f"{s['internal_url']}/api/tokens/{body['authToken']}", timeout=10, verify=s["verify_tls"])
    except Exception:  # noqa: BLE001 — token-i skadon vetë
        pass
    return {"ok": True, "data_source": body.get("dataSource"), "internal_url": s["internal_url"],
            "public_url": s["public_url"]}


@frappe.whitelist()
def ui_state():
    """A duhet të shfaqen butonat "Lidhu" për këtë përdorues."""
    return {"enabled": remote_enabled_for_user()}


def remote_enabled_for_user() -> bool:
    try:
        return remote_settings(require=False)["enabled"] and has_remote_role()
    except Exception:  # noqa: BLE001
        return False
