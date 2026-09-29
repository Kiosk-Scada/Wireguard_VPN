frappe.listview_settings["VPN Router Event"] = {
	add_fields: ["event"],
	get_indicator(doc) {
		const colors = {
			Created: "blue", Activated: "green", "Key Changed": "orange", Online: "green",
			Offline: "orange", "Sync Error": "red", Revoked: "gray", "Hub Down": "red", "Hub Up": "green", "Remote Session": "purple",
		};
		return [__(doc.event), colors[doc.event] || "gray", "event,=," + doc.event];
	},
};
