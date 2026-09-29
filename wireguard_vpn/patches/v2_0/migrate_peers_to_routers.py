"""v1 → v2: "WireGuard Peer" (i lidhur me DocType-in Client) → "VPN Router" (i pavarur).

* çdo peer bëhet VPN Router me TË NJËJTIN Tunnel IP dhe public key → hub-et s'preken fare
* tabela e vjetër ruhet si `_wgvpn_v1_peer_backup` (mund të fshihet më vonë me dorë)
* hiqet Client Script "WireGuard VPN - Buttons" dhe fushat MQTT / Client te Settings
"""
import frappe

OLD = "WireGuard Peer"
BACKUP = "_wgvpn_v1_peer_backup"
SETTINGS = "WireGuard VPN Settings"
OLD_SETTINGS_FIELDS = ("client_doctype", "client_id_field", "mqtt_host", "mqtt_port", "mqtt_username",
                       "mqtt_password", "mqtt_tls_ca")
CLIENT_SCRIPT = "WireGuard VPN - Buttons"


def _unique_site_name(base: str) -> str:
    base = (base or "Router").strip()[:130]
    name, n = base, 2
    while frappe.db.exists("VPN Router", {"site_name": name}):
        name, n = f"{base} ({n})", n + 1
    return name


def _migrate_rows():
    from wireguard_vpn.vpn import KEY_RE, LAN_IP_RE, STATUSES

    rows = frappe.db.sql(f"select * from `tab{OLD}` order by creation", as_dict=True)
    moved = 0
    for r in rows:
        if not r.get("tunnel_ip") or frappe.db.exists("VPN Router", {"tunnel_ip": r.tunnel_ip}):
            continue
        key = r.get("public_key") if KEY_RE.match(r.get("public_key") or "") else None
        status = r.get("status") if r.get("status") in STATUSES else "Pending"
        if status in ("Active", "Offline", "Sync Error") and not key:
            status = "Pending"
        notes = [f"Migruar nga v1 (WireGuard Peer {r.name}, klienti: {r.get('client') or '—'})."]
        if r.get("client_id"):
            notes.append(f"MQTT client_id: {r.client_id}")
        if r.get("pending_public_key"):
            notes.append(f"Çelës në pritje në v1 (s'u aktivizua): {r.pending_public_key}")
        lan = r.get("kiosk_ip") if LAN_IP_RE.match(r.get("kiosk_ip") or "") else None
        doc = frappe.get_doc({
            "doctype": "VPN Router",
            "site_name": _unique_site_name(r.get("client") or r.name),
            "customer": r.get("client"),
            "tunnel_ip": r.tunnel_ip,
            "public_key": key,
            "status": status,
            "last_handshake": r.get("last_handshake"),
            "provisioned_on": r.get("provisioned_on"),
            "hub_synced_on": r.get("hub_synced_on"),
            "status_changed_on": r.get("modified"),
            "device_lan_ip": lan,
            "last_error": r.get("last_error"),
            "notes": "\n".join(notes),
        })
        doc.flags.from_migration = True
        doc.insert(ignore_permissions=True)
        moved += 1
    return len(rows), moved


def execute():
    frappe.reload_doc("wireguard_vpn", "doctype", "vpn_router_event")
    frappe.reload_doc("wireguard_vpn", "doctype", "vpn_router")

    if frappe.db.table_exists(OLD):
        total, moved = _migrate_rows()
        frappe.db.commit()
        if not frappe.db.sql("show tables like %s", BACKUP):
            frappe.db.sql_ddl(f"rename table `tab{OLD}` to `{BACKUP}`")
        print(f"WireGuard VPN v2: {moved}/{total} peer-a u kaluan te VPN Router "
              f"(tabela e vjetër: {BACKUP}).")

    if frappe.db.exists("DocType", OLD):
        try:
            frappe.delete_doc("DocType", OLD, force=True, ignore_permissions=True, ignore_missing=True)
        except Exception:  # noqa: BLE001 — remove_orphan_doctypes e fshin më vonë
            frappe.db.rollback()

    if frappe.db.exists("Client Script", CLIENT_SCRIPT):
        frappe.delete_doc("Client Script", CLIENT_SCRIPT, force=True, ignore_permissions=True)

    frappe.db.delete("Singles", {"doctype": SETTINGS, "field": ["in", OLD_SETTINGS_FIELDS]})
    frappe.db.delete("__Auth", {"doctype": SETTINGS, "fieldname": "mqtt_password"})
    frappe.clear_cache(doctype=SETTINGS)
    frappe.db.commit()
