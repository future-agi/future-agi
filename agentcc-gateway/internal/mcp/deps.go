package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"sort"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Deps are the MCP chain's late-bound dependencies.
// Every member has a safe default. Each providing lane installs its member
// from its own wiring_mcp_<concern>.go file, and wiring_mcp.go hands the
// result to the server with SetDeps once every afterServer callback has run.
// Hooks read s.deps() at call time and never import a provider package.
// Frozen: members are only ever added.
type Deps struct {
	Events        models.EventSink // [discard]
	Dialer        Dialer           // [net.Dialer]
	ClientFactory ClientFactory    // [today's client, wrapped]
	// ClientAdmission is the one admission decision. Tenancy evaluates it at
	// entry construction, at every credential lookup and before every send.
	ClientAdmission ClientAdmissionPolicy

	SpendGuard Holder // [none; MCP billing installs the spend guard]
	// KeyLimits is the customer-budget hold a managed tool call takes after
	// the spend guard and before RESERVE. [none; MCP billing installs the
	// key-limit check and the all-level hold]
	KeyLimits Holder

	BearerVerifier      BearerVerifier    // [reject JWTs]
	ResourceMetadataURL string            // [""]
	SessionStore        SessionStore      // [memory]
	HostedAdmission     *HostedAdmission  // [nil: separate self-host legacy path]
	LiveAuthorizer      LiveAuthorizer    // [nil: refuse hosted]
	SessionLeases       SessionLeaseStore // [nil: refuse hosted]
	ManagedServers      ManagedServers    // [none]
	Credits             Holder            // [refuse managed: managed_billing_unavailable]
	RunBudget           Holder            // [none]
	ToolBudget          Holder            // [none]
	RateLimiter         RateLimiter       // [none]
	Pins                Pins              // [none]
	Scanner             Scanner           // [today's guard]
	Approvals           Approvals         // [none]

	CredentialResolver CredentialResolver                  // [config credential]
	SharedCredentials  SharedCredentials                   // [none: a shared OAuth server does not connect]
	Translator         Translator                          // [decline elicitation]
	DecorateUpstream   func(cc *CallContext, msg *Message) // [identity]
	Tracer             Tracer                              // [none]
	Validator          Validator                           // [today's basic check]
	Clock              func() time.Time                    // [time.Now]

	// Added when the MCP extensions were merged in.
	RunGuard          RunGuard          // [none]
	TokenExchanger    TokenExchanger    // [none]
	ToolPolicy        ToolPolicy        // [allow]
	FlowControl       FlowControl       // [none]
	ResultShaper      ResultShaper      // [identity]
	ToolStats         ToolStatsObserver // [discard]
	Builtins          *BuiltinRegistry  // [empty]
	VendorCredentials VendorCredentials // [none] (the credential resolver's vendor_end_user)
	Memory            Memory            // [none]
	Terms             TermsSource       // [none]
}

// Dialer opens upstream connections (the probe step installs the SSRF guard).
type Dialer interface {
	DialContext(ctx context.Context, network, address string) (net.Conn, error)
}

// Holder takes a hold for micros before a call is dispatched: credits, the
// spend guard, key limits, run and tool budgets. A denial refuses the call.
type Holder interface {
	Hold(cc *CallContext, micros int64) (BudgetHold, *Denial)
}

// BudgetHold is one hold: step 19 settles it with the billed micros, and a
// finalizer voids it on every other exit. A parked call keeps it.
type BudgetHold interface {
	Settle(billedMicros int64)
	Void()
}

// BearerVerifier checks bearer tokens that are not virtual keys (JWT access
// tokens, runtime and callback tokens). It returns nil and no error for
// a token it does not handle, which MCP auth then tries as a key.
type BearerVerifier interface {
	Verify(ctx context.Context, token, audience string) (*Principal, error)
}

// SessionStore keeps protocol session state by id.
type SessionStore interface {
	Save(ctx context.Context, id string, data []byte, ttl time.Duration) error
	Load(ctx context.Context, id string) ([]byte, bool, error)
	Delete(ctx context.Context, id string) error
}

