// Package pricesfeed projects already-selected global fixed-price offers.
// It has no store, credentials, HTTP client, tenant lookup or clock. Selection
// belongs to serving's exact-offer helpers; this package never refreshes data.
package pricesfeed

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const SchemaVersion = "model-prices-v1"

// Input contains only public fields from one eligible exact endpoint. A blank
// endpoint ID means that serving uses the listing's own supported price card.
// Card's internal snapshot/source fields are never serialized or hashed.
type Input struct {
	ProviderID, CatalogSource, ModelID, EndpointID, UpstreamProvider string
	EndpointStatus                                                   int
	ExpiresAt                                                        string
	Card                                                             models.PriceCard
}

type Dimension struct {
	Name                 string  `json:"name"`
	Unit                 string  `json:"unit"`
	Rule                 string  `json:"rule"`
	CardRateUSD          *string `json:"card_rate_usd"`
	ProviderRateUSD      *string `json:"provider_rate_usd"`
	NominalOneKeyRateUSD *string `json:"nominal_one_key_rate_usd"`
}

type Offer struct {
	ProviderID       string      `json:"provider_id"`
	CatalogSource    string      `json:"catalog_source"`
	ModelID          string      `json:"model_id"`
	EndpointID       string      `json:"endpoint_id"`
	UpstreamProvider string      `json:"upstream_provider"`
	EndpointStatus   int         `json:"endpoint_status"`
	ExpiresAt        string      `json:"expires_at,omitempty"`
	ExplicitFree     bool        `json:"explicit_free"`
	Dimensions       []Dimension `json:"dimensions"`
}

type Source struct {
	ProviderID    string `json:"provider_id"`
	CatalogSource string `json:"catalog_source"`
	SnapshotID    string `json:"snapshot_id"`
}

type Coverage struct {
	ChatCompletions bool   `json:"chat_completions"`
	Messages        bool   `json:"messages"`
	Eligibility     string `json:"eligibility"`
	Excluded        string `json:"excluded"`
}

type Rules struct {
	RateMeaning        string `json:"rate_meaning"`
	PromptTotals       string `json:"prompt_totals"`
	CacheRates         string `json:"cache_rates"`
	Reasoning          string `json:"reasoning"`
	ReportedCost       string `json:"reported_cost"`
	SettlementRounding string `json:"settlement_rounding"`
	Admission          string `json:"admission"`
	BYOK               string `json:"byok"`
	SourceSnapshot     string `json:"source_snapshot"`
}

type Document struct {
	SchemaVersion     string   `json:"schema_version"`
	ContentVersion    string   `json:"content_version"`
	Currency          string   `json:"currency"`
	QuoteScope        string   `json:"quote_scope"`
	OneKeyMarkupBps   int      `json:"one_key_markup_bps"`
	BYOKGatewayFeeBps int      `json:"byok_gateway_fee_bps"`
	Coverage          Coverage `json:"coverage"`
	Rules             Rules    `json:"rules"`
	Sources           []Source `json:"sources"`
	Offers            []Offer  `json:"offers"`
}

type Feed struct {
	Document Document
	Body     []byte
	ETag     string
}

