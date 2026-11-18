package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
)

// proxyRunCall relays a call that starts or continues a run, through the
// pipeline: pre-plugins may refuse it (auth, rate limits, budgets), and
// post-plugins log it. The tokens a run spends are known only when it
// finishes, so they are charged then — on this call's stream, or on a later
// poll — once, by whichever call takes the run's pending charge.
func (h *Handlers) proxyRunCall(w http.ResponseWriter, ctx context.Context, rc *models.RequestContext, ap providers.AssistantsProvider, upstreamPath string, body []byte, startsRun bool) {
	if rc.IsStream {
		// Pre-plugins only; the relay runs the post-plugins when it ends.
		if err := h.engine.Process(ctx, rc, func(context.Context, *models.RequestContext) error { return nil }); err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
		h.relayRunStream(w, ctx, rc, ap, upstreamPath, body, startsRun)
		return
	}

	var respBody []byte
	var status int
	var respHeaders http.Header
	err := h.engine.Process(ctx, rc, func(callCtx context.Context, callRC *models.RequestContext) error {
		var callErr error
		respBody, status, respHeaders, callErr = ap.ProxyAssistantsRequest(callCtx, http.MethodPost, upstreamPath, body, nil)
		if callErr != nil {
			return callErr
		}
		if status >= http.StatusBadRequest {
			// Relayed as sent, and logged as the failure it is.
			callRC.AddError(models.ErrUpstreamProvider(status, fmt.Sprintf("assistants error (HTTP %d)", status)))
			return nil
		}
		h.observeRun(callCtx, callRC, respBody, startsRun)
		return nil
	})
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	h.writeAssistantsResponse(w, rc, respBody, status, respHeaders)
}

// relayRunStream relays a run's event stream, charging the run if it
// finishes on it, and ends a stream that fails with an error event.
func (h *Handlers) relayRunStream(w http.ResponseWriter, ctx context.Context, rc *models.RequestContext, ap providers.AssistantsProvider, upstreamPath string, body []byte, startsRun bool) {
	finalize := func(detach bool) {
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
	}

	stream, _, respHeaders, err := ap.StreamAssistantsRequest(ctx, http.MethodPost, upstreamPath, body, nil)
	if err != nil {
		rc.AddError(err)
		models.WriteErrorFromError(w, err)
		finalize(false)
		return
	}
	defer stream.Close()

	h.setAgentccHeaders(w, rc)
	forwardAssistantsHeaders(w, respHeaders)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		slog.Warn("streaming not supported by server", "request_id", rc.RequestID)
		finalize(true)
		return
	}
	flusher.Flush()

	// fail ends a stream that stopped early, telling the client why unless
	// the client is the one that left.
	fail := func(err error) {
		if apiErr := streamEndError(ctx, err); apiErr != nil {
			rc.AddError(apiErr)
			writeAndFlush(w, flusher, assistantsStreamErrorEvent(apiErr))
		}
		finalize(ctx.Err() != nil)
	}

	events := streaming.NewEventReader(stream)
	for {
		ev, readErr := events.Next()
		if readErr != nil && readErr != io.EOF {
			// The event the error cut off is not forwarded: run into the error
			// event, it would hide that event from the client's parser.
			slog.Warn("error reading assistants stream", "request_id", rc.RequestID, "error", readErr)
			fail(readErr)
			return
		}
		switch {
		case ev.Name == "error":
			rc.AddError(upstreamStreamError(ev.Data()))
		case strings.HasPrefix(ev.Name, "thread.run."):
			h.observeRun(ctx, rc, ev.Data(), startsRun && ev.Name == "thread.run.created")
		}
		if len(ev.Raw) > 0 {
			if _, writeErr := w.Write(ev.Raw); writeErr != nil {
				slog.Warn("error writing assistants stream", "request_id", rc.RequestID, "error", writeErr)
				finalize(true)
				return
			}
			flusher.Flush()
		}
		switch {
		case readErr == io.EOF:
			finalize(false)
			return
		case ctx.Err() != nil:
			fail(ctx.Err())
			return
		}
	}
}

