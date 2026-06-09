package modeldb

import (
	"sort"
	"strings"
)

// ModelMode defines the type of model (chat, embedding, etc.).
type ModelMode string

const (
	ModeChat               ModelMode = "chat"
	ModeEmbedding          ModelMode = "embedding"
	ModeImageGeneration    ModelMode = "image_generation"
	ModeAudioTranscription ModelMode = "audio_transcription"
	ModeAudioSpeech        ModelMode = "audio_speech"
	ModeRerank             ModelMode = "rerank"
	ModeModeration         ModelMode = "moderation"
)

// ModelInfo contains all metadata for a single model.
type ModelInfo struct {
	ID              string          `json:"id"`
	Provider        string          `json:"provider"`
	Mode            ModelMode       `json:"mode"`
	MaxInputTokens  int             `json:"max_input_tokens"`
	MaxOutputTokens int             `json:"max_output_tokens"`
	Pricing         PricingInfo     `json:"pricing"`
	Capabilities    CapabilityFlags `json:"capabilities"`
	DeprecationDate string          `json:"deprecation_date,omitempty"`
	Regions         []string        `json:"regions,omitempty"`
}

// ModelDB is an immutable, thread-safe model metadata database.
// Constructed once, swapped atomically on hot-reload.
type ModelDB struct {
	models     map[string]*ModelInfo
	byProvider map[string][]*ModelInfo
}

// New creates a ModelDB from bundled data merged with config overrides.
func New(bundled map[string]*ModelInfo, overrides map[string]ModelOverride) *ModelDB {
	models := make(map[string]*ModelInfo, len(bundled)+len(overrides))

	// Deep-copy bundled data.
	for id, m := range bundled {
		cpy := *m
		cpy.ID = id
		if len(m.Regions) > 0 {
			cpy.Regions = make([]string, len(m.Regions))
			copy(cpy.Regions, m.Regions)
		}
		models[id] = &cpy
	}

	// Apply overrides.
	for id, ov := range overrides {
		existing, ok := models[id]
		if !ok {
			// New model from override.
			existing = &ModelInfo{ID: id}
			models[id] = existing
		}
		mergeOverride(existing, ov)
	}

	// Build provider index.
	byProvider := make(map[string][]*ModelInfo)
	for _, m := range models {
		byProvider[m.Provider] = append(byProvider[m.Provider], m)
	}
	for _, list := range byProvider {
		sort.Slice(list, func(i, j int) bool { return list[i].ID < list[j].ID })
	}

	return &ModelDB{models: models, byProvider: byProvider}
}

// Get returns model info by ID. Tries exact match, then strips "provider/"
// prefix, then falls back to the last path segment (for Bedrock-style
// "bedrock/arn:…/inference-profile/<model>" identifiers).
func (db *ModelDB) Get(id string) (*ModelInfo, bool) {
	if m, ok := db.models[id]; ok {
		return m, true
	}
	// Try stripping provider prefix: "openai/gpt-4o" → "gpt-4o"
	if idx := strings.IndexByte(id, '/'); idx >= 0 {
		if m, ok := db.models[id[idx+1:]]; ok {
			return m, true
		}
	}
	// Fallback: last path segment — handles Bedrock inference-profile ARNs
	// like "bedrock/arn:aws:bedrock:…:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"
	if idx := strings.LastIndexByte(id, '/'); idx >= 0 && idx < len(id)-1 {
		if m, ok := db.models[id[idx+1:]]; ok {
			return m, true
		}
	}
	return nil, false
}

// GetPricing returns pricing info for a model.
func (db *ModelDB) GetPricing(id string) (*PricingInfo, bool) {
	m, ok := db.Get(id)
	if !ok {
		return nil, false
	}
	return &m.Pricing, true
}

// GetCapabilities returns capability flags for a model.
func (db *ModelDB) GetCapabilities(id string) (*CapabilityFlags, bool) {
	m, ok := db.Get(id)
	if !ok {
		return nil, false
	}
	return &m.Capabilities, true
}

// GetLimits returns the max input and output tokens for a model.
func (db *ModelDB) GetLimits(id string) (maxInput, maxOutput int, ok bool) {
	m, found := db.Get(id)
	if !found {
		return 0, 0, false
	}
	return m.MaxInputTokens, m.MaxOutputTokens, true
}

