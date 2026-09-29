import frappe
from frappe.utils import now_datetime

from wireguard_vpn.remote import has_remote_role, remote_enabled_for_user
from wireguard_vpn.vpn import DOCTYPE, EVENT, KEY_RE, hub_state, raw_settings


@frappe.whitelist()
def get_data():
    if not has_remote_role():          # System Manager, VPN Manager, VPN Remote Access
        frappe.throw("Not permitted", frappe.PermissionError)
    s = raw_settings()
    routers = frappe.get_all(
        DOCTYPE,
        fields=["name", "site_name", "site_code", "customer", "city", "tunnel_ip", "status",
                "last_handshake", "active_hub", "status_changed_on", "mute_alerts", "device_lan_ip"],
        order_by="site_name asc",
        limit_page_length=0,
    )
    remote = remote_enabled_for_user()
    if remote:
        by_router = {}
        for svc in frappe.get_all("VPN Router Service", filters={"parenttype": DOCTYPE, "enabled": 1},
                                  fields=["parent", "name", "service_name", "protocol", "target", "lan_ip"],
                                  order_by="idx asc", limit_page_length=0):
            by_router.setdefault(svc.pop("parent"), []).append(svc)
        for r in routers:
            r["services"] = by_router.get(r.name, [])
    counts = {k: 0 for k in ("Active", "Offline", "Pending", "Sync Error", "Revoked")}
    for r in routers:
        counts[r.status] = counts.get(r.status, 0) + 1
    events = frappe.get_all(
        EVENT, fields=["name", "router", "site_name", "event", "details", "hub", "creation"],
        order_by="creation desc", limit_page_length=30)
    cached = hub_state()
    hubs = [{"hub": h, **(cached.get(h) or {"ok": None, "fresh": None, "checked_at": None, "error": None})}
            for h in s["hubs"]]
    missing = [label for key, label in (("hubs", "Hub-et"), ("hub_public_key", "Hub Public Key"),
                                        ("endpoint_host", "Endpoint Host"), ("sync_key", "SSH Key Path"),
                                        ("known_hosts", "Known Hosts Path")) if not s[key]]
    if s["hub_public_key"] and not KEY_RE.match(s["hub_public_key"]):
        missing.append("Hub Public Key (i pavlefshëm)")
    return {
        "routers": routers,
        "counts": counts,
        "total": len(routers),
        "events": events,
        "hubs": hubs,
        "now": str(now_datetime()),
        "settings": {
            "enabled": s["enabled"],
            "missing": missing,
            "endpoint": f"{s['endpoint_host']}:{s['endpoint_port']}" if s["endpoint_host"] else None,
            "notify_enabled": s["notify_enabled"],
            "offline_after": s["offline_after"],
            "is_admin": "System Manager" in frappe.get_roles(),
            "remote": remote,
        },
    }
