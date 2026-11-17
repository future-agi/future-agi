package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func (s *Server) recordEnvelopeRejection(reason string) {
	slog.Warn("service envelope rejected", "reason", reason)
	if s.metricsRegistry != nil {
		s.metricsRegistry.CounterInc("envelope_rejected_total", "Rejected service envelopes", map[string]string{"reason": reason})
	}
}

func rejectDelegated(w http.ResponseWriter) {
	apiErr := models.ErrUnauthorized("service_envelope_rejected")
	apiErr.Code = "service_envelope_rejected"
	models.WriteError(w, apiErr)
}

// refuseDelegatedBatchOnCluster refuses a delegated batch route on a
// cluster whose replicas share no batch store (a cluster node without
// Redis), with the answer every refused envelope gets. Without one a batch
// lives in the memory of the replica that took it, so another replica could
// neither read nor cancel it, whatever nonce store the replicas share.
func (s *Server) refuseDelegatedBatchOnCluster(w http.ResponseWriter) bool {
	if !s.cfg.Cluster.Enabled || s.batchProcessor.Shared() {
		return false
	}
	s.recordEnvelopeRejection("route_or_headers")
	rejectDelegated(w)
	return true
}

func (s *Server) verifyDelegatedRequest(w http.ResponseWriter, r *http.Request) (*http.Request, bool) {
	// The in-process nonce store is not safe for a multi-replica cluster: a
	// cluster serves delegated calls only with the store its replicas share.
	if (s.cfg.Cluster.Enabled && !s.delegated.Shared()) || s.delegated == nil || r.URL.RawQuery != "" || len(r.Header.Values(auth.EnvelopeHeader)) != 1 || len(r.Header.Values(auth.GrantHeader)) != 1 {
		s.recordEnvelopeRejection("route_or_headers")
		rejectDelegated(w)
		return nil, false
	}
	const limit = 10 << 20
	body, err := io.ReadAll(io.LimitReader(r.Body, limit+1))
	if err != nil || len(body) > limit {
		s.recordEnvelopeRejection("body")
		rejectDelegated(w)
		return nil, false
	}
	_, grant, err := s.delegated.VerifyDelegated(r.Header.Get(auth.EnvelopeHeader), r.Header.Get(auth.GrantHeader), r.Method, r.URL.EscapedPath(), body, time.Now())
	if err != nil {
		rejectDelegated(w)
		return nil, false
	}
	// A console-run grant names the models the Console run may call (fe1c
	// field 20). Refuse any other model here, before routing, plugins, credit
	// holds or a stream begin.
	if grant.Kind == "console-run" {
		if model := delegatedRequestedModel(body); !grant.PermitsModel(model) {
			s.recordEnvelopeRejection("model_cap")
			models.WriteError(w, &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: "model_not_permitted", Message: fmt.Sprintf("model %q is not one this Console run may call", model)})
			return nil, false
		}
		// A caller's own fallback models (the body's models[], a provider
		// options models list, or an x-agentcc-config fallback) could reach a
		// model outside the cap after the first attempt fails, and a routing
		// header (x-agentcc-complexity-override, x-agentcc-auto-preference)
		// could route it to another model before the first: a Console run
		// names its one model and nothing else.
		if field := consoleRunFallbackField(body, r); field != "" {
			s.recordEnvelopeRejection("model_cap")
			models.WriteError(w, &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: "model_not_permitted", Message: fmt.Sprintf("a Console run calls only its own model; %s is not accepted", field)})
			return nil, false
		}
	}
	out := r.Clone(auth.WithDelegatedPrincipal(r.Context(), grant, r.Header.Get(auth.GrantHeader)))
	out.Body = io.NopCloser(bytes.NewReader(body))
	out.ContentLength = int64(len(body))
	// Credentials live solely in the unexported context, not in metadata or
	// headers the inner handler might forward or log.
	for _, h := range []string{"Authorization", "X-Api-Key", "X-Goog-Api-Key", auth.EnvelopeHeader, auth.GrantHeader, batch.KeyHeader} {
		out.Header.Del(h)
	}
	return out, true
}

