package mock

import (
	"encoding/json"
	"math"
	"net/http"
	"strings"
)

// Route is the kind of inference call.
type Route string

const (
	RouteChat       Route = "chat"
	RouteResponses  Route = "responses"
	RouteMessages   Route = "messages"
	RouteEmbeddings Route = "embeddings"
	RouteImages     Route = "images"
	RouteSpeech     Route = "speech"
)

// Request is one inference call as directives and renderers see it.
type Request struct {
	Route  Route
	HTTP   *http.Request
	Body   []byte
	JSON   map[string]any // the decoded body; nil when it is not a JSON object
	Model  string
	Stream bool
	Bearer string
}

// Response is what the mock will send. It starts as a success built from the
// fixtures, and each directive edits it before it is rendered.
type Response struct {
	Status       int // 2xx renders the route's success body; anything else an error
	ErrorCode    string
	ErrorMessage string

	LatencyMs    int // wait before answering
	ChunkDelayMs int // wait between stream events
	AbortAfter   int // drop the connection after this many stream events (0: never)

	Content      string
	ToolCalls    []ToolCall
	FinishReason string // empty: the route's default ("stop", "tool_calls", ...)
	Usage        Usage
	NoUsage      bool     // omit usage from the body and the stream
	ReportCost   bool     // put usage.cost on OpenAI-shaped bodies
	CostUSD      *float64 // usage_cost: the cost to report and charge; nil prices the usage

	ServedProvider string // the top-level "provider" field; empty omits it
}

// ToolCall is one tool call the response makes.
type ToolCall struct {
	ID        string
	Name      string
	Arguments string // JSON text
}

// Usage holds token counts. Prompt includes the cached and cache-write tokens
// and Completion the reasoning tokens (OpenAI's convention); the Anthropic
// renderer reports input_tokens without them.
type Usage struct {
	Prompt, Completion                 int
	CachedTokens, CacheWriteTokens     int
	ReasoningTokens                    int
	EmbeddingInputs, Images, TextChars int
}

// newRequest reads the fields every route shares from the decoded body.
func newRequest(route Route, r *http.Request, body []byte) *Request {
	req := &Request{Route: route, HTTP: r, Body: body, Bearer: bearerOf(r)}
	var obj map[string]any
	if json.Unmarshal(body, &obj) == nil {
		req.JSON = obj
		req.Model, _ = obj["model"].(string)
		req.Stream, _ = obj["stream"].(bool)
	}
	return req
}

func bearerOf(r *http.Request) string {
	if v, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer "); ok {
		return v
	}
	return r.Header.Get("x-api-key")
}

// baseResponse is the success the fixtures describe for req.
func (s *Server) baseResponse(req *Request) *Response {
	cfg := s.fix.Config
	resp := &Response{
		Status:         http.StatusOK,
		Content:        "(mock " + req.Model + ") hello",
		Usage:          Usage{Prompt: cfg.PromptTokens, Completion: cfg.CompletionTokens},
		ReportCost:     cfg.ReportCost,
		ServedProvider: firstProviderOrder(req.JSON),
	}
	switch req.Route {
	case RouteEmbeddings:
		resp.Usage = embeddingUsage(req.JSON)
	case RouteImages:
		resp.Usage = Usage{Images: intField(req.JSON, "n", 1)}
	case RouteSpeech:
		text, _ := req.JSON["input"].(string)
		resp.Usage = Usage{TextChars: len(text)}
	}
	return resp
}

// firstProviderOrder is the first entry of the request's OpenRouter
// provider.order, which the mock reports as the provider that served it.
func firstProviderOrder(body map[string]any) string {
	prov, _ := body["provider"].(map[string]any)
	order, _ := prov["order"].([]any)
	if len(order) == 0 {
		return ""
	}
	first, _ := order[0].(string)
	return first
}

func embeddingUsage(body map[string]any) Usage {
	var texts []string
	switch v := body["input"].(type) {
	case string:
		texts = []string{v}
	case []any:
		for _, item := range v {
			s, _ := item.(string) // token arrays count as one input of one token
			texts = append(texts, s)
		}
	}
	u := Usage{EmbeddingInputs: len(texts)}
	for _, t := range texts {
		u.Prompt += max(1, len(t)/4)
	}
	return u
}

func intField(body map[string]any, key string, fallback int) int {
	if f, ok := body[key].(float64); ok && f >= 1 {
		return int(f)
	}
	return fallback
}

// normalize makes the token counts consistent after the directives ran:
// cached and cache-write tokens are part of the prompt, reasoning tokens part
// of the completion.
func (u *Usage) normalize() {
	u.Prompt = max(u.Prompt, u.CachedTokens+u.CacheWriteTokens)
	u.Completion = max(u.Completion, u.ReasoningTokens)
}

// costUSD is what resp costs at the fixtures' prices for req.Model.
func (s *Server) costUSD(req *Request, resp *Response) float64 {
	if resp.CostUSD != nil {
		return *resp.CostUSD
	}
	p := s.fix.PriceOf(req.Model)
	u := resp.Usage
	var cost float64
	switch req.Route {
	case RouteImages:
		cost = float64(u.Images) * float64(s.fix.Config.ImagePriceUSD)
	case RouteSpeech:
		cost = float64(u.TextChars) * float64(s.fix.Config.SpeechPricePerCharUSD)
	case RouteEmbeddings:
		cost = float64(u.Prompt) * p.Prompt
	default:
		uncached := u.Prompt - u.CachedTokens - u.CacheWriteTokens
		cost = float64(uncached)*p.Prompt + float64(u.CachedTokens)*p.CacheRead +
			float64(u.CacheWriteTokens)*p.CacheWrite + float64(u.Completion)*p.Completion
	}
	return math.Round(cost*1e9) / 1e9
}
