package mcp

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// SearchEmbeddingRoute is the gateway's embedding route, not a provider client.
// Implementations must authenticate the caller again, enforce the org's shared
// admission bound and run ordinary embedding credit/BYOK accounting. No key is
// stored in the index. Unsupported identities must fail closed to lexical search.
type SearchEmbeddingRoute interface {
	Embed(context.Context, *CallContext, *models.EmbeddingRequest) (*models.EmbeddingResponse, string, error)
}

const (
	searchCacheEntries      = 4096
	searchVectorDimensions  = 2048
	SearchEmbeddingMaxBytes = 32 * 1024
)

type searchCacheKey struct {
	scope [32]byte
	name  string
}
type searchVector struct {
	fingerprint [32]byte
	space       string
	values      []float64
}
type hybridSearch struct {
	route   SearchEmbeddingRoute
	mu      sync.Mutex
	indexMu sync.Mutex
	cache   map[searchCacheKey]searchVector
	order   []searchCacheKey
	slots   chan struct{}
}

func newHybridSearch(route SearchEmbeddingRoute) *hybridSearch {
	return &hybridSearch{route: route, cache: map[searchCacheKey]searchVector{}, slots: make(chan struct{}, 8)}
}

// SetSearchEmbeddingRoute is installed once before serving. Nil keeps lexical
// search. A fresh installation intentionally drops the old in-memory index.
func (s *Server) SetSearchEmbeddingRoute(route SearchEmbeddingRoute) {
	if route == nil {
		s.search.Store(nil)
		return
	}
	s.search.Store(newHybridSearch(route))
}

func searchScope(cc *CallContext) ([32]byte, error) {
	// Include the policy/provider snapshot so a model map, upstream key, model or
	// override change cannot reuse a vector from another embedding space.
	raw, err := json.Marshal(struct {
		Org, Key string
		Config   any
	}{cc.Principal.OrgID, cc.Principal.KeyID, cc.OrgConfig})
	return sha256.Sum256(raw), err
}
func searchFingerprint(t Tool) [32]byte {
	// Raw and gateway revision metadata are deliberately omitted by Tool's JSON
	// marshaler, but must participate in invalidation. Encode raw JSON as strings
	// so even a malformed schema cannot collapse all fingerprints to hash(nil).
	annotations, _ := json.Marshal(t.Annotations)
	meta := make(map[string]string, len(t.GatewayMeta))
	for key, value := range t.GatewayMeta {
		meta[key] = string(value)
	}
	raw, _ := json.Marshal(struct {
		Fields []string
		Meta   map[string]string
	}{[]string{t.Name, t.Title, t.Description, string(t.InputSchema), string(t.OutputSchema), string(t.Icons), string(t.Meta), string(t.Raw), string(annotations)}, meta})
	return sha256.Sum256(raw)
}

