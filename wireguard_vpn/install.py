import frappe

ROLES = {
    "VPN Manager": {"two_factor_auth": 0},
    "VPN Remote Access": {"two_factor_auth": 1},   # vetëm "Lidhu" (SSH/VNC/RDP), pa menaxhim
}


def ensure_role():
    for role, extra in ROLES.items():
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1, **extra}).insert(
                ignore_permissions=True)
    frappe.db.commit()


def before_install():
    ensure_role()


def after_install():
    print(
        "\nWireGuard VPN u instalua.\n"
        "  1) Konfigurimi:  /app/wireguard-vpn-settings  (hub-et, çelësi, Floating IP) → Testo hub-et\n"
        "  2) Router-at:    /app/vpn-dashboard  → Shto router / Import CSV\n"
        "  3) Rolet: 'VPN Manager' (menaxhon router-at), 'VPN Remote Access' (vetëm Lidhu SSH/VNC/RDP)\n"
    )


def after_migrate():
    ensure_role()
