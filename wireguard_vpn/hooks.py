app_name = "wireguard_vpn"
app_title = "WireGuard VPN"
app_publisher = "Bleron"
app_description = "WireGuard VPN për router-at RUT142: lidhja, monitorimi dhe njoftimet nga Frappe"
app_email = "bleronos6@gmail.com"
app_license = "mit"
required_apps = ["frappe"]

# App-i është i pavarur: DocType-t e veta (VPN Router, VPN Router Event, WireGuard VPN Settings),
# faqja /app/vpn-dashboard dhe workspace "WireGuard VPN". S'varet nga asnjë DocType tjetër.

before_install = "wireguard_vpn.install.before_install"
after_install = "wireguard_vpn.install.after_install"
after_migrate = "wireguard_vpn.install.after_migrate"

scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "wireguard_vpn.vpn.job_handshakes",
            "wireguard_vpn.vpn.job_retry_sync",
        ],
    },
    "hourly_long": [
        "wireguard_vpn.vpn.job_reconcile",
    ],
    "daily": [
        "wireguard_vpn.vpn.job_cleanup",
    ],
}
