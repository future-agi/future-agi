package tenant

import (
	"reflect"
	"testing"
)

// A server added from the connector catalog records catalog_ref, and the
// connector's own virtual server carries its approvals and the tools its
// posture denies. The compatibility view, what a key bound to no virtual
// server is served when the org has no default one, leaves every such server
// out; a configured virtual server still serves the members it names.
func TestMCPCompatibilityViewLeavesOutCatalogServers(t *testing.T) {
	const servers = `"servers": {
		"own": {"url": "https://own.example/mcp"},
		"close": {"url": "https://close.example/mcp", "catalog_ref": "close/close-mcp@1.0.0"},
		"off": {"url": "https://off.example/mcp", "enabled": false}}`

	bare := mustParse(t, `{"mcp": {"access": {"default": "allow"}, `+servers+`}}`)
	compat, ok := bare.MCPDefaultVirtualServer("key_unbound")
	if ok {
		t.Fatal("a config with no virtual servers reported a configured default")
	}
	if !reflect.DeepEqual(compat.Members, []string{"own"}) {
		t.Errorf("compatibility view members = %v; want [own]: a catalog_ref server is reached only through its own virtual server", compat.Members)
	}

	configured := mustParse(t, `{"mcp": {"access": {"default": "allow"}, `+servers+`,
		"virtual_servers": {
			"close": {"members": ["close"], "access": {"keys": ["key_bound"]}},
			"desk": {"members": ["close", "own"], "access": {"default_for_org": true}}}}}`)
	bound, ok := configured.MCPDefaultVirtualServer("key_bound")
	if !ok || bound.Slug != "close" || !reflect.DeepEqual(bound.Members, []string{"close"}) {
		t.Errorf("key-bound default = %q %v, %v; want the connector's own virtual server", bound.Slug, bound.Members, ok)
	}
	org, ok := configured.MCPDefaultVirtualServer("key_unbound")
	if !ok || org.Slug != "desk" || !reflect.DeepEqual(org.Members, []string{"close", "own"}) {
		t.Errorf("org default = %q %v, %v; want desk with the members it names", org.Slug, org.Members, ok)
	}
}
