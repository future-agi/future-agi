// Package eligibility is the one engine that decides whether a model on a
// provider may serve a request: the key's ACL, the org's provider
// lock and managed allow/deny lists, the model's catalog state, the
// effective data policy, the caller's provider filters and caps, the billing
// mode restriction and the transcript's tool history. Every routing path
// applies it: an explicit route, each auto candidate and each fallback hop.
//
// The rules run in the order of one registration list (rules, below). A rule
// either refuses the candidate with a code, or narrows the candidate's
// endpoints (managed candidates only: those are the endpoints the gateway
// pins), and a candidate whose endpoints are all narrowed away is refused
// with that rule's code. A later rule is one appended line in the list and
// its own file.
//
// The package is pure: it reads its Input and the catalog entry it is
// given, never the request context, and allocates nothing to allow a
// candidate that has no endpoints (the chat hot path).
package eligibility

import (
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// GroupChecker is the access-group check (routing.AccessGroupChecker): the
// group that lets model through, and whether one does.
type GroupChecker interface {
	Check(model string, groups []string) (string, bool)
}

// KeyACL is the presented key's ACL, as peekKeyOrgID records it before
// routing.
type KeyACL struct {
	// AllowedModels and AllowedProviders are comma lists; empty allows any.
	AllowedModels    string
	AllowedProviders string
	// Internal service keys are exempt from AllowedProviders, as on chat.
	Internal bool
	// Groups are the key's access groups, checked by GroupCheck when both
	// are set.
	Groups     []string
	GroupCheck GroupChecker
}

// Input is everything one check reads.
type Input struct {
	// Model is the resolved model the candidate would be sent, never the
	// literal "auto"; ProviderID is the gateway provider that would serve it.
	Model      string
	ProviderID string
	// ACLModel is the model the key's ACL is judged on (rule 1): on an
	// explicit route the model the caller asked for, which the auth plugin
	// checks too; empty means Model.
	ACLModel string
	// Auto marks an auto-routing candidate: deprecated and degraded models
	// are excluded from auto, and allowed for an explicit model.
	Auto bool

	Key KeyACL
	// Org is the effective org config for the request, nil for none.
	Org *tenant.OrgConfig

	// Managed is set when the attempt would be billed to the org as managed:
	// a customer key on FutureAGI's managed provider, without the org's own
	// credentials for it.
	Managed bool
	// Aggregator marks an OpenRouter-type upstream, which the gateway sends a
	// provider object: on a managed attempt the pinned one, carrying the
	// effective data policy; on a BYOK attempt the caller's own.
	Aggregator bool
	// Entry is the provider's catalog entry for the model, nil when none.
	Entry *catalog.Model
	// ZDRAttested and DataRegion are the org's attestations for its own
	// (BYOK) provider: providers.<id>.zdr_attested and data_region.
	ZDRAttested bool
	DataRegion  string

	// Request is the chat request (parameters and transcript); Prefs is the
	// caller's provider object (rc.Routing.Provider), nil for none.
	Request *models.ChatCompletionRequest
	Prefs   *models.ProviderPrefs
	// Env is the agent context's env, which the preview block reads.
	Env string
	// ModeRestriction is the caller's billing-mode restriction
	// (rc.Billing.ModeRestriction): byok, managed, any or empty.
	ModeRestriction string
	// ThoughtSignatureModels are catalog.thought_signature_models; nil
	// means Gemini generation ThoughtSignatureFromGeneration and later.
	ThoughtSignatureModels []string
	// Shutdowns is the model shutdown calendar (catalog/deprecations.json),
	// nil for none; Host is the serving provider's type
	// (modeldb.DeprecationHost), never its gateway ID. Rule 4 refuses a model
	// past its calendar shutdown date with model_expired.
	Shutdowns *modeldb.Deprecations
	Host      string
	// Now is the clock the expiration date is judged on; zero is time.Now.
	Now time.Time
}

// Verdict is the outcome of Check.
type Verdict struct {
	OK bool
	// Code is the rejection code, and Reason a sentence for the caller.
	Code   string
	Reason string
	// Endpoint is the best-ranked eligible endpoint of a managed candidate
	// (nil when the candidate has no endpoint list); EndpointSet ranks them
	// all.
	Endpoint *catalog.Endpoint
	// Deprecated is the expiration date of a deprecated model that has not
	// expired yet, which an explicit route reports in a response header.
	Deprecated string
	// Effective is the data policy the candidate runs under.
	Effective models.EffectiveDataPolicy

	entry *catalog.Model
	alive endpointMask
}

// rule is one eligibility rule. It refuses the candidate by returning a
// code (and a reason), or narrows c's endpoints and returns "".
type rule struct {
	name  string
	check func(c *checker) (code, reason string)
}

// rules is the eligibility engine, in order. Append a rule here, in its own
// file; never reorder or edit an existing entry.
var rules = []rule{
	{"key_acl", checkKeyACL},
	{"org_provider_lock", checkOrgProviderLock},
	{"managed_allow_deny", checkManaged},
	{"catalog_state", checkCatalogState},
	{"data_policy", checkDataPolicy},
	{"provider_filter", checkProviderFilter},
	{"max_price", checkMaxPrice},
	{"quantization_parameters", checkQuantizationAndParameters},
	{"billing_mode", checkBillingMode},
	{"tool_history", checkToolHistory},
	{"server_tools", checkServerTools},
}

// RuleNames lists the rules in the order Check runs them.
func RuleNames() []string {
	out := make([]string, len(rules))
	for i, r := range rules {
		out[i] = r.name
	}
	return out
}

// Check runs every rule on in, in order, and returns the first refusal, or
// an OK verdict with the candidate's best endpoint and effective policy.
func Check(in *Input) Verdict {
	// The rules are called through the registration list, so the run's state
	// would escape to the heap on every check; a pooled checker, holding a
	// copy of the input, keeps a check free of allocations.
	c := checkerPool.Get().(*checker)
	*c = checker{in: *in, now: in.Now}
	defer func() {
		*c = checker{}
		checkerPool.Put(c)
	}()
	if c.now.IsZero() {
		c.now = time.Now()
	}
	c.eff = EffectivePolicy(in.Org, in.Prefs, in.Managed)
	c.initEndpoints()
	for _, r := range rules {
		if code, reason := r.check(c); code != "" {
			return Verdict{Code: code, Reason: reason, Effective: c.eff}
		}
	}
	v := Verdict{OK: true, Deprecated: c.deprecated, Effective: c.eff, entry: in.Entry, alive: c.alive}
	if c.hasEndpoints {
		v.Endpoint = bestEndpoint(in.Entry.Endpoints, c.alive)
	}
	return v
}

var checkerPool = sync.Pool{New: func() any { return new(checker) }}

// checker is one Check run: the input, the clock, the effective data policy
// and the endpoints still eligible.
type checker struct {
	in  Input
	now time.Time
	eff models.EffectiveDataPolicy
	// hasEndpoints is set for a managed candidate whose catalog entry lists
	// endpoints through its provider; alive is the set still eligible.
	hasEndpoints bool
	alive        endpointMask
	deprecated   string
}

// maxEndpoints is how many of a model's endpoints a check considers; a
// model lists far fewer (OpenRouter's busiest list about 25).
const maxEndpoints = 64

// endpointMask is a set of endpoint indexes into Entry.Endpoints.
type endpointMask uint64

func (m endpointMask) has(i int) bool { return m&(1<<uint(i)) != 0 }

// initEndpoints marks the endpoints the check narrows: a managed candidate's
// endpoints reached through its own provider. A BYOK attempt is never
// pinned to endpoints, so its endpoints do not count.
func (c *checker) initEndpoints() {
	in := &c.in
	if !in.Managed || in.Entry == nil {
		return
	}
	for i := range in.Entry.Endpoints {
		if i == maxEndpoints {
			break
		}
		if p := in.Entry.Endpoints[i].Provider; p == "" || p == in.ProviderID {
			c.alive |= 1 << uint(i)
		}
	}
	c.hasEndpoints = c.alive != 0
}

// narrow keeps the eligible endpoints keep accepts and reports whether any
// is left.
func (c *checker) narrow(keep func(e *catalog.Endpoint) bool) bool {
	eps := c.in.Entry.Endpoints
	for i := range eps {
		if c.alive.has(i) && !keep(&eps[i]) {
			c.alive &^= 1 << uint(i)
		}
	}
	return c.alive != 0
}
