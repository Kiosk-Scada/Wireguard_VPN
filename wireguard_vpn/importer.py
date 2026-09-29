"""Import i router-ave nga CSV (Excel → "Save as CSV"). Ndarësi , ; ose TAB njihet vetë.

Kolona e detyrueshme: emri i lokacionit (site_name / Emri / Lokacioni).
Router-at e importuar marrin Tunnel IP menjëherë dhe mbeten "Pending" derisa të lidhen
(skripti + Aktivizo), njëri pas tjetrit.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata

import frappe
from frappe import _

from wireguard_vpn.vpn import DOCTYPE, LAN_IP_RE, require_role

MAX_ROWS = 3000
FIELDS = {
    "site_name": ("site_name", "emri", "emri i lokacionit", "lokacioni", "site", "name", "lokacion"),
    "site_code": ("site_code", "kodi", "code", "kodi i lokacionit"),
    "customer": ("customer", "klienti", "kompania", "client", "company"),
    "city": ("city", "qyteti", "vendi"),
    "address": ("address", "adresa"),
    "contact_name": ("contact_name", "kontakti", "personi kontaktues", "contact", "personi"),
    "contact_phone": ("contact_phone", "telefoni", "phone", "tel", "nr telefoni"),
    "router_model": ("router_model", "modeli", "model"),
    "router_serial": ("router_serial", "serial", "nr serik", "nr. serik", "serial number"),
    "device_lan_ip": ("device_lan_ip", "ip lan", "lan ip", "ip e pajisjes", "ip lan e pajisjes",
                      "kiosk_ip", "kiosk ip"),
    "notes": ("notes", "shenime", "shënime", "komente", "note"),
}
TEMPLATE_HEADER = ["site_name", "site_code", "customer", "city", "address", "contact_name",
                   "contact_phone", "router_serial", "device_lan_ip", "notes"]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.replace("_", " ").strip().lower())


ALIASES = {_norm(alias): field for field, aliases in FIELDS.items() for alias in aliases}


def _parse(content: str) -> tuple[list[str], list[list[str]]]:
    content = (content or "").lstrip("﻿")
    if not content.strip():
        frappe.throw(_("CSV është bosh."))
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        class dialect(csv.excel):  # noqa: N801
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(content), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        frappe.throw(_("CSV duhet të ketë rreshtin e titujve dhe të paktën një router."))
    if len(rows) - 1 > MAX_ROWS:
        frappe.throw(_("Maksimumi {0} router-a për import.").format(MAX_ROWS))
    return rows[0], rows[1:]


def _map_header(header: list[str]) -> dict[int, str]:
    mapping = {}
    for i, title in enumerate(header):
        field = ALIASES.get(_norm(title))
        if field and field not in mapping.values():
            mapping[i] = field
    if "site_name" not in mapping.values():
        frappe.throw(_("S'u gjet kolona e emrit të lokacionit (site_name / Emri / Lokacioni). "
                       "Titujt e gjetur: {0}").format(", ".join(header)))
    return mapping


@frappe.whitelist()
def import_routers(content: str, dry_run: int = 1, update_existing: int = 0):
    """Kontrollon (dry_run=1) ose importon router-at nga CSV. Kthen raportin për çdo rresht."""
    require_role()
    frappe.has_permission(DOCTYPE, "create", throw=True)
    dry_run, update_existing = int(dry_run), int(update_existing)
    header, rows = _parse(content)
    mapping = _map_header(header)

    existing = {r.site_name.strip().lower(): r for r in frappe.get_all(
        DOCTYPE, fields=["name", "site_name", "site_code", "status"])}
    existing_codes = {(r.site_code or "").strip().lower(): r.name for r in existing.values() if r.site_code}
    seen_names, seen_codes = set(), set()
    report = []

    for n, row in enumerate(rows, start=2):
        values = {}
        for i, field in mapping.items():
            if i < len(row) and row[i].strip():
                values[field] = row[i].strip()
        name = values.get("site_name", "")
        item = {"row": n, "site_name": name, "city": values.get("city"), "action": "create", "error": None}
        key = name.lower()
        code = (values.get("site_code") or "").lower()
        if not name:
            item.update(action="error", error=_("mungon emri i lokacionit"))
        elif len(name) > 140:
            item.update(action="error", error=_("emri më i gjatë se 140 karaktere"))
        elif key in seen_names:
            item.update(action="error", error=_("emri përsëritet në CSV"))
        elif values.get("device_lan_ip") and not LAN_IP_RE.match(values["device_lan_ip"]):
            item.update(action="error", error=_("IP LAN e pavlefshme: {0}").format(values["device_lan_ip"]))
        elif code and code in seen_codes:
            item.update(action="error", error=_("kodi përsëritet në CSV"))
        elif key in existing:
            if update_existing:
                item.update(action="update", router=existing[key].name)
            else:
                item.update(action="skip", router=existing[key].name, error=_("ekziston — kapërcehet"))
        elif code and code in existing_codes:
            item.update(action="error", error=_("kodi përdoret nga {0}").format(existing_codes[code]))
        seen_names.add(key)
        if code:
            seen_codes.add(code)
        item["values"] = values
        report.append(item)

    if not dry_run:
        frappe.flags.wg_used_ips = set(frappe.get_all(DOCTYPE, pluck="tunnel_ip"))
        try:
            for item in report:
                if item["action"] not in ("create", "update"):
                    continue
                frappe.db.savepoint("wg_import_row")
                try:
                    if item["action"] == "create":
                        doc = frappe.get_doc({"doctype": DOCTYPE, **item["values"]})
                        doc.flags.from_import = True
                        doc.insert()
                        item["router"], item["tunnel_ip"] = doc.name, doc.tunnel_ip
                    else:
                        doc = frappe.get_doc(DOCTYPE, item["router"])
                        doc.update({k: v for k, v in item["values"].items() if k != "site_name"})
                        doc.save()
                        item["tunnel_ip"] = doc.tunnel_ip
                    item["done"] = True
                except Exception as exc:  # noqa: BLE001 — raportohet për rreshtin
                    frappe.db.rollback(save_point="wg_import_row")
                    frappe.clear_messages()
                    item.update(action="error", error=str(exc)[:300] or type(exc).__name__)
        finally:
            frappe.flags.wg_used_ips = None
        frappe.db.commit()

    for item in report:
        item.pop("values", None)
    summary = {k: sum(1 for i in report if i["action"] == k) for k in ("create", "update", "skip", "error")}
    return {"dry_run": bool(dry_run), "summary": summary, "rows": report, "total": len(report)}


@frappe.whitelist()
def template():
    """Shembull CSV për Excel."""
    require_role()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(TEMPLATE_HEADER)
    w.writerow(["Pika Petrol – Prishtinë 3", "PP-003", "Pika Petrol", "Prishtinë", "Rr. B, nr 12",
                "Arben K.", "+38344123456", "1101234567", "192.168.1.50", ""])
    w.writerow(["Pika Petrol – Ferizaj 1", "PP-011", "Pika Petrol", "Ferizaj", "", "", "", "", "", ""])
    return buf.getvalue()

