from wireguard_vpn.install import ensure_role


def execute():
    # para sinkronizimit të DocType-ve: lejet e reja i referohen rolit "VPN Remote Access"
    ensure_role()