// List returns all models sorted by ID.
func (db *ModelDB) List() []*ModelInfo {
	result := make([]*ModelInfo, 0, len(db.models))
	for _, m := range db.models {
		result = append(result, m)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

// ListByProvider returns models for a specific provider.
func (db *ModelDB) ListByProvider(provider string) []*ModelInfo {
	return db.byProvider[provider]
}

// ListByMode returns all models with the given mode.
func (db *ModelDB) ListByMode(mode ModelMode) []*ModelInfo {
	var result []*ModelInfo
	for _, m := range db.models {
		if m.Mode == mode {
			result = append(result, m)
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

// Count returns the total number of models.
func (db *ModelDB) Count() int {
	return len(db.models)
}

// CalculateCost returns the cost for a given model and token counts.
// Returns 0, false if the model is not found or has no pricing.
func (db *ModelDB) CalculateCost(modelID string, inputTokens, outputTokens int, opts CostOptions) (float64, bool) {
	m, ok := db.Get(modelID)
	if !ok || m.Pricing.InputPerToken == 0 {
		return 0, false
	}
	return m.Pricing.Calculate(inputTokens, outputTokens, opts), true
}

// ModelOverride defines config-level overrides for a model entry.
// Pointer fields are optional — nil means "don't override".
type ModelOverride struct {
	Provider        string           `yaml:"provider"`
	Mode            string           `yaml:"mode"`
	MaxInputTokens  *int             `yaml:"max_input_tokens"`
	MaxOutputTokens *int             `yaml:"max_output_tokens"`
	Pricing         *PricingOverride `yaml:"pricing"`
	Capabilities    *CapOverride     `yaml:"capabilities"`
	DeprecationDate string           `yaml:"deprecation_date"`
	Regions         []string         `yaml:"regions"`
}

// PricingOverride allows overriding individual pricing fields.
type PricingOverride struct {
	InputPerToken       *float64 `yaml:"input_per_token"`
	OutputPerToken      *float64 `yaml:"output_per_token"`
	CachedInputPerToken *float64 `yaml:"cached_input_per_token"`
	BatchInputPerToken  *float64 `yaml:"batch_input_per_token"`
	BatchOutputPerToken *float64 `yaml:"batch_output_per_token"`
}

// CapOverride allows overriding individual capability flags.
type CapOverride struct {
	FunctionCalling   *bool `yaml:"function_calling"`
	ParallelToolCalls *bool `yaml:"parallel_tool_calls"`
	Vision            *bool `yaml:"vision"`
	AudioInput        *bool `yaml:"audio_input"`
	AudioOutput       *bool `yaml:"audio_output"`
	PDFInput          *bool `yaml:"pdf_input"`
	Streaming         *bool `yaml:"streaming"`
	ResponseSchema    *bool `yaml:"response_schema"`
	SystemMessages    *bool `yaml:"system_messages"`
	PromptCaching     *bool `yaml:"prompt_caching"`
	Reasoning         *bool `yaml:"reasoning"`
}

func mergeOverride(m *ModelInfo, ov ModelOverride) {
	if ov.Provider != "" {
		m.Provider = ov.Provider
	}
	if ov.Mode != "" {
		m.Mode = ModelMode(ov.Mode)
	}
	if ov.MaxInputTokens != nil {
		m.MaxInputTokens = *ov.MaxInputTokens
	}
	if ov.MaxOutputTokens != nil {
		m.MaxOutputTokens = *ov.MaxOutputTokens
	}
	if ov.DeprecationDate != "" {
		m.DeprecationDate = ov.DeprecationDate
	}
	if len(ov.Regions) > 0 {
		m.Regions = ov.Regions
	}

	if p := ov.Pricing; p != nil {
		if p.InputPerToken != nil {
			m.Pricing.InputPerToken = *p.InputPerToken
		}
		if p.OutputPerToken != nil {
			m.Pricing.OutputPerToken = *p.OutputPerToken
		}
		if p.CachedInputPerToken != nil {
			m.Pricing.CachedInputPerToken = *p.CachedInputPerToken
		}
		if p.BatchInputPerToken != nil {
			m.Pricing.BatchInputPerToken = *p.BatchInputPerToken
		}
		if p.BatchOutputPerToken != nil {
			m.Pricing.BatchOutputPerToken = *p.BatchOutputPerToken
		}
	}

	if c := ov.Capabilities; c != nil {
		if c.FunctionCalling != nil {
			m.Capabilities.FunctionCalling = *c.FunctionCalling
		}
		if c.ParallelToolCalls != nil {
			m.Capabilities.ParallelToolCalls = *c.ParallelToolCalls
		}
		if c.Vision != nil {
			m.Capabilities.Vision = *c.Vision
		}
		if c.AudioInput != nil {
			m.Capabilities.AudioInput = *c.AudioInput
		}
		if c.AudioOutput != nil {
			m.Capabilities.AudioOutput = *c.AudioOutput
		}
		if c.PDFInput != nil {
			m.Capabilities.PDFInput = *c.PDFInput
		}
		if c.Streaming != nil {
			m.Capabilities.Streaming = *c.Streaming
		}
		if c.ResponseSchema != nil {
			m.Capabilities.ResponseSchema = *c.ResponseSchema
		}
		if c.SystemMessages != nil {
			m.Capabilities.SystemMessages = *c.SystemMessages
		}
		if c.PromptCaching != nil {
			m.Capabilities.PromptCaching = *c.PromptCaching
		}
		if c.Reasoning != nil {
			m.Capabilities.Reasoning = *c.Reasoning
		}
	}
}
