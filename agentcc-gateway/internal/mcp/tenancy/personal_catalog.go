package tenancy

import (
	"context"
	"encoding/json"
	"slices"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// personalCatalog is immutable after publication on one userClient. No part of
// it is copied into entry.status, PoolMembers or an organization-wide cache.
type personalCatalog struct {
	tools, resources, prompts []json.RawMessage
	refreshedAt               time.Time
}

// personalLists is called inside begin/finish, so cached metadata is subject to
// the same grant checks as payload calls. A slow old listing can only populate
// its old userClient, never a replacement's catalog or another person's view.
func (t *timeoutClient) personalLists(ctx context.Context) (*personalCatalog, error) {
	e := t.entry
	e.mu.Lock()
	uc := e.users[t.userKey]
	if uc == nil || uc.client != t.UpstreamClient {
		e.mu.Unlock()
		return nil, mcp.CredentialRequired(e.id, "superseded")
	}
	e.mu.Unlock()
	uc.catalogMu.Lock()
	defer uc.catalogMu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := t.finish(ctx, nil); err != nil {
		return nil, err
	}
	ttl := time.Duration(e.spec().cacheTTLSeconds) * time.Second
	if cached := uc.catalog; cached != nil && (ttl <= 0 || e.pool.m.now().Before(cached.refreshedAt.Add(ttl))) {
		return cached, nil
	}
	ctx, cancel := context.WithTimeout(ctx, e.pool.m.opts.ListTimeout)
	defer cancel()
	tools, err := t.UpstreamClient.ListTools(ctx)
	if err != nil {
		return nil, err
	}
	// Resources and prompts are optional, just as for shared discovery.
	resources, err := t.UpstreamClient.ListResources(ctx)
	if err != nil {
		resources = nil
	}
	prompts, err := t.UpstreamClient.ListPrompts(ctx)
	if err != nil {
		prompts = nil
	}
	if err := t.finish(ctx, ctx.Err()); err != nil {
		return nil, err
	}
	if !t.UpstreamClient.Healthy() {
		return nil, mcp.CredentialRequired(e.id, "needs_reauth")
	}
	uc.catalog = &personalCatalog{tools: cloneCatalogItems(tools), resources: cloneCatalogItems(resources),
		prompts: cloneCatalogItems(prompts), refreshedAt: e.pool.m.now()}
	return uc.catalog, nil
}

// Raw objects are mutable slices. Neither upstream buffers nor a consumer
// editing a returned definition may mutate the next request's cached catalog.
func cloneCatalogItems(items []json.RawMessage) []json.RawMessage {
	out := make([]json.RawMessage, len(items))
	for i, raw := range items {
		out[i] = slices.Clone(raw)
	}
	return out
}
