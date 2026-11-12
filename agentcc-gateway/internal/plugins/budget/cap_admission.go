package budget

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Admission is a server-owned policy snapshot, never populated from request
// metadata. Only the central authenticated route guard creates it. Component
// callers of ResolveAdmission must supply an already-verified identity.
type Admission struct {
	org, key, agent, nonce         string
	revision                       int
	agentLimit, keyLimit, runLimit int64
	period                         time.Time
	deadline                       time.Time
	attempted                      atomic.Bool
	qualified                      atomic.Bool
	runReady                       atomic.Bool
	quoteMicros                    int64
	quoteReady                     bool
	managed                        bool
	provider, model, endpoint      string
	agentActivation, keyActivation string
	forwarded, priceSnapshot       []byte
}

type admissionKey struct{}
type admissionDecision struct{ admission *Admission }

var capRequests sync.Map // *models.RequestContext -> *Admission; removed by finalizer

func CapUnenforceable(reason string) *models.APIError {
	return &models.APIError{Status: http.StatusPaymentRequired, Type: models.ErrTypeInvalidRequest,
		Code: "call_cap_unenforceable", Message: reason}
}

// ResolveAdmission resolves applicability from a verified stable key and org.
// An org with agent caps requires every execution key to have an administrative
// binding; omission is not evidence that the caller has no cap.
func ResolveAdmission(id auth.Identity, cfg *tenant.OrgConfig, deadline time.Time) (*Admission, *models.APIError) {
	if !CapsEnabled(cfg) {
		return nil, nil
	}
	if err := ValidateCapPolicy(cfg); err != nil {
		return nil, CapUnenforceable(err.Error())
	}
	if !capIdentity.MatchString(id.OrgID) || !capIdentity.MatchString(id.KeyID) {
		return nil, CapUnenforceable("strict admission requires a verified org and stable key identity")
	}
	a := capAgents(cfg)
	agent := a.KeyBindings[id.KeyID]
	if len(cfg.Budgets.Agents) > 0 && agent == "" {
		return nil, CapUnenforceable("strict agent policy requires an administrative key binding")
	}
	ac, kc := cfg.Budgets.Agents[agent], cfg.Budgets.KeyIDs[id.KeyID]
	if ac == nil && kc == nil && a.MaxRunUSD == nil {
		return nil, nil
	}
	if deadline.IsZero() || !deadline.After(time.Now()) || deadline.Sub(time.Now()) > 24*time.Hour {
		return nil, CapUnenforceable("strict admission requires a bounded request deadline")
	}
	var nonce [24]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, CapUnenforceable("admission nonce unavailable")
	}
	now := time.Now().UTC()
	st := &Admission{org: id.OrgID, key: id.KeyID, agent: agent, nonce: hex.EncodeToString(nonce[:]),
		agentLimit: -1, keyLimit: -1, runLimit: capMicros(a.MaxRunUSD), period: time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC), deadline: deadline}
	if ac != nil {
		st.agentLimit = capMicros(ac.Limit)
		st.agentActivation = ac.ActivatedAt
	}
	if kc != nil {
		st.keyLimit = capMicros(kc.Limit)
		st.keyActivation = kc.ActivatedAt
	}
	st.revision = cfg.Budgets.PolicyRevision
	if st.revision < 1 {
		return nil, st.Refusal(capStateUnavailable(), "", "")
	}
	return st, nil
}

func WithAdmission(ctx context.Context, a *Admission) context.Context {
	return context.WithValue(ctx, admissionKey{}, admissionDecision{a})
}
func AdmissionFromContext(ctx context.Context) (*Admission, bool) {
	d, ok := ctx.Value(admissionKey{}).(admissionDecision)
	return d.admission, ok
}
func CapRequestChecked(rc *models.RequestContext) bool { _, ok := capRequests.Load(rc); return ok }
func BindAdmission(rc *models.RequestContext, a *Admission) {
	capRequests.Store(rc, a)
	rc.AddFinalizer(func() { capRequests.Delete(rc) })
}
func RequestAdmission(rc *models.RequestContext) *Admission {
	v, _ := capRequests.Load(rc)
	a, _ := v.(*Admission)
	return a
}
func (a *Admission) Org() string         { return a.org }
func (a *Admission) Key() string         { return a.key }
func (a *Admission) Agent() string       { return a.agent }
func (a *Admission) Nonce() string       { return a.nonce }
func (a *Admission) RunLimit() int64     { return a.runLimit }
func (a *Admission) Deadline() time.Time { return a.deadline }
func (a *Admission) Qualified() bool     { return a.qualified.Load() }
func (a *Admission) MarkRunReady()       { a.runReady.Store(true) }
func (a *Admission) ClaimAttempt() bool {
	return a.qualified.Load() && (a.runLimit < 0 || a.runReady.Load()) && time.Now().Before(a.deadline) && a.attempted.CompareAndSwap(false, true)
}

// QualifiedQuote is the same bounded estimate held by every strict ledger.
func (a *Admission) QualifiedQuote() (int64, bool) {
	return a.quoteMicros, a.quoteReady && a.qualified.Load()
}