// ManagedServers lists the operator's managed servers an org may use, as
// client configs whose ServerID is "fagi.<id>" (the tenancy merges them).
type ManagedServers interface {
	ForOrg(orgID string) []ClientConfig
}

// RateLimiter admits a call against the rate and concurrency limits;
// release ends the call's concurrency slot.
type RateLimiter interface {
	Admit(cc *CallContext) (release func(), d *Denial)
}

// Pins checks a call's tool revision and filters a listing.
type Pins interface {
	CheckCall(cc *CallContext) *Denial
	FilterList(cc *CallContext, tools []Tool) []Tool
}

// Scanner runs the guardrails on a call's arguments and result and on a
// listing's descriptions.
type Scanner interface {
	ScanArgs(cc *CallContext) *Denial
	ScanResult(cc *CallContext) *Denial
	ScanList(cc *CallContext, tools []Tool) []Tool
}

// Approvals checks that a call needing approval has one.
type Approvals interface {
	Check(cc *CallContext) *Denial
}

// CredentialResolver picks a call's upstream credential. A nil override
// keeps the server's configured credential.
type CredentialResolver interface {
	Resolve(cc *CallContext) (*AuthOverride, *Denial)
}

// SharedCredentials gives a shared OAuth server's client its credential:
// the headers of the organization's signed-in account, fetched when
// each request is sent, for orgID's serverID only.
type SharedCredentials interface {
	SharedAuth(orgID, serverID string) HeaderSource
}

// PushedGrant is one pushed bearer/API-key grant of an org (or supplied)
// server. Identity is an opaque exact-grant digest. Revision is the control
// plane's grant revision for the server's shared credential slot: every new or
// rotated secret has a larger one, and an eviction names the revision it
// revokes through. 0 means the push named none. A zero Expiry is no deadline.
type PushedGrant struct {
	Identity string
	Revision int64
	Expiry   time.Time
}

// PushedCredentials is the distributed lifetime seam implemented by the shared
// resolver for bearer/API-key grants. A grant is admitted only while its
// revision is newer than every revision an eviction has revoked for the slot,
// on any replica, whether or not this one has seen the grant before.
// The returned override owns only lifetime/check; it never changes placement.
// Local-only embedders without this extension keep their entry lifetime.
type PushedCredentials interface {
	PushedCredential(ctx context.Context, orgID, serverID string, grant PushedGrant) (*AuthOverride, error)
}

// PushedGrantRevoker is a CredentialPurger that also fences pushed grants:
// POST /-/mcp/credentials/purge with a revision revokes, for every replica,
// each of serverID's pushed grants in orgID at or below that revision, and
// cancels them within the invalidation bound. A newer grant is untouched.
type PushedGrantRevoker interface {
	RevokePushedGrants(ctx context.Context, orgID, serverID string, through int64) error
}

// Translator answers an upstream's server-initiated request, such as
// elicitation/create, during a call.
type Translator interface {
	ServerRequest(cc *CallContext, method string, params json.RawMessage) (json.RawMessage, error)
}

// Tracer starts a call's span; finish ends it.
type Tracer interface {
	Start(cc *CallContext) (finish func())
}

// Validator checks a call's arguments against the tool's input schema.
type Validator interface {
	Validate(cc *CallContext, schema json.RawMessage) *Denial
}

// RunGuard refuses calls of a stopped or over-envelope run.
type RunGuard interface {
	Check(cc *CallContext) *Denial
}

// TokenExchanger exchanges the caller's identity for an upstream token when
// the server's credential mode is exchange or id_jag. handled is false
// when it does not apply, and the credential resolver's own flow runs.
type TokenExchanger interface {
	Exchange(cc *CallContext) (auth *AuthOverride, handled bool, d *Denial)
}

// ToolPolicy evaluates the org's tool policy after the access rules.
type ToolPolicy interface {
	Evaluate(cc *CallContext) PolicyDecision
}

// PolicyDecision is a tool-policy outcome: deny is tool_denied with
// metadata.rule = "policy:<RuleID>", approve sets cc.PolicyApproval.
type PolicyDecision struct {
	Effect PolicyEffect
	RuleID string
}

