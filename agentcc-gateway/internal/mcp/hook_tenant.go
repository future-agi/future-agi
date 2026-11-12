package mcp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// hookResolveTenant is step 3 of every chain (chain.go).
const hookResolveTenant = "resolveTenant"

// CodePolicyUnavailable is resolveTenant's refusal: the org's MCP
// policy could not be evaluated, so the call is denied, never allowed.
const CodePolicyUnavailable = "policy_unavailable"

// resolveTenant loads the principal's org config snapshot into cc.OrgConfig
// and attaches the org's pool (TenantPoolOf), refusing with
// policy_unavailable when the org's MCP config cannot be evaluated: a config
// with no last-known-good version, or a resolver that fails or panics.
// Without a tenancy (SetTenancy) it does nothing, as before tenancy existed.
//
// Keep observeHook as its first statement.
func resolveTenant(s *Server, cc *CallContext) (d *Denial) {
	observeHook(cc, hookResolveTenant)
	t := s.Tenancy()
	if t == nil {
		return nil
	}
	defer func() {
		if r := recover(); r != nil {
			slog.Error("mcp tenancy resolution panicked; denying the call", "org", cc.Principal.OrgID, "panic", r)
			d = policyUnavailable()
		}
	}()
	snapshot, pool, err := t.Resolve(cc.Principal.OrgID)
	if err != nil || pool == nil {
		slog.Warn("mcp org policy unavailable; denying the call", "org", cc.Principal.OrgID, "error", err)
		return policyUnavailable()
	}
	cc.OrgConfig = snapshot
	cc.ctx = context.WithValue(cc.Context(), tenantPoolKey{}, pool)
	return nil
}

// policyUnavailable is the in-band denial of a call whose org policy could
// not be evaluated.
func policyUnavailable() *Denial {
	return &Denial{
		Type:    models.ErrTypeServer,
		Code:    CodePolicyUnavailable,
		Message: "the organization's MCP policy is unavailable; the call was not made",
	}
}

// --- The tenancy seam (read by the virtual servers, the credential resolver and the admin API) ---

// Tenancy resolves an org to its policy snapshot and its pool of upstream
// servers. tenancy.Manager implements it.
type Tenancy interface {
	// Resolve returns the org's MCP policy snapshot (the last org config
	// version its pool accepted; nil when the org has no config) and the
	// pool the org's principals are served from. orgID "" is a principal
	// with no org, served from the operator's static pool. An error means
	// the org's policy cannot be evaluated: the caller denies.
	Resolve(orgID string) (*tenant.OrgConfig, TenantPool, error)
	// Known reports whether orgID names an org this gateway serves: one with
	// a stored config, or the static pool's org.
	Known(orgID string) bool
	// ReloadStatic rebuilds the static YAML pool from a reloaded operator
	// config (/-/reload); only the servers whose upstream changed reconnect.
	ReloadStatic(cfg config.MCPConfig)
}

// TenantPool is one org's upstream servers. Later hooks read it from the
// call (TenantPoolOf) instead of the server's global registry.
type TenantPool interface {
	// OrgID is the org the pool serves ("" for the static pool).
	OrgID() string
	// Servers returns every server of the pool, sorted by id: the org's own,
	// the managed fagi.* servers the org may use and, for the static org,
	// the operator's YAML servers.
	Servers() []PoolServer
	// Server returns one server by id.
	Server(id string) (PoolServer, bool)
	// Client returns the connected client of serverID for one credential
	// identity: credKey "" is the server's shared credential, a user id is
	// that user's own client on a per_user server (which has no shared
	// client, so credKey "" is an error there). A non-empty auth becomes the
	// credential the client is built with; a later call with another auth for
	// the same credKey gets a client rebuilt with it. Calls through it default
	// to the server's timeout_ms.
	Client(serverID, credKey string, auth AuthOverride) (UpstreamClient, error)
	// CachedList returns the cached list for key, building and caching it
	// with build on a miss. Any change to the pool's servers, tools or policy
	// snapshot empties the cache.
	CachedList(key ListCacheKey, build func() []Tool) []Tool
}

// PoolServer is one upstream server of a pool, as hooks and the admin API
// read it.
type PoolServer struct {
	ID string
	// Source is where the server comes from: "org" (the org's pushed
	// config), "managed" (Deps.ManagedServers) or "static" (gateway YAML).
	Source  string
	Managed bool
	Builtin bool
	PerUser bool
	// SharedOAuth is a shared server whose organization account is signed
	// in over OAuth: its token is fetched when it is used.
	SharedOAuth bool

	Era                  string
	Transport            string
	TimeoutMs            int
	ToolsCacheTTLSeconds int
	ForwardHeaders       []string
	PropagateDepth       bool
	TrustedAnnotations   bool
	CredentialState      string

	Healthy         bool
	LastError       string
	LastRefreshedAt time.Time

	Tools     []Tool
	Resources []Resource
	Prompts   []Prompt
}

// ListCacheKey names one cached list within an org's pool: the
// virtual server, the sha256 of the principal's effective access rules
// (AccessHash) and, when the virtual server has a per_user member, the user.
type ListCacheKey struct {
	VirtualServer string
	AccessHash    string
	UserID        string
}

// AccessHash is the sha256 (hex) of the JSON of the effective access rules
// that decide a principal's list (key, agent and user rules, header
// narrowing, callback-token tools). Two principals share a cached list only
// when their rules hash the same.
func AccessHash(rules any) string {
	raw, err := json.Marshal(rules)
	if err != nil {
		raw = []byte("unhashable")
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

// tenantPoolKey is the context key resolveTenant stores the pool under.
type tenantPoolKey struct{}

// TenantPoolOf returns the pool resolveTenant attached to the call, or nil
// when no tenancy is installed or the step has not run.
func TenantPoolOf(cc *CallContext) TenantPool {
	if cc == nil {
		return nil
	}
	p, _ := cc.Context().Value(tenantPoolKey{}).(TenantPool)
	return p
}

// tenancies holds each server's tenancy. Server's fields are frozen, so
// SetTenancy keeps it here, keyed by the server (an integrator
// request asks for a Deps member to replace this).
var tenancies sync.Map // *Server → Tenancy

// SetTenancy installs the tenancy the chain and the admin API resolve orgs
// with. The wiring calls it once, before the server serves.
func (s *Server) SetTenancy(t Tenancy) {
	if t == nil {
		tenancies.Delete(s)
		return
	}
	tenancies.Store(s, t)
}

// Tenancy returns the installed tenancy, or nil.
func (s *Server) Tenancy() Tenancy {
	t, _ := tenancies.Load(s)
	tt, _ := t.(Tenancy)
	return tt
}

// noManagedServers is Deps.ManagedServers' default: no managed servers.
type noManagedServers struct{}

func (noManagedServers) ForOrg(string) []ClientConfig { return nil }
