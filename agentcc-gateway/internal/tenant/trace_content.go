package tenant

// TraceContentEnabled is explicit organization-admin consent for MCP span
// bodies. Missing config, privacy settings, or consent always means off.
// The node's body setting, content mode and retention remain separate gates.
func (c *OrgConfig) TraceContentEnabled() bool {
	return c != nil && c.Privacy != nil && c.Privacy.TraceContent
}
