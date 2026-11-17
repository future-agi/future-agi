package mcp

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"time"
)

// index runs at tools/list discovery time, after policy, pins, scanning and
// step narrowing. Queries never trigger this work. Changed definitions miss by
// fingerprint immediately; a subsequent discovery replaces only those vectors.
// This is a bounded, ephemeral accelerator, never an authority for visibility.
func (h *hybridSearch) index(cc *CallContext, offered []Tool) {
	cfg := cc.OrgConfig.MCPSearch()
	if h == nil || h.route == nil || !cfg.Enabled || cc.Principal.OrgID == "" || !h.indexMu.TryLock() {
		return
	}
	defer h.indexMu.Unlock()
	scope, err := searchScope(cc)
	if err != nil {
		return
	}
	tools := append([]Tool(nil), offered...)
	sort.Slice(tools, func(i, j int) bool { return tools[i].Name < tools[j].Name })
	tools = tools[:min(len(tools), searchCacheEntries)]
	pending := []Tool{}
	h.mu.Lock()
	for _, t := range tools {
		if v, ok := h.cache[searchCacheKey{scope, t.Name}]; !ok || v.fingerprint != searchFingerprint(t) {
			pending = append(pending, t)
		}
	}
	h.mu.Unlock()
	// Discovery itself is bounded; partial indexes are safe, since any cache
	// miss falls back to the complete lexical answer rather than partial rerank.
	ctx, cancel := context.WithTimeout(cc.Context(), 2*time.Second)
	defer cancel()
	for len(pending) > 0 {
		batch := pending[:min(16, len(pending))]
		texts := make([]string, len(batch))
		for i, t := range batch {
			texts[i] = searchDocument(t)
		}
		// JSON escaping can expand non-ASCII/control-heavy metadata. Batch by the
		// actual encoded input size, never by a character/token approximation.
		for len(batch) > 1 {
			raw, _ := json.Marshal(texts)
			if len(raw) <= SearchEmbeddingMaxBytes {
				break
			}
			batch = batch[:len(batch)/2]
			texts = texts[:len(batch)]
		}
		callCtx, done := context.WithTimeout(ctx, time.Duration(cfg.TimeoutMS)*time.Millisecond)
		vectors, space, err := h.embed(callCtx, cc, texts)
		done()
		if err != nil {
			return
		}
		h.mu.Lock()
		for i, t := range batch {
			key := searchCacheKey{scope, t.Name}
			if _, exists := h.cache[key]; !exists {
				if len(h.cache) >= searchCacheEntries {
					delete(h.cache, h.order[0])
					h.order = h.order[1:]
				}
				h.order = append(h.order, key)
			}
			h.cache[key] = searchVector{searchFingerprint(t), space, vectors[i]}
		}
		h.mu.Unlock()
		pending = pending[len(batch):]
	}
}

func searchDocument(t Tool) string {
	// Embed only bounded searchable fields, not schema defaults/examples or
	// arbitrary raw server metadata. Fingerprints still cover the full definition.
	text := searchPrefix(t.Name, 512) + "\n" + searchPrefix(t.Title, 256) + "\n" + searchPrefix(t.Description, 2048) + "\n" + searchPrefix(searchParameters(t.InputSchema), 1024)
	return strings.ToValidUTF8(text, " ")
}
