package config

// OrgConfigsArrive reports whether org configs can reach a gateway with this
// config: the control plane syncs them (control_plane.url) or an operator
// PUTs them with the admin token (admin.token, PUT /-/orgs/{org_id}/config).
// The plugins that read an org's ip_acl, budgets, tool_policy and alerting
// sections are then built whatever the node's own switches say.
func (c *Config) OrgConfigsArrive() bool {
	return c.ControlPlane.URL != "" || c.Admin.Token != ""
}