// PolicyEffect is what a tool-policy rule does with a call.
type PolicyEffect string

const (
	PolicyAllow   PolicyEffect = "allow"
	PolicyDeny    PolicyEffect = "deny"
	PolicyApprove PolicyEffect = "approve"
)

// FlowControl tracks untrusted input across a run's calls.
type FlowControl interface {
	Before(cc *CallContext) *Denial
	After(cc *CallContext, result json.RawMessage) *Denial
}

// ResultShaper trims or spills a large result and serves a spilled one
// back to the same principal and run.
type ResultShaper interface {
	Shape(cc *CallContext, result json.RawMessage) json.RawMessage
	Read(cc *CallContext, uri string) (json.RawMessage, bool)
}

// ToolStatsObserver observes each finished call (the tool search ranking).
type ToolStatsObserver interface {
	Observe(cc *CallContext)
}

// VendorCredentials resolves a vendor_end_user credential.
type VendorCredentials interface {
	Resolve(cc *CallContext, vendor string) (*AuthOverride, *Denial)
}

// Memory scopes a memory tool's call to its subject.
type Memory interface {
	Scope(cc *CallContext) *Denial
}

// TermsSource resolves the vendor terms that bind a call; the zero
// Terms means no constraint.
type TermsSource interface {
	Resolve(cc *CallContext) Terms
}

// SetDeps installs d, with every unset member at its safe default. wiring
// calls it once, before the server starts serving.
func (s *Server) SetDeps(d Deps) {
	if d.HostedAdmission != nil {
		s.sessions.hosted.Store(true)
	}
	def := s.defaultDeps()
	fillDefaults(&d, def)
	s.depsPtr.Store(&d)
}

// Deps returns the dependencies the chain uses now.
func (s *Server) Deps() *Deps {
	if d := s.depsPtr.Load(); d != nil {
		return d
	}
	s.depsPtr.CompareAndSwap(nil, s.defaultDeps())
	return s.depsPtr.Load()
}

// defaultDeps are the safe defaults: behaviour exactly as before the chain.
func (s *Server) defaultDeps() *Deps {
	return &Deps{
		Events:             models.DiscardEvents,
		Dialer:             &net.Dialer{},
		ClientFactory:      DefaultClientFactory,
		SpendGuard:         noHolds{},
		KeyLimits:          noHolds{},
		BearerVerifier:     rejectJWTs{},
		SessionStore:       newMemorySessionStore(),
		ManagedServers:     noManagedServers{},
		Credits:            refuseManaged{},
		RunBudget:          noHolds{},
		ToolBudget:         noHolds{},
		RateLimiter:        noRateLimit{},
		Pins:               noPins{},
		Scanner:            guardScanner{s: s},
		Approvals:          noApprovals{},
		CredentialResolver: configCredential{},
		SharedCredentials:  noSharedCredentials{},
		Translator:         declineElicitation{},
		DecorateUpstream:   func(*CallContext, *Message) {},
		Tracer:             noTracer{},
		Validator:          basicValidator{},
		Clock:              time.Now,
		RunGuard:           noRunGuard{},
		TokenExchanger:     noTokenExchange{},
		ToolPolicy:         allowAllTools{},
		FlowControl:        noFlowControl{},
		ResultShaper:       identityShaper{},
		ToolStats:          discardToolStats{},
		Builtins:           NewBuiltinRegistry(),
		VendorCredentials:  noVendorCredentials{},
		Memory:             noMemoryScope{},
		Terms:              noTerms{},
	}
}

