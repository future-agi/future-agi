package server

import (
	"context"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Direct catalogue routes may not have a strategy-router target. They still
// honor the org's retry policy. Failover-owned calls already run a Retryer and
// never call this wrapper (no multiplied retry budgets or duplicate holds).
func (h *Handlers) retryRoutedCall(ctx context.Context, org *tenant.OrgConfig, messagesDefault bool, kind models.AttemptKind, previous error, call func(models.AttemptKind, error) error) error {
	var retryer *routing.Retryer
	if org != nil && org.Routing != nil && org.Routing.Retry != nil {
		retryer = routing.NewRetryer(h.buildOrgRetryConfig(org.Routing.Retry))
	} else if messagesDefault {
		// Preserve Messages' existing one retry before the first output, unless the
		// org explicitly disables or configures it. No bytes have been committed.
		retryer = routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: 250 * time.Millisecond, MaxDelay: 250 * time.Millisecond, OnStatusCodes: []int{502, 504}})
	}
	_, err := retryer.Execute(ctx, func() error {
		err := call(kind, previous)
		previous, kind = err, models.AttemptRetry
		return err
	})
	return err
}
