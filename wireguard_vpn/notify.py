"""Njoftimet: Telegram dhe/ose email kur router-at bien offline, kthehen online, ose bie një hub.

Për të shmangur spam-in:
  * njoftohet vetëm pasi router-i është pa handshake 'notify_after_minutes' (default 10)
  * për çdo kontroll (5 min) dërgohet maksimum një mesazh "offline" dhe një "online"
  * kur bien shumë njëherësh (> notify_group_threshold), mesazhi është përmbledhës
"""
from __future__ import annotations

import html

import frappe
from frappe import _
from frappe.utils import get_url_to_form

MAX_LISTED = 40
TELEGRAM_LIMIT = 3900


def _telegram(s: dict, text: str):
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{s['telegram_token']}/sendMessage",
        json={"chat_id": s["telegram_chat_id"], "text": text[:TELEGRAM_LIMIT],
              "disable_web_page_preview": True},
        timeout=10,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not resp.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {resp.status_code}: {data.get('description') or resp.text[:200]}")


def _email(s: dict, subject: str, text: str):
    body = "<pre style='font-family:inherit;white-space:pre-wrap'>" + html.escape(text) + "</pre>"
    frappe.sendmail(recipients=s["notify_emails"], subject=subject, message=body,
                    reference_doctype="WireGuard VPN Settings", reference_name="WireGuard VPN Settings")


def send(s: dict, subject: str, text: str) -> list[str]:
    """Dërgon në të gjitha kanalet e konfiguruara. Kthen gabimet (s'hedh exception)."""
    errors = []
    if s.get("telegram_token") and s.get("telegram_chat_id"):
        try:
            _telegram(s, text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Telegram: {exc}")
    if s.get("notify_emails"):
        try:
            _email(s, subject, text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Email: {exc}")
    if errors:
        frappe.log_error(title="VPN: njoftimi dështoi", message="\n".join(errors)[:2000])
    return errors


def channels(s: dict) -> list[str]:
    out = []
    if s.get("telegram_token") and s.get("telegram_chat_id"):
        out.append("Telegram")
    if s.get("notify_emails"):
        out.append("Email")
    return out


def _line(r) -> str:
    where = f", {r.city}" if r.get("city") else ""
    return f"• {r.site_name}{where} — {r.tunnel_ip}"


def _detail(r, offline: bool) -> str:
    lines = [_line(r)]
    if offline and r.get("last_handshake"):
        lines.append("   " + _("handshake i fundit: {0}").format(str(r.last_handshake)[:16]))
    try:
        lines.append("   " + get_url_to_form("VPN Router", r.name))
    except Exception:  # noqa: BLE001 — pa host_name
        pass
    return "\n".join(lines)


def _compose(routers: list, offline: bool, group: int) -> tuple[str, str]:
    n = len(routers)
    icon, word = ("🔴", "OFFLINE") if offline else ("🟢", "ONLINE")
    if n == 1:
        r = routers[0]
        subject = f"VPN {word}: {r.site_name}"
        return subject, f"{icon} VPN {word}\n" + _detail(r, offline)
    subject = _("VPN {0}: {1} router-a").format(word, n)
    head = f"{icon} " + _("{0} router-a {1}").format(n, word)
    if n <= group:
        body = "\n".join(_detail(r, offline) for r in routers)
    else:
        shown = sorted(routers, key=lambda r: r.site_name or "")[:MAX_LISTED]
        body = "\n".join(_line(r) for r in shown)
        if n > MAX_LISTED:
            body += "\n" + _("… dhe {0} të tjerë").format(n - MAX_LISTED)
        if offline:
            body += "\n\n" + _("Kur bien shumë njëherësh, kontrollo së pari hub-et / Floating IP.")
    return subject, f"{head}\n{body}"


def routers_changed(s: dict, went_offline: list, came_online: list):
    if not channels(s):
        return
    if went_offline:
        send(s, *_compose(went_offline, True, s["notify_group"]))
    if came_online:
        send(s, *_compose(came_online, False, s["notify_group"]))


def hub_changed(s: dict, kind: str, hub: str, error: str | None):
    if not channels(s):
        return
    if kind == "down":
        text = "⚠️ " + _("HUB NUK PËRGJIGJET: {0}").format(hub) + f"\n{error or ''}".rstrip()
        send(s, _("VPN: hub {0} s'përgjigjet").format(hub), text)
    else:
        send(s, _("VPN: hub {0} OK").format(hub), "✅ " + _("Hub-i u kthye: {0}").format(hub))


@frappe.whitelist()
def test_notifications():
    """Butoni te Settings: dërgon një mesazh prove në kanalet e konfiguruara."""
    frappe.only_for("System Manager")
    from wireguard_vpn.vpn import raw_settings, validate_settings

    s = validate_settings(raw_settings(), require_complete=False)
    ch = channels(s)
    if not ch:
        return {"ok": False, "errors": [_("S'ka kanal: vendos Telegram Bot Token + Chat ID, ose email-at.")]}
    errors = send(s, _("VPN: test njoftimi"), "✅ " + _("Test nga WireGuard VPN (Frappe) — njoftimet punojnë."))
    return {"ok": not errors, "channels": ch, "errors": errors}