// observeRun looks at a run object the gateway relayed. A run this call
// started is remembered, so its usage can be charged when it finishes. A
// finished run is charged now if its pending charge is still there: the
// charge moves onto rc, with the attribution of the call that started it.
// It reports whether rc now carries a charge.
func (h *Handlers) observeRun(ctx context.Context, rc *models.RequestContext, data []byte, startsRun bool) bool {
	if h.runCharges == nil {
		return false
	}
	var run struct {
		ID     string                     `json:"id"`
		Object string                     `json:"object"`
		Status string                     `json:"status"`
		Model  string                     `json:"model"`
		Usage  *models.AssistantsRunUsage `json:"usage"`
	}
	if json.Unmarshal(data, &run) != nil || run.ID == "" || run.Object != "thread.run" {
		return false
	}
	if startsRun {
		h.runCharges.Put(ctx, run.ID, newPendingCharge(rc, run.Model))
	}
	if !runFinished(run.Status) || run.Usage == nil || run.Usage.TotalTokens == 0 {
		return false
	}
	charge, ok := h.runCharges.Take(ctx, run.ID)
	if !ok {
		return false // not started through the gateway, or charged already
	}

	// The charge is the starter's, so its attribution replaces the caller's
	// rather than merging over it: a poller's org, team or tags must not
	// apply to a run it did not start.
	for key := range rc.Metadata {
		delete(rc.Metadata, key)
	}
	for key, value := range charge.Metadata {
		rc.Metadata[key] = value
	}
	rc.UserID = charge.UserID
	model := run.Model
	if model == "" {
		model = charge.Model
	}
	rc.Model, rc.ResolvedModel = model, model
	if charge.Provider != "" {
		rc.Provider = charge.Provider
	}
	rc.Metadata["run_id"] = run.ID
	rc.Response = &models.ChatCompletionResponse{
		Model: model,
		Usage: &models.Usage{
			PromptTokens:     run.Usage.PromptTokens,
			CompletionTokens: run.Usage.CompletionTokens,
			TotalTokens:      run.Usage.TotalTokens,
		},
	}
	return true
}

// runFinished reports whether a run's status is final. A run spends tokens
// until then, whatever the outcome.
func runFinished(status string) bool {
	switch status {
	case "completed", "failed", "incomplete", "expired", "cancelled":
		return true
	}
	return false
}

// assistantsStreamErrorEvent is an error as an Assistants stream event; the
// OpenAI SDKs raise on an error event whose data carries an error object.
func assistantsStreamErrorEvent(apiErr *models.APIError) []byte {
	data, _ := json.Marshal(models.ErrorResponse{Error: models.ErrorDetail{
		Message: apiErr.Message,
		Type:    apiErr.Type,
		Param:   apiErr.Param,
		Code:    apiErr.Code,
	}})
	return fmt.Appendf(nil, "event: error\ndata: %s\n\n", data)
}

// writeAssistantsResponse writes a relayed Assistants response.
func (h *Handlers) writeAssistantsResponse(w http.ResponseWriter, rc *models.RequestContext, respBody []byte, statusCode int, respHeaders http.Header) {
	h.setAgentccHeaders(w, rc)
	forwardAssistantsHeaders(w, respHeaders)
	w.Header().Set("Content-Type", "application/json")
	if statusCode == 0 {
		statusCode = http.StatusOK
	}
	w.WriteHeader(statusCode)
	w.Write(respBody)
}

func forwardAssistantsHeaders(w http.ResponseWriter, respHeaders http.Header) {
	for _, key := range []string{"openai-organization", "openai-processing-ms", "openai-version", "x-request-id"} {
		if v := respHeaders.Get(key); v != "" {
			w.Header().Set(key, v)
		}
	}
}
