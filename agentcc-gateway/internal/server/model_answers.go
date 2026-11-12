package server

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Why a key limited to its org's providers cannot call a model
// (DX-ERRORS-1). One 403 "not available for this API key" used to answer a
// typo, a Future AGI model while the org's Future AGI credits are off, and a
// real key restriction alike, and sent the caller looking at their key.

// codeFutureAGICreditsOff is the code of a Future AGI model called while
// the org's Future AGI credits are off.
const codeFutureAGICreditsOff = "future_agi_credits_off"

// maxModelSuggestions is the most close model ids a model_not_found offers.
const maxModelSuggestions = 3

// modelNotServed is why model is not served to rc's key, which may use only
// its org's providers, under orgCfg, once no org provider failed to build
// for it (unavailableModelError), in this order:
//  1. the key's allowed_models leave it out: 403 model_not_permitted naming
//     the restriction, as eligibility answers a resolved model;
//  2. it is a Future AGI model the org could call on Future AGI credits,
//     which are off: 403 future_agi_credits_off naming where to turn them on;
//  3. nothing the org can use or the gateway knows serves it: 404
//     model_not_found with up to three close ids the key could call;
//  4. otherwise (a model only the gateway's own providers serve, or one
//     Future AGI credits cannot bill): 403 permission_denied, as before.
func (h *Handlers) modelNotServed(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) *models.APIError {
	if apiErr := keyModelRestriction(rc, model); apiErr != nil {
		return apiErr
	}
	if h.futureAGIModelWithCreditsOff(rc, orgCfg, model) {
		return errFutureAGICreditsOff(model)
	}
	if h.unknownModel(orgCfg, model) {
		return errModelNotFound(model, h.modelSuggestions(rc, orgCfg, model))
	}
	return errModelNotAvailable(model)
}

// keyModelRestriction is the 403 model_not_permitted for a key whose
// allowed_models (recorded before routing, recordKeyACL) leave model out,
// with eligibility rule 1's message; nil when the key has no model list or
// its list names model.
func keyModelRestriction(rc *models.RequestContext, model string) *models.APIError {
	allowed := rc.Metadata[metaKeyAllowedModels]
	if allowed == "" {
		return nil
	}
	code, reason := eligibility.CheckKeyACL(&eligibility.Input{Model: model, Key: eligibility.KeyACL{AllowedModels: allowed}})
	if code == "" {
		return nil
	}
	return (&Verdict{Code: code, Message: reason}).apiError()
}

// futureAGIModelWithCreditsOff reports whether model is one a managed
// provider would serve on Future AGI credits for rc's endpoint, as
// resolveManaged resolves it, while orgCfg has Future AGI credits off.
func (h *Handlers) futureAGIModelWithCreditsOff(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) bool {
	if orgCfg.ManagedModelsEnabled() || !managedEndpoint(rc) || h.registry == nil {
		return false
	}
	p, upstream, ok := h.registry.ResolveManaged(model, h.catalogHas)
	if !ok {
		if id, _ := h.managedCatalogID(model); id != "" {
			p, upstream, ok = h.registry.ResolveManaged(id, h.catalogHas)
		}
	}
	return ok && h.managedServes(p.ID(), upstream)
}

// unknownModel reports whether nothing knows model by name: no provider of
// the gateway's own lists it or is named by its prefix (ServesByName), no
// managed catalogue lists it, at any price, or a name it stands for
// (catalogueLists), and the org neither maps it (model_map) nor has a
// provider that lists it. A model one of these knows is refused for another
// reason (a catalogue model Future AGI credits cannot bill, say), never as
// not found.
func (h *Handlers) unknownModel(orgCfg *tenant.OrgConfig, model string) bool {
	if h.registry != nil {
		if h.registry.ServesByName(model) {
			return false
		}
		if _, _, ok := h.registry.ResolveManaged(model, h.catalogHas); ok {
			return false
		}
	}
	if id, candidates := h.managedCatalogID(model); id != "" || len(candidates) > 0 {
		return false
	}
	if h.catalogueLists(model) {
		return false
	}
	if orgCfg != nil {
		if _, mapped := orgCfg.ModelMap[model]; mapped {
			return false
		}
	}
	for _, id := range orgProviderModels(orgCfg) {
		if strings.EqualFold(id, model) {
			return false
		}
	}
	return true
}

// catalogueLists reports whether a managed provider's catalogue lists model
// at all, priced or not: as an id, as the serving identity it names
// (catalog.ManagedCanonical), or, for a bare name, as the model part of a
// listed id.
func (h *Handlers) catalogueLists(model string) bool {
	cat := h.catalog.Load()
	if cat == nil || h.registry == nil {
		return false
	}
	bare := !strings.Contains(model, "/")
	for _, snap := range cat.Snapshots() {
		if !h.registry.IsManaged(snap.ProviderID) {
			continue
		}
		if _, ok := snap.Models[model]; ok {
			return true
		}
		if canon := catalog.ManagedCanonical(snap.ProviderID, model); canon != "" {
			if _, ok := snap.Models[canon]; ok {
				return true
			}
		}
		if bare {
			for id := range snap.Models {
				if strings.EqualFold(modelPart(id), model) {
					return true
				}
			}
		}
	}
	return false
}

