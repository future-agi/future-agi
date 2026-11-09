package mcp

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// CallContext is one MCP request on its way through the chain (plan section
// 6.16, MC.1). Each hook reads and fills the fields it owns; a later lane may
// add fields but never renames or removes one (frozen after W2b).
type CallContext struct {
	RequestID       string
	ToolCallID      string
	Method          string
	Era             string
	ProtocolVersion string
	Principal       Principal

	Agent        models.AgentContext
	SessionID    string
	TraceParent  string
	GenerationID string

	// OrgConfig is the snapshot of the principal's org config (M9), nil when
	// none applies.
	OrgConfig *tenant.OrgConfig
	// VirtualServer is the id of the virtual server the call addresses (M10).
	VirtualServer string
	Upstream      UpstreamRef
	Tool          ToolRef

	// Args is the tools/call arguments object as the client sent it.
	Args     json.RawMessage
	ArgsHash string

	Credential CredentialRef
	Billing    CallBilling
	Budget     CallBudget

	// Decision is the denial that stopped the chain, or nil.
	Decision *Denial
	// Result is the upstream result as raw JSON once the call ran.
	Result   json.RawMessage
	IsError  bool
	Executed bool
	// Dispatched is set once the request's bytes were written to the
	// upstream connection (M18's dispatch boundary; WithDispatchNotice,
	// IC-R1-8): a failed dial or a refused connection leaves it false.
	Dispatched bool

	Guardrail GuardrailState
	Timings   CallTimings
	Bytes     CallBytes

	// Terms are the vendor terms that bind the call's retention and eval
	// export (V20, step 18a). The zero value means no constraint.
	Terms Terms
	// PolicyApproval is the id of the tool-policy rule (P8) that pre-approved
	// the call, or "".
	PolicyApproval string
	// Evidence holds optional record fields by name (identity, account_label,
	// policy, flow, shaped; plan section 6.34). M15 writes each entry as the
	// same-named mcp-tool-call-v1 field. Set it with SetEvidence.
	Evidence map[string]json.RawMessage
	// ResponseMeta holds the entries the response's _meta carries, by key
	// (for example io.futureagi/tool_call, written by M15's evidenceMeta).
	// Set it with SetResponseMeta.
	ResponseMeta map[string]json.RawMessage

	// ToolList, ResourceList and PromptList are the items a list method
	// answers with; the list hooks filter and annotate them in place.
	ToolList     []Tool
	ResourceList []Resource
	PromptList   []Prompt

	ctx     context.Context
	req     *http.Request
	msg     *Message
	session *Session
	observe func(hook string, cc *CallContext)
	// retry is true when the request resumes a parked call (M28): the chain
	// then runs only the steps marked for a retry.
	retry bool
	// legacy is the state today's default hooks hand each other.
	legacy legacyState

	mu         sync.Mutex
	finalizers []finalizer
	parked     *Parked
}

// Principal is who is calling (MC.1), resolved by hook 1 (M8).
type Principal struct {
	Kind  PrincipalKind
	KeyID string
	OrgID string
	// UserID on a key principal is the canonical reserved user_id from
	// trusted person-key issuance (733/IR-2), never an owner label, caller
	// metadata or a config seed. Identity alone grants no upstream credential.
	UserID   string
	ClientID string
	Scopes   []string
	Depth    int
	TokenID  string
	// AllowedTools and MaxCalls are callback-token claims (M34).
	AllowedTools []string
	MaxCalls     int
}

// PrincipalKind names how the principal authenticated.
type PrincipalKind string

const (
	PrincipalKey      PrincipalKind = "key"
	PrincipalOAuth    PrincipalKind = "oauth"
	PrincipalRuntime  PrincipalKind = "runtime"
	PrincipalCallback PrincipalKind = "callback"
	PrincipalAdmin    PrincipalKind = "admin"
)

// UpstreamRef names the upstream server a call goes to.
type UpstreamRef struct {
	ServerID string
	Managed  bool
}

// ToolRef is the tool a call names: as the client sees it and as the
// upstream knows it.
type ToolRef struct {
	ExposedName  string
	UpstreamName string
	Revision     string
	Annotations  *ToolAnnotations
	Price        *ToolPrice
}

// ToolPrice is a managed tool's price: its tool-price-card-v1 reference
// (plan section 6.37) and the per-call price resolved from it (M18).
type ToolPrice struct {
	Capability     string
	Vendor         string
	SKU            string
	UpstreamMicros int64
}

