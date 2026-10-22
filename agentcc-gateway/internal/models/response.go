package models

import (
	"encoding/json"
	"reflect"
	"strconv"
)

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
	// UpstreamProvider is OpenRouter's `provider` field: the upstream that
	// served the request. Decoded here so every OpenAI-format adapter reads
	// it without change; it passes through to the client as OpenRouter sends it.
	UpstreamProvider string `json:"provider,omitempty"`

	// Extra carries the response fields this struct does not name, so a
	// provider's own additions reach the caller: Perplexity's citations and
	// search_results, and whatever the next provider adds. See passthrough.go.
	Extra map[string]json.RawMessage `json:"-"`
}

func (r *ChatCompletionResponse) UnmarshalJSON(data []byte) error {
	type Alias ChatCompletionResponse
	var a Alias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	*r = ChatCompletionResponse(a)
	extra, err := captureExtra(data, reflect.TypeFor[ChatCompletionResponse]())
	if err != nil {
		return err
	}
	r.Extra = extra
	return nil
}

func (r ChatCompletionResponse) MarshalJSON() ([]byte, error) {
	type Alias ChatCompletionResponse
	data, err := json.Marshal((*Alias)(&r))
	if err != nil {
		return nil, err
	}
	return mergeExtra(data, r.Extra)
}

type Choice struct {
	Index        int              `json:"index"`
	Message      Message          `json:"message"`
	FinishReason string           `json:"finish_reason"`
	Logprobs     *json.RawMessage `json:"logprobs,omitempty"`
}

// MarshalJSON guarantees the assistant message always carries a "content" key.
//
// Message.Content is omitempty (it has to be: the same struct is marshaled back
// out on the request path, where a null content is rejected upstream), so a
// message with no text drops the key entirely. Providers that translate a
// non-OpenAI response leave Content unset whenever the model returned no text —
// a tool-call-only reply, or one where reasoning consumed the whole budget — and
// clients that expect the OpenAI shape then read a message with no content field
// at all. Normalizing here rather than in each provider keeps the guarantee in
// one place; Choice is only ever marshaled on the response path.
//
// The value mirrors OpenAI: null when the model called tools, empty string
// otherwise.
func (c Choice) MarshalJSON() ([]byte, error) {
	type Alias Choice
	if c.Message.Content == nil {
		if len(c.Message.ToolCalls) > 0 {
			c.Message.Content = json.RawMessage(`null`)
		} else {
			c.Message.Content = json.RawMessage(`""`)
		}
	}
	return json.Marshal((*Alias)(&c))
}

type Usage struct {
	PromptTokens            int              `json:"prompt_tokens"`
	CompletionTokens        int              `json:"completion_tokens"`
	TotalTokens             int              `json:"total_tokens"`
	PromptTokensDetails     *json.RawMessage `json:"prompt_tokens_details,omitempty"`
	CompletionTokensDetails *json.RawMessage `json:"completion_tokens_details,omitempty"`
	// ServerToolUse counts provider-executed tool calls, which are billed per
	// call on top of tokens.  A response that reports tokens alone understates
	// what the request cost.
	ServerToolUse *ServerToolUsage `json:"server_tool_use,omitempty"`
	// Cost is the charge an upstream reports for the request in USD
	// (OpenRouter sends it on every response). The gateway prefers it to its
	// own price table, and rewrites it to the billed amount before a managed
	// response reaches the client.
	Cost *float64 `json:"cost,omitempty"`
	// IsByok and CostDetails are OpenRouter's usage fields.
	IsByok      *bool             `json:"is_byok,omitempty"`
	CostDetails *UsageCostDetails `json:"cost_details,omitempty"`

	// PromptDetails and CompletionDetails are the parsed token details. They
	// never marshal: PromptTokensDetails and CompletionTokensDetails stay raw
	// so subfields the gateway does not know pass through unchanged.
	// ParseDetails fills them from the raw fields; MergeDetails writes them
	// back.
	PromptDetails     PromptTokensDetails     `json:"-"`
	CompletionDetails CompletionTokensDetails `json:"-"`

	// Extra carries a provider's own counters, such as Perplexity's
	// citation_tokens and num_search_queries. See passthrough.go.
	Extra map[string]json.RawMessage `json:"-"`
}

func (u *Usage) UnmarshalJSON(data []byte) error {
	type Alias Usage
	var a Alias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	*u = Usage(a)
	extra, err := captureExtra(data, reflect.TypeFor[Usage]())
	if err != nil {
		return err
	}
	u.Extra = extra
	return nil
}

func (u Usage) MarshalJSON() ([]byte, error) {
	type Alias Usage
	data, err := json.Marshal((*Alias)(&u))
	if err != nil {
		return nil, err
	}
	return mergeExtra(data, u.Extra)
}

