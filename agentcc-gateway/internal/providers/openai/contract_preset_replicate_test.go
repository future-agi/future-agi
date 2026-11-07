package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The replicate preset (api.replicate.com). It is not OpenAI-compatible: it
// reaches Replicate's own predictions API, whose official SDKs
// (replicate-python-stainless 48e138a api.md, 37 operations) have none of the
// gateway's endpoints but GET /v1/models, and that answers a page of
// Replicate models (gap RP1). Every other cell is not served.
//
// The model list declares no value set: the one documented enum,
// ModelListResponse.visibility (public, private), is "" in the fixture, the
// official Go client's test mock as json.Marshal writes it, and the whole page
// is unknown to the adapter anyway.
func init() {
	registerContractPreset(contractPreset{
		Name: "replicate",
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointListModels: nil,
		},
	})
}
