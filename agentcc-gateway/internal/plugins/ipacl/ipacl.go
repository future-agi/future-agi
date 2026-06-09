package ipacl

import (
	"context"
	"log/slog"
	"net"
	"strings"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plugin implements IP-based access control as a pipeline plugin.
// Priority 10: runs first so blocked IPs are rejected before auth or any processing.
type Plugin struct {
	enabled     bool
	allow       []net.IPNet
	deny        []net.IPNet
	tenantStore *tenant.Store
	orgNets     sync.Map // orgID -> *orgACLNets (cached parsed CIDRs per org)
}

// orgACLNets holds parsed CIDR networks for a single org.
type orgACLNets struct {
	allow []net.IPNet
	deny  []net.IPNet
}

// New creates a new IP ACL plugin from config.
func New(cfg config.IPACLConfig, tenantStore *tenant.Store) *Plugin {
	p := &Plugin{enabled: cfg.Enabled, tenantStore: tenantStore}

	for _, cidr := range cfg.Allow {
		if n := parseCIDROrIP(cidr); n != nil {
			p.allow = append(p.allow, *n)
		} else {
			slog.Warn("ip_acl: invalid allow entry, skipping", "entry", cidr)
		}
	}

	for _, cidr := range cfg.Deny {
		if n := parseCIDROrIP(cidr); n != nil {
			p.deny = append(p.deny, *n)
		} else {
			slog.Warn("ip_acl: invalid deny entry, skipping", "entry", cidr)
		}
	}

	return p
}

func (p *Plugin) Name() string  { return "ipacl" }
func (p *Plugin) Priority() int { return 10 } // Earliest: block bad IPs before any processing.

// AllowCount returns the number of allow rules.
func (p *Plugin) AllowCount() int { return len(p.allow) }

// DenyCount returns the number of deny rules.
func (p *Plugin) DenyCount() int { return len(p.deny) }

// ProcessRequest checks the client IP against allow/deny lists (global then per-org).
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	clientIP := net.ParseIP(rc.Metadata["client_ip"])

	// Global ACL check.
	if p.enabled {
		if clientIP == nil {
			return pipeline.ResultError(models.ErrForbidden("Client IP address could not be determined"))
		}

		// Deny list takes precedence.
		if p.matchesAny(clientIP, p.deny) {
			slog.Warn("ip_acl: request denied",
				"ip", clientIP.String(),
				"reason", "global_deny_list",
				"request_id", rc.RequestID,
			)
			return pipeline.ResultError(models.ErrForbidden("Access denied: IP address is not allowed"))
		}

		// If allow list is non-empty, IP must match.
		if len(p.allow) > 0 && !p.matchesAny(clientIP, p.allow) {
			slog.Warn("ip_acl: request denied",
				"ip", clientIP.String(),
				"reason", "not_in_global_allow_list",
				"request_id", rc.RequestID,
			)
			return pipeline.ResultError(models.ErrForbidden("Access denied: IP address is not allowed"))
		}
	}

	// Per-org ACL check (runs even if global ACL is disabled — org can have its own).
	if p.tenantStore != nil {
		orgID := rc.Metadata[tenant.MetadataKeyOrgID]
		if orgID != "" {
			orgCfg := p.tenantStore.Get(orgID)
			if orgCfg != nil && orgCfg.IPACL != nil && orgCfg.IPACL.Enabled {
				if clientIP == nil {
					return pipeline.ResultError(models.ErrForbidden("Client IP address could not be determined"))
				}
				nets := p.getOrgNets(orgID, orgCfg.IPACL)

				if p.matchesAny(clientIP, nets.deny) {
					slog.Warn("ip_acl: request denied by org acl",
						"ip", clientIP.String(),
						"org_id", orgID,
						"reason", "org_deny_list",
						"request_id", rc.RequestID,
					)
					return pipeline.ResultError(models.ErrForbidden("Access denied: IP address is not allowed"))
				}

				if len(nets.allow) > 0 && !p.matchesAny(clientIP, nets.allow) {
					slog.Warn("ip_acl: request denied by org acl",
						"ip", clientIP.String(),
						"org_id", orgID,
						"reason", "not_in_org_allow_list",
						"request_id", rc.RequestID,
					)
					return pipeline.ResultError(models.ErrForbidden("Access denied: IP address is not allowed"))
				}
			}
		}
	}

	return pipeline.ResultContinue()
}

// getOrgNets returns cached parsed CIDR networks for an org, parsing on first access.
func (p *Plugin) getOrgNets(orgID string, acl *tenant.IPACLConfig) *orgACLNets {
	if v, ok := p.orgNets.Load(orgID); ok {
		return v.(*orgACLNets)
	}

	nets := &orgACLNets{}
	for _, cidr := range acl.Allow {
		if n := parseCIDROrIP(cidr); n != nil {
			nets.allow = append(nets.allow, *n)
		}
	}
	for _, cidr := range acl.Deny {
		if n := parseCIDROrIP(cidr); n != nil {
			nets.deny = append(nets.deny, *n)
		}
	}

	p.orgNets.Store(orgID, nets)
	return nets
}

// ProcessResponse is a no-op for IP ACL.
func (p *Plugin) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// InvalidateOrg removes cached parsed CIDRs for an org so updated config is reloaded.
func (p *Plugin) InvalidateOrg(orgID string) {
	if p == nil || orgID == "" {
		return
	}
	p.orgNets.Delete(orgID)
}

// CheckIP checks a single IP against the allow/deny lists.
// Returns true if the IP is allowed, false if denied.
// Exported for per-key IP checking by the auth plugin.
func CheckIP(ip net.IP, allowedCIDRs []string) bool {
	for _, cidr := range allowedCIDRs {
		n := parseCIDROrIP(cidr)
		if n != nil && n.Contains(ip) {
			return true
		}
	}
	return false
}

func (p *Plugin) matchesAny(ip net.IP, networks []net.IPNet) bool {
	for _, n := range networks {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// parseCIDROrIP parses a CIDR string or a bare IP address into a net.IPNet.
func parseCIDROrIP(s string) *net.IPNet {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}

	// Try CIDR first.
	_, n, err := net.ParseCIDR(s)
	if err == nil {
		return n
	}

	// Try bare IP — wrap in /32 or /128.
	ip := net.ParseIP(s)
	if ip == nil {
		return nil
	}
	if ip.To4() != nil {
		return &net.IPNet{IP: ip.To4(), Mask: net.CIDRMask(32, 32)}
	}
	return &net.IPNet{IP: ip, Mask: net.CIDRMask(128, 128)}
}
