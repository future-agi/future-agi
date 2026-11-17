package server

import (
	"context"
	"errors"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// routedChatStream is the fixed-plan opener's result. Both renderers use the
// same admission, attempt ownership and cancellation path.
type routedChatStream = routedStream

// openRoutedChatStream keeps pre-plugins ahead of upstream admission for chat
// and Messages. CAT2's opener owns the sole fallback plan and every attempt;
// retries remain explicitly configured, never implicit on Messages.
func (h *Handlers) openRoutedChatStream(ctx context.Context, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) (*routedChatStream, error) {
	var opened *routedStream
	err := h.engine.Process(ctx, rc, func(ctx context.Context, rc *models.RequestContext) error {
		var err error
		opened, err = h.openRoutedStream(ctx, rc, provider, orgCfg, false)
		if err == nil && opened != nil {
			// The request's time to first token: the serving attempt's, from
			// its dispatch to its first chunk of model output (a stream
			// starts only on output, waitForManagedStreamStart). Recorded
			// once, here, before any post-plugin reads rc.Timings.
			recordTTFT(rc, opened.firstChunkLatency)
		}
		return startDeadlineError(rc, err)
	})
	if err != nil {
		if opened != nil && opened.cancel != nil {
			opened.cancel()
		}
		return nil, err
	}
	return opened, nil
}

// startDeadlineError is err, for a stream that could not start, with a bare
// context deadline as the gateway timeout it is (as attemptLimitError answers
// it), so the request's post-plugins record the 504 the client gets.
func startDeadlineError(rc *models.RequestContext, err error) error {
	var apiErr *models.APIError
	if err == nil || !errors.Is(err, context.DeadlineExceeded) || errors.As(err, &apiErr) {
		return err
	}
	if deadline, ok := requestDeadline(rc); ok && !time.Now().Before(deadline) {
		return errDeadlineCut(rc, "The request's deadline (x-agentcc-deadline-ms) passed before the upstream answered.")
	}
	return models.ErrGatewayTimeout("the request timed out before the upstream answered")
}
