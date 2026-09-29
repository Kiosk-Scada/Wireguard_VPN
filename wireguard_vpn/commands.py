import click
from frappe.commands import get_site, pass_context


@click.command("wireguard-vpn-check")
@pass_context
def wireguard_vpn_check(context):
    """Teston lidhjen SSH me hub-et (wg-peer ping)."""
    import frappe

    site = get_site(context)
    frappe.init(site=site)
    frappe.connect()
    try:
        from wireguard_vpn.vpn import check_hubs

        check_hubs()
    finally:
        frappe.destroy()


commands = [wireguard_vpn_check]
