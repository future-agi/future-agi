package catalog

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Catalog sources: where a provider's catalog comes from (config.yaml
// providers.<id>.catalog_source). Every endpoint carries the one it came from.
const (
	SourceOpenRouter       = "openrouter"
	SourceOpenAICompatible = "openai_compatible"
	SourcePriceCard        = "price_card"
)

// Data-collection policies of an endpoint.
const (
	DataCollectionAllow   = "allow"
	DataCollectionDeny    = "deny"
	DataCollectionUnknown = "unknown"
)

// Prices is what a model or endpoint charges: USD per token for Prompt,
// Completion, CacheRead, CacheWrite and Reasoning, and USD per unit for
// Request, Image (an input image) and WebSearch. Zero means no charge.
type Prices struct {
	Prompt     float64 `json:"prompt"`
	Completion float64 `json:"completion"`
	CacheRead  float64 `json:"cache_read,omitempty"`
	CacheWrite float64 `json:"cache_write,omitempty"`
	Request    float64 `json:"request,omitempty"`
	Image      float64 `json:"image,omitempty"`
	WebSearch  float64 `json:"web_search,omitempty"`
	Reasoning  float64 `json:"reasoning,omitempty"`
}

// PriceTier is the price card that replaces the base one for a request whose
// prompt has at least MinPromptTokens tokens (OpenRouter's pricing overrides,
// for example long-context pricing).
type PriceTier struct {
	MinPromptTokens int    `json:"min_prompt_tokens"`
	Prices          Prices `json:"prices"`
}

// Endpoint is one way to reach a model: one upstream provider (and variant)
// behind a catalog source. ID is "<source>:<upstream_provider>[/<variant>]:<model>",
// where the middle part is the upstream's tag.
type Endpoint struct {
	ID string `json:"id"`
	// Name is the upstream's display name, "<Provider> | <model>".
	Name string `json:"name,omitempty"`
	// Provider is the gateway provider (config.yaml providers.<id>) the
	// endpoint is reached through.
	Provider string `json:"provider"`
	// Source is the catalog source: openrouter, openai_compatible or price_card.
	Source string `json:"source"`
	// UpstreamProvider is the provider's slug in the source's naming, which
	// OpenRouter's provider.order and provider.only match on.
	UpstreamProvider string `json:"upstream_provider"`
	// ProviderName is the upstream provider's display name.
	ProviderName string `json:"provider_name,omitempty"`
	// UpstreamFeeBps is what the source adds on top of Prices:
	// providers.<id>.upstream_fee_bps, 550 for OpenRouter by default.
	UpstreamFeeBps int `json:"upstream_fee_bps"`
	// Tag is the upstream's endpoint tag, "<upstream_provider>[/<variant>...]".
	Tag          string   `json:"tag,omitempty"`
	Quantization string   `json:"quantization,omitempty"`
	Regions      []string `json:"regions,omitempty"`

	ContextLength int `json:"context_length,omitempty"`
	MaxCompletion int `json:"max_completion,omitempty"`
	MaxPrompt     int `json:"max_prompt,omitempty"`

	Prices     Prices      `json:"prices"`
	PriceTiers []PriceTier `json:"price_tiers,omitempty"`
	// DynamicPrice marks an endpoint whose price only the upstream's reported
	// cost knows (a negative listed price).
	DynamicPrice bool `json:"dynamic_price,omitempty"`

	SupportedParams []string `json:"supported_params,omitempty"`
	// Status is the upstream's endpoint status: 0 is serving, negative is
	// degraded or down.
	Status        int      `json:"status"`
	UptimeLast30m *float64 `json:"uptime_last_30m,omitempty"`
	LatencyP50Ms  *float64 `json:"latency_p50_ms,omitempty"`
	LatencyP90Ms  *float64 `json:"latency_p90_ms,omitempty"`
	ThroughputP50 *float64 `json:"throughput_p50,omitempty"`

	// ZDR is true for a zero-data-retention endpoint, false for one the
	// source's ZDR list leaves out, and nil when no list has been read.
	// Consumers treat nil as not ZDR.
	ZDR *bool `json:"zdr,omitempty"`
	// DataCollection is allow, deny or unknown. A ZDR endpoint is deny.
	DataCollection          string `json:"data_collection"`
	SupportsImplicitCaching bool   `json:"supports_implicit_caching,omitempty"`
}

