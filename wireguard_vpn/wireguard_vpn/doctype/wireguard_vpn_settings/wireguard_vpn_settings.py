# Copyright (c) 2026, Bleron and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class WireGuardVPNSettings(Document):
    def validate(self):
        from wireguard_vpn import vpn

        s = vpn.raw_settings_from_doc(self)
        vpn.validate_settings(s, require_complete=bool(self.enabled))
        if self.notify_enabled:
            has_telegram = bool(self.telegram_bot_token) and bool(self.telegram_chat_id)
            if not has_telegram and not s["notify_emails"]:
                frappe.throw(_("Njoftimet: vendos Telegram Bot Token + Chat ID, ose të paktën një email."))
            if bool(self.telegram_bot_token) != bool(self.telegram_chat_id):
                frappe.throw(_("Telegram kërkon edhe Bot Token edhe Chat ID."))
        if self.notify_after_minutes is not None and self.notify_after_minutes < 0:
            frappe.throw(_("Minutat e njoftimit s'mund të jenë negative."))
        for email in s["notify_emails"]:
            frappe.utils.validate_email_address(email, throw=True)
        if self.has_value_changed("pool_cidr") and frappe.db.count("VPN Router"):
            frappe.msgprint(_("Kujdes: router-at ekzistues e mbajnë IP-në e tyre; pool-i i ri vlen për të rinjtë."),
                            indicator="orange")
