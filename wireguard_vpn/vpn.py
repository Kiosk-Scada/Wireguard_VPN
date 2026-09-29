"""WireGuard VPN për router-at RUT142 — logjika e serverit Frappe (v2, i pavarur).

Rrjedha e një router-i (asnjë private key s'largohet nga router-i):
    1. VPN Router i ri (forma / Import CSV) → merr automatikisht një Tunnel IP (10.50.x.y)
    2. "Lidh router-in" → skripti ngjitet në CLI të RUT142 → router-i shfaq Public Key
    3. Public Key ngjitet te Frappe → "Aktivizo" → activate() e shton peer-in te TË DY
       hub-et (SSH si `wgsync`, vetëm komanda `wg-peer`)
    4. Punë periodike:
         job_handshakes  (5 min)  Active / Offline + njoftimet
         job_retry_sync  (5 min)  riprovon hub-et kur ka "Sync Error"
         job_reconcile   (orë)    hub-et marrin listën e plotë nga Frappe
         job_cleanup     (ditë)   fshin historikun e vjetër

Konfigurimi: DocType "WireGuard VPN Settings" (/app/wireguard-vpn-settings).
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import now_datetime

DOCTYPE = "VPN Router"
EVENT = "VPN Router Event"
SETTINGS = "WireGuard VPN Settings"
ROLES = ("System Manager", "VPN Manager")
STATUSES = ("Pending", "Active", "Offline", "Sync Error", "Revoked")
SYNCED_STATES = ("Active", "Offline", "Sync Error")   # router-a që duhet të jenë te hub-et
KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
HUB_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")
FWD_NAME_RE = re.compile(r"^[a-z0-9]{1,8}$")
LAN_IP_RE = re.compile(r"^(10|172|192)\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")

DEFAULT_FORWARDS = [{"name": "ssh", "ext_port": 2222, "int_port": 22}]
DEFAULT_RESERVED = ["10.50.0.0/24", "10.50.255.0/24"]
HUB_STATE_KEY = "wireguard_vpn:hub_state"


# --------------------------------------------------------------------------- konfigurimi
def _lines(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[\s,;]+", value or "") if v.strip()]


def _json_value(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except ValueError:
        frappe.throw(_("VPN: Port-forward duhet të jetë JSON i vlefshëm"))
        return default


def _password(d, field):
    if not d or not getattr(d, "get_password", None) or not d.get(field):
        return None
    try:
        return d.get_password(field, raise_exception=False)
    except Exception:  # noqa: BLE001 — p.sh. gjatë validate para ruajtjes
        return None


def raw_settings_from_doc(d) -> dict:
    d = d or frappe._dict()

    def val(field, default=None):
        value = d.get(field)
        return default if value in (None, "") else value

    return {
        "enabled": bool(d.get("enabled")),
        "hubs": _lines(val("hubs", "")),
        "sync_user": val("sync_user", "wgsync"),
        "sync_key": val("sync_key_path"),
        "known_hosts": val("known_hosts_path"),
        "endpoint_host": (val("endpoint_host") or "").strip() or None,
        "endpoint_port": int(val("endpoint_port", 51820) or 51820),
        "hub_public_key": (val("hub_public_key") or "").strip(),
        "hub_tunnel_ip": (val("hub_tunnel_ip") or "10.50.0.1").strip(),
        "pool": (val("pool_cidr") or "10.50.0.0/15").strip(),
        "reserved": _lines(val("reserved_ranges", DEFAULT_RESERVED)),
        "forwards": _json_value(val("kiosk_forwards"), DEFAULT_FORWARDS),
        "offline_after": int(val("offline_after_seconds", 300) or 300),
        "notify_enabled": bool(d.get("notify_enabled")),
        "notify_after": int(val("notify_after_minutes", 10) or 0),
        "notify_recovery": bool(d.get("notify_recovery")) if d.get("notify_recovery") is not None else True,
        "notify_group": max(int(val("notify_group_threshold", 5) or 5), 1),
        "telegram_token": _password(d, "telegram_bot_token"),
        "telegram_chat_id": (val("telegram_chat_id") or "").strip() or None,
        "notify_emails": _lines(val("notify_emails", "")),
        "retention_days": int(val("event_retention_days", 180) or 180),
    }


def raw_settings() -> dict:
    try:
        doc = frappe.get_cached_doc(SETTINGS)
    except frappe.DoesNotExistError:
        doc = None
    return raw_settings_from_doc(doc)


def validate_settings(s: dict, require_complete: bool = True) -> dict:
    labels = {"hubs": "Hub-et", "sync_key": "SSH Private Key Path", "known_hosts": "Known Hosts Path",
              "endpoint_host": "Endpoint Host", "hub_public_key": "Hub Public Key"}
    missing = [labels[k] for k in labels if not s[k]]
    if missing and require_complete:
        frappe.throw(_("VPN: mungon konfigurimi te WireGuard VPN Settings: {0}").format(", ".join(missing)))
    for hub in s["hubs"]:
        if not HUB_RE.match(hub):
            frappe.throw(_("VPN: hub i pavlefshëm: {0}").format(hub))
    if s["hub_public_key"] and not KEY_RE.match(s["hub_public_key"]):
        frappe.throw(_("VPN: Hub Public Key s'është public key i vlefshëm WireGuard"))
    if s["endpoint_host"] and not HOST_RE.match(str(s["endpoint_host"])):
        frappe.throw(_("VPN: Endpoint Host i pavlefshëm"))
    if not 1 <= s["endpoint_port"] <= 65535:
        frappe.throw(_("VPN: Endpoint Port duhet 1-65535"))
    try:
        pool = ipaddress.ip_network(s["pool"])
        hub_ip = ipaddress.ip_address(s["hub_tunnel_ip"])
        for r in s["reserved"]:
            ipaddress.ip_network(r, strict=False)
    except ValueError as exc:
        frappe.throw(_("VPN: rrjet/IP i pavlefshëm: {0}").format(exc))
    if pool.version != 4 or hub_ip not in pool:
        frappe.throw(_("VPN: Hub Tunnel IP duhet të jetë brenda Pool CIDR (IPv4)"))
    if not isinstance(s["forwards"], list):
        frappe.throw(_("VPN: Port-forward duhet të jetë listë JSON"))
    s["forwards"] = [_validate_forward(x) for x in s["forwards"]]
    if len({x["name"] for x in s["forwards"]}) != len(s["forwards"]):
        frappe.throw(_("VPN: emrat te Port-forward duhet të jenë unikë"))
    if s["offline_after"] < 60:
        frappe.throw(_("VPN: 'Offline pas' duhet të jetë të paktën 60 sekonda"))
    if s["telegram_chat_id"] and not re.match(r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{4,64})$", s["telegram_chat_id"]):
        frappe.throw(_("VPN: Telegram Chat ID duhet të jetë numër (p.sh. -1001234567890) ose @kanali"))
    return s


def settings() -> dict:
    return validate_settings(raw_settings(), require_complete=True)


def _validate_forward(fwd: dict) -> dict:
    if not isinstance(fwd, dict):
        frappe.throw(_("VPN: çdo element te Port-forward duhet të jetë objekt JSON"))
    name = str(fwd.get("name", ""))
    try:
        ext, internal = int(fwd.get("ext_port", 0)), int(fwd.get("int_port", 0))
    except (TypeError, ValueError):
        ext = internal = 0
    proto = str(fwd.get("proto", "tcp"))
    if not FWD_NAME_RE.match(name) or not (1 <= ext <= 65535) or not (1 <= internal <= 65535) \
            or proto not in ("tcp", "udp"):
        frappe.throw(_("VPN: Port-forward ka element të pavlefshëm: {0}").format(fwd))
    return {"name": name, "ext_port": ext, "int_port": internal, "proto": proto}


def require_role():
    frappe.only_for(list(ROLES))


# --------------------------------------------------------------------------- IP-të e tuneleve
def allocate_ip(s: dict, used: set[str]) -> str:
    pool = ipaddress.ip_network(s["pool"])
    reserved = [ipaddress.ip_network(r, strict=False) for r in s["reserved"]]
    hub = ipaddress.ip_address(s["hub_tunnel_ip"])
    for ip in pool.hosts():
        last_octet = int(ip) & 0xFF
        if last_octet in (0, 255) or ip == hub or any(ip in block for block in reserved):
            continue                     # .0/.255 shmangen (disa pajisje i trajtojnë keq)
        text = str(ip)
        if text not in used:
            return text
    frappe.throw(_("VPN: pool-i i IP-ve është plot — zgjero Pool CIDR te Settings."))
    return ""


def next_tunnel_ip(exclude: set[str] | None = None) -> str:
    """IP e lirë e radhës. Gjatë importit, IP-të e dhëna mbahen te frappe.flags (pa query të përsëritura)."""
    s = validate_settings(raw_settings(), require_complete=False)
    cache = frappe.flags.get("wg_used_ips")
    used = cache if cache is not None else set(frappe.get_all(DOCTYPE, pluck="tunnel_ip"))
    used = used | (exclude or set())
    ip = allocate_ip(s, used)
    if cache is not None:
        cache.add(ip)
    return ip


# --------------------------------------------------------------------------- historiku
def log_event(router: str | None, event: str, details: str | None = None, hub: str | None = None):
    try:
        frappe.get_doc({
            "doctype": EVENT, "router": router, "event": event, "hub": hub,
            "details": (details or "")[:2000] or None,
            "user": frappe.session.user if frappe.session.user not in (None, "Guest") else None,
        }).insert(ignore_permissions=True)
    except Exception:  # noqa: BLE001 — historiku s'duhet ta ndalë punën kryesore
        frappe.log_error(title="VPN: historiku s'u ruajt")


def _set_status(doc, status: str):
    if doc.status != status:
        doc.status = status
        doc.status_changed_on = now_datetime()


# --------------------------------------------------------------------------- hub-et (SSH)
def _cache():
    return frappe.cache() if callable(frappe.cache) else frappe.cache


@contextmanager
def hub_lock(timeout: int = 900, wait: int = 600):
    """Një ndryshim i hub-eve në një kohë (add / remove / reconcile), në të gjithë worker-at."""
    lock = _cache().lock(f"{frappe.local.site}:wg-hub-sync", timeout=timeout, blocking_timeout=wait)
    if not lock.acquire():
        raise frappe.ValidationError(_("VPN: sinkronizimi i hub-eve është i zënë — provo përsëri"))
    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001 — skadoi vetë
            pass


def _hub_exec(s: dict, hub: str, command: str, stdin_data: str | None = None, timeout: int = 60):
    import paramiko

    host, _sep, port = str(hub).partition(":")
    client = paramiko.SSHClient()
    client.load_host_keys(s["known_hosts"])           # host key i fiksuar (pa MITM)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(host, port=int(port or 22), username=s["sync_user"],
                   key_filename=s["sync_key"], timeout=10, banner_timeout=15,
                   auth_timeout=15, look_for_keys=False, allow_agent=False)
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()
    return code, out, err


def _on_all_hubs(s: dict, command: str, stdin_data: str | None = None):
    """Ekzekuton komandën te çdo hub. Kthen (rezultatet {hub: stdout}, gabimet [str])."""
    results, errors = {}, []
    for hub in s["hubs"]:
        try:
            code, out, err = _hub_exec(s, hub, command, stdin_data)
        except Exception as exc:  # noqa: BLE001 — rrjet, auth, host key
            errors.append(f"{hub}: {type(exc).__name__}: {exc}")
            continue
        if code != 0:
            errors.append(f"{hub}: {(err or out).strip()[:300]}")
        else:
            results[hub] = out
    return results, errors


def _check_peer_values(public_key: str, tunnel_ip: str):
    if not KEY_RE.match(public_key or ""):
        raise ValueError(f"public key i pavlefshëm: {public_key!r}")
    ipaddress.ip_address(tunnel_ip)


def _activate_key(s: dict, doc, public_key: str) -> list[str]:
    """Shton (ose ndërron) çelësin e router-it te të gjithë hub-et. Kthen gabimet."""
    with hub_lock():
        doc.reload()
        errors = []
        old_key = doc.public_key
        if old_key and old_key != public_key and KEY_RE.match(old_key):
            _res, errors = _on_all_hubs(s, f"remove {old_key}")
        _res, add_errors = _on_all_hubs(s, f"add {public_key} {doc.tunnel_ip}")
        errors += add_errors

        doc.public_key = public_key
        if old_key != public_key:
            doc.provisioned_on = now_datetime()      # periudhë pritjeje për handshake-un e parë
            doc.offline_notified = 0
        if errors:
            _set_status(doc, "Sync Error")
            doc.last_error = "; ".join(errors)[:1000]
        else:
            _set_status(doc, "Active")
            doc.last_error = None
            doc.hub_synced_on = now_datetime()
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    return errors


# --------------------------------------------------------------------------- API: router-i
def _payload(s: dict, doc) -> dict:
    from wireguard_vpn import router_script
    from wireguard_vpn.remote import lan_forwards

    forwards = lan_forwards(doc)
    title = f"{doc.site_name} ({doc.name})"
    return {
        "router": doc.name,
        "site_name": doc.site_name,
        "tunnel_ip": doc.tunnel_ip,
        "status": doc.status,
        "public_key": doc.public_key,
        "hub_public_key": s["hub_public_key"],
        "script": router_script.connect_script(
            title=title, tunnel_ip=doc.tunnel_ip, hub_tunnel_ip=s["hub_tunnel_ip"],
            hub_public_key=s["hub_public_key"], endpoint_host=s["endpoint_host"],
            endpoint_port=s["endpoint_port"], forwards=forwards),
        "remove_script": router_script.remove_script(title=title),
        "manual": {
            "address": f"{doc.tunnel_ip}/32",
            "peer_public_key": s["hub_public_key"],
            "endpoint_host": s["endpoint_host"],
            "endpoint_port": s["endpoint_port"],
            "allowed_ips": f"{s['hub_tunnel_ip']}/32",
            "keepalive": 25,
            "forwards": forwards,
            "hub_tunnel_ip": s["hub_tunnel_ip"],
        },
    }


@frappe.whitelist()
def get_router_script(router: str):
    """Skripti për CLI-në e router-it + vlerat për WebUI."""
    require_role()
    s = settings()
    doc = frappe.get_doc(DOCTYPE, router)
    doc.check_permission("read")
    if not doc.tunnel_ip:
        frappe.throw(_("VPN: router-i s'ka Tunnel IP — ruaje së pari."))
    return _payload(s, doc)


@frappe.whitelist()
def activate(router: str, public_key: str):
    """Admini ngjit public key-in që shfaqi router-i → router-i shtohet te të dy hub-et."""
    require_role()
    s = settings()
    doc = frappe.get_doc(DOCTYPE, router)
    doc.check_permission("write")
    public_key = (public_key or "").strip()
    if not KEY_RE.match(public_key):
        frappe.throw(_("VPN: ky s'është public key i vlefshëm WireGuard (44 karaktere, mbaron me '=')."))
    if public_key == s["hub_public_key"]:
        frappe.throw(_("VPN: ky është public key i HUB-it, jo i router-it. Kopjo rreshtin e fundit "
                       "që shfaq skripti në router."))
    other = frappe.db.get_value(DOCTYPE, {"public_key": public_key, "name": ["!=", doc.name]},
                                ["name", "site_name"], as_dict=True)
    if other:
        frappe.throw(_("VPN: ky çelës përdoret tashmë nga {0} ({1}).").format(other.site_name, other.name))

    old_key = doc.public_key
    errors = _activate_key(s, doc, public_key)
    doc.reload()
    if old_key and old_key != public_key:
        log_event(doc.name, "Key Changed", _("Çelës i ri: {0}…").format(public_key[:12]))
    elif old_key != public_key:
        log_event(doc.name, "Activated", _("Tunnel IP {0}").format(doc.tunnel_ip))
    if errors:
        log_event(doc.name, "Sync Error", "; ".join(errors))
    frappe.db.commit()
    return {"router": doc.name, "status": doc.status, "tunnel_ip": doc.tunnel_ip,
            "last_error": doc.last_error}


@frappe.whitelist()
def revoke(router: str):
    """Heq router-in nga hub-et. Konfigurimi në router mbetet derisa të ekzekutohet skripti i heqjes."""
    require_role()
    s = settings()
    doc = frappe.get_doc(DOCTYPE, router)
    doc.check_permission("write")
    errors = []
    with hub_lock():
        doc.reload()
        key = doc.public_key
        _set_status(doc, "Revoked")
        doc.public_key = None                 # ri-aktivizimi më vonë pranon çelës të ri pa konfirmim
        doc.offline_notified = 0
        doc.save(ignore_permissions=True)
        frappe.db.commit()                    # reconcile e sheh menjëherë si Revoked
        if key and KEY_RE.match(key):
            _res, errors = _on_all_hubs(s, f"remove {key}")
    # Nëse ndonjë hub s'u arrit, reconcile (replace-all) e heq gjithsesi — s'është më në listë.
    doc.db_set("last_error", "; ".join(errors)[:1000] if errors else None, update_modified=False)
    log_event(doc.name, "Revoked", "; ".join(errors) if errors else None)
    frappe.db.commit()
    msg = _("U revokua — router-i s'ka më qasje në VPN.")
    if errors:
        msg += " " + _("Disa hub-e s'u arritën — reconcile do ta përfundojë.")
    return {"router": doc.name, "hub_errors": errors, "message": msg}


# --------------------------------------------------------------------------- API: sistemi
@frappe.whitelist()
def test_hubs():
    """Provon SSH + wg-peer te çdo hub."""
    require_role()
    s = settings()
    results, errors = _on_all_hubs(s, "ping")
    return {"ok": {h: o.strip() for h, o in results.items()}, "errors": errors}


@frappe.whitelist()
def reconcile_now(max_drop: int = 20):
    """Sinkronizim i plotë i hub-eve tani (në background)."""
    frappe.only_for("System Manager")
    settings()
    frappe.enqueue(f"{__name__}.job_reconcile", queue="long", timeout=1800,
                   max_drop=int(max_drop), force=True)
    return {"message": _("Reconcile u fut në radhë. Rezultati shihet te Error Log nëse dështon.")}


@frappe.whitelist()
def check_now():
    """Kontrollon handshake-t tani (sinkron, ~2 lidhje SSH)."""
    require_role()
    settings()
    ran = job_handshakes(force=True)
    return {"ran": ran}


def hub_state() -> dict:
    return _cache().get_value(HUB_STATE_KEY) or {}


# --------------------------------------------------------------------------- punë periodike
def _enabled() -> bool:
    try:
        return bool(raw_settings()["enabled"])
    except Exception:  # noqa: BLE001 — mos e ndal scheduler-in për shkak të VPN-së
        return False


def _ts_to_system_datetime(ts: int):
    from zoneinfo import ZoneInfo

    from frappe.utils import get_system_timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(
        ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _update_hub_state(s: dict, results: dict, errors: list[str], latest: dict) -> list[tuple]:
    """Ruaj gjendjen e hub-eve (për dashboard) dhe kthe ndryshimet down/up për njoftime."""
    previous = hub_state()
    now = time.time()
    state, changes = {}, []
    err_by_hub = {}
    for e in errors:
        hub = e.split(":", 1)[0] if ":" in e else e
        for h in s["hubs"]:
            if e.startswith(f"{h}:"):
                hub = h
        err_by_hub[hub] = e
    for hub in s["hubs"]:
        ok = hub in results
        fresh = sum(1 for ts, h in latest.values() if h == hub and now - ts <= s["offline_after"])
        state[hub] = {"ok": ok, "error": None if ok else err_by_hub.get(hub, "pa përgjigje")[:300],
                      "fresh": fresh, "checked_at": str(now_datetime())[:19]}
        was_ok = (previous.get(hub) or {}).get("ok", True)
        if was_ok and not ok:
            changes.append(("down", hub, state[hub]["error"]))
        elif not was_ok and ok:
            changes.append(("up", hub, None))
    _cache().set_value(HUB_STATE_KEY, state)
    return changes


def job_handshakes(force: bool = False) -> bool:
    """Çdo 5 min: last_handshake, Active/Offline, hub aktiv dhe njoftimet."""
    if not force and not _enabled():
        return False
    lock = _cache().lock(f"{frappe.local.site}:wg-handshakes", timeout=300, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return False                            # një kontroll tjetër po punon tani
    try:
        _handshakes_locked(settings())
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            pass
    return True


def _handshakes_locked(s: dict):
    from wireguard_vpn import notify

    results, errors = _on_all_hubs(s, "handshakes")
    latest: dict[str, tuple[int, str]] = {}       # public_key → (ts, hub)
    for hub, out in results.items():
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
                ts = int(parts[1])
                if ts > latest.get(parts[0], (0, None))[0]:
                    latest[parts[0]] = (ts, hub)

    for kind, hub, err in _update_hub_state(s, results, errors, latest):
        log_event(None, "Hub Down" if kind == "down" else "Hub Up", err, hub=hub)
        if s["notify_enabled"]:
            notify.hub_changed(s, kind, hub, err)
    if not results:                     # asnjë hub s'u përgjigj → mos i shëno të gjithë offline
        frappe.log_error(title="VPN handshakes: asnjë hub s'u përgjigj", message="; ".join(errors)[:2000])
        frappe.db.commit()
        return

    now, now_dt = time.time(), now_datetime()
    routers = frappe.get_all(
        DOCTYPE, filters={"status": ["in", ["Active", "Offline"]]},
        fields=["name", "site_name", "city", "tunnel_ip", "public_key", "status", "last_handshake",
                "provisioned_on", "offline_notified", "mute_alerts", "active_hub"])
    went_offline, came_online = [], []
    for r in routers:
        ts, hub = latest.get(r.public_key or "", (0, None))
        updates = {}
        last_hs = r.last_handshake
        if ts:
            dt = _ts_to_system_datetime(ts)
            if not last_hs or dt > last_hs:
                updates["last_handshake"] = last_hs = dt
            if hub and hub != r.active_hub:
                updates["active_hub"] = hub
        fresh = bool(ts) and now - ts <= s["offline_after"]
        new_status = "Active" if fresh else "Offline"
        in_grace = (not fresh and r.provisioned_on
                    and (now_dt - r.provisioned_on).total_seconds() <= s["offline_after"]
                    and (not last_hs or last_hs < r.provisioned_on))
        if in_grace and r.status == "Active":
            new_status = "Active"               # sapo u aktivizua — pret handshake-un e parë
        if new_status != r.status:
            updates["status"] = new_status
            updates["status_changed_on"] = now_dt
            log_event(r.name, "Online" if new_status == "Active" else "Offline",
                      _("Handshake i fundit: {0}").format(str(last_hs)[:19] if last_hs else "—"),
                      hub=hub)
        if new_status == "Active" and r.offline_notified:
            updates["offline_notified"] = 0
            came_online.append(r)
        elif (new_status == "Offline" and not r.offline_notified and not r.mute_alerts
              and s["notify_enabled"] and last_hs
              and (now_dt - last_hs).total_seconds() >= s["notify_after"] * 60):
            updates["offline_notified"] = 1     # router-i ka qenë online dhe tani s'është
            went_offline.append(frappe._dict(r, last_handshake=last_hs))
        if updates:
            frappe.db.set_value(DOCTYPE, r.name, updates, update_modified=False)
    frappe.db.commit()

    if s["notify_enabled"]:
        if not s["notify_recovery"]:
            came_online = []
        notify.routers_changed(s, went_offline, came_online)


def job_reconcile(max_drop: int = 20, force: bool = False):
    """Çdo orë: hub-et marrin listën e plotë nga Frappe (rregullon çdo mospërputhje).

    Pas një revokimi masiv (>20%):
      bench --site SITE execute wireguard_vpn.vpn.job_reconcile --kwargs "{'max_drop': 100, 'force': True}"
    """
    if not force and not _enabled():
        return
    s = settings()
    with hub_lock():
        _reconcile_locked(s, max_drop)


def _reconcile_locked(s: dict, max_drop: int):
    frappe.db.rollback()                 # lexo gjendjen më të fundit të komituar
    routers = frappe.get_all(DOCTYPE, filters={"status": ["in", list(SYNCED_STATES)]},
                             fields=["name", "public_key", "tunnel_ip", "status"])
    lines, seen = [], set()
    for r in routers:
        try:
            _check_peer_values(r.public_key or "", r.tunnel_ip)
        except ValueError:
            continue
        if r.public_key in seen:
            continue
        seen.add(r.public_key)
        lines.append(f"{r.public_key} {r.tunnel_ip}\n")
    if not lines:
        return                           # asnjëherë mos e fshij krejt listën automatikisht
    results, errors = _on_all_hubs(s, f"replace-all --max-drop {int(max_drop)}", "".join(lines))
    if errors:
        frappe.log_error(title="VPN reconcile: disa hub-e dështuan", message="\n".join(errors)[:4000])
    if results and not errors:
        now_dt = now_datetime()
        for r in routers:
            if r.status == "Sync Error":
                frappe.db.set_value(DOCTYPE, r.name, {"status": "Active", "last_error": None,
                                                      "hub_synced_on": now_dt, "status_changed_on": now_dt,
                                                      "provisioned_on": now_dt},
                                    update_modified=False)
        frappe.db.commit()


def job_retry_sync():
    """Çdo 5 min: nëse ka router-a me "Sync Error", provo reconcile menjëherë."""
    if not _enabled():
        return
    if frappe.db.exists(DOCTYPE, {"status": "Sync Error"}):
        job_reconcile()


def job_cleanup():
    """Çdo ditë: fshin historikun më të vjetër se 'Mbaj historikun (ditë)'."""
    from frappe.utils import add_days

    days = raw_settings()["retention_days"]
    if days <= 0:
        return
    frappe.db.delete(EVENT, {"creation": ["<", add_days(now_datetime(), -days)]})
    frappe.db.commit()


def check_hubs():
    """Test nga terminali: bench --site SITE wireguard-vpn-check"""
    s = settings()
    results, errors = _on_all_hubs(s, "ping")
    for hub, out in results.items():
        print(f"OK    {hub}: {out.strip()}")
    for err in errors:
        print(f"GABIM {err}")