// Build gives unchanged semantic input identical bytes and a strong ETag.
// Nominal rates are exact decimal-scaled rate labels, NOT independently
// rounded invoice lines. Existing billing rounds the reconstructed aggregate
// upstream cost per attempt before applying markup and summing attempts.
func Build(inputs []Input, markupBps int, schemaVersion string) (Feed, error) {
	if markupBps < 0 || markupBps > 10000 || schemaVersion == "" {
		return Feed{}, fmt.Errorf("invalid pricing contract")
	}
	d := Document{SchemaVersion: schemaVersion, Currency: "USD", QuoteScope: "fixed_chat_card_only", OneKeyMarkupBps: markupBps,
		Coverage: Coverage{ChatCompletions: true, Messages: true,
			Eligibility: "default_catalog_policy_with_free_opt_in; not_account_entitlement_or_live_provider_qualification",
			Excluded:    "tenant_BYOK_custom_prices,capabilities,tools,embeddings,image,audio,video,OCR,generation,tiered,dynamic"},
		Rules: Rules{
			RateMeaning:        "USD_per_unit; nominal_one_key_rates_are_rates_not_independently_rounded_invoice_lines",
			PromptTotals:       "uncached=prompt-cache_read-cache_write; if negative, prompt is exclusive uncached input",
			CacheRates:         "positive_cache_rate_else_prompt_rate; cache counts replace included prompt tokens",
			Reasoning:          "positive_reasoning_rate replaces clamp(reasoning,0,completion); otherwise included in completion",
			ReportedCost:       "provider_reported_cost_precedes_card_reconstruction_including_reported_zero",
			SettlementRounding: "snap_USD_times_1e6_to_1e-6_micro; half_up_upstream_per_attempt; half_up_markup_on_integer_upstream; sum_billable_attempts",
			Admission:          "ceil_estimated_prompt_plus_output_cap_plus_request_times_markup; admission_hold_not_settled_actual; separate_tool_units_not_covered_by_this_bound",
			BYOK:               "zero_Future_AGI_gateway_fee; upstream_still_payable_to_provider; tenant_specific_BYOK_quotes_not_published",
			SourceSnapshot:     "sha256_of_public_source_projection_excluding_markup; not_fetch_time_or_private_catalogue_snapshot",
		}, Sources: []Source{}, Offers: []Offer{}}
	for _, in := range inputs {
		p := in.Card
		if in.ProviderID == "" || in.ModelID == "" || !knownSource(in.CatalogSource) || !p.Billable() || p.Source == models.PriceSourceOrgCustom ||
			(p.ExplicitFree && (in.CatalogSource != "openrouter" || !(strings.HasSuffix(in.ModelID, ":free") || in.ModelID == "openrouter/free"))) ||
			(p.ExplicitFree && (p.Prompt != 0 || p.Completion != 0 || p.CacheRead != 0 || p.CacheWrite != 0 || p.Reasoning != 0 || p.Request != 0 || p.WebSearch != 0)) {
			return Feed{}, fmt.Errorf("unsupported fixed offer")
		}
		o := Offer{ProviderID: in.ProviderID, CatalogSource: in.CatalogSource, ModelID: in.ModelID, EndpointID: in.EndpointID,
			UpstreamProvider: in.UpstreamProvider, EndpointStatus: in.EndpointStatus, ExpiresAt: in.ExpiresAt, ExplicitFree: p.ExplicitFree}
		o.Dimensions = []Dimension{
			priced("uncached_input", "token", "prompt_minus_cache_unless_exclusive", p.Prompt, p.Prompt, markupBps),
			priced("output", "token", "completion_minus_separately_priced_reasoning", p.Completion, p.Completion, markupBps),
			cache("cache_read", p.CacheRead, p.Prompt, markupBps), cache("cache_write", p.CacheWrite, p.Prompt, markupBps),
			priced("reasoning", "token", "replaces_clamped_completion", p.Reasoning, p.Reasoning, markupBps),
			priced("request", "request", "once_per_billable_attempt", p.Request, p.Request, markupBps),
			priced("web_search", "search_request", "per_reported_search_request", p.WebSearch, p.WebSearch, markupBps),
		}
		if p.Reasoning == 0 {
			o.Dimensions[4].Rule = "included_in_output"
			o.Dimensions[4].ProviderRateUSD = nil
			o.Dimensions[4].NominalOneKeyRateUSD = nil
		}
		// A missing scalar search rate cannot promise a free tool call: native
		// tools may have separate fee contracts which this feed does not read.
		if p.WebSearch == 0 {
			o.Dimensions[6] = Dimension{Name: "web_search", Unit: "search_request", Rule: "not_quoted"}
		}
		d.Offers = append(d.Offers, o)
	}
	sort.Slice(d.Offers, func(i, j int) bool { return lessOffer(d.Offers[i], d.Offers[j]) })
	for i := 1; i < len(d.Offers); i++ {
		if !lessOffer(d.Offers[i-1], d.Offers[i]) {
			return Feed{}, fmt.Errorf("duplicate exact offer")
		}
	}
	// One public semantic snapshot per source. Never use Catalog.ID(): its
	// hash also covers non-allowlisted providers and omits live eligibility.
	for start := 0; start < len(d.Offers); {
		end := start + 1
		for end < len(d.Offers) && d.Offers[end].ProviderID == d.Offers[start].ProviderID && d.Offers[end].CatalogSource == d.Offers[start].CatalogSource {
			end++
		}
		rows := make([]Offer, 0, end-start)
		for _, o := range d.Offers[start:end] {
			o.Dimensions = append([]Dimension(nil), o.Dimensions...)
			for i := range o.Dimensions {
				o.Dimensions[i].NominalOneKeyRateUSD = nil
			}
			rows = append(rows, o)
		}
		b, err := json.Marshal(rows)
		if err != nil {
			return Feed{}, err
		}
		d.Sources = append(d.Sources, Source{ProviderID: d.Offers[start].ProviderID, CatalogSource: d.Offers[start].CatalogSource, SnapshotID: digest(b)})
		start = end
	}
	b, err := json.Marshal(d)
	if err != nil {
		return Feed{}, err
	}
	d.ContentVersion = digest(b)
	b, err = json.Marshal(d)
	if err != nil {
		return Feed{}, err
	}
	return Feed{Document: d, Body: b, ETag: `"` + digest(b) + `"`}, nil
}

func knownSource(s string) bool {
	return s == "openrouter" || s == "openai_compatible" || s == "price_card"
}

func lessOffer(a, b Offer) bool {
	if a.ProviderID != b.ProviderID {
		return a.ProviderID < b.ProviderID
	}
	if a.CatalogSource != b.CatalogSource {
		return a.CatalogSource < b.CatalogSource
	}
	if a.ModelID != b.ModelID {
		return a.ModelID < b.ModelID
	}
	return a.EndpointID < b.EndpointID
}

func digest(b []byte) string { sum := sha256.Sum256(b); return hex.EncodeToString(sum[:]) }
func decimal(v float64) string {
	if v == 0 {
		return "0"
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}
func stringPtr(v string) *string { return &v }

func priced(name, unit, rule string, raw, effective float64, markup int) Dimension {
	rate := decimal(effective)
	return Dimension{Name: name, Unit: unit, Rule: rule, CardRateUSD: stringPtr(decimal(raw)), ProviderRateUSD: stringPtr(rate), NominalOneKeyRateUSD: stringPtr(markedRate(rate, markup))}
}
func cache(name string, raw, prompt float64, markup int) Dimension {
	if raw > 0 {
		return priced(name, "token", "replaces_included_prompt", raw, raw, markup)
	}
	return priced(name, "token", "fallback_to_prompt", raw, prompt, markup)
}
func markedRate(rate string, markup int) string {
	r, _ := new(big.Rat).SetString(rate)
	r.Mul(r, big.NewRat(int64(10000+markup), 10000))
	places := 4
	if i := strings.IndexByte(rate, '.'); i >= 0 {
		places += len(rate) - i - 1
	}
	out := strings.TrimRight(strings.TrimRight(r.FloatString(places), "0"), ".")
	if out == "" {
		return "0"
	}
	return out
}
