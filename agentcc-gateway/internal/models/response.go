package models

import "encoding/json"

// ChatCompletionResponse represents an OpenAI-compatible chat completion response.
type ChatCompletionResponse struct {
	ID                string   `json:"id"`
	Object            string   `json:"object"`
	Created           int64    `json:"created"`
	Model             string   `json:"model"`
	Choices           []Choice `json:"choices"`
	Usage             *Usage   `json:"usage,omitempty"`
	SystemFingerprint string   `json:"system_fingerprint,omitempty"`
	ServiceTier       string   `json:"service_tier,omitempty"`
}

type Choice struct {
	Index        int              `json:"index"`
	Message      Message          `json:"message"`
	FinishReason string           `json:"finish_reason"`
	Logprobs     *json.RawMessage `json:"logprobs,omitempty"`
}

type Usage struct {
	PromptTokens            int              `json:"prompt_tokens"`
	CompletionTokens        int              `json:"completion_tokens"`
	TotalTokens             int              `json:"total_tokens"`
	PromptTokensDetails     *json.RawMessage `json:"prompt_tokens_details,omitempty"`
	CompletionTokensDetails *json.RawMessage `json:"completion_tokens_details,omitempty"`
}

// StreamChunk represents a single SSE chunk in a streaming response.
type StreamChunk struct {
	ID                string               `json:"id"`
	Object            string               `json:"object"`
	Created           int64                `json:"created"`
	Model             string               `json:"model"`
	Choices           []StreamChoice       `json:"choices"`
	Usage             *Usage               `json:"usage,omitempty"`
	AgentccMetadata     *AgentccStreamMetadata `json:"agentcc_metadata,omitempty"`
	SystemFingerprint string               `json:"system_fingerprint,omitempty"`
}

type AgentccStreamMetadata struct {
	Cost      float64 `json:"cost"`
	LatencyMs int64   `json:"latency_ms"`
}

type StreamChoice struct {
	Index        int     `json:"index"`
	Delta        Delta   `json:"delta"`
	FinishReason *string `json:"finish_reason"`
}

type Delta struct {
	Role      string          `json:"role,omitempty"`
	Content   *string         `json:"content,omitempty"`
	ToolCalls []ToolCallDelta `json:"tool_calls,omitempty"`
}

type ToolCallDelta struct {
	Index    int           `json:"index"`
	ID       string        `json:"id,omitempty"`
	Type     string        `json:"type,omitempty"`
	Function *FunctionCall `json:"function,omitempty"`
}

// ModelObject represents a model in the /v1/models response.
type ModelObject struct {
	ID      string `json:"id"`
	Object  string `json:"object"`
	Created int64  `json:"created"`
	OwnedBy string `json:"owned_by"`
}

// ModelListResponse is the response for GET /v1/models.
type ModelListResponse struct {
	Object string        `json:"object"`
	Data   []ModelObject `json:"data"`
}

// EnrichedModelObject adds pricing, capabilities, and limits to the base model object.
type EnrichedModelObject struct {
	ID              string           `json:"id"`
	Object          string           `json:"object"`
	Created         int64            `json:"created"`
	OwnedBy         string           `json:"owned_by"`
	MaxInputTokens  int              `json:"max_input_tokens,omitempty"`
	MaxOutputTokens int              `json:"max_output_tokens,omitempty"`
	Pricing         *ModelPricingAPI `json:"pricing,omitempty"`
	Capabilities    *ModelCapsAPI    `json:"capabilities,omitempty"`
	Mode            string           `json:"mode,omitempty"`
	Deprecated      bool             `json:"deprecated,omitempty"`
}

// ModelPricingAPI is the API representation of model pricing (per million tokens).
type ModelPricingAPI struct {
	InputPerMTokens       float64 `json:"input_per_m_tokens"`
	OutputPerMTokens      float64 `json:"output_per_m_tokens"`
	CachedInputPerMTokens float64 `json:"cached_input_per_m_tokens,omitempty"`
}

// ModelCapsAPI is the API representation of model capabilities.
type ModelCapsAPI struct {
	Vision          bool `json:"vision"`
	FunctionCalling bool `json:"function_calling"`
	Streaming       bool `json:"streaming"`
	ResponseSchema  bool `json:"response_schema"`
	PromptCaching   bool `json:"prompt_caching"`
	Reasoning       bool `json:"reasoning"`
}

// EnrichedModelListResponse is the enriched response for GET /v1/models.
type EnrichedModelListResponse struct {
	Object string                `json:"object"`
	Data   []EnrichedModelObject `json:"data"`
}
