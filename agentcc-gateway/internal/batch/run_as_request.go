package batch

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ChatPath is the route each batch item runs on.
const ChatPath = "/v1/chat/completions"

// RunAsChatRequest returns the BatchHandler that runs each item as a POST
// /v1/chat/completions request served by handler(): the gateway's own HTTP
// handler chain, so an item gets everything a request gets (its own request
// id, auth, the org's config, providers, guardrails, budgets and credits, the
// request log). The request carries the batch's key as its Authorization,
// the trace id of the batch request (models.GetTraceID on ctx) as
// x-agentcc-trace-id, and the submit request's client address. An item never
// streams. handler is called per item, so it may name a handler built after
// the processor.
func RunAsChatRequest(handler func() http.Handler) BatchHandler {
	return func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		item := *req
		item.Stream = false
		item.StreamOptions = nil
		body, err := json.Marshal(item)
		if err != nil {
			return nil, nil, fmt.Errorf("encoding the item: %w", err)
		}
		httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, ChatPath, bytes.NewReader(body))
		if err != nil {
			return nil, nil, err
		}
		httpReq.RequestURI = ChatPath
		httpReq.Header.Set("Content-Type", "application/json")
		if traceID := models.GetTraceID(ctx); traceID != "" {
			httpReq.Header.Set("x-agentcc-trace-id", traceID)
		}
		if c, ok := callerFrom(ctx); ok {
			httpReq.RemoteAddr = c.remoteAddr
			for h, v := range c.forwarded {
				httpReq.Header[h] = append([]string(nil), v...)
			}
			if c.key != "" {
				httpReq.Header.Set("Authorization", "Bearer "+c.key)
			}
		}

		w := newItemWriter()
		handler().ServeHTTP(w, httpReq)

		// The item's result names the id its request-log entry is under.
		meta := map[string]string{MetaRequestID: w.header.Get("x-agentcc-request-id")}
		if cost := w.header.Get("x-agentcc-cost"); cost != "" {
			meta["cost"] = cost
		}
		if w.status() < 200 || w.status() > 299 {
			return nil, meta, itemError(w)
		}
		var resp models.ChatCompletionResponse
		if err := json.Unmarshal(w.body.Bytes(), &resp); err != nil {
			return nil, meta, fmt.Errorf("the chat completion answered %d with a body that is not a chat completion: %w", w.status(), err)
		}
		return &resp, meta, nil
	}
}

// itemError is the gateway's error answer to an item as an *models.APIError
// (its Error() is "code: message", as an item's error always read).
func itemError(w *itemWriter) error {
	var e models.ErrorResponse
	if err := json.Unmarshal(w.body.Bytes(), &e); err == nil && e.Error.Message != "" {
		return &models.APIError{
			Status:   w.status(),
			Type:     e.Error.Type,
			Code:     e.Error.Code,
			Message:  e.Error.Message,
			Param:    e.Error.Param,
			Metadata: e.Error.Metadata,
		}
	}
	return fmt.Errorf("chat completion failed with status %d", w.status())
}

// itemWriter is the in-memory http.ResponseWriter an item's request is served
// into.
type itemWriter struct {
	header http.Header
	code   int
	body   bytes.Buffer
}

func newItemWriter() *itemWriter { return &itemWriter{header: make(http.Header)} }

func (w *itemWriter) Header() http.Header { return w.header }

func (w *itemWriter) WriteHeader(code int) {
	if w.code == 0 {
		w.code = code
	}
}

func (w *itemWriter) Write(b []byte) (int, error) {
	if w.code == 0 {
		w.code = http.StatusOK
	}
	return w.body.Write(b)
}

func (w *itemWriter) status() int {
	if w.code == 0 {
		return http.StatusOK
	}
	return w.code
}
