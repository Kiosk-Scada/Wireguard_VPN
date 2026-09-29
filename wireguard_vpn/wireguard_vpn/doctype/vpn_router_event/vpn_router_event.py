# Copyright (c) 2026, Bleron and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class VPNRouterEvent(Document):
    def before_insert(self):
        if not self.router and self.hub and not self.site_name:
            self.site_name = f"Hub {self.hub}"
