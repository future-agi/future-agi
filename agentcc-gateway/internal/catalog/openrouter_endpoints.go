package catalog

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sync"
	"time"
)

// endpointsSnapshot is every bundled model's OpenRouter endpoints
// (/api/v1/models/{id}/endpoints) and the zero-data-retention list
// (/api/v1/endpoints/zdr), as scripts/update-openrouter-catalog.sh writes
// them.
//
//go:embed endpoints_snapshot.json
var endpointsSnapshot []byte

// bundledEndpoints is the parsed endpoints snapshot.
type bundledEndpoints struct {
	FetchedAt time.Time
	ByModel   map[string][]Endpoint
	ZDR       map[string]bool
}

var (
	bundledEndpointsOnce sync.Once
	bundledEndpointsVal  *bundledEndpoints
	bundledEndpointsErr  error
)

// BundledEndpoints parses the endpoints snapshot bundled with the gateway,
// once.
func BundledEndpoints() (*bundledEndpoints, error) {
	bundledEndpointsOnce.Do(func() {
		bundledEndpointsVal, bundledEndpointsErr = parseEndpointsSnapshot(endpointsSnapshot)
	})
	return bundledEndpointsVal, bundledEndpointsErr
}

func parseEndpointsSnapshot(body []byte) (*bundledEndpoints, error) {
	var snap struct {
		FetchedAt time.Time `json:"fetched_at"`
		ZDR       []zdrRow  `json:"zdr"`
		Data      []struct {
			ID        string               `json:"id"`
			Endpoints []openRouterEndpoint `json:"endpoints"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &snap); err != nil {
		return nil, fmt.Errorf("parsing bundled endpoints: %w", err)
	}
	out := &bundledEndpoints{FetchedAt: snap.FetchedAt, ByModel: make(map[string][]Endpoint, len(snap.Data)), ZDR: zdrSet(snap.ZDR)}
	for _, d := range snap.Data {
		out.ByModel[d.ID] = convertEndpoints(d.ID, d.Endpoints)
	}
	return out, nil
}

// openRouterEndpoint is one entry of OpenRouter's endpoints list.
type openRouterEndpoint struct {
	Name                    string         `json:"name"`
	ProviderName            string         `json:"provider_name"`
	Tag                     string         `json:"tag"`
	Quantization            *string        `json:"quantization"`
	ContextLength           int            `json:"context_length"`
	MaxCompletionTokens     *int           `json:"max_completion_tokens"`
	MaxPromptTokens         *int           `json:"max_prompt_tokens"`
	Pricing                 map[string]any `json:"pricing"`
	SupportedParameters     []string       `json:"supported_parameters"`
	Status                  *int           `json:"status"`
	UptimeLast30m           *float64       `json:"uptime_last_30m"`
	SupportsImplicitCaching bool           `json:"supports_implicit_caching"`
	LatencyLast30m          *struct {
		P50 *float64 `json:"p50"`
		P90 *float64 `json:"p90"`
	} `json:"latency_last_30m"`
	ThroughputLast30m *struct {
		P50 *float64 `json:"p50"`
	} `json:"throughput_last_30m"`
}

// ParseOpenRouterEndpoints reads an OpenRouter
// /api/v1/models/{id}/endpoints response for model.
func ParseOpenRouterEndpoints(model string, body []byte) ([]Endpoint, error) {
	var resp struct {
		Data *struct {
			ID        string               `json:"id"`
			Endpoints []openRouterEndpoint `json:"endpoints"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("parsing openrouter endpoints of %s: %w", model, err)
	}
	if resp.Data == nil || resp.Data.ID != model {
		return nil, fmt.Errorf("openrouter endpoints: the response is not for %s", model)
	}
	return convertEndpoints(model, resp.Data.Endpoints), nil
}

func convertEndpoints(model string, in []openRouterEndpoint) []Endpoint {
	out := make([]Endpoint, 0, len(in))
	for _, e := range in {
		tag := e.Tag
		slug := upstreamSlug(tag, e.ProviderName)
		if tag == "" {
			tag = slug
		}
		ep := Endpoint{
			ID:                      EndpointID(SourceOpenRouter, tag, model),
			Name:                    e.Name,
			Source:                  SourceOpenRouter,
			UpstreamProvider:        slug,
			ProviderName:            e.ProviderName,
			Tag:                     tag,
			Regions:                 RegionsFromTag(tag),
			ContextLength:           e.ContextLength,
			SupportedParams:         e.SupportedParameters,
			UptimeLast30m:           e.UptimeLast30m,
			SupportsImplicitCaching: e.SupportsImplicitCaching,
			DataCollection:          DataCollectionUnknown,
		}
		if e.Quantization != nil && *e.Quantization != "unknown" {
			ep.Quantization = *e.Quantization
		}
		if e.MaxCompletionTokens != nil {
			ep.MaxCompletion = *e.MaxCompletionTokens
		}
		if e.MaxPromptTokens != nil {
			ep.MaxPrompt = *e.MaxPromptTokens
		}
		if e.Status != nil {
			ep.Status = *e.Status
		}
		if e.LatencyLast30m != nil {
			ep.LatencyP50Ms, ep.LatencyP90Ms = e.LatencyLast30m.P50, e.LatencyLast30m.P90
		}
		if e.ThroughputLast30m != nil {
			ep.ThroughputP50 = e.ThroughputLast30m.P50
		}
		ep.Prices, ep.PriceTiers, ep.DynamicPrice = parsePricing(e.Pricing)
		out = append(out, ep)
	}
	return out
}

// zdrRow identifies one endpoint of OpenRouter's /api/v1/endpoints/zdr list.
type zdrRow struct {
	ModelID string `json:"model_id"`
	Tag     string `json:"tag"`
}

// ParseZDR reads an OpenRouter /api/v1/endpoints/zdr response into the set
// of zero-data-retention endpoints, keyed zdrKey(model, tag).
func ParseZDR(body []byte) (map[string]bool, error) {
	var resp struct {
		Data *[]zdrRow `json:"data"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("parsing openrouter zdr endpoints: %w", err)
	}
	if resp.Data == nil {
		return nil, fmt.Errorf("openrouter zdr endpoints: no data")
	}
	return zdrSet(*resp.Data), nil
}

func zdrSet(rows []zdrRow) map[string]bool {
	out := make(map[string]bool, len(rows))
	for _, r := range rows {
		if r.ModelID != "" && r.Tag != "" {
			out[zdrKey(r.ModelID, r.Tag)] = true
		}
	}
	return out
}

func zdrKey(model, tag string) string { return model + "|" + tag }

// applyZDR marks e from the source's ZDR list: a listed endpoint is ZDR and
// collects no data (deny); once a list has been read, the rest are not ZDR,
// and their data collection is unknown, since OpenRouter publishes no
// per-endpoint policy. With no list read the endpoint is left as parsed.
func applyZDR(e *Endpoint, model string, zdr map[string]bool) {
	if zdr == nil || e.Source != SourceOpenRouter {
		return
	}
	on := zdr[zdrKey(model, e.Tag)]
	e.ZDR = &on
	e.DataCollection = DataCollectionUnknown
	if on {
		e.DataCollection = DataCollectionDeny
	}
}