// UsageCostDetails is OpenRouter's usage.cost_details, in USD.
type UsageCostDetails struct {
	UpstreamInferenceCost            *float64 `json:"upstream_inference_cost,omitempty"`
	UpstreamInferencePromptCost      *float64 `json:"upstream_inference_prompt_cost,omitempty"`
	UpstreamInferenceCompletionsCost *float64 `json:"upstream_inference_completions_cost,omitempty"`
}

// PromptTokensDetails is the part of usage.prompt_tokens_details the gateway
// reads.
type PromptTokensDetails struct {
	CachedTokens     int `json:"cached_tokens,omitempty"`
	CacheWriteTokens int `json:"cache_write_tokens,omitempty"`
	AudioTokens      int `json:"audio_tokens,omitempty"`
}

// CompletionTokensDetails is the part of usage.completion_tokens_details the
// gateway reads.
type CompletionTokensDetails struct {
	ReasoningTokens int `json:"reasoning_tokens,omitempty"`
}

// ServerToolUsage mirrors Anthropic's usage.server_tool_use block, and
// carries the other providers' provider-run tool counts in the same place
// (search, fetch, code execution), each filled by the provider's own
// translation and priced by
// the cost plugin from the server-tool fee table.
type ServerToolUsage struct {
	// WebSearchRequests counts searches: Anthropic's web_search_requests,
	// and OpenAI's web_search_call items made with the web_search tool.
	WebSearchRequests int `json:"web_search_requests,omitempty"`
	// WebFetchRequests is Anthropic's web_fetch_requests (tokens only).
	WebFetchRequests int `json:"web_fetch_requests,omitempty"`
	// CodeExecutionRequests is Anthropic's code-execution count.
	CodeExecutionRequests int `json:"code_execution_requests,omitempty"`
	// WebSearchPreviewRequests counts OpenAI web_search_call items made
	// with the web_search_preview tool, which is priced apart.
	WebSearchPreviewRequests int `json:"web_search_preview_requests,omitempty"`
	// FileSearchRequests counts OpenAI file_search_call items.
	FileSearchRequests int `json:"file_search_requests,omitempty"`
	// WebSearchQueries is Gemini's groundingMetadata.webSearchQueries,
	// summed over the candidates (Gemini 3 and later bill per query).
	WebSearchQueries int `json:"web_search_queries,omitempty"`
	// GroundedPrompts is 1 when a Gemini candidate was grounded with
	// Google Search (Gemini 2.5 bills per grounded prompt);
	// MapsGroundedPrompts likewise for Google Maps.
	GroundedPrompts     int `json:"grounded_prompts,omitempty"`
	MapsGroundedPrompts int `json:"maps_grounded_prompts,omitempty"`
	// Containers are the code-execution containers the response used, one
	// entry per container.
	Containers []ServerToolContainer `json:"containers,omitempty"`
}

// ServerToolContainer is one provider-run container a response used.
type ServerToolContainer struct {
	ID string `json:"id,omitempty"`
	// MemoryGB is an OpenAI container's size; 0 when the provider has one
	// size (Anthropic) or the size is unknown.
	MemoryGB int `json:"memory_gb,omitempty"`
	// Minutes is how long the container ran, 0 when the provider does not
	// report it (the fee is then the minimum, an estimate).
	Minutes int `json:"minutes,omitempty"`
}

// StreamChunk represents a single SSE chunk in a streaming response.
type StreamChunk struct {
	ID                string                 `json:"id"`
	Object            string                 `json:"object"`
	Created           int64                  `json:"created"`
	Model             string                 `json:"model"`
	Choices           []StreamChoice         `json:"choices"`
	Usage             *Usage                 `json:"usage,omitempty"`
	AgentccMetadata   *AgentccStreamMetadata `json:"agentcc_metadata,omitempty"`
	SystemFingerprint string                 `json:"system_fingerprint,omitempty"`
	// UpstreamProvider is OpenRouter's `provider` field on a stream chunk.
	UpstreamProvider string `json:"provider,omitempty"`

	// Extra carries the chunk fields this struct does not name. A provider
	// that sends its citations on the last chunk rather than in a whole
	// response needs this to reach a streaming caller. See passthrough.go.
	Extra map[string]json.RawMessage `json:"-"`
}

func (c *StreamChunk) UnmarshalJSON(data []byte) error {
	type Alias StreamChunk
	var a Alias
	if err := json.Unmarshal(data, &a); err != nil {
		return err
	}
	*c = StreamChunk(a)
	extra, err := captureExtra(data, reflect.TypeFor[StreamChunk]())
	if err != nil {
		return err
	}
	c.Extra = extra
	return nil
}

func (c StreamChunk) MarshalJSON() ([]byte, error) {
	type Alias StreamChunk
	data, err := json.Marshal((*Alias)(&c))
	if err != nil {
		return nil, err
	}
	return mergeExtra(data, c.Extra)
}

