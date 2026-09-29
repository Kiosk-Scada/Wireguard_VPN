frappe.listview_settings["VPN Router"] = {
	add_fields: ["status", "tunnel_ip", "last_handshake"],
	get_indicator(doc) {
		const colors = { Active: "green", Offline: "orange", Pending: "blue", "Sync Error": "red", Revoked: "gray" };
		const labels = { Active: __("Online") };
		return [labels[doc.status] || __(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("VPN Dashboard"), () => frappe.set_route("vpn-dashboard"));
		listview.page.add_inner_button(__("Import CSV"), () => {
			frappe.route_options = { import: 1 };
			frappe.set_route("vpn-dashboard");
		});
	},
};
