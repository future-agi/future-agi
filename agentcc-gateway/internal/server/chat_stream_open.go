package server

import (
	"context"

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
		return err
	})
	if err != nil {
		if opened != nil && opened.cancel != nil {
			opened.cancel()
		}
		return nil, err
	}
	return opened, nil
}