// fillDefaults sets every unset member of d from def.
func fillDefaults(d, def *Deps) {
	if d.Events == nil {
		d.Events = def.Events
	}
	if d.Dialer == nil {
		d.Dialer = def.Dialer
	}
	if d.ClientFactory == nil {
		d.ClientFactory = def.ClientFactory
	}
	if d.SpendGuard == nil {
		d.SpendGuard = def.SpendGuard
	}
	if d.KeyLimits == nil {
		d.KeyLimits = def.KeyLimits
	}
	if d.BearerVerifier == nil {
		d.BearerVerifier = def.BearerVerifier
	}
	if d.SessionStore == nil {
		d.SessionStore = def.SessionStore
	}
	if d.ManagedServers == nil {
		d.ManagedServers = def.ManagedServers
	}
	if d.Credits == nil {
		d.Credits = def.Credits
	}
	if d.RunBudget == nil {
		d.RunBudget = def.RunBudget
	}
	if d.ToolBudget == nil {
		d.ToolBudget = def.ToolBudget
	}
	if d.RateLimiter == nil {
		d.RateLimiter = def.RateLimiter
	}
	if d.Pins == nil {
		d.Pins = def.Pins
	}
	if d.Scanner == nil {
		d.Scanner = def.Scanner
	}
	if d.Approvals == nil {
		d.Approvals = def.Approvals
	}
	if d.CredentialResolver == nil {
		d.CredentialResolver = def.CredentialResolver
	}
	if d.SharedCredentials == nil {
		d.SharedCredentials = def.SharedCredentials
	}
	if d.Translator == nil {
		d.Translator = def.Translator
	}
	if d.DecorateUpstream == nil {
		d.DecorateUpstream = def.DecorateUpstream
	}
	if d.Tracer == nil {
		d.Tracer = def.Tracer
	}
	if d.Validator == nil {
		d.Validator = def.Validator
	}
	if d.Clock == nil {
		d.Clock = def.Clock
	}
	if d.RunGuard == nil {
		d.RunGuard = def.RunGuard
	}
	if d.TokenExchanger == nil {
		d.TokenExchanger = def.TokenExchanger
	}
	if d.ToolPolicy == nil {
		d.ToolPolicy = def.ToolPolicy
	}
	if d.FlowControl == nil {
		d.FlowControl = def.FlowControl
	}
	if d.ResultShaper == nil {
		d.ResultShaper = def.ResultShaper
	}
	if d.ToolStats == nil {
		d.ToolStats = def.ToolStats
	}
	if d.Builtins == nil {
		d.Builtins = def.Builtins
	}
	if d.VendorCredentials == nil {
		d.VendorCredentials = def.VendorCredentials
	}
	if d.Memory == nil {
		d.Memory = def.Memory
	}
	if d.Terms == nil {
		d.Terms = def.Terms
	}
}

// --- Safe defaults ---

// noHolds admits every call with a hold that does nothing.
type noHolds struct{}

func (noHolds) Hold(*CallContext, int64) (BudgetHold, *Denial) { return noHold{}, nil }

type noHold struct{}

func (noHold) Settle(int64) {}
func (noHold) Void()        {}

// --- Built-in servers ---

// BuiltinServer is an in-process MCP server: the pool serves it
// like an upstream client, with no SSRF probe and no network client.
type BuiltinServer interface {
	UpstreamClient
}

// BuiltinRegistry holds the built-in servers by id. Each built-in registers
// itself from its own wiring file, before the server starts serving.
type BuiltinRegistry struct {
	mu      sync.RWMutex
	servers map[string]BuiltinServer
}

// NewBuiltinRegistry returns an empty registry.
func NewBuiltinRegistry() *BuiltinRegistry {
	return &BuiltinRegistry{servers: map[string]BuiltinServer{}}
}

// Register adds srv under id. Ids are one namespace: a second registration
// of the same id is refused.
func (r *BuiltinRegistry) Register(id string, srv BuiltinServer) error {
	if id == "" || srv == nil {
		return errors.New("mcp: a built-in server needs an id and a server")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, dup := r.servers[id]; dup {
		return errors.New("mcp: built-in server " + id + " is already registered")
	}
	r.servers[id] = srv
	return nil
}

// Get returns the built-in server registered under id.
func (r *BuiltinRegistry) Get(id string) (BuiltinServer, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	srv, ok := r.servers[id]
	return srv, ok
}

// IDs returns the registered ids, sorted.
func (r *BuiltinRegistry) IDs() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	ids := make([]string, 0, len(r.servers))
	for id := range r.servers {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
