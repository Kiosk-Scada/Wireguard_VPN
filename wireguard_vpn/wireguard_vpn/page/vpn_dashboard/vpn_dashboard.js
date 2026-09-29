// WireGuard VPN — Dashboard: gjendja e të gjithë router-ave, hub-et, historiku, importi CSV.

frappe.pages["vpn-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("VPN Dashboard"), single_column: true });
	wrapper.vpn_dashboard = new WireGuardVPNDashboard(page, wrapper);
};

frappe.pages["vpn-dashboard"].on_page_show = function (wrapper) {
	const dash = wrapper.vpn_dashboard;
	if (!dash) return;
	dash.refresh();
	if (frappe.route_options && frappe.route_options.import) {
		frappe.route_options = null;
		dash.import_dialog();
	}
};

class WireGuardVPNDashboard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.filter = { status: "", q: "" };
		this.sort = { key: "site_name", dir: 1 };
		this.limit = 300;
		this.data = null;
		this.setup_actions();
		this.make_layout();
		this.timer = setInterval(() => {
			if (frappe.get_route()[0] === "vpn-dashboard" && !document.hidden) this.refresh(true);
		}, 60000);
	}

	setup_actions() {
		this.page.set_primary_action(__("Shto router"), () => frappe.new_doc("VPN Router"), "add");
		this.page.add_inner_button(__("Import CSV"), () => this.import_dialog());
		this.page.add_inner_button(__("Kontrollo tani"), () => {
			frappe.call({
				method: "wireguard_vpn.vpn.check_now",
				freeze: true,
				freeze_message: __("Po pyes hub-et për handshake..."),
				callback: (r) => {
					if (!r.message.ran) frappe.show_alert({ message: __("Një kontroll tjetër po punon — provo pas pak."), indicator: "orange" });
					this.refresh();
				},
			});
		});
		this.page.add_inner_button(__("Testo hub-et"), () => {
			frappe.call({
				method: "wireguard_vpn.vpn.test_hubs",
				freeze: true,
				callback: (r) => {
					const esc = frappe.utils.escape_html;
					const lines = Object.entries(r.message.ok).map(([h, o]) => `<span class="indicator-pill green">OK</span> ${esc(h)}: ${esc(o)}`)
						.concat(r.message.errors.map((e) => `<span class="indicator-pill red">${__("GABIM")}</span> ${esc(e)}`));
					frappe.msgprint({ title: __("Hub-et"), message: lines.join("<br>") || __("S'ka hub-e"), indicator: r.message.errors.length ? "red" : "green" });
				},
			});
		});
		this.page.add_menu_item(__("Lista e router-ave"), () => frappe.set_route("List", "VPN Router"));
		this.page.add_menu_item(__("Historiku"), () => frappe.set_route("List", "VPN Router Event"));
		this.page.add_menu_item(__("Settings"), () => frappe.set_route("Form", "WireGuard VPN Settings"));
	}

	make_layout() {
		this.$body = $(`
<div class="wg-dash">
  <style>
    .wg-dash { padding: 4px 0 24px; }
    .wg-banner { margin-bottom: 12px; }
    .wg-cards { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
    .wg-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md, 8px);
      padding: 10px 12px; cursor: pointer; transition: border-color .15s; }
    .wg-card:hover, .wg-card.active { border-color: var(--primary); }
    .wg-card .n { font-size: 22px; font-weight: 600; line-height: 1.2; }
    .wg-card .l { font-size: 12px; color: var(--text-muted); }
    .wg-hubs { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; margin-bottom: 14px; }
    .wg-hub { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md, 8px); padding: 10px 12px; font-size: 12px; }
    .wg-hub b { font-size: 13px; }
    .wg-grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 1fr); gap: 14px; }
    .wg-panel { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md, 8px); }
    .wg-toolbar { display: flex; gap: 8px; padding: 10px; border-bottom: 1px solid var(--border-color); flex-wrap: wrap; align-items: center; }
    .wg-toolbar input { max-width: 320px; }
    .wg-toolbar .wg-count { margin-left: auto; color: var(--text-muted); font-size: 12px; }
    .wg-table-wrap { overflow-x: auto; }
    .wg-table { width: 100%; font-size: 13px; margin: 0; }
    .wg-table th { font-size: 12px; color: var(--text-muted); font-weight: 500; cursor: pointer; white-space: nowrap;
      padding: 8px 10px; border-bottom: 1px solid var(--border-color); position: sticky; top: 0; background: var(--card-bg); }
    .wg-table td { padding: 7px 10px; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
    .wg-table tr.wg-row { cursor: pointer; }
    .wg-table tr.wg-row:hover td { background: var(--subtle-fg, var(--control-bg)); }
    .wg-muted { color: var(--text-muted); font-size: 12px; }
    .wg-events { max-height: 640px; overflow-y: auto; }
    .wg-ev { padding: 8px 12px; border-bottom: 1px solid var(--border-color); font-size: 12px; }
    .wg-ev:last-child { border-bottom: none; }
    .wg-ev a { font-weight: 500; }
    .wg-panel h6 { margin: 0; padding: 10px 12px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
    .wg-more { padding: 10px; text-align: center; }
    .wg-city-sm { display: none; }
    @media (max-width: 767px) { .wg-city-sm { display: inline; } }
    @media (max-width: 767px) {
      .wg-table th:nth-child(2), .wg-table td:nth-child(2), .wg-table th:nth-child(3), .wg-table td:nth-child(3) { display: none; }
      .wg-toolbar input, .wg-toolbar select { max-width: none !important; flex: 1 1 100%; }
    }
    @media (max-width: 991px) {
      .wg-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .wg-grid { grid-template-columns: minmax(0, 1fr); }
    }
  </style>
  <div class="wg-banner"></div>
  <div class="wg-cards"></div>
  <div class="wg-hubs"></div>
  <div class="wg-grid">
    <div class="wg-panel">
      <div class="wg-toolbar">
        <input type="search" class="form-control input-sm wg-search" placeholder="${__("Kërko: lokacion, qytet, klient, IP, kod...")}">
        <select class="form-control input-sm wg-status" style="max-width:190px">
          <option value="">${__("Të gjitha statuset")}</option>
          <option value="Active">${__("Online")}</option>
          <option value="Offline">${__("Offline")}</option>
          <option value="Pending">${__("Në pritje")}</option>
          <option value="Sync Error">${__("Sync Error")}</option>
          <option value="Revoked">${__("Revokuar")}</option>
        </select>
        <span class="wg-count"></span>
      </div>
      <div class="wg-table-wrap"><table class="wg-table"><thead></thead><tbody></tbody></table></div>
      <div class="wg-more"></div>
    </div>
    <div class="wg-panel">
      <h6>${__("Ngjarjet e fundit")}</h6>
      <div class="wg-events"></div>
    </div>
  </div>
</div>`).appendTo(this.page.main);

		this.$body.find(".wg-search").on("input", frappe.utils.debounce((e) => {
			this.filter.q = (e.target.value || "").trim().toLowerCase();
			this.limit = 300;
			this.render_table();
		}, 200));
		this.$body.find(".wg-status").on("change", (e) => this.set_status(e.target.value));
	}

	set_status(status) {
		this.filter.status = status;
		this.limit = 300;
		this.$body.find(".wg-status").val(status);
		this.render_cards();
		this.render_table();
	}

	refresh(silent) {
		frappe.call({
			method: "wireguard_vpn.wireguard_vpn.page.vpn_dashboard.vpn_dashboard.get_data",
			freeze: !silent && !this.data,
			callback: (r) => {
				this.data = r.message;
				this.render();
			},
		});
	}

	render() {
		this.render_banner();
		this.render_cards();
		this.render_hubs();
		this.render_table();
		this.render_events();
	}

	render_banner() {
		const s = this.data.settings;
		const esc = frappe.utils.escape_html;
		let html = "";
		const link = s.is_admin ? ` <a href="/app/wireguard-vpn-settings">${__("Hap Settings")}</a>` : "";
		if (s.missing.length) {
			html = `<div class="alert alert-warning" style="margin:0">${__("Konfigurimi s'është i plotë — mungon: {0}.", [esc(s.missing.join(", "))])}${link}</div>`;
		} else if (!s.enabled) {
			html = `<div class="alert alert-info" style="margin:0">${__("Kontrolli automatik (çdo 5 min), njoftimet dhe reconcile janë të fikura: aktivizo 'Aktiv' te Settings.")}${link}</div>`;
		}
		this.$body.find(".wg-banner").html(html);
		const sub = [s.endpoint ? `Endpoint ${esc(s.endpoint)}` : null, s.notify_enabled ? __("njoftimet: ON") : __("njoftimet: OFF")]
			.filter(Boolean).join(" · ");
		this.page.set_title_sub(`<span class="wg-muted">${sub}</span>`);
	}

	render_cards() {
		const c = this.data.counts;
		const cards = [
			["", __("Gjithsej"), this.data.total, "var(--text-color)"],
			["Active", __("Online"), c.Active, "var(--green-500, #28a745)"],
			["Offline", __("Offline"), c.Offline, "var(--orange-500, #fd7e14)"],
			["Pending", __("Në pritje"), c.Pending, "var(--blue-500, #2490ef)"],
			["Sync Error", __("Sync Error"), c["Sync Error"], "var(--red-500, #e03636)"],
			["Revoked", __("Revokuar"), c.Revoked, "var(--gray-500, #98a2b3)"],
		];
		const $cards = this.$body.find(".wg-cards").empty();
		cards.forEach(([status, label, n, color]) => {
			$(`<div class="wg-card ${this.filter.status === status ? "active" : ""}">
				<div class="n" style="color:${color}">${n || 0}</div><div class="l">${label}</div></div>`)
				.on("click", () => this.set_status(this.filter.status === status ? "" : status))
				.appendTo($cards);
		});
	}

	render_hubs() {
		const esc = frappe.utils.escape_html;
		const $h = this.$body.find(".wg-hubs").empty();
		if (!this.data.hubs.length) return;
		this.data.hubs.forEach((h) => {
			let state, color;
			if (h.ok === null || h.ok === undefined) {
				state = __("pa të dhëna ende"); color = "gray";
			} else if (!h.ok) {
				state = __("NUK PËRGJIGJET"); color = "red";
			} else if (h.fresh > 0) {
				state = __("aktiv — mban trafikun"); color = "green";
			} else {
				state = __("gati (rezervë)"); color = "blue";
			}
			$h.append(`<div class="wg-hub">
				<div><span class="indicator ${color}"></span><b>${esc(h.hub)}</b></div>
				<div>${state}${h.ok ? ` · ${__("{0} router-a me handshake", [h.fresh || 0])}` : ""}</div>
				${h.error ? `<div class="text-danger" style="margin-top:2px">${esc(h.error)}</div>` : ""}
				<div class="wg-muted">${h.checked_at ? __("kontrolluar {0}", [frappe.datetime.comment_when(h.checked_at)]) : ""}</div>
			</div>`);
		});
	}

	rows() {
		const q = this.filter.q;
		const st = this.filter.status;
		let rows = this.data.routers.filter((r) => {
			if (st && r.status !== st) return false;
			if (!q) return true;
			return [r.site_name, r.site_code, r.city, r.customer, r.tunnel_ip, r.name]
				.some((v) => v && String(v).toLowerCase().includes(q));
		});
		const { key, dir } = this.sort;
		rows.sort((a, b) => {
			let x = a[key] || "", y = b[key] || "";
			if (key === "tunnel_ip") {
				const n = (ip) => (ip || "0.0.0.0").split(".").reduce((acc, o) => acc * 256 + (+o), 0);
				x = n(a.tunnel_ip); y = n(b.tunnel_ip);
			}
			return x < y ? -dir : x > y ? dir : 0;
		});
		return rows;
	}

	render_table() {
		const esc = frappe.utils.escape_html;
		const cols = [
			["site_name", __("Lokacioni")], ["city", __("Qyteti")], ["customer", __("Klienti")],
			["tunnel_ip", __("Tunnel IP")], ["status", __("Statusi")], ["last_handshake", __("Handshake")],
		];
		const arrow = (k) => (this.sort.key === k ? (this.sort.dir > 0 ? " ▲" : " ▼") : "");
		const $thead = this.$body.find(".wg-table thead").html(
			`<tr>${cols.map(([k, l]) => `<th data-key="${k}">${l}${arrow(k)}</th>`).join("")}</tr>`);
		$thead.find("th").on("click", (e) => {
			const k = $(e.currentTarget).data("key");
			this.sort = { key: k, dir: this.sort.key === k ? -this.sort.dir : (k === "last_handshake" ? -1 : 1) };
			this.render_table();
		});

		const colors = { Active: "green", Offline: "orange", Pending: "blue", "Sync Error": "red", Revoked: "gray" };
		const labels = { Active: __("Online"), Pending: __("Në pritje"), Revoked: __("Revokuar") };
		const rows = this.rows();
		const shown = rows.slice(0, this.limit);
		const html = shown.map((r) => `
			<tr class="wg-row" data-name="${esc(r.name)}">
			  <td><div>${esc(r.site_name)}${r.mute_alerts ? ` <span class="wg-muted" title="${__("pa njoftime")}">🔕</span>` : ""}</div>
			      <div class="wg-muted">${esc(r.name)}${r.site_code ? " · " + esc(r.site_code) : ""}<span class="wg-city-sm">${r.city ? " · " + esc(r.city) : ""}</span></div></td>
			  <td>${esc(r.city || "")}</td>
			  <td>${esc(r.customer || "")}</td>
			  <td><code>${esc(r.tunnel_ip || "")}</code></td>
			  <td><span class="indicator-pill ${colors[r.status] || "gray"}">${labels[r.status] || __(r.status)}</span></td>
			  <td>${r.last_handshake ? `<span title="${esc(r.last_handshake)}">${frappe.datetime.comment_when(r.last_handshake)}</span>` : '<span class="wg-muted">—</span>'}
			      ${r.active_hub && r.status === "Active" ? `<div class="wg-muted">${esc(r.active_hub)}</div>` : ""}</td>
			</tr>`).join("");
		const $tb = this.$body.find(".wg-table tbody").html(html ||
			`<tr><td colspan="6" class="text-center wg-muted" style="padding:28px">${this.data.total ? __("Asnjë router s'përputhet me filtrin.") : __("S'ka router-a ende — kliko <b>Shto router</b> ose <b>Import CSV</b>.")}</td></tr>`);
		$tb.find("tr.wg-row").on("click", (e) => frappe.set_route("Form", "VPN Router", $(e.currentTarget).data("name")));
		this.$body.find(".wg-count").text(__("{0} nga {1}", [rows.length, this.data.total]));
		const $more = this.$body.find(".wg-more").empty();
		if (rows.length > shown.length) {
			$(`<button class="btn btn-xs btn-default">${__("Shfaq më shumë ({0})", [rows.length - shown.length])}</button>`)
				.on("click", () => { this.limit += 500; this.render_table(); }).appendTo($more);
		}
	}

	render_events() {
		const esc = frappe.utils.escape_html;
		const colors = {
			Created: "blue", Activated: "green", "Key Changed": "orange", Online: "green", Offline: "orange",
			"Sync Error": "red", Revoked: "gray", "Hub Down": "red", "Hub Up": "green",
		};
		const html = this.data.events.map((e) => {
			const who = e.router
				? `<a href="/app/vpn-router/${encodeURIComponent(e.router)}">${esc(e.site_name || e.router)}</a>`
				: `<b>${esc(e.hub || "")}</b>`;
			return `<div class="wg-ev">
				<div><span class="indicator-pill ${colors[e.event] || "gray"}" style="font-size:11px">${__(e.event)}</span> ${who}</div>
				${e.details ? `<div class="wg-muted" style="margin-top:2px">${esc(e.details)}</div>` : ""}
				<div class="wg-muted">${frappe.datetime.comment_when(e.creation)}</div></div>`;
		}).join("");
		this.$body.find(".wg-events").html(html || `<div class="wg-ev wg-muted">${__("S'ka ngjarje ende.")}</div>`);
	}

	// ------------------------------------------------------------------ Import CSV
	import_dialog() {
		const esc = frappe.utils.escape_html;
		let content = null;
		let checked = false;
		const d = new frappe.ui.Dialog({
			title: __("Import router-ash nga CSV"),
			size: "extra-large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "intro",
					options: `<div style="font-size:13px">
<p>${__("Nga Excel: <b>Ruaje si → CSV UTF-8</b>. Kolona e detyrueshme: <code>site_name</code> (ose <i>Emri</i> / <i>Lokacioni</i>). Opsionale: <code>site_code</code>, <code>customer</code>, <code>city</code>, <code>address</code>, <code>contact_name</code>, <code>contact_phone</code>, <code>router_serial</code>, <code>device_lan_ip</code>, <code>notes</code>.")}</p>
<p class="wg-muted">${__("Çdo router merr Tunnel IP menjëherë dhe mbetet 'Në pritje' derisa ta lidhësh (skripti + Aktivizo).")}</p>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0">
  <input type="file" accept=".csv,.txt,text/csv" class="wg-file">
  <button class="btn btn-xs btn-default wg-tpl">${__("Shkarko shembullin")}</button>
</div></div>`,
				},
				{ fieldtype: "Small Text", fieldname: "paste", label: __("… ose ngjite CSV këtu") },
				{ fieldtype: "Check", fieldname: "update_existing", label: __("Përditëso lokacionet që ekzistojnë (sipas emrit)") },
				{ fieldtype: "HTML", fieldname: "result" },
			],
			primary_action_label: __("Kontrollo"),
			primary_action: (v) => {
				const text = content || v.paste;
				if (!text || !text.trim()) return frappe.msgprint(__("Zgjidh një file CSV ose ngjite përmbajtjen."));
				run(text, !checked, v.update_existing ? 1 : 0);
			},
		});
		const reset = () => { checked = false; d.set_primary_action(__("Kontrollo")); };
		d.fields_dict.paste.$input.on("input", () => { content = null; reset(); });
		d.fields_dict.update_existing.$input.on("change", reset);
		d.$wrapper.find(".wg-file").on("change", (e) => {
			const file = e.target.files[0];
			if (!file) return;
			file.arrayBuffer().then((buf) => {
				try {
					content = new TextDecoder("utf-8", { fatal: true }).decode(buf);
				} catch (err) {
					content = new TextDecoder("windows-1250").decode(buf);   // Excel "CSV" i vjetër (ë, ç)
				}
				reset();
				d.fields_dict.result.$wrapper.html(`<div class="wg-muted">${__("U lexua: {0}", [esc(file.name)])}</div>`);
			});
		});
		d.$wrapper.find(".wg-tpl").on("click", () => {
			frappe.call({
				method: "wireguard_vpn.importer.template",
				callback: (r) => {
					const blob = new Blob(["﻿" + r.message], { type: "text/csv;charset=utf-8" });
					const a = document.createElement("a");
					a.href = URL.createObjectURL(blob);
					a.download = "vpn-routers-shembull.csv";
					document.body.appendChild(a); a.click();
					setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
				},
			});
		});

		const badge = { create: ["green", __("krijohet")], update: ["blue", __("përditësohet")], skip: ["gray", __("kapërcehet")], error: ["red", __("gabim")] };
		const run = (text, dry, update_existing) => {
			frappe.call({
				method: "wireguard_vpn.importer.import_routers",
				args: { content: text, dry_run: dry ? 1 : 0, update_existing },
				freeze: true,
				freeze_message: dry ? __("Po e kontrolloj...") : __("Po importoj..."),
				callback: (r) => {
					const x = r.message;
					const s = x.summary;
					const rows = x.rows.slice(0, 500).map((i) => `<tr>
						<td>${i.row}</td><td>${esc(i.site_name || "")}</td><td>${esc(i.city || "")}</td>
						<td><span class="indicator-pill ${badge[i.action][0]}">${badge[i.action][1]}</span></td>
						<td>${i.tunnel_ip ? `<code>${esc(i.tunnel_ip)}</code>` : ""}</td>
						<td class="text-danger">${esc(i.error || "")}</td></tr>`).join("");
					const head = `<p><b>${dry ? __("Kontrolli") : __("Rezultati")}:</b>
						${__("{0} krijohen", [s.create])} · ${__("{0} përditësohen", [s.update])} · ${__("{0} kapërcehen", [s.skip])} ·
						<span class="${s.error ? "text-danger" : ""}">${__("{0} gabime", [s.error])}</span></p>`;
					d.fields_dict.result.$wrapper.html(`${head}<div style="max-height:320px;overflow:auto">
						<table class="table table-bordered table-sm" style="font-size:12px"><thead><tr>
						<th>#</th><th>${__("Lokacioni")}</th><th>${__("Qyteti")}</th><th>${__("Veprimi")}</th><th>Tunnel IP</th><th>${__("Shënim")}</th>
						</tr></thead><tbody>${rows}</tbody></table></div>`);
					if (dry) {
						const n = s.create + s.update;
						if (n) {
							checked = true;
							d.set_primary_action(__("Importo {0} router-a", [n]));
						}
					} else {
						checked = false;
						d.set_primary_action(__("Mbyll"), () => d.hide());
						frappe.show_alert({ message: __("U importuan {0} router-a", [s.create + s.update]), indicator: "green" });
						this.refresh(true);
					}
				},
			});
		};
		d.show();
	}
}
