package server

import (
	"fmt"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Plain model names on one Future AGI key. The managed catalogue is keyed by
// OpenRouter's "vendor/model" ids ("openai/gpt-4o"), but SDKs and tools send
// the names their vendor uses: "gpt-4o", a dated Claude id
// ("claude-sonnet-4-5-20250929", what Claude Code sends), a bare Gemini id.
// When no managed provider takes the name as it is (an exact catalogue id
// always wins), it is read as the catalogue id it names:
//  1. its serving identity (catalog.ManagedCanonical: a declared identity
//     alias, a listed dated canonical slug, or the vendor's spelling),
//     when a managed catalogue lists it; never by dropping an unknown date;
//  2. else, for a bare name, the one managed catalogue id whose model part
//     is that name ("foo-7b" for "vendor/foo-7b").
//
// A bare name more than one catalogue id ends in is ambiguous: the request
// is refused with a 400 that names every candidate, never routed to one of
// them by guess. A name with a variant suffix (":free", ":online") is served
// only as the exact catalogue id it is; it is never read as the model it
// decorates.

// managedCatalogID is the managed catalogue id model stands for, "" when it
// stands for none. candidates lists, sorted, every id a bare name matched
// when it matched more than one (id is "" then).
func (h *Handlers) managedCatalogID(model string) (id string, candidates []string) {
	cat := h.catalog.Load()
	name := strings.ToLower(strings.TrimSpace(model))
	if cat == nil || h.registry == nil || name == "" {
		return "", nil
	}
	// A variant (":free", ":online", ":thinking") is another price or
	// behaviour of its model: only the exact catalogue id carries one
	// (ResolveManaged), so a name with a variant stands for no other id and
	// is refused as not available, never served as the model it decorates.
	if catalog.HasVariant(name) {
		return "", nil
	}
	snaps := cat.Snapshots()
	for _, snap := range snaps {
		if !h.registry.IsManaged(snap.ProviderID) {
			continue
		}
		if canon := catalog.ManagedCanonical(snap.ProviderID, model); canon != "" && cat.Has(snap.ProviderID, canon) {
			return canon, nil
		}
	}
	if strings.Contains(name, "/") {
		return "", nil
	}
	seen := make(map[string]bool)
	for _, snap := range snaps {
		if !h.registry.IsManaged(snap.ProviderID) {
			continue
		}
		for mid := range snap.Models {
			slash := strings.IndexByte(mid, '/')
			if slash < 0 || seen[mid] || !strings.EqualFold(mid[slash+1:], name) || !cat.Has(snap.ProviderID, mid) {
				continue
			}
			seen[mid] = true
			candidates = append(candidates, mid)
		}
	}
	sort.Strings(candidates)
	switch len(candidates) {
	case 0:
		return "", nil
	case 1:
		return candidates[0], nil
	default:
		return "", candidates
	}
}

// ambiguousManagedModel is the 400 for a bare model name that more than one
// managed catalogue id ends in, for a request a managed model could serve;
// nil otherwise.
func (h *Handlers) ambiguousManagedModel(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) *models.APIError {
	if !orgCfg.ManagedModelsEnabled() || !managedEndpoint(rc) {
		return nil
	}
	_, candidates := h.managedCatalogID(model)
	if len(candidates) < 2 {
		return nil
	}
	return models.ErrBadRequest("model_ambiguous", fmt.Sprintf(
		"model %q matches more than one Future AGI model: %s. Send one of these ids as the model",
		model, strings.Join(candidates, ", ")))
}