// delegatedRequestedModel is the body's top-level "model" as the chat handler
// reads it: encoding/json into a field tagged "model", so the same key
// matching (case-insensitive, the last match wins). Other fields are
// tolerated. A body that does not decode, or whose model is not a JSON
// string, has model "".
func delegatedRequestedModel(body []byte) string {
	var probe struct {
		Model json.RawMessage `json:"model"`
	}
	if err := json.Unmarshal(body, &probe); err != nil || len(probe.Model) == 0 {
		return ""
	}
	var model string
	if err := json.Unmarshal(probe.Model, &model); err != nil {
		return ""
	}
	return model
}

func (s *Server) delegatedChatHandler(w http.ResponseWriter, r *http.Request) {
	inner, ok := s.verifyDelegatedRequest(w, r)
	if !ok {
		return
	}
	inner.URL.Path = batch.ChatPath
	inner.URL.RawPath = ""
	inner.RequestURI = batch.ChatPath
	// Reuse the actual chain, and the original writer: no buffering of SSE.
	s.httpServer.Handler.ServeHTTP(w, inner)
}

// The org's provider/guardrail config is resolved BEFORE the auth plugin.
// Do not let a delegated request fall back to the node's org/provider here.
func peekDelegatedKey(ctx context.Context, rc *models.RequestContext) bool {
	key := auth.DelegatedPrincipal(ctx)
	if key == nil {
		return false
	}
	rc.Metadata["key_org_id"] = key.Metadata["org_id"]
	rc.Metadata["key_user_id"] = key.Metadata["user_id"]
	rc.Metadata["key_workspace_id"] = key.Metadata["workspace_id"]
	rc.Metadata["key_type"] = key.KeyType
	rc.Metadata[privacy.KeyLogContentRequestMetadata] = key.Metadata[privacy.KeyLogContentMetadata]
	recordKeyACL(rc, auth.KeyPeek{KeyType: key.KeyType, Metadata: key.Metadata})
	// A console-run grant holds every attempt of the call to the models it
	// names (console_run_cap.go).
	stampConsoleRunCap(ctx, rc)
	return true
}

// delegatedBatchMode is the one selection rule for listing, getting and
// cancelling batches: any envelope or grant header selects the verified
// principal, even beside FA's admin bearer, so an envelope never falls back to
// the admin token's view. The admin token alone keeps admin mode.
func delegatedBatchMode(r *http.Request) bool {
	return len(r.Header.Values(auth.EnvelopeHeader)) > 0 || len(r.Header.Values(auth.GrantHeader)) > 0
}

// GET /-/batches has two callers.
//   - FA's delegated batch recovery sends an fe1 envelope with a batch grant
//     and reads only the envelope's org (733 add.13(3)), under submit's gate.
//     Any envelope or grant header selects this mode, even beside FA's admin
//     bearer, so the envelope can never widen to the whole inventory.
//   - FA's background reconciliation and ops, with the admin token alone,
//     read the whole inventory: on a cluster with a shared batch store every
//     replica's batches, else this replica's. Shared state it cannot read
//     answers 503, never a shorter list a caller could take for complete.
//
// Both modes answer the same metadata-only rows (batch.ListRow): batch_id,
// status, error_code, org_id, key_id, total, counts and timestamps, never
// results, request bodies or prompt text.
func (s *Server) listBatchesHandler(w http.ResponseWriter, r *http.Request) {
	if delegatedBatchMode(r) {
		s.listDelegatedBatches(w, r)
		return
	}
	if !s.checkAdminAuth(w, r) {
		return
	}
	rows, err := s.batchProcessor.ClusterInventory()
	if err != nil {
		writeBatchStateUnavailable(w, batchStateUnreadable)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"batches": rows})
}

// listDelegatedBatches answers the verified principal's org only: each row's
// org_id, key_id (dlg_<jti> of the grant that submitted it), status, counts
// and timestamps, never results or request bodies.
func (s *Server) listDelegatedBatches(w http.ResponseWriter, r *http.Request) {
	if s.refuseDelegatedBatchOnCluster(w) {
		return
	}
	r, ok := s.verifyDelegatedRequest(w, r)
	if !ok {
		return
	}
	// The same membership, revocation and freshness gate as submit.
	caller, apiErr := s.batchProcessor.CallerFor(r)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}
	rows, err := s.batchProcessor.ListFor(caller.OrgID)
	if err != nil {
		writeBatchStateUnavailable(w, batchStateUnreadable)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(map[string]any{"batches": rows})
}