// CredentialRef says which credential the upstream call uses (M25).
type CredentialRef struct {
	Source CredentialSource
	Ref    string
	State  string
}

// CredentialSource is where a call's upstream credential comes from.
type CredentialSource string

const (
	CredentialNone      CredentialSource = "none"
	CredentialOrgShared CredentialSource = "org_shared"
	CredentialUser      CredentialSource = "user"
	CredentialManaged   CredentialSource = "managed"
)

// CallBilling is the call's money state (M18). A customer-connected server is
// byok with billed 0.
type CallBilling struct {
	Mode           string // byok | managed
	CredentialType string // byok | managed | none
	ReserveMicros  int64
	UpstreamMicros int64
	BilledMicros   int64
	MarkupMicros   int64
	MarkupBps      int
	CostSource     string // none | tool_price | upstream_reported
	UsageEstimated bool
	Settled        bool
}

// Billing modes.
const (
	BillingBYOK    = "byok"
	BillingManaged = "managed"
)

// CallBudget is the run or tool budget the call draws on (M19). A nil
// remaining value is unknown or unlimited.
type CallBudget struct {
	Scope          string
	CallsRemaining *int64
	USDRemaining   *float64
}

// GuardrailState lists the checks that ran on the call (M22).
type GuardrailState struct {
	Checked []string
	Blocked []string
	Masked  []string
	Shadow  []string
}

// CallTimings are the call's clock readings; a zero time was not reached.
type CallTimings struct {
	Started         time.Time
	UpstreamStarted time.Time
	UpstreamEnded   time.Time
}

// CallBytes counts the bytes in and out of the call.
type CallBytes struct {
	In  int64
	Out int64
}

// Terms are the vendor terms that bind a call (V20). The zero value means no
// constraint.
type Terms struct {
	Retention   TermsRetention
	EvalExport  TermsEvalExport
	Attribution string
}

// TermsRetention is how much of a call the record and spans may keep.
type TermsRetention string

const (
	RetentionFull         TermsRetention = "full"
	RetentionMetadataOnly TermsRetention = "metadata_only"
	RetentionHashed       TermsRetention = "hashed"
)

// TermsEvalExport says whether a call's content may be exported to evals.
type TermsEvalExport string

const (
	EvalExportAllowed   TermsEvalExport = "allowed"
	EvalExportForbidden TermsEvalExport = "forbidden"
)

// Constrained reports whether the terms constrain anything; the zero value
// does not.
func (t Terms) Constrained() bool {
	return t != Terms{}
}

// legacyState is what today's default hooks pass between themselves. A lane
// that replaces a default keeps or drops the part it reads.
type legacyState struct {
	tool         *RegisteredTool
	arguments    map[string]interface{}
	toolResult   *ToolCallResult
	resourceURI  string
	resource     *RegisteredResource
	readResult   *ReadResourceResult
	promptName   string
	promptArgs   map[string]string
	prompt       *RegisteredPrompt
	promptResult *GetPromptResult
}

// Context is the request's context, cancelled when the client goes away.
func (cc *CallContext) Context() context.Context {
	if cc.ctx == nil {
		return context.Background()
	}
	return cc.ctx
}

// Request is the HTTP request the call arrived on, or nil.
func (cc *CallContext) Request() *http.Request { return cc.req }

// Message is the JSON-RPC message of the call, or nil.
func (cc *CallContext) Message() *Message { return cc.msg }

// SetEvidence stores v as JSON under name, for the tool-call record field of
// that name. A json.RawMessage is stored as it is; a value that does not
// marshal is dropped and logged.
func (cc *CallContext) SetEvidence(name string, v any) {
	raw, ok := toRaw(v)
	if !ok {
		slog.Warn("mcp evidence dropped: value does not marshal", "name", name)
		return
	}
	cc.mu.Lock()
	defer cc.mu.Unlock()
	if cc.Evidence == nil {
		cc.Evidence = map[string]json.RawMessage{}
	}
	cc.Evidence[name] = raw
}

// SetResponseMeta stores v as JSON under key in the response's _meta.
func (cc *CallContext) SetResponseMeta(key string, v any) {
	raw, ok := toRaw(v)
	if !ok {
		slog.Warn("mcp response meta dropped: value does not marshal", "key", key)
		return
	}
	cc.mu.Lock()
	defer cc.mu.Unlock()
	if cc.ResponseMeta == nil {
		cc.ResponseMeta = map[string]json.RawMessage{}
	}
	cc.ResponseMeta[key] = raw
}

