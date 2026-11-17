package billing

import "github.com/futureagi/agentcc-gateway/internal/models"

// DimensionalReceipt is the opt-in v2 settlement sidecar. A v1 consumer must
// not receive it until upgraded. No production catalogue currently opts in.
// Rates, caps, counts and exact identities suffice; prompts, assets and URLs
// must never be put in this record.
type DimensionalReceipt struct {
	Contract          string                    `json:"contract"`
	Model             string                    `json:"model"`
	Endpoint          string                    `json:"endpoint"`
	Snapshot          string                    `json:"snapshot"`
	State             string                    `json:"state"` // measured, estimated_bound, provider_total
	ProviderTotal     *string                   `json:"provider_total_usd,omitempty"`
	Lines             []DimensionalLine         `json:"lines"`
	InclusiveAttempts []models.InclusiveAttempt `json:"inclusive_attempts,omitempty"`
}

type DimensionalLine struct {
	Unit           string `json:"unit"`
	RateUSD        string `json:"rate_usd"`
	Cap            int64  `json:"cap"`
	Quantity       *int64 `json:"quantity,omitempty"` // absent is unknown, not zero
	Estimated      bool   `json:"estimated"`
	CoveredByTotal bool   `json:"covered_by_total"`
}