// rank always starts with the existing lexical ranker over current offered
// tools. It never builds an index or embeds a document on the query path.
func (h *hybridSearch) rank(cc *CallContext, offered []Tool, query string, limit int) []Tool {
	terms, whole := searchTerms(query), strings.ToLower(strings.TrimSpace(query))
	cfg := cc.OrgConfig.MCPSearch()
	enabled := h != nil && h.route != nil && cfg.Enabled && cc.Principal.OrgID != "" && limit > 0 && len(query) <= searchMaxQueryBytes
	candidateLimit := limit
	if enabled {
		candidateLimit = max(limit, cfg.TopK)
	}
	candidates := rankTools(offered, terms, whole, candidateLimit)
	lexical := candidates[:min(max(0, limit), len(candidates))]
	if !enabled || len(candidates) < 2 {
		return lexical
	}
	scope, err := searchScope(cc)
	if err != nil {
		return lexical
	}
	vectors := make([]searchVector, len(candidates))
	h.mu.Lock()
	for i, t := range candidates {
		v, ok := h.cache[searchCacheKey{scope, t.Name}]
		if !ok || v.fingerprint != searchFingerprint(t) {
			h.mu.Unlock()
			return lexical
		}
		vectors[i] = v
	}
	h.mu.Unlock()
	ctx, cancel := context.WithTimeout(cc.Context(), time.Duration(cfg.TimeoutMS)*time.Millisecond)
	defer cancel()
	values, space, err := h.embed(ctx, cc, []string{query})
	if err != nil {
		return lexical
	}
	scores := make([]float64, len(candidates))
	for i, v := range vectors {
		if v.space != space || len(v.values) != len(values[0]) {
			return lexical
		}
		for j, x := range v.values {
			scores[i] += x * values[0][j]
		}
	}
	order := make([]int, len(candidates))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(i, j int) bool {
		a, b := order[i], order[j]
		ae, be := strings.EqualFold(candidates[a].Name, whole), strings.EqualFold(candidates[b].Name, whole)
		if ae != be {
			return ae
		}
		return scores[a] > scores[b] // stable ties retain the deterministic lexical order
	})
	out := make([]Tool, 0, min(limit, len(order)))
	for _, i := range order[:min(limit, len(order))] {
		out = append(out, candidates[i])
	}
	return out
}

// embed bounds latency even if a broken adapter ignores cancellation. Slots
// remain occupied until the adapter actually returns, bounding stranded work.
func (h *hybridSearch) embed(ctx context.Context, cc *CallContext, texts []string) ([][]float64, string, error) {
	unavailable := errors.New("search embeddings unavailable")
	if ctx.Err() != nil {
		return nil, "", ctx.Err()
	}
	select {
	case h.slots <- struct{}{}:
	default:
		return nil, "", unavailable
	}
	input, _ := json.Marshal(texts)
	if len(input) > SearchEmbeddingMaxBytes {
		<-h.slots
		return nil, "", unavailable
	}
	req := &models.EmbeddingRequest{Model: cc.OrgConfig.MCPSearch().Model, Input: input, EncodingFormat: "float"}
	// Do not hand an asynchronous worker the mutable chain context.
	snapshot := &CallContext{Principal: cc.Principal, OrgConfig: cc.OrgConfig, ctx: ctx}
	request := cc.searchRequest
	if request == nil {
		request = cc.req
	}
	if request != nil {
		snapshot.req = request.Clone(ctx)
	}
	type answer struct {
		resp  *models.EmbeddingResponse
		space string
		err   error
	}
	done := make(chan answer, 1)
	go func() {
		defer func() { <-h.slots }()
		defer func() {
			if recover() != nil {
				done <- answer{err: unavailable}
			}
		}()
		resp, space, err := h.route.Embed(ctx, snapshot, req)
		done <- answer{resp, space, err}
	}()
	select {
	case <-ctx.Done():
		return nil, "", ctx.Err()
	case a := <-done:
		if ctx.Err() != nil {
			return nil, "", ctx.Err()
		}
		if a.err != nil || a.resp == nil || a.space == "" || len(a.resp.Data) != len(texts) {
			return nil, "", unavailable
		}
		out := make([][]float64, len(texts))
		dims := 0
		for _, d := range a.resp.Data {
			if d.Index < 0 || d.Index >= len(out) || out[d.Index] != nil {
				return nil, "", unavailable
			}
			var v []float64
			if len(d.Embedding) > 64*1024 || json.Unmarshal(d.Embedding, &v) != nil || len(v) == 0 || len(v) > searchVectorDimensions {
				return nil, "", unavailable
			}
			if dims != 0 && dims != len(v) {
				return nil, "", unavailable
			}
			dims = len(v)
			norm := 0.0
			for _, x := range v {
				norm = math.Hypot(norm, x)
			}
			if norm == 0 || math.IsNaN(norm) || math.IsInf(norm, 0) {
				return nil, "", unavailable
			}
			for i := range v {
				v[i] /= norm
			}
			out[d.Index] = v
		}
		return out, a.space, nil
	}
}
