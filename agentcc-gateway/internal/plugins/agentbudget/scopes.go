package agentbudget

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// DefaultScopeTTL is routing.agents.budget_scope_ttl_seconds' default.
const DefaultScopeTTL = 24 * time.Hour

// defaultHoldTTL is managed.hold_ttl's default.
const defaultHoldTTL = 15 * time.Minute

// Scopes is the run-budget scope store with the refusals the pipeline plugin
// returns, for every caller: the plugin itself, and callers outside the
// request pipeline that hold a run scope by their own hold id (capability and
// tool budgets). A hold is RESERVE, then exactly
// one SETTLE (at what the call cost) or VOID.
type Scopes struct {
	store   *redisstate.AgentBudgetStore
	holdTTL time.Duration
}

// NewScopes returns the scopes over store, whose holds expire after holdTTL
// (managed.hold_ttl; 15 minutes when not positive).
func NewScopes(store *redisstate.AgentBudgetStore, holdTTL time.Duration) *Scopes {
	if holdTTL <= 0 {
		holdTTL = defaultHoldTTL
	}
	return &Scopes{store: store, holdTTL: holdTTL}
}

// ScopeOptions are what a RESERVE says about its scope.
type ScopeOptions struct {
	// LimitMicros is the budget the caller sends for the scope, or
	// redisstate.AgentBudgetNoLimit: the first one sets the limit, a lower
	// one lowers it, a higher one is ignored.
	LimitMicros int64
	// TTL is the scope's sliding TTL (routing.agents.budget_scope_ttl_seconds);
	// DefaultScopeTTL when not positive.
	TTL time.Duration
}

func scopeTTL(ttl time.Duration) time.Duration {
	if ttl <= 0 {
		return DefaultScopeTTL
	}
	return ttl
}

// Reserve holds micros on org's scope under holdID. refusal is the 402 to
// return when the scope is stopped (agent_run_stopped) or cannot hold micros
// (agent_budget_exhausted); err is the store's failure, on which a caller
// fails closed with ErrBudgetStateUnavailable unless budgets.fail_mode is
// open. The same hold id twice is one hold.
func (s *Scopes) Reserve(ctx context.Context, org, scope, holdID string, micros int64, o ScopeOptions) (st redisstate.AgentBudgetState, refusal *models.APIError, err error) {
	st, err = s.store.Reserve(ctx, org, scope, holdID, micros, o.LimitMicros, s.holdTTL, scopeTTL(o.TTL))
	if err != nil {
		return st, nil, err
	}
	switch st.Status {
	case redisstate.AgentBudgetStopped:
		return st, ErrRunStopped(scope, st.StopReason), nil
	case redisstate.AgentBudgetExhausted:
		return st, ErrBudgetExhausted(scope, micros, st.RemainingMicros()), nil
	}
	return st, nil, nil
}

// Settle releases holdID's hold and counts micros, what the call cost, in
// the scope. Settle each call once: a second SETTLE counts it again.
func (s *Scopes) Settle(ctx context.Context, org, scope, holdID string, micros int64, ttl time.Duration) (redisstate.AgentBudgetState, error) {
	return s.store.Settle(ctx, org, scope, holdID, micros, scopeTTL(ttl))
}

// Void releases holdID's hold and counts nothing: the call cost nothing, or
// never ran.
func (s *Scopes) Void(ctx context.Context, org, scope, holdID string, ttl time.Duration) (redisstate.AgentBudgetState, error) {
	return s.store.Void(ctx, org, scope, holdID, scopeTTL(ttl))
}

// Stopped returns the 402 agent_run_stopped when org's scope was stopped,
// else nil.
func (s *Scopes) Stopped(ctx context.Context, org, scope string) (*models.APIError, error) {
	st, err := s.store.Peek(ctx, org, scope)
	if err != nil || !st.Stopped {
		return nil, err
	}
	return ErrRunStopped(scope, st.StopReason), nil
}

// Peek reads org's scope without changing it.
func (s *Scopes) Peek(ctx context.Context, org, scope string) (redisstate.AgentBudgetState, error) {
	return s.store.Peek(ctx, org, scope)
}