func toRaw(v any) (json.RawMessage, bool) {
	if raw, ok := v.(json.RawMessage); ok {
		if !json.Valid(raw) {
			return nil, false
		}
		return append(json.RawMessage(nil), raw...), true
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, false
	}
	return raw, true
}

// observeHook reports hook to the server's hook observer, a test seam that is
// nil in a running gateway. Every hook calls it as its first statement, and a
// lane that takes over a hook file keeps that call, so the order tests keep
// proving the order (the G0b seam).
func observeHook(cc *CallContext, hook string) {
	if cc.observe != nil {
		cc.observe(hook, cc)
	}
}

// --- Finalizers and parking (MC.1) ---

// Finalizer names that a resumed call treats specially.
const (
	// FinalizerRecord is the tool-call record finalizer (M15, step 2a).
	FinalizerRecord = "record"
	// FinalizerSpan is the trace span finalizer (M27).
	FinalizerSpan = "span"
)

type finalizer struct {
	name string
	fn   func()
}

// Defer registers fn to run when the call ends. Finalizers run last in,
// first out, exactly once, on every exit path: success, a denial or a panic.
// A parked call's finalizers run once, when the parked call completes or
// expires (Park).
func (cc *CallContext) Defer(name string, fn func()) {
	cc.mu.Lock()
	defer cc.mu.Unlock()
	cc.finalizers = append(cc.finalizers, finalizer{name, fn})
}

// runFinalizers runs and clears the call's finalizers. The chain defers it, so
// it also runs while a panic unwinds.
func (cc *CallContext) runFinalizers() {
	cc.mu.Lock()
	list := cc.finalizers
	cc.finalizers = nil
	cc.mu.Unlock()
	runLIFO(list)
}

// runLIFO runs list from the last entry to the first. A finalizer that panics
// is logged and does not stop the ones after it.
func runLIFO(list []finalizer) {
	for i := len(list) - 1; i >= 0; i-- {
		func(f finalizer) {
			defer func() {
				if r := recover(); r != nil {
					slog.Error("mcp finalizer panicked", "finalizer", f.name, "panic", r)
				}
			}()
			f.fn()
		}(list[i])
	}
}

// Parked holds the finalizers of a call that answered input_required (M28):
// they run once, when the parked call completes or expires, never at the end
// of the round that parked it.
type Parked struct {
	// ToolCallID is the parked call's id, which a resumed call adopts.
	ToolCallID string

	mu         sync.Mutex
	finalizers []finalizer
	done       bool
}

// Park detaches the call's finalizers into a Parked handle, so they do not
// run when this round ends. Finalizers registered after Park join the same
// handle on the next Park.
func (cc *CallContext) Park() *Parked {
	cc.mu.Lock()
	defer cc.mu.Unlock()
	if cc.parked == nil || cc.parked.taken() {
		cc.parked = &Parked{ToolCallID: cc.ToolCallID}
	}
	cc.parked.add(cc.finalizers)
	cc.finalizers = nil
	return cc.parked
}

// Adopt makes a resumed call the parked call it resumes (hook 7, M28): it
// takes the parked call's tool_call_id and finalizers and drops its own
// record finalizer, so the logical call is recorded once. It returns false,
// and changes nothing, when the parked call already completed or expired.
func (cc *CallContext) Adopt(p *Parked) bool {
	list, ok := p.take()
	if !ok {
		return false
	}
	cc.mu.Lock()
	defer cc.mu.Unlock()
	kept := cc.finalizers[:0]
	for _, f := range cc.finalizers {
		if f.name != FinalizerRecord {
			kept = append(kept, f)
		}
	}
	cc.finalizers = append(kept, list...)
	cc.ToolCallID = p.ToolCallID
	return true
}

// Complete runs the parked finalizers when the parked call completes. Only
// the first of Complete, Expire and a resumed call's Adopt has any effect.
func (p *Parked) Complete() {
	if list, ok := p.take(); ok {
		runLIFO(list)
	}
}

// Expire runs the parked finalizers when the parked call expires unanswered.
func (p *Parked) Expire() {
	p.Complete()
}

func (p *Parked) add(list []finalizer) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.finalizers = append(p.finalizers, list...)
}

// take returns the finalizers once; later calls get false.
func (p *Parked) take() ([]finalizer, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.done {
		return nil, false
	}
	p.done = true
	list := p.finalizers
	p.finalizers = nil
	return list, true
}

func (p *Parked) taken() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.done
}