// EndpointID names an endpoint: "<source>:<tag>:<model>".
func EndpointID(source, tag, model string) string {
	return source + ":" + tag + ":" + model
}

// defaultUpstreamFeeBps is the fee a source charges when config names none,
// read from the config's own rule so the two cannot drift.
func defaultUpstreamFeeBps(source string) int {
	return (&config.ProviderConfig{CatalogSource: source}).EffectiveUpstreamFeeBps()
}

// upstreamSlug is the provider part of a tag ("google-vertex/us" →
// "google-vertex"), or the display name as a slug when there is no tag.
func upstreamSlug(tag, providerName string) string {
	if tag != "" {
		if i := strings.IndexByte(tag, '/'); i > 0 {
			return tag[:i]
		}
		return tag
	}
	return strings.ReplaceAll(strings.ToLower(strings.TrimSpace(providerName)), " ", "-")
}

// regionPattern matches the region forms upstream tags use: a bare area
// ("us", "eu", "global"), a cloud region ("us-east-1", "eu-west-1",
// "us-east5", "us-central1") and an Azure-style one ("swedencentral",
// "eastus").
var regionPattern = regexp.MustCompile(`^(?:` +
	`us|eu|europe|global|asia|apac|emea|uk|jp|au|ca|in|sg|kr|br|latam` +
	`|(?:us|eu|europe|asia|ap|me|sa|af|ca|uk|au|jp|in|northamerica|southamerica|australia)-[a-z]+-?\d*` +
	`|[a-z]*(?:central|east|west|north|south)[a-z]*\d*` +
	`)$`)

// notRegions are tag variants that name a quantization or a service tier,
// some of which the Azure-style pattern would otherwise take for a region.
var notRegions = map[string]bool{
	"fp4": true, "fp8": true, "fp16": true, "fp32": true, "bf16": true, "int4": true, "int8": true,
	"nvfp4": true, "mxfp4": true, "zdr": true, "flex": true, "fast": true, "priority": true,
	"turbo": true, "highspeed": true, "ultrafast": true, "prime": true, "default": true,
	"batch": true, "free": true, "nitro": true, "floor": true, "thinking": true,
}

// RegionsFromTag returns the regions an upstream tag names: every variant
// segment after the provider that reads as a region ("google-vertex/us" →
// ["us"], "fp8/us-east-1" → ["us-east-1"], "deepinfra/fp8" → none).
func RegionsFromTag(tag string) []string {
	parts := strings.Split(strings.ToLower(tag), "/")
	var out []string
	for _, p := range parts[1:] {
		if p != "" && !notRegions[p] && regionPattern.MatchString(p) {
			out = append(out, p)
		}
	}
	return out
}

// priced reports whether the endpoint has a fixed price card.
func (e *Endpoint) priced() bool { return !e.DynamicPrice }

// EndpointsDocument is a model and its endpoints in the catalog-endpoints-v1
// shape: the body of the admin GET /-/catalog/endpoints,
// and of the developer endpoints API, which leaves the upstream fee out.
type EndpointsDocument struct {
	Data EndpointsModel `json:"data"`
}

// EndpointsModel is the model part of an EndpointsDocument.
type EndpointsModel struct {
	ID           string           `json:"id"`
	Name         string           `json:"name"`
	Architecture ArchitectureView `json:"architecture"`
	Endpoints    []EndpointView   `json:"endpoints"`
}

// ArchitectureView is a model's modalities and tokenizer.
type ArchitectureView struct {
	Modality         string   `json:"modality,omitempty"`
	InputModalities  []string `json:"input_modalities"`
	OutputModalities []string `json:"output_modalities"`
	Tokenizer        string   `json:"tokenizer,omitempty"`
	InstructType     *string  `json:"instruct_type"`
}

