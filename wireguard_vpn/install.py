import frappe

ROLE = "VPN Manager"


def ensure_role():
    if not frappe.db.exists("Role", ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(
            ignore_permissions=True)
        frappe.db.commit()


def before_install():
    ensure_role()


def after_install():
    print(
        "\nWireGuard VPN u instalua.\n"
        "  1) Konfigurimi:  /app/wireguard-vpn-settings  (hub-et, çelësi, Floating IP) → Testo hub-et\n"
        "  2) Router-at:    /app/vpn-dashboard  → Shto router / Import CSV\n"
        "  3) Roli 'VPN Manager' u jep qasje stafit pa qenë System Manager.\n"
    )


def after_migrate():
    ensure_role()