// AgentccStreamMetadata is the gateway's evidence object at the end of a
// stream. Cost and LatencyMs are the v1 fields; every
// other field is omitted when empty.
type AgentccStreamMetadata struct {
	Cost      float64 `json:"cost"`
	LatencyMs int64   `json:"latency_ms"`

	RequestID          string                      `json:"request_id,omitempty"`
	GenerationID       string                      `json:"generation_id,omitempty"`
	ModelUsed          string                      `json:"model_used,omitempty"`
	Provider           string                      `json:"provider,omitempty"`
	BillingMode        string                      `json:"billing_mode,omitempty"`
	CostUpstream       *float64                    `json:"cost_upstream,omitempty"`
	CostSource         string                      `json:"cost_source,omitempty"`
	CreditsRemaining   *float64                    `json:"credits_remaining,omitempty"`
	BudgetRemainingUSD *float64                    `json:"budget_remaining_usd,omitempty"`
	Attempts           int                         `json:"attempts,omitempty"`
	PolicyVersion      string                      `json:"policy_version,omitempty"`
	PolicyVariant      string                      `json:"policy_variant,omitempty"`
	Auto               *AgentccStreamAuto          `json:"auto,omitempty"`
	UpstreamProvider   string                      `json:"upstream_provider,omitempty"`
	ContentMode        string                      `json:"content_mode,omitempty"`
	Guardrail          *AgentccStreamGuardrailInfo `json:"guardrail,omitempty"`
}

// AgentccStreamAuto is the auto-routing part of the stream evidence object.
type AgentccStreamAuto struct {
	Model    string `json:"model,omitempty"`
	Tier     string `json:"tier,omitempty"`
	Reason   string `json:"reason,omitempty"`
	Step     string `json:"step,omitempty"`
	Affinity string `json:"affinity,omitempty"`
}

// AgentccStreamGuardrailInfo is the guardrail part of the stream evidence
// object.
type AgentccStreamGuardrailInfo struct {
	ToolCallsBlocked []string `json:"tool_calls_blocked,omitempty"`
}

type StreamChoice struct {
	Index        int     `json:"index"`
	Delta        Delta   `json:"delta"`
	FinishReason *string `json:"finish_reason"`
}

type Delta struct {
	Role             string          `json:"role,omitempty"`
	Content          *string         `json:"content,omitempty"`
	ReasoningContent *string         `json:"reasoning_content,omitempty"`
	ToolCalls        []ToolCallDelta `json:"tool_calls,omitempty"`
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

// ParseDetails fills PromptDetails and CompletionDetails from the raw token
// details. A raw field that is absent or not a JSON object leaves its sidecar
// as it is.
func (u *Usage) ParseDetails() {
	if u.PromptTokensDetails != nil {
		var d PromptTokensDetails
		if json.Unmarshal(*u.PromptTokensDetails, &d) == nil {
			u.PromptDetails = d
		}
	}
	if u.CompletionTokensDetails != nil {
		var d CompletionTokensDetails
		if json.Unmarshal(*u.CompletionTokensDetails, &d) == nil {
			u.CompletionDetails = d
		}
	}
}

// MergeDetails writes the sidecar values back into the raw token details,
// keeping every subfield it does not know. A raw field is rewritten only when
// a non-zero sidecar value differs from it, so details that did not change
// stay byte-for-byte as the upstream sent them. A zero sidecar value never
// clears a raw one. A raw field that is not a JSON object is left alone.
func (u *Usage) MergeDetails() {
	u.PromptTokensDetails = mergeTokenDetails(u.PromptTokensDetails, map[string]int{
		"cached_tokens":      u.PromptDetails.CachedTokens,
		"cache_write_tokens": u.PromptDetails.CacheWriteTokens,
		"audio_tokens":       u.PromptDetails.AudioTokens,
	})
	u.CompletionTokensDetails = mergeTokenDetails(u.CompletionTokensDetails, map[string]int{
		"reasoning_tokens": u.CompletionDetails.ReasoningTokens,
	})
}

// mergeTokenDetails returns raw with each non-zero value in set written over
// it, or raw itself when nothing changes.
func mergeTokenDetails(raw *json.RawMessage, set map[string]int) *json.RawMessage {
	var fields map[string]json.RawMessage
	if raw != nil {
		if err := json.Unmarshal(*raw, &fields); err != nil || fields == nil {
			return raw
		}
	}
	changed := false
	for key, v := range set {
		if v == 0 {
			continue
		}
		if cur, ok := fields[key]; ok {
			var n int
			if json.Unmarshal(cur, &n) == nil && n == v {
				continue
			}
		}
		if fields == nil {
			fields = make(map[string]json.RawMessage, len(set))
		}
		fields[key] = json.RawMessage(strconv.Itoa(v))
		changed = true
	}
	if !changed {
		return raw
	}
	b, err := json.Marshal(fields)
	if err != nil {
		return raw
	}
	out := json.RawMessage(b)
	return &out
}
