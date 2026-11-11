package modeldb

// PricingInfo contains a model's pricing in USD: per token, and for speech models per second of
// audio transcribed or per character synthesised.
type PricingInfo struct {
	InputPerToken       float64 `json:"input_per_token"`
	OutputPerToken      float64 `json:"output_per_token"`
	CachedInputPerToken float64 `json:"cached_input_per_token,omitempty"`
	BatchInputPerToken  float64 `json:"batch_input_per_token,omitempty"`
	BatchOutputPerToken float64 `json:"batch_output_per_token,omitempty"`
	InputPerImage       float64 `json:"input_per_image,omitempty"`
	OutputPerImage      float64 `json:"output_per_image,omitempty"`
	InputPerSecond      float64 `json:"input_per_second,omitempty"`
	InputPerCharacter   float64 `json:"input_per_character,omitempty"`

	// Prices in the units the non-token endpoints are billed in.
	InputPerQuery       float64 `json:"input_per_query,omitempty"`   // rerank: per search unit
	PerPage             float64 `json:"per_page,omitempty"`          // OCR
	OutputPerSecond     float64 `json:"output_per_second,omitempty"` // video generation
	InputPerPixel       float64 `json:"input_per_pixel,omitempty"`
	OutputPerPixel      float64 `json:"output_per_pixel,omitempty"`
	InputPerImageToken  float64 `json:"input_per_image_token,omitempty"`
	OutputPerImageToken float64 `json:"output_per_image_token,omitempty"`
}

// CostOptions controls which pricing tier to use.
type CostOptions struct {
	Cached bool // use cached input rate
	Batch  bool // use batch rates
}

// Calculate returns the total cost for the given token counts.
func (p *PricingInfo) Calculate(inputTokens, outputTokens int, opts CostOptions) float64 {
	inputRate := p.InputPerToken
	outputRate := p.OutputPerToken

	if opts.Cached && p.CachedInputPerToken > 0 {
		inputRate = p.CachedInputPerToken
	}
	if opts.Batch {
		if p.BatchInputPerToken > 0 {
			inputRate = p.BatchInputPerToken
		}
		if p.BatchOutputPerToken > 0 {
			outputRate = p.BatchOutputPerToken
		}
	}

	return float64(inputTokens)*inputRate + float64(outputTokens)*outputRate
}

// InputPerMTok returns the input price per million tokens (for display).
func (p *PricingInfo) InputPerMTok() float64 {
	return p.InputPerToken * 1_000_000
}

// OutputPerMTok returns the output price per million tokens (for display).
func (p *PricingInfo) OutputPerMTok() float64 {
	return p.OutputPerToken * 1_000_000
}

// HasPricing returns true if this model has non-zero pricing.
func (p *PricingInfo) HasPricing() bool {
	return p.InputPerToken > 0 || p.OutputPerToken > 0
}