// orgProviderModels lists the models the org's enabled providers with
// credentials list.
func orgProviderModels(orgCfg *tenant.OrgConfig) []string {
	if orgCfg == nil {
		return nil
	}
	var out []string
	for _, pc := range orgCfg.Providers {
		if pc != nil && pc.Enabled && pc.HasCredentials() {
			out = append(out, pc.Models...)
		}
	}
	return out
}

// modelSuggestions is up to maxModelSuggestions ids close to model that the
// key could name: the org's providers' models and the managed catalogues'
// ids, nearest first, limited to the key's allowed_models when it has them.
// A bare name is matched against the catalogue ids' model part and offered
// as that part when only one id ends in it, the id otherwise.
func (h *Handlers) modelSuggestions(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) []string {
	want := strings.ToLower(strings.TrimSpace(model))
	if want == "" {
		return nil
	}
	bare := !strings.Contains(want, "/")
	var ids []string
	ids = append(ids, orgProviderModels(orgCfg)...)
	if cat := h.catalog.Load(); cat != nil && h.registry != nil {
		for _, snap := range cat.Snapshots() {
			if !h.registry.IsManaged(snap.ProviderID) {
				continue
			}
			for id := range snap.Models {
				if cat.Has(snap.ProviderID, id) {
					ids = append(ids, id)
				}
			}
		}
	}
	// How many ids end in each bare name, to offer a bare name only when it
	// stands for one id.
	endings := make(map[string]int)
	for _, id := range ids {
		endings[strings.ToLower(modelPart(id))]++
	}
	allowed := rc.Metadata[metaKeyAllowedModels]
	limit := max(2, len(want)/3)
	best := make(map[string]int)
	for _, id := range ids {
		offer, against := id, strings.ToLower(id)
		if bare {
			part := modelPart(id)
			against = strings.ToLower(part)
			if part != id && endings[against] == 1 {
				offer = part
			}
		}
		if strings.EqualFold(offer, model) {
			continue
		}
		if allowed != "" && !eligibility.CSVContains(allowed, offer) && !eligibility.CSVContains(allowed, id) {
			continue
		}
		d := editDistance(want, against, limit)
		if d > limit {
			continue
		}
		if prev, seen := best[offer]; !seen || d < prev {
			best[offer] = d
		}
	}
	out := make([]string, 0, len(best))
	for offer := range best {
		out = append(out, offer)
	}
	sort.Slice(out, func(i, j int) bool {
		if best[out[i]] != best[out[j]] {
			return best[out[i]] < best[out[j]]
		}
		return out[i] < out[j]
	})
	if len(out) > maxModelSuggestions {
		out = out[:maxModelSuggestions]
	}
	return out
}

// modelPart is id without its "<vendor>/" prefix.
func modelPart(id string) string {
	if i := strings.IndexByte(id, '/'); i >= 0 {
		return id[i+1:]
	}
	return id
}

// editDistance is the Levenshtein distance between a and b, by byte, or any
// value above limit once it is certain to exceed limit.
func editDistance(a, b string, limit int) int {
	if d := len(a) - len(b); d > limit || -d > limit {
		return limit + 1
	}
	prev := make([]int, len(b)+1)
	cur := make([]int, len(b)+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= len(a); i++ {
		cur[0] = i
		rowMin := cur[0]
		for j := 1; j <= len(b); j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			cur[j] = min(prev[j]+1, cur[j-1]+1, prev[j-1]+cost)
			rowMin = min(rowMin, cur[j])
		}
		if rowMin > limit {
			return limit + 1
		}
		prev, cur = cur, prev
	}
	return prev[len(b)]
}

// errModelNotFound is the 404 for a model nothing serves, offering
// suggestions (metadata.suggestions too).
func errModelNotFound(model string, suggestions []string) *models.APIError {
	var b strings.Builder
	fmt.Fprintf(&b, "model %q was not found: no provider your organization can use serves it.", model)
	if len(suggestions) > 0 {
		quoted := make([]string, len(suggestions))
		for i, s := range suggestions {
			quoted[i] = strconv.Quote(s)
		}
		b.WriteString(" Did you mean ")
		switch len(quoted) {
		case 1:
			b.WriteString(quoted[0])
		default:
			b.WriteString(strings.Join(quoted[:len(quoted)-1], ", ") + " or " + quoted[len(quoted)-1])
		}
		b.WriteString("?")
	}
	b.WriteString(" GET /v1/models lists the models this key can call.")
	apiErr := models.ErrNotFound("model_not_found", b.String())
	if len(suggestions) > 0 {
		apiErr.Metadata = map[string]any{"suggestions": suggestions}
	}
	return apiErr
}

// errFutureAGICreditsOff is the 403 for a Future AGI model called while the
// org's Future AGI credits are off.
func errFutureAGICreditsOff(model string) *models.APIError {
	return &models.APIError{
		Status: http.StatusForbidden,
		Type:   models.ErrTypePermission,
		Code:   codeFutureAGICreditsOff,
		Message: fmt.Sprintf("model %q is a Future AGI model, and Future AGI credits are off for your organization. "+
			"Turn on Future AGI credits on the Gateway Models page, or add your own provider key on the Gateway Providers page.", model),
	}
}
