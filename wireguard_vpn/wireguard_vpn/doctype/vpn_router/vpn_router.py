# Copyright (c) 2026, Bleron and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from wireguard_vpn.remote import forwards_signature, validate_services
from wireguard_vpn.vpn import EVENT, LAN_IP_RE, log_event, next_tunnel_ip


class VPNRouter(Document):
    def before_insert(self):
        if not self.tunnel_ip:
            self.tunnel_ip = next_tunnel_ip()
            self.flags.auto_ip = True
        if not self.status:
            self.status = "Pending"
        if not self.services:
            self.append("services", {"service_name": "SSH router", "protocol": "SSH", "target": "Router",
                                     "port": 22, "username": "root", "enabled": 1})

    def validate(self):
        self.site_name = (self.site_name or "").strip()
        for field in ("site_code", "customer", "city", "contact_name", "contact_phone",
                      "router_serial", "device_lan_ip"):
            if self.get(field):
                self.set(field, self.get(field).strip())
        if not self.site_name:
            frappe.throw(_("Emri i lokacionit është i detyrueshëm."))
        other = frappe.db.get_value("VPN Router", {"site_name": self.site_name, "name": ["!=", self.name]})
        if other:
            frappe.throw(_("Lokacioni '{0}' ekziston tashmë ({1}).").format(self.site_name, other))
        if self.site_code:
            other = frappe.db.get_value("VPN Router", {"site_code": self.site_code, "name": ["!=", self.name]})
            if other:
                frappe.throw(_("Kodi '{0}' përdoret tashmë nga {1}.").format(self.site_code, other))
        if self.device_lan_ip and not LAN_IP_RE.match(self.device_lan_ip):
            frappe.throw(_("IP LAN e pajisjes duhet të jetë IPv4 private, p.sh. 192.168.1.50"))
        if self.status in ("Active", "Offline", "Sync Error") and not self.public_key:
            frappe.throw(_("Router-i s'mund të jetë {0} pa public key.").format(self.status))
        if not self.is_new() and self.has_value_changed("tunnel_ip"):
            frappe.throw(_("Tunnel IP s'mund të ndryshohet."))
        validate_services(self)

    def on_update(self):
        before = self.get_doc_before_save()
        if before and self.public_key and not self.flags.from_migration and forwards_signature(before) != forwards_signature(self):
            frappe.msgprint(
                _("Portet e pajisjeve në LAN ndryshuan. Ekzekuto sërish skriptin në router "
                  "(VPN → Skripti / çelësi i ri) — mund ta bësh edhe nga <b>Lidhu → SSH router</b>. "
                  "Çelësi i router-it mbetet i njëjtë."),
                title=_("Përditëso router-in"), indicator="orange")

    def db_insert(self, *args, **kwargs):
        # Dy insert-e paralele mund të marrin të njëjtën IP; indeksi unik e ndalon të dytin → IP tjetër.
        tried = set()
        for attempt in range(6):
            try:
                return super().db_insert(*args, **kwargs)
            except frappe.UniqueValidationError:
                if not self.flags.auto_ip or attempt == 5 or not frappe.db.sql(
                        "select 1 from `tabVPN Router` where tunnel_ip=%s for update", self.tunnel_ip):
                    raise
                frappe.clear_last_message()
                tried.add(self.tunnel_ip)
                self.tunnel_ip = next_tunnel_ip(exclude=tried)

    def after_insert(self):
        details = None
        if self.flags.from_import:
            details = _("Import CSV")
        elif self.flags.from_migration:
            details = _("Migruar nga v1 (WireGuard Peer)")
        log_event(self.name, "Created", details)

    def on_trash(self):
        if self.public_key and self.status != "Revoked":
            frappe.throw(_("Bëj së pari 'Revoke VPN' (që ta heqë nga hub-et), pastaj fshije."))
        frappe.db.delete(EVENT, {"router": self.name})
