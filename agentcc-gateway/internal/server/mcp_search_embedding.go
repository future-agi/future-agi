package server

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/redis/go-redis/v9"
)

// mcpSearchEmbeddingRoute dispatches in process through CreateEmbedding, never
// directly to a provider or back out over HTTP. Thus the same auth/model ACL,
// guardrails, attempt attribution, pricing and credits/BYOK plugins apply.
// OAuth/runtime/anonymous MCP identities have no embedding credential and use
// lexical search; they must never borrow an operator's key.
// This recovery uses the CURRENT public embedding policy unchanged. It does
// not restore the historical managed_search_embedding capability. Main owns
// the still-missing public managed grant/ledger binding; a managed refusal is
// explicit unfinished integration, not qualification or a feature cut.
type mcpSearchEmbeddingRoute struct {
	handlers *Handlers
	admit    func(context.Context, string, int) bool
}

func (r *mcpSearchEmbeddingRoute) Embed(ctx context.Context, cc *mcp.CallContext, req *models.EmbeddingRequest) (*models.EmbeddingResponse, string, error) {
	if cc == nil {
		return nil, "", errors.New("search embedding caller unavailable")
	}
	return r.embed(ctx, cc.Principal, cc.Request(), req)
}
func (r *mcpSearchEmbeddingRoute) embed(ctx context.Context, p mcp.Principal, parent *http.Request, req *models.EmbeddingRequest) (*models.EmbeddingResponse, string, error) {
	unavailable := errors.New("search embedding route unavailable")
	h := r.handlers
	if ctx.Err() != nil || middleware.IsLicenseAuthorized(ctx) || h == nil || h.keyStore == nil || h.tenantStore == nil || parent == nil || p.Kind != mcp.PrincipalKey || p.OrgID == "" || r.admit == nil || req == nil || len(req.Input) > mcp.SearchEmbeddingMaxBytes {
		return nil, "", unavailable
	}
	raw := parent.Header.Get("x-api-key")
	if value := parent.Header.Get("Authorization"); value != "" {
		parts := strings.Fields(value)
		if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
			return nil, "", unavailable
		}
		raw = parts[1]
	}
	key, meta := h.keyStore.LookupActive(raw)
	if key == nil || key.ID != p.KeyID || meta["org_id"] != p.OrgID {
		return nil, "", unavailable
	}
	// The parent MCP identity proves who called, not permission to use a
	// model route. Re-read scope from the active key snapshot just obtained,
	// using the public embedding route's rule before consuming any quota or
	// reserving credit. No MCP root can confer authority on this model route.
	if !keyscope.Parse(meta).Allows(http.MethodPost, "/v1/embeddings", "") {
		return nil, "", unavailable
	}
	cfg := h.tenantStore.Get(p.OrgID).MCPSearch()
	if !cfg.Enabled || req.Model != cfg.Model || !r.admit(ctx, p.OrgID, cfg.CallsPerMinute) || ctx.Err() != nil {
		return nil, "", unavailable
	}
	body, err := json.Marshal(req)
	if err != nil {
		return nil, "", unavailable
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://gateway.internal/v1/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, "", unavailable
	}
	request.RemoteAddr = parent.RemoteAddr
	// Never inherit a model/provider/BYOK override, billing mode or claimed org
	// from an MCP header. Only the already authenticated key funds this call.
	request.Header.Set("Authorization", "Bearer "+raw)
	request.Header.Set("Content-Type", "application/json")
	for _, name := range []string{"x-agentcc-agent-id", "x-agentcc-run-id", "x-agentcc-step", "traceparent", "x-agentcc-trace-id"} {
		if value := parent.Header.Get(name); value != "" {
			request.Header.Set(name, value)
		}
	}
	writer := &searchEmbeddingWriter{header: make(http.Header)}
	// Generates a distinct request ID for each index batch and query; reusing
	// the MCP request ID would collide with ledger reservation idempotency.
	middleware.RequestID(http.HandlerFunc(h.CreateEmbedding)).ServeHTTP(writer, request)
	if writer.status != http.StatusOK || writer.overflow || ctx.Err() != nil {
		return nil, "", unavailable
	}
	var resp models.EmbeddingResponse
	if json.Unmarshal(writer.body.Bytes(), &resp) != nil {
		return nil, "", unavailable
	}
	space := writer.header.Get(headerEmbeddingSpace)
	if space == "" {
		return nil, "", unavailable
	}
	return &resp, writer.header.Get("x-agentcc-provider") + "/" + space, nil
}

// Bounded response capture avoids a second unbounded response buffer. The
// vectors are validated (indices, dimensions, finite nonzero norm) by MCP.
type searchEmbeddingWriter struct {
	header   http.Header
	body     bytes.Buffer
	status   int
	overflow bool
}

func (w *searchEmbeddingWriter) Header() http.Header { return w.header }
func (w *searchEmbeddingWriter) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}
func (w *searchEmbeddingWriter) Write(p []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	if w.overflow || w.body.Len()+len(p) > 2*1024*1024 {
		w.overflow = true
		return len(p), nil
	}
	return w.body.Write(p)
}

// Fixed-window admission uses Redis TIME, not a replica's wall clock. It is a
// per-org call envelope (index + query), not a monetary ledger. Normal credits
// still hold/charge the real price. Failures and timeouts consume their slot:
// refunding them could admit unbounded paid work when upstream ignores cancel.
var searchAdmissionScript = redis.NewScript(`
local now = redis.call('TIME')
local window = math.floor(tonumber(now[1]) / 60)
local old = tonumber(redis.call('HGET', KEYS[1], 'window') or '-1')
local used = tonumber(redis.call('HGET', KEYS[1], 'used') or '0')
if old ~= window then used = 0 end
if used >= tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[1], 'window', window, 'used', used + 1)
redis.call('EXPIRE', KEYS[1], 120)
return 1
`)

func searchAdmission(client *redisstate.Client) func(context.Context, string, int) bool {
	return func(ctx context.Context, org string, limit int) bool {
		if client == nil || org == "" || limit < 1 || limit > 600 {
			return false
		}
		sum := sha256.Sum256([]byte(org))
		name := "agentcc:mcp-search:" + hex.EncodeToString(sum[:])
		var admitted int
		err := client.Do(func(rdb redis.UniversalClient) error {
			value, err := searchAdmissionScript.Run(ctx, rdb, []string{name}, limit).Int()
			admitted = value
			return err
		})
		return err == nil && admitted == 1 // never fall back to replica-local quota
	}
}
