package catalog

import (
	_ "embed"
	"encoding/json"
	"log/slog"
	"regexp"
	"sort"
	"strings"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// Canonical ids. Sources name one model differently: OpenRouter lists
// "anthropic/claude-haiku-4.5", Anthropic itself "claude-haiku-4-5-20251001",
// Bedrock "us.anthropic.claude-haiku-4-5-20251001-v1:0". The canonical id is
// one lowercased "vendor/model" in OpenRouter's naming (its undated id, with
// no ":variant"), so a model offered by N sources is one model with N
// endpoints, and the catalog is counted in models rather than listings.

//go:embed aliases.json
var aliasesJSON []byte

// aliasTable maps a lowercased model id, as some source or SDK spells it, to
// its canonical id: the names no rule below can derive (retired models,
// host-specific suffixes and spellings). Read once from aliases.json.
var aliasTable = sync.OnceValue(func() map[string]string {
	var doc struct {
		Aliases map[string]string `json:"aliases"`
	}
	if err := json.Unmarshal(aliasesJSON, &doc); err != nil {
		slog.Error("catalog: aliases.json unreadable; no aliases apply", "error", err)
		return map[string]string{}
	}
	out := make(map[string]string, len(doc.Aliases))
	for from, to := range doc.Aliases {
		out[strings.ToLower(from)] = strings.ToLower(to)
	}
	return out
})

// openRouterIDs are the ids of the bundled OpenRouter snapshot (without
// variants), the naming canonical ids are spelled in, and its dated
// canonical slugs, each naming its id.
type openRouterIDs struct {
	ids   map[string]bool
	slugs map[string]string
}

var bundledOpenRouterIDs = sync.OnceValue(func() openRouterIDs {
	out := openRouterIDs{ids: map[string]bool{}, slugs: map[string]string{}}
	var doc struct {
		Data []struct {
			ID            string `json:"id"`
			CanonicalSlug string `json:"canonical_slug"`
		} `json:"data"`
	}
	if err := json.Unmarshal(openRouterSnapshot, &doc); err != nil {
		slog.Error("catalog: bundled openrouter snapshot unreadable; canonical ids use rules only", "error", err)
		return out
	}
	for _, d := range doc.Data {
		id := stripVariant(strings.ToLower(d.ID))
		if id == "" {
			continue
		}
		out.ids[id] = true
		if slug := strings.ToLower(d.CanonicalSlug); slug != "" && slug != id {
			out.slugs[slug] = id
		}
	}
	return out
})

// openRouterVariants are OpenRouter's ":variant" suffixes, the same model at
// another price or speed.
var openRouterVariants = map[string]bool{
	"free": true, "batch": true, "beta": true, "thinking": true, "nitro": true, "floor": true,
	"extended": true, "online": true, "exacto": true,
}

var bedrockRevision = regexp.MustCompile(`:\d+$`)

// stripVariant drops an OpenRouter variant (":free") or a Bedrock revision
// (":0"). Any other colon suffix (an Ollama size tag, "llama3:8b") names a
// different model and stays.
func stripVariant(id string) string {
	if i := strings.LastIndexByte(id, ':'); i > 0 && (openRouterVariants[id[i+1:]] || bedrockRevision.MatchString(id)) {
		return id[:i]
	}
	return id
}

// hostPrefixes are the prefixes model databases and hosts put before a
// vendor's id (litellm's "together_ai/...", Fireworks' "accounts/fireworks/
// models/..."). They name where a model runs, not whose it is.
var hostPrefixes = []string{
	"openrouter/", "together_ai/", "groq/", "fireworks_ai/", "accounts/fireworks/models/", "deepinfra/",
	"huggingface/", "bedrock/", "bedrock_converse/", "vertex_ai/", "azure/", "azure_ai/", "nebius/", "novita/",
}

// vendorAliases map a vendor as a model database, a host or Hugging Face
// spells it to OpenRouter's vendor slug.
var vendorAliases = map[string]string{
	"gemini": "google", "vertex_ai": "google", "vertex_ai-language-models": "google", "google-ai-studio": "google",
	"xai": "x-ai", "mistral": "mistralai", "deepseek-ai": "deepseek", "zai-org": "z-ai", "zhipuai": "z-ai",
	"cohere_chat": "cohere", "moonshot": "moonshotai", "meta": "meta", "meta-llama": "meta-llama",
	"openai": "openai", "anthropic": "anthropic", "google": "google", "x-ai": "x-ai", "mistralai": "mistralai",
	"deepseek": "deepseek", "cohere": "cohere", "qwen": "qwen", "moonshotai": "moonshotai", "z-ai": "z-ai",
	"perplexity": "perplexity", "amazon": "amazon", "microsoft": "microsoft", "nvidia": "nvidia",
}

// namePrefixVendors give the vendor of a bare model id by its first word,
// when the model database does not know the id.
var namePrefixVendors = []struct{ prefix, vendor string }{
	{"claude", "anthropic"}, {"gpt-", "openai"}, {"chatgpt", "openai"}, {"text-embedding-", "openai"},
	{"dall-e", "openai"}, {"whisper", "openai"}, {"tts-", "openai"}, {"omni-moderation", "openai"},
	{"gemini", "google"}, {"gemma", "google"}, {"imagen", "google"}, {"veo-", "google"},
	{"grok", "x-ai"}, {"mistral", "mistralai"}, {"mixtral", "mistralai"}, {"codestral", "mistralai"},
	{"magistral", "mistralai"}, {"ministral", "mistralai"}, {"pixtral", "mistralai"}, {"devstral", "mistralai"},
	{"deepseek", "deepseek"}, {"command", "cohere"}, {"llama", "meta-llama"}, {"qwen", "qwen"}, {"qwq", "qwen"},
	{"kimi", "moonshotai"}, {"glm", "z-ai"}, {"sonar", "perplexity"}, {"phi-", "microsoft"},
}

var openAIReasoningID = regexp.MustCompile(`^o\d+(-|$)`)

// bedrockID is Bedrock's "<vendor>.<model>" form, after any region prefix.
var bedrockID = regexp.MustCompile(`^(?:(?:us|eu|apac|global|au|jp|ca|us-gov)\.)?(anthropic|amazon|meta|mistral|cohere|ai21|deepseek|qwen|openai|writer)\.(.+?)(?:-v\d+)?$`)

// Canonical returns the canonical id of model as providerID lists it: a
// lowercased "vendor/model" in OpenRouter's naming. In order:
//   - the id lowercased and without a variant; an id of the bundled
//     OpenRouter list is canonical as it is, with or without a leading
//     "<providerID>/";
//   - aliases.json;
//   - host prefixes and Bedrock's "<region>.<vendor>.<model>-v1" form are
//     unwrapped, and the vendor named or inferred (the model database's
//     provider, else the id's first word, else providerID itself, so an
//     unknown model never merges with another vendor's);
//   - the first of that id, its version written with a dot or a dash
//     ("4-5" or "4.5"), and each without a date suffix, that the bundled
//     OpenRouter list names, as an id or as a dated canonical_slug;
//   - else that id as it is.
//
// It reads only bundled data, so the same build always gives the same id.
func Canonical(providerID, model string) string {
	id := stripVariant(strings.ToLower(strings.TrimSpace(model)))
	if canon, ok := knownCanonical(id); ok {
		return canon
	}
	if p := strings.ToLower(providerID) + "/"; strings.HasPrefix(id, p) && len(id) > len(p) {
		id = id[len(p):]
		if canon, ok := knownCanonical(id); ok {
			return canon
		}
	}
	aliases := aliasTable()
	if to, ok := aliases[id]; ok {
		return to
	}
	vendor, rest := vendorAndModel(providerID, id)
	if to, ok := aliases[rest]; ok {
		return to
	}
	full := vendor + "/" + rest
	if to, ok := aliases[full]; ok {
		return to
	}
	for _, candidate := range spellings(full) {
		if canon, ok := knownCanonical(candidate); ok {
			return canon
		}
	}
	return full
}

// knownCanonical is the bundled OpenRouter id that id names, as itself or as
// a dated canonical slug.
func knownCanonical(id string) (string, bool) {
	known := bundledOpenRouterIDs()
	if known.ids[id] {
		return id, true
	}
	to, ok := known.slugs[id]
	return to, ok
}

// vendorAndModel splits id, with host prefixes and Bedrock's form unwrapped,
// into a vendor slug and the vendor's model id.
func vendorAndModel(providerID, id string) (vendor, model string) {
	for stripped := true; stripped; {
		stripped = false
		for _, p := range hostPrefixes {
			if strings.HasPrefix(id, p) && len(id) > len(p) {
				id, stripped = id[len(p):], true
			}
		}
	}
	if m := bedrockID.FindStringSubmatch(id); m != nil {
		return vendorSlug(m[1]), m[2]
	}
	if i := strings.IndexByte(id, '/'); i > 0 {
		return vendorSlug(id[:i]), id[i+1:]
	}
	return inferVendor(providerID, id), id
}

// vendorSlug is OpenRouter's slug for vendor.
func vendorSlug(vendor string) string {
	if v, ok := vendorAliases[vendor]; ok {
		return v
	}
	return vendor
}

// ProviderVendor is the vendor whose models a provider id names, when the id
// is a vendor ("anthropic", "gemini", "xai"); ok is false for a host or an
// aggregator ("groq", "openrouter", "my-proxy").
func ProviderVendor(providerID string) (string, bool) {
	v, ok := vendorAliases[strings.ToLower(providerID)]
	return v, ok
}

// inferVendor is the vendor of a bare model id: the model database's
// provider when it is a vendor, else the id's first word, else providerID.
func inferVendor(providerID, model string) string {
	if info, ok := modeldb.BundledModels[model]; ok {
		if v, ok := vendorAliases[info.Provider]; ok {
			return v
		}
	}
	if openAIReasoningID.MatchString(model) {
		return "openai"
	}
	for _, p := range namePrefixVendors {
		if strings.HasPrefix(model, p.prefix) {
			return p.vendor
		}
	}
	return strings.ToLower(providerID)
}

var (
	datedSuffix  = regexp.MustCompile(`-(?:\d{8}|\d{4}-\d{2}-\d{2})$`)
	dottedNumber = regexp.MustCompile(`(\d)\.(\d)`)
)

// spellings are the forms a vendor/model id may take in OpenRouter's list:
// as it is, its version with a dot ("claude-haiku-4-5" → "4.5") or a dash
// ("mistral-medium-3.5" → "3-5"), and each of those without a date suffix.
func spellings(id string) []string {
	forms := []string{id, dotVersions(id), dottedNumber.ReplaceAllString(id, "$1-$2")}
	out := make([]string, 0, 2*len(forms))
	for _, f := range forms {
		out = append(out, f)
	}
	for _, f := range forms {
		if undated := datedSuffix.ReplaceAllString(f, ""); undated != f {
			out = append(out, undated)
		}
	}
	return out
}

// dotVersions joins each pair of single-digit words of the model part with
// a dot: "anthropic/claude-3-5-sonnet-20241022" → ".../claude-3.5-sonnet-20241022".
func dotVersions(id string) string {
	slash := strings.IndexByte(id, '/')
	words := strings.Split(id[slash+1:], "-")
	out := make([]string, 0, len(words))
	for i := 0; i < len(words); i++ {
		if i+1 < len(words) && singleDigit(words[i]) && singleDigit(words[i+1]) {
			out = append(out, words[i]+"."+words[i+1])
			i++
			continue
		}
		out = append(out, words[i])
	}
	return id[:slash+1] + strings.Join(out, "-")
}

func singleDigit(s string) bool { return len(s) == 1 && s[0] >= '0' && s[0] <= '9' }

// canonicalOf is m's canonical id as providerID lists it: the one its price
// card names, else the derived one. A canonical id from any other source is
// re-derived, so a persisted model follows this build's rules.
func canonicalOf(providerID, catalogSource string, m *Model) string {
	if catalogSource == SourcePriceCard && m.Canonical != "" {
		return strings.ToLower(m.Canonical)
	}
	return Canonical(providerID, m.ID)
}

// --- the merged view -----------------------------------------------------

// CanonicalModel is one model as every managed source of a catalog version
// offers it.
type CanonicalModel struct {
	ID string `json:"id"`
	// Name and ContextLength are the first listing's (by provider id) that
	// has one.
	Name          string `json:"name,omitempty"`
	ContextLength int    `json:"context_length,omitempty"`
	// Listings are the model as each provider lists it, by provider id.
	Listings []Listing `json:"listings"`
	// Endpoints are every listing's ways to reach the model, each carrying
	// its provider, source, upstream provider and upstream fee: the set the
	// router ranks by landed cost. A listing whose endpoints were never read is one
	// endpoint priced from the listing's own card, whose UpstreamProvider
	// and Tag are empty (the source routes it). An unpriced or stale-priced
	// listing offers none.
	Endpoints []Endpoint `json:"endpoints"`
}

// Listing is one provider's entry for a canonical model.
type Listing struct {
	ProviderID string `json:"provider"`
	// Source is the provider's catalog source kind.
	Source string `json:"source"`
	// Model is the provider's entry, its own id the one sent upstream.
	Model *Model `json:"model"`
}

type mergedView struct {
	models []*CanonicalModel // by id
	byID   map[string]*CanonicalModel
}

// CanonicalModels returns every canonical model of the catalog version, by
// id. It is built once per version; callers must not modify it.
func (c *Catalog) CanonicalModels() []*CanonicalModel {
	if c == nil {
		return nil
	}
	return c.view().models
}

// CanonicalModel returns the canonical model id.
func (c *Catalog) CanonicalModel(id string) (*CanonicalModel, bool) {
	if c == nil {
		return nil, false
	}
	cm, ok := c.view().byID[id]
	return cm, ok
}

func (c *Catalog) view() *mergedView {
	c.mergeOnce.Do(func() { c.merged = c.merge() })
	return c.merged
}

// merge groups every provider's models by canonical id.
func (c *Catalog) merge() *mergedView {
	v := &mergedView{byID: map[string]*CanonicalModel{}}
	for _, snap := range c.Snapshots() {
		for _, m := range snap.Sorted() {
			id := m.Canonical
			if id == "" {
				id = Canonical(snap.ProviderID, m.ID)
			}
			cm := v.byID[id]
			if cm == nil {
				cm = &CanonicalModel{ID: id, Listings: []Listing{}, Endpoints: []Endpoint{}}
				v.byID[id] = cm
				v.models = append(v.models, cm)
			}
			if cm.Name == "" {
				cm.Name = m.Name
			}
			if cm.ContextLength == 0 {
				cm.ContextLength = m.ContextLength
			}
			cm.Listings = append(cm.Listings, Listing{ProviderID: snap.ProviderID, Source: snap.CatalogSource, Model: m})
			cm.Endpoints = append(cm.Endpoints, listingEndpoints(snap, m)...)
		}
	}
	sort.Slice(v.models, func(i, j int) bool { return v.models[i].ID < v.models[j].ID })
	return v
}

// listingEndpoints are what one listing offers: its endpoints, or, when none
// were read and it has a fixed price, one endpoint priced from its own card.
func listingEndpoints(snap *Snapshot, m *Model) []Endpoint {
	if !m.Sellable() {
		return nil
	}
	if len(m.Endpoints) > 0 {
		return m.Endpoints
	}
	if !m.Priced() {
		return nil
	}
	return []Endpoint{{
		ID:              EndpointID(snap.CatalogSource, "", m.ID),
		Name:            m.Name,
		Provider:        snap.ProviderID,
		Source:          snap.CatalogSource,
		UpstreamFeeBps:  snap.UpstreamFeeBps,
		ContextLength:   m.ContextLength,
		MaxCompletion:   m.MaxOutputTokens,
		Prices:          m.Prices,
		PriceTiers:      m.PriceTiers,
		SupportedParams: m.SupportedParameters,
		DataCollection:  DataCollectionUnknown,
	}}
}
