package server

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// keyGateIPACL builds the KeyAuth gate's ip_acl, as cmd/agentcc builds the
// pipeline's: with the node's rules when ip_acl is on, and with none of them
// (orgs' ip_acl only) where middleware.OrgIPACLWithoutNodeSwitch holds and a
// tenant store exists, so an org's ip_acl also covers the key-gated routes
// that make no model call. nil otherwise. It reads each org's ACL from
// tenantStore per request and re-parses one that changed, so it needs no
// cache invalidation.
func keyGateIPACL(cfg *config.Config, tenantStore *tenant.Store) *ipacl.Plugin {
	switch {
	case cfg.IPACL.Enabled:
		return ipacl.New(cfg.IPACL, tenantStore)
	case tenantStore != nil && middleware.OrgIPACLWithoutNodeSwitch(cfg):
		return ipacl.New(config.IPACLConfig{}, tenantStore)
	}
	return nil
}
