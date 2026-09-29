// Copyright (c) 2026, Bleron and contributors
// For license information, please see license.txt

frappe.ui.form.on("WireGuard VPN Settings", {
	refresh(frm) {
		const show = (title, lines, ok) =>
			frappe.msgprint({ title, message: lines.map((l) => frappe.utils.escape_html(l)).join("<br>"), indicator: ok ? "green" : "red" });

		frm.add_custom_button(__("Testo hub-et"), () => {
			frappe.call({
				method: "wireguard_vpn.vpn.test_hubs",
				freeze: true,
				freeze_message: __("Po lidhem me hub-et..."),
				callback: (r) => {
					const lines = Object.entries(r.message.ok).map(([h, o]) => `OK  ${h}: ${o}`)
						.concat(r.message.errors.map((e) => `GABIM  ${e}`));
					show(__("Hub-et"), lines.length ? lines : [__("S'ka hub-e të konfiguruara")], !r.message.errors.length);
				},
			});
		}, __("Testo"));

		frm.add_custom_button(__("Testo njoftimet"), () => {
			if (frm.is_dirty()) return frappe.msgprint(__("Ruaje së pari konfigurimin."));
			frappe.call({
				method: "wireguard_vpn.notify.test_notifications",
				freeze: true,
				callback: (r) => {
					const m = r.message;
					show(__("Njoftimet"), m.ok ? [__("U dërgua te: {0}", [m.channels.join(", ")])] : m.errors, m.ok);
				},
			});
		}, __("Testo"));

		frm.add_custom_button(__("Testo Guacamole"), () => {
			if (frm.is_dirty()) return frappe.msgprint(__("Ruaje së pari konfigurimin."));
			frappe.call({
				method: "wireguard_vpn.remote.test_guacamole",
				freeze: true,
				freeze_message: __("Po lidhem me Guacamole..."),
				callback: (r) => show(__("Guacamole"), [
					__("OK — çelësi pranohet (data source: {0})", [r.message.data_source]),
					__("Adresa e brendshme: {0}", [r.message.internal_url]),
					__("Punëtorët hapin: {0}", [r.message.public_url]),
				], true),
			});
		}, __("Testo"));

		frm.add_custom_button(__("Reconcile tani"), () => {
			frappe.confirm(__("Hub-et do të marrin listën e plotë të router-ave nga Frappe. Vazhdo?"), () =>
				frappe.call({
					method: "wireguard_vpn.vpn.reconcile_now",
					callback: (r) => frappe.show_alert({ message: r.message.message, indicator: "blue" }),
				}));
		}, __("Testo"));

		frm.add_custom_button(__("VPN Dashboard"), () => frappe.set_route("vpn-dashboard"));
	},
});