// delegatedBatchCaller runs submit's gate for one batch's route, GET
// /-/batches/{id} or POST /-/batches/{id}/cancel (733 add.16(a)), and returns
// the verified caller and the batch id the envelope signed. Refuse all caller
// queries (733 add.17(2)), before stripping the router's synthetic batch_id
// query parameter to verify the signed path. The org is the principal's.
func (s *Server) delegatedBatchCaller(w http.ResponseWriter, r *http.Request, suffix string) (batch.Caller, string, bool) {
	if s.refuseDelegatedBatchOnCluster(w) {
		return batch.Caller{}, "", false
	}
	query := r.URL.RawQuery
	if original, ok := r.Context().Value(originalRawQueryKey{}).(string); ok {
		query = original
	}
	if query != "" {
		s.recordEnvelopeRejection("route_or_headers")
		rejectDelegated(w)
		return batch.Caller{}, "", false
	}
	batchID := r.URL.Query().Get("batch_id")
	signed := r.Clone(r.Context())
	signed.URL.RawQuery = ""
	signed, ok := s.verifyDelegatedRequest(w, signed)
	if !ok {
		return batch.Caller{}, "", false
	}
	// The MAC bound this exact path: scope by the id it names.
	if batchID == "" || signed.URL.EscapedPath() != "/-/batches/"+batchID+suffix {
		s.recordEnvelopeRejection("route_or_headers")
		rejectDelegated(w)
		return batch.Caller{}, "", false
	}
	// The same membership, revocation and freshness gate as submit.
	caller, apiErr := s.batchProcessor.CallerFor(signed)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return batch.Caller{}, "", false
	}
	return caller, batchID, true
}

// getDelegatedBatch answers get's contract, results included, only for a
// batch of the verified principal's org. Another org's batch answers exactly
// as one that does not exist.
func (s *Server) getDelegatedBatch(w http.ResponseWriter, r *http.Request) {
	caller, batchID, ok := s.delegatedBatchCaller(w, r, "")
	if !ok {
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	b, err := s.batchProcessor.GetFor(batchID, caller.OrgID)
	if writeBatchLookupError(w, err) {
		return
	}
	writeBatchJSON(w, batchGetResponse(b))
}

// cancelDelegatedBatch cancels only a batch of the verified principal's org.
// Another org's batch is not found, exactly as one that does not exist, and
// keeps running.
func (s *Server) cancelDelegatedBatch(w http.ResponseWriter, r *http.Request) {
	caller, batchID, ok := s.delegatedBatchCaller(w, r, "/cancel")
	if !ok {
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	b, err := s.batchProcessor.CancelFor(batchID, caller.OrgID)
	if writeBatchLookupError(w, err) {
		return
	}
	writeBatchJSON(w, batchCancelResponse(b))
}

// consoleRunFallbackField names the part of a Console run's call that asks
// for models beyond its own, "" when none does: a top-level "models" or
// "fallbacks" key, a "providerOptions"/"provider_options" object (which can
// carry a models list), an x-agentcc-config header, or a routing header that
// moves the call to another model (consoleRunRoutingHeaders: the complexity
// tier, auto's preference), sent with any value. Keys match as the chat
// handler matches them, case-insensitively.
func consoleRunFallbackField(body []byte, r *http.Request) string {
	if r.Header.Get("X-Agentcc-Config") != "" {
		return "the x-agentcc-config header"
	}
	for _, name := range consoleRunRoutingHeaders {
		if len(r.Header.Values(name)) > 0 {
			return "the " + name + " header"
		}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return ""
	}
	for key, value := range fields {
		switch strings.ToLower(key) {
		case "models", "fallbacks", "provideroptions", "provider_options":
			if v := strings.TrimSpace(string(value)); v != "null" && v != "[]" && v != "{}" {
				return fmt.Sprintf("the %q field", key)
			}
		}
	}
	return ""
}
