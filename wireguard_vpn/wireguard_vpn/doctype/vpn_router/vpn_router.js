// Copyright (c) 2026, Bleron and contributors
// For license information, please see license.txt

var WG_COLORS = { Active: "green", Offline: "orange", Pending: "blue", "Sync Error": "red", Revoked: "gray" };
var WG_LABELS = {
	Pending: __("Në pritje — s'është lidhur ende"),
	Active: __("Online"),
	Offline: __("Offline"),
	"Sync Error": __("Gabim sinkronizimi me hub-et"),
	Revoked: __("Revokuar"),
};

frappe.ui.form.on("VPN Router", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(__("Plotëso emrin e lokacionit dhe ruaje. Tunnel IP ndahet automatikisht, pastaj kliko <b>Lidh router-in</b>."), "blue");
			return;
		}
		frm.set_intro("");
		const st = frm.doc.status;
		frm.page.set_indicator(__(st), WG_COLORS[st] || "gray");
		wireguard_vpn_headline(frm);

		if (!frappe.user.has_role(["System Manager", "VPN Manager"])) {
			// "VPN Remote Access": vetëm Lidhu + historiku
			frm.add_custom_button(__("Historiku"), () => frappe.set_route("List", "VPN Router Event", { router: frm.doc.name }), __("VPN"));
			wireguard_vpn_remote_buttons(frm);
			return;
		}
		if (st === "Pending" || st === "Revoked") {
			frm.add_custom_button(__("Lidh router-in"), () => wireguard_vpn_connect_dialog(frm)).addClass("btn-primary");
		} else {
			frm.add_custom_button(__("Skripti / çelësi i ri"), () => wireguard_vpn_connect_dialog(frm), __("VPN"));
		}
		if (st !== "Pending" && st !== "Revoked") {
			frm.add_custom_button(__("Kontrollo tani"), () => {
				frappe.call({
					method: "wireguard_vpn.vpn.check_now",
					freeze: true,
					freeze_message: __("Po pyes hub-et..."),
					callback: () => frm.reload_doc(),
				});
			}, __("VPN"));
		}
		if (frm.doc.public_key && st !== "Revoked") {
			frm.add_custom_button(__("Revoke VPN"), () => {
				frappe.confirm(
					__("Ta heq qasjen VPN për <b>{0}</b>? Router-i shkëputet menjëherë nga hub-et.", [frappe.utils.escape_html(frm.doc.site_name)]),
					() => frappe.call({
						method: "wireguard_vpn.vpn.revoke",
						args: { router: frm.doc.name },
						freeze: true,
						callback: (r) => { frappe.msgprint(r.message.message); frm.reload_doc(); },
					}));
			}, __("VPN"));
		}
		if (st === "Revoked") {
			frm.add_custom_button(__("Skripti i heqjes nga router-i"), () => {
				frappe.call({
					method: "wireguard_vpn.vpn.get_router_script",
					args: { router: frm.doc.name },
					callback: (r) => wireguard_vpn_script_only(__("Hiq VPN nga router-i"), r.message.remove_script,
						`okvpn-remove-${frm.doc.name}.sh`),
				});
			}, __("VPN"));
		}
		frm.add_custom_button(__("Historiku"), () => frappe.set_route("List", "VPN Router Event", { router: frm.doc.name }), __("VPN"));
		wireguard_vpn_remote_buttons(frm);
		frm.add_custom_button(__("VPN Dashboard"), () => frappe.set_route("vpn-dashboard"), __("VPN"));
	},
});

function wireguard_vpn_headline(frm) {
	const d = frm.doc;
	const esc = frappe.utils.escape_html;
	let html = `<b>${esc(WG_LABELS[d.status] || d.status)}</b> · Tunnel IP <b>${esc(d.tunnel_ip || "—")}</b>`;
	if (d.last_handshake) html += ` · ${__("handshake")} ${frappe.datetime.comment_when(d.last_handshake)}`;
	if (d.active_hub && d.status !== "Revoked") html += ` · hub ${esc(d.active_hub)}`;
	if (["Active", "Offline"].includes(d.status)) {
		html += `<br><span class="text-muted">${__("Qasja (Tailscale)")}:</span>
			<code>ssh root@${esc(d.tunnel_ip)}</code> · <code>https://${esc(d.tunnel_ip)}</code>`;
		if (d.device_lan_ip) html += ` · ${__("pajisja")}: <code>ssh -p 2222 USER@${esc(d.tunnel_ip)}</code>`;
	}
	if (d.status === "Sync Error" && d.last_error) html += `<br><span class="text-danger">${esc(d.last_error)}</span>`;
	frm.dashboard.set_headline_alert(html, WG_COLORS[d.status] === "green" ? "green" : WG_COLORS[d.status] === "red" ? "red" : "blue");
}

