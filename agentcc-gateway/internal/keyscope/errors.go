package keyscope

import (
	"fmt"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The refusal codes of a scoped key (omega-ee-attach/v1, spec section 3.5).
const (
	// CodeScopeDenied is the 403 for a route outside the key's scope.
	CodeScopeDenied = "key_scope_denied"
	// CodeBudgetExhausted is the 402 for a model call once the key's budget
	// for the period is spent.
	CodeBudgetExhausted = "agent_key_budget_exhausted"
)

const remedyRaiseKeyBudget = "raise_key_budget"

// ErrScopeDenied is the 403 for method on path, which a key with scope s may
// not call. It never says whether the route exists.
func ErrScopeDenied(s Scope, method, path string) *models.APIError {
	return &models.APIError{
		Status: http.StatusForbidden,
		Type:   models.ErrTypePermission,
		Code:   CodeScopeDenied,
		Message: fmt.Sprintf("This API key may call only %s; %s %s is outside its scope.",
			s.describe(), method, truncate(path, 128)),
	}
}

// ErrBudgetExhausted is the 402 for a call once the key's budget for the
// period is spent: limit_source agentcc_key_limit, scope key:<id>, resets_at
// the period's end. An unreadable budget lifts only when the control plane
// sends a readable one, so its resets_at is null. Its contract is
// omega-ee-attach/v1 gateway_key_budget_error: the body has error-402-v1's
// shape and metadata, but CodeBudgetExhausted is not in error-402-v1's list
// of codes.
func ErrBudgetExhausted(keyID string, b Budget, spentMicros int64, now time.Time) *models.APIError {
	available := float64(max(b.LimitMicros-spentMicros, 0)) / 1_000_000
	meta := models.PaymentMeta{
		LimitSource:  models.LimitSourceKeyLimit,
		RemedyHint:   remedyRaiseKeyBudget,
		AvailableUSD: &available,
		Scope:        models.ScopeKey(keyID),
	}
	msg := "This API key's spend budget cannot be read, so it makes no model call. An org admin sets the workspace's agent key budget."
	if b.valid {
		resets := b.ResetsAt(now)
		meta.ResetsAt = &resets
		msg = fmt.Sprintf("This API key has spent its %s budget ($%.2f). It resets at %s, or an org admin raises the workspace's agent key budget.",
			b.Period, float64(b.LimitMicros)/1_000_000, resets.UTC().Format(time.RFC3339))
	}
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, CodeBudgetExhausted, msg, meta)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
