"""v2.1: çdo router merr shërbimin "SSH router"; ai me 'IP LAN e pajisjes' edhe "SSH pajisja" (2222 → 22),
njësoj si port-forward-i global i v2.0 — pra router-at e konfiguruar s'kanë nevojë për skript të ri."""
import frappe


def execute():
    frappe.reload_doc("wireguard_vpn", "doctype", "vpn_router_service")
    frappe.reload_doc("wireguard_vpn", "doctype", "vpn_router")
    for name in frappe.get_all("VPN Router", pluck="name"):
        doc = frappe.get_doc("VPN Router", name)
        if doc.services:
            continue
        doc.append("services", {"service_name": "SSH router", "protocol": "SSH", "target": "Router",
                                "port": 22, "username": "root", "enabled": 1})
        doc.flags.from_migration = True
        doc.flags.ignore_permissions = True
        doc.save()
    frappe.db.commit()
