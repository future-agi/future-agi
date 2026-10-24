// Package mock is the agentcc-mockup upstream: an OpenAI-, OpenRouter- and
// Anthropic-compatible server whose answers are shaped per request by
// x-mock-behavior directives, with inspection endpoints (/__*) that let a
// scenario see what the gateway sent and what it was charged.
package mock

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"strings"
	"time"
)

// maxBodyBytes bounds a request body the mock reads.
const maxBodyBytes = 32 << 20

// Server is the mock upstream.
type Server struct {
	fix   *Fixtures
	state *State
	mux   *http.ServeMux
	hook  DirectiveFunc
}

// New builds the mock over fix.
func New(fix *Fixtures) *Server {
	s := &Server{
		fix:   fix,
		state: NewState(usdToMicros(float64(fix.Config.BalanceUSD))),
		mux:   http.NewServeMux(),
	}
	s.routes()
	return s
}

// State is the mock's state, for the ext package.
func (s *Server) State() *State { return s.state }

// Mux is where the ext package registers its routes.
func (s *Server) Mux() *http.ServeMux { return s.mux }

// SetDirectiveHook sets the handler for directives the mock does not know.
func (s *Server) SetDirectiveHook(fn DirectiveFunc) { s.hook = fn }

// ServeHTTP records every upstream-facing request for /__received, then serves it.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
	if err != nil {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_body", "cannot read the request body")
		return
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	if !isInspectionPath(r.URL.Path) {
		s.state.recordRequest(r, body)
	}
	s.mux.ServeHTTP(w, r)
}

// isInspectionPath reports whether path is the mock's own (/__*) or the log
// webhook, neither of which an upstream would receive.
func isInspectionPath(path string) bool {
	return strings.HasPrefix(path, "/__") || path == logWebhookPath
}

func (s *Server) routes() {
	inference := map[string]Route{
		"/chat/completions":   RouteChat,
		"/responses":          RouteResponses,
		"/messages":           RouteMessages,
		"/embeddings":         RouteEmbeddings,
		"/images/generations": RouteImages,
		"/audio/speech":       RouteSpeech,
	}
	for path, route := range inference {
		h := s.inferenceHandler(route)
		s.mux.HandleFunc("POST /v1"+path, h)
		s.mux.HandleFunc("POST /api/v1"+path, h)
	}
	s.catalogRoutes()
	s.inspectionRoutes()
	s.mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		writeOpenAIError(w, http.StatusNotFound, "not_found", "the mock does not serve "+r.Method+" "+r.URL.Path)
	})
}

// inferenceHandler runs one inference call: build the success the fixtures
// describe, apply the directives, wait, then answer and charge. A streamed
// answer is charged for the output it streamed (renderStream).
func (s *Server) inferenceHandler(route Route) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body) // already bounded and buffered by ServeHTTP
		req := newRequest(route, r, body)
		resp := s.baseResponse(req)
		if err := s.applyDirectives(s.behaviorFor(req), req, resp); err != nil {
			status, code := directiveErrorStatus(err)
			writeRouteError(w, route, status, code, err.Error())
			return
		}
		if !sleepCtx(r.Context(), resp.LatencyMs) {
			return
		}
		if resp.Status < 200 || resp.Status > 299 {
			writeRouteError(w, route, resp.Status, resp.ErrorCode, resp.ErrorMessage)
			return
		}
		cost := s.costUSD(req, resp)
		if req.Stream && streamedRoute(route) {
			s.renderStream(w, req, resp, cost)
			return
		}
		s.recordCharge(req, cost)
		s.render(w, req, resp, cost)
	}
}

// streamedRoute reports whether route answers "stream": true with events.
func streamedRoute(route Route) bool {
	return route == RouteChat || route == RouteResponses || route == RouteMessages
}

func (s *Server) recordCharge(req *Request, cost float64) {
	s.state.RecordCharge(Charge{Bearer: req.Bearer, Model: req.Model, Route: req.Route, Micros: usdToMicros(cost)})
}

// renderStream streams the answer and charges it once: cost, the complete
// call's price, when all its output went out; the streamed share when
// stream_abort_after or a departed client cut it short (streamCharge).
func (s *Server) renderStream(w http.ResponseWriter, req *Request, resp *Response, cost float64) {
	charge := newStreamCharge(resp, func(sent, total int) {
		s.recordCharge(req, s.streamedCostUSD(req, resp, cost, sent, total))
	})
	req.HTTP = req.HTTP.WithContext(context.WithValue(req.HTTP.Context(), streamChargeKey{}, charge))
	completed := false // stays false when stream_abort_after panics out of render
	defer func() { charge.end(completed && req.HTTP.Context().Err() == nil) }()
	s.render(w, req, resp, cost)
	completed = true
}

// streamedCostUSD prices a stream that sent sent of its total output runes:
// the prompt plus that share of the completion tokens (rounded down), at the
// complete call's prices. A usage_cost, which fixes the complete call's cost
// (full), is scaled by the share of the table price that was streamed; with
// a zero table price there is nothing to apportion, and full is charged.
func (s *Server) streamedCostUSD(req *Request, resp *Response, full float64, sent, total int) float64 {
	if sent >= total {
		return full
	}
	cut := *resp
	cut.CostUSD = nil
	cut.Usage.Completion = resp.Usage.Completion * sent / total
	partial := s.costUSD(req, &cut)
	if resp.CostUSD == nil {
		return partial
	}
	whole := *resp
	whole.CostUSD = nil
	if table := s.costUSD(req, &whole); table > 0 {
		return math.Round(full*partial/table*1e9) / 1e9
	}
	return full
}

func (s *Server) render(w http.ResponseWriter, req *Request, resp *Response, cost float64) {
	switch req.Route {
	case RouteChat:
		s.renderChat(w, req, resp, cost)
	case RouteResponses:
		s.renderResponses(w, req, resp, cost)
	case RouteMessages:
		s.renderMessages(w, req, resp)
	case RouteEmbeddings:
		s.renderEmbeddings(w, req, resp, cost)
	case RouteImages:
		renderImages(w, req, resp)
	case RouteSpeech:
		renderSpeech(w, resp)
	}
}

// sleepCtx waits ms milliseconds, or until ctx ends (false).
func sleepCtx(ctx context.Context, ms int) bool {
	if ms <= 0 {
		return ctx.Err() == nil
	}
	t := time.NewTimer(time.Duration(ms) * time.Millisecond)
	defer t.Stop()
	select {
	case <-t.C:
		return true
	case <-ctx.Done():
		return false
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeRawJSON(w http.ResponseWriter, status int, b []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

// writeRouteError answers in the error shape the route's clients parse.
func writeRouteError(w http.ResponseWriter, route Route, status int, code, msg string) {
	if route == RouteMessages {
		writeJSON(w, status, map[string]any{
			"type":  "error",
			"error": map[string]any{"type": errorType(status, true), "message": msg, "code": code},
		})
		return
	}
	writeOpenAIError(w, status, code, msg)
}

func writeOpenAIError(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, map[string]any{
		"error": map[string]any{"message": msg, "type": errorType(status, false), "code": code},
	})
}

func errorType(status int, anthropic bool) string {
	switch {
	case status == http.StatusUnauthorized:
		return "authentication_error"
	case status == http.StatusForbidden:
		return "permission_error"
	case status == http.StatusNotFound:
		return "not_found_error"
	case status == http.StatusTooManyRequests:
		return "rate_limit_error"
	case status >= 500 && anthropic:
		return "api_error"
	case status >= 500:
		return "server_error"
	}
	return "invalid_request_error"
}