function wireguard_vpn_download(filename, text) {
	const blob = new Blob([text], { type: "text/x-sh;charset=utf-8" });
	const a = document.createElement("a");
	a.href = URL.createObjectURL(blob);
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function wireguard_vpn_script_only(title, script, filename) {
	const esc = frappe.utils.escape_html;
	const d = new frappe.ui.Dialog({
		title,
		size: "large",
		fields: [{
			fieldtype: "HTML",
			fieldname: "body",
			options: `<p>${__("Ngjite në terminalin e router-it (SSH ose WebUI → System → Administration → CLI).")}</p>
<div style="display:flex;gap:6px;justify-content:flex-end;margin-bottom:4px">
  <button class="btn btn-xs btn-default wg-copy">${__("Kopjo")}</button>
  <button class="btn btn-xs btn-default wg-dl">${__("Shkarko .sh")}</button>
</div>
<pre style="max-height:320px;overflow:auto;font-size:11px;white-space:pre">${esc(script)}</pre>`,
		}],
	});
	d.show();
	d.$wrapper.find(".wg-copy").on("click", () => frappe.utils.copy_to_clipboard(script));
	d.$wrapper.find(".wg-dl").on("click", () => wireguard_vpn_download(filename, script));
}

function wireguard_vpn_connect_dialog(frm) {
	frappe.call({
		method: "wireguard_vpn.vpn.get_router_script",
		args: { router: frm.doc.name },
		freeze: true,
		callback: (r) => wireguard_vpn_show_connect(frm, r.message),
	});
}

function wireguard_vpn_show_connect(frm, p) {
	const esc = frappe.utils.escape_html;
	const m = p.manual;
	let fwdRows = "";
	(m.forwards || []).forEach((f) => {
		fwdRows += `<tr><td>${__("Port forward")} (${esc(f.service || f.name)})</td><td>${__("Zona")} <code>okvpn</code>, ${__("porti")} <code>${f.ext_port}</code>
			→ <code>${esc(f.lan_ip)}:${f.int_port}</code> (${esc(f.proto)}), ${__("burimi")} <code>${esc(m.hub_tunnel_ip)}</code></td></tr>`;
	});
	const already = !!p.public_key;
	const d = new frappe.ui.Dialog({
		title: __("Lidh router-in: {0} — IP {1}", [p.site_name, p.tunnel_ip]),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "steps",
				options: `
<div class="wg-steps" style="font-size:13px">
<p style="margin-bottom:6px"><b>${__("Hapi 1.")}</b> ${__("Hyr në router: <code>ssh root@192.168.1.1</code> (ose WebUI → System → Administration → CLI).")}</p>
<p style="margin-bottom:6px"><b>${__("Hapi 2.")}</b> ${__("Kopjo skriptin dhe ngjite krejt në terminalin e router-it. Router-i krijon çelësin e vet privat (s'largohet kurrë nga router-i).")}</p>
<div style="display:flex;gap:6px;justify-content:flex-end;margin-bottom:4px">
  <button class="btn btn-xs btn-default wg-copy">${__("Kopjo skriptin")}</button>
  <button class="btn btn-xs btn-default wg-dl">${__("Shkarko .sh")}</button>
</div>
<pre style="max-height:230px;overflow:auto;font-size:11px;white-space:pre">${esc(p.script)}</pre>
<p style="margin:6px 0"><b>${__("Hapi 3.")}</b> ${__("Në fund router-i shfaq <b>Router Public Key</b> (44 karaktere, mbaron me '='). Ngjite poshtë dhe kliko <b>Aktivizo</b>.")}</p>
<details style="margin:8px 0"><summary>${__("Pa SSH? Vlerat për WebUI (Services → VPN → WireGuard)")}</summary>
<table class="table table-bordered" style="margin-top:6px;font-size:12px">
  <tr><td style="width:34%">${__("Instanca / interface")}</td><td><code>okvpn</code> — ${__("kliko Generate për çelësat; kopjo PUBLIC key-in (jo private)")}</td></tr>
  <tr><td>${__("IP address")}</td><td><code>${esc(m.address)}</code></td></tr>
  <tr><td>${__("Peer: Public key")}</td><td><code>${esc(m.peer_public_key)}</code></td></tr>
  <tr><td>${__("Peer: Endpoint host / port")}</td><td><code>${esc(m.endpoint_host)}</code> / <code>${m.endpoint_port}</code></td></tr>
  <tr><td>${__("Peer: Allowed IPs")}</td><td><code>${esc(m.allowed_ips)}</code> · ${__("Route allowed IPs: ON")}</td></tr>
  <tr><td>${__("Peer: Persistent keepalive")}</td><td><code>${m.keepalive}</code></td></tr>
  <tr><td>${__("Firewall")}</td><td>${__("Zona <code>okvpn</code>: input REJECT; rregull që pranon gjithçka nga")} <code>${esc(m.hub_tunnel_ip)}</code></td></tr>
  ${fwdRows}
</table></details>
${already ? `<div class="alert alert-warning" style="font-size:12px;margin:0">${__("Ky router ka tashmë një çelës aktiv. Ngjit çelës të ri vetëm nëse router-i u resetua ose u ndërrua — çelësi i vjetër hiqet nga hub-et.")}</div>` : ""}
</div>`,
			},
			{
				fieldtype: "Data",
				fieldname: "public_key",
				label: __("Router Public Key"),
				description: __("Rreshti mes vijave ===== në fund të skriptit."),
			},
		],
		primary_action_label: __("Aktivizo"),
		primary_action(values) {
			const key = (values.public_key || "").trim();
			if (!key) return frappe.msgprint(__("Ngjit public key-in e router-it."));
			if (key === p.hub_public_key) return frappe.msgprint(__("Ky është çelësi i HUB-it. Kopjo rreshtin e fundit që shfaq router-i."));
			const go = () => frappe.call({
				method: "wireguard_vpn.vpn.activate",
				args: { router: p.router, public_key: key },
				freeze: true,
				freeze_message: __("Po e shtoj te hub-et..."),
				callback: (r) => {
					d.hide();
					const x = r.message;
					if (x.status === "Active") {
						frappe.msgprint({
							title: __("VPN aktiv"), indicator: "green",
							message: __("Router-i u shtua te të gjithë hub-et. IP e tunelit: <b>{0}</b>.<br>Router-i lidhet brenda ~30 sekondave; kliko <b>VPN → Kontrollo tani</b> për ta parë handshake-un.", [esc(x.tunnel_ip)]),
						});
					} else {
						frappe.msgprint({ title: __("Statusi: {0}", [x.status]), indicator: "orange", message: esc(x.last_error || "") });
					}
					frm.reload_doc();
				},
			});
			if (already && key !== p.public_key) {
				frappe.confirm(__("Çelësi ndryshon. A u resetua ose u ndërrua router-i?"), go);
			} else {
				go();
			}
		},
	});
	d.show();
	d.$wrapper.find(".wg-copy").on("click", () => frappe.utils.copy_to_clipboard(p.script));
	d.$wrapper.find(".wg-dl").on("click", () => wireguard_vpn_download(`okvpn-${p.router}.sh`, p.script));
}

