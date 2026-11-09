package models

// InclusivePrice is a versioned admission quote, NOT a component billing card.
// Settlement uses only the upstream's inclusive total. Scalars keep snapshots
// immutable/comparable; no map can be mutated by a later catalogue refresh.
type InclusivePrice struct {
	// Keep the immutable runtime universe once, not in every serialized model.
	RouterSnapshot string `json:"router_snapshot,omitempty"`
	RouterUniverse string `json:"-"`
	SnapshotModels string `json:"-"`
	Contract       string `json:"contract"`
	Model          string `json:"model"`
	Prompt         string `json:"prompt"`
	Completion     string `json:"completion"`
	CacheRead      string `json:"cache_read"`
	CacheWrite     string `json:"cache_write"`
	Reasoning      string `json:"reasoning"`
	Image          string `json:"image"`
	// Output permissions are frozen with the exact listing's admission card.
	// An input-image price alone must not enable image generation.
	TextOutput   bool   `json:"text_output,omitempty"`
	ImageOutput  bool   `json:"image_output,omitempty"`
	Generation   string `json:"generation"`
	ContextLimit int64  `json:"context_limit"`
	OutputLimit  int64  `json:"output_limit"`
}

// InclusiveAttempt binds reconciliation to the exact dispatch, not the request
// id the gateway substitutes in client responses. No prompt, URL or key belongs
// here. Estimated amounts are full holds, pending authoritative reconciliation.
type InclusiveAttempt struct {
	Identity       InclusiveIdentity `json:"-"`
	AttemptID      string            `json:"attempt_id"`
	Model          string            `json:"model"`
	ServedModel    string            `json:"served_model"`
	GenerationID   string            `json:"generation_id"`
	HoldMicros     int64             `json:"hold_micros"`
	UpstreamMicros int64             `json:"upstream_micros"`
	BilledMicros   int64             `json:"billed_micros"`
	Estimated      bool              `json:"estimated"`
	Anomaly        string            `json:"anomaly,omitempty"`
	ProviderTotal  string            `json:"provider_total_usd,omitempty"`
}
