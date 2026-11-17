package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// routeRefused records a request the gateway refused while choosing its
// provider: resolving its route (a provider lock, the model, the key's access
// groups), the key's provider list, or the checks on the chosen route before
// any provider is called (chat's routed-request check and cost-cap
// qualification, every other endpoint's call cap gate). These refusals are
// answered before the pipeline runs, so neither its auth plugin nor its
// logging plugin has seen the request.
//
// The key is checked first, by the pipeline's own first plugins (the IP ACL,
// then auth): a request that does not pass gets their refusal, returned here
// for the caller to answer with instead of err, and no request-log row, so
// the gateway never writes a row to an organization's log for a request it
// has not authenticated. A request that passes gets one row, written by the
// logging plugin as for a refusal inside the pipeline: err's status and
// error code, error_source "gateway", and no request or response body
// (RequestFlags.RouteRefused). Then routeRefused returns nil and the caller
// answers with err as before.
func (h *Handlers) routeRefused(ctx context.Context, rc *models.RequestContext, err error) *models.APIError {
	if apiErr := h.engine.RunRequestPlugins(ctx, rc, "ipacl", "auth"); apiErr != nil {
		return apiErr
	}
	rc.AddError(err)
	rc.Flags.RouteRefused = true
	h.engine.RunResponsePlugins(ctx, rc, "logging")
	return nil
}

// routeRefusal is routeRefused for a caller that writes either answer the
// same way: the error to answer with.
func (h *Handlers) routeRefusal(ctx context.Context, rc *models.RequestContext, err error) error {
	if apiErr := h.routeRefused(ctx, rc, err); apiErr != nil {
		return apiErr
	}
	return err
}