// ------------------------------------------------------------------ Qasja nga browser-i (Guacamole)
function wireguard_vpn_remote_state() {
	if (!window.__wg_remote_state) {
		window.__wg_remote_state = frappe.xcall("wireguard_vpn.remote.ui_state").catch(() => ({ enabled: false }));
	}
	return window.__wg_remote_state;
}

function wireguard_vpn_open_remote(router, service) {
	// Dritarja hapet menjëherë (brenda klikimit), që browser-i të mos e bllokojë si popup.
	const w = window.open("", "_blank");
	if (w) {
		w.document.title = __("Duke u lidhur…");
		w.document.body.innerHTML = `<p style="font-family:sans-serif;padding:24px">${__("Duke u lidhur…")}</p>`;
	}
	frappe.call({
		method: "wireguard_vpn.remote.connect",
		args: { router, service },
		callback: (r) => {
			const x = r.message;
			if (x.offline) frappe.show_alert({ message: __("Router-i duket offline — lidhja mund të dështojë."), indicator: "orange" });
			if (w && !w.closed) {
				w.opener = null;
				w.location.href = x.url;
			} else {
				frappe.msgprint(`<a href="${encodeURI(x.url)}" target="_blank" rel="noopener">${__("Hape lidhjen: {0}", [frappe.utils.escape_html(x.title)])}</a>`);
			}
		},
		error: () => { if (w && !w.closed) w.close(); },
	});
}

function wireguard_vpn_remote_buttons(frm) {
	if (["Pending", "Revoked"].includes(frm.doc.status)) return;
	const services = (frm.doc.services || []).filter((s) => s.enabled);
	if (!services.length) return;
	wireguard_vpn_remote_state().then((st) => {
		if (!st || !st.enabled || frm.is_dirty()) return;
		services.forEach((s) => {
			const where = s.target === "Router" ? __("router") : s.lan_ip;
			frm.add_custom_button(frappe.utils.escape_html(`${s.protocol} · ${s.service_name} (${where})`),
				() => wireguard_vpn_open_remote(frm.doc.name, s.name), __("Lidhu"));
		});
	});
}