// EndpointView is one endpoint in an EndpointsDocument. Prices are decimal
// strings in USD per token or per unit.
type EndpointView struct {
	Name                string          `json:"name"`
	ProviderName        string          `json:"provider_name"`
	Tag                 string          `json:"tag"`
	Quantization        *string         `json:"quantization"`
	Region              *string         `json:"region"`
	ContextLength       int             `json:"context_length"`
	MaxCompletionTokens *int            `json:"max_completion_tokens"`
	Pricing             PricingView     `json:"pricing"`
	SupportedParameters []string        `json:"supported_parameters"`
	Status              int             `json:"status"`
	UptimeLast30m       *float64        `json:"uptime_last_30m"`
	LatencyLast30m      *LatencyView    `json:"latency_last_30m"`
	ThroughputLast30m   *ThroughputView `json:"throughput_last_30m"`
	ZDR                 *bool           `json:"zdr"`
	DataCollection      string          `json:"data_collection"`
	HealthSource        string          `json:"health_source"`
	Source              string          `json:"source"`
	UpstreamFeeBps      *int            `json:"upstream_fee_bps,omitempty"`
}

// PricingView is a price card as decimal strings, in OpenRouter's names.
type PricingView struct {
	Prompt            string `json:"prompt"`
	Completion        string `json:"completion"`
	InputCacheRead    string `json:"input_cache_read"`
	InputCacheWrite   string `json:"input_cache_write"`
	Request           string `json:"request"`
	Image             string `json:"image"`
	WebSearch         string `json:"web_search"`
	InternalReasoning string `json:"internal_reasoning"`
}

// LatencyView is an endpoint's latency percentiles over the last 30 minutes.
type LatencyView struct {
	P50 *float64 `json:"p50"`
	P90 *float64 `json:"p90"`
}

// ThroughputView is an endpoint's median throughput over the last 30 minutes.
type ThroughputView struct {
	P50 *float64 `json:"p50"`
}

// healthSourceUpstream marks health figures the upstream reported, not ones
// the gateway measured.
const healthSourceUpstream = "upstream"

// EndpointsDocument describes m and its endpoints; admin adds each
// endpoint's upstream fee, which customers never see. An
// endpoint with a dynamic price has no price to show and is left out.
func (m *Model) EndpointsDocument(admin bool) EndpointsDocument {
	arch := ArchitectureView{
		Modality:         m.Modality,
		InputModalities:  nonNil(m.InputModalities),
		OutputModalities: nonNil(m.OutputModalities),
		Tokenizer:        m.Tokenizer,
		InstructType:     optional(m.InstructType),
	}
	eps := make([]EndpointView, 0, len(m.Endpoints))
	for i := range m.Endpoints {
		e := &m.Endpoints[i]
		if !e.priced() {
			continue
		}
		v := EndpointView{
			Name: e.Name, ProviderName: e.ProviderName, Tag: e.Tag,
			Quantization: optional(e.Quantization), ContextLength: e.ContextLength,
			Pricing: pricingView(e.Prices), SupportedParameters: nonNil(e.SupportedParams),
			Status: e.Status, UptimeLast30m: e.UptimeLast30m, ZDR: e.ZDR,
			DataCollection: e.DataCollection, HealthSource: healthSourceUpstream, Source: e.Source,
		}
		if len(e.Regions) > 0 {
			v.Region = &e.Regions[0]
		}
		if e.MaxCompletion > 0 {
			v.MaxCompletionTokens = &e.MaxCompletion
		}
		if e.LatencyP50Ms != nil || e.LatencyP90Ms != nil {
			v.LatencyLast30m = &LatencyView{P50: e.LatencyP50Ms, P90: e.LatencyP90Ms}
		}
		if e.ThroughputP50 != nil {
			v.ThroughputLast30m = &ThroughputView{P50: e.ThroughputP50}
		}
		if admin {
			fee := e.UpstreamFeeBps
			v.UpstreamFeeBps = &fee
		}
		eps = append(eps, v)
	}
	return EndpointsDocument{Data: EndpointsModel{ID: m.ID, Name: m.Name, Architecture: arch, Endpoints: eps}}
}

func pricingView(p Prices) PricingView {
	return PricingView{
		Prompt: formatPrice(p.Prompt), Completion: formatPrice(p.Completion),
		InputCacheRead: formatPrice(p.CacheRead), InputCacheWrite: formatPrice(p.CacheWrite),
		Request: formatPrice(p.Request), Image: formatPrice(p.Image),
		WebSearch: formatPrice(p.WebSearch), InternalReasoning: formatPrice(p.Reasoning),
	}
}

// formatPrice writes a price as the plain decimal string catalog APIs use
// ("0.0000025"), never in exponent form.
func formatPrice(f float64) string {
	return strconv.FormatFloat(f, 'f', -1, 64)
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func optional(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
