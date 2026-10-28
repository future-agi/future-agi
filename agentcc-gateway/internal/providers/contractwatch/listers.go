package contractwatch

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

// Model is one model a provider lists, with whatever capability metadata its
// API returns. Fields a provider does not report are left zero.
type Model struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name,omitempty"`
	// Created is when the provider says the model was released or created.
	Created *time.Time `json:"created,omitempty"`
	// OwnedBy is the vendor (OpenAI owned_by, Bedrock providerName).
	OwnedBy string `json:"owned_by,omitempty"`
	// Lifecycle is the provider's lifecycle word, verbatim: Anthropic
	// active/deprecated/retired, Bedrock ACTIVE/LEGACY, Cohere "deprecated",
	// Groq "inactive".
	Lifecycle string `json:"lifecycle,omitempty"`
	// Retires is an announced shutdown or retirement date.
	Retires string `json:"retires,omitempty"`
	// Thinking is the provider's own statement on thinking/reasoning
	// support; nil when the API does not say.
	Thinking *bool `json:"thinking,omitempty"`
	// Line is Anthropic's model line (opus, sonnet, haiku, ...).
	Line string `json:"line,omitempty"`
	// Capabilities lists what the API says the model can do: Gemini
	// supportedGenerationMethods, Cohere endpoints and features, Bedrock
	// modalities and inference types, Anthropic capabilities marked
	// supported.
	Capabilities  []string `json:"capabilities,omitempty"`
	ContextWindow int      `json:"context_window,omitempty"`
	MaxOutput     int      `json:"max_output_tokens,omitempty"`
	// AutoIgnore, when set, is why the metadata alone rules the model out
	// (no chat endpoint, embeddings only, deprecated, ...). The registry
	// does not need an entry for it.
	AutoIgnore string `json:"auto_ignore,omitempty"`
	// Variants are other listed IDs of the same model the diff folded into
	// this one: Bedrock inference profiles that differ only in geography.
	Variants []string `json:"variants,omitempty"`
}

// Lister lists one provider's models.
type Lister interface {
	List(ctx context.Context) ([]Model, error)
}

// ListerFunc adapts a function to Lister.
type ListerFunc func(ctx context.Context) ([]Model, error)

// List calls f.
func (f ListerFunc) List(ctx context.Context) ([]Model, error) { return f(ctx) }

// maxPages bounds every paginated listing, so a provider that keeps handing
// back a cursor cannot loop the job forever.
const maxPages = 100

// maxBody bounds a list response.
const maxBody = 32 << 20

// fetcher does the HTTP part every lister shares.
type fetcher struct {
	client  *http.Client
	secrets []string
}

// getJSON GETs u with headers and decodes the 200 body into v. A non-200
// answer is an error carrying the status and the start of the body, with
// every credential redacted.
func (f fetcher) getJSON(ctx context.Context, u string, headers map[string]string, sign func(*http.Request), v any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	for k, val := range headers {
		req.Header.Set(k, val)
	}
	if sign != nil {
		sign(req)
	}
	resp, err := f.client.Do(req)
	if err != nil {
		return f.redactErr(fmt.Errorf("GET %s: %v", redactURL(u), err))
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return f.redactErr(fmt.Errorf("GET %s: reading the body: %v", redactURL(u), err))
	}
	if resp.StatusCode != http.StatusOK {
		snippet := strings.TrimSpace(string(body))
		if len(snippet) > 300 {
			snippet = snippet[:300] + "..."
		}
		return f.redactErr(fmt.Errorf("GET %s: HTTP %d: %s", redactURL(u), resp.StatusCode, snippet))
	}
	if err := json.Unmarshal(body, v); err != nil {
		return fmt.Errorf("GET %s: decoding the body: %v", redactURL(u), err)
	}
	return nil
}

func (f fetcher) redactErr(err error) error {
	msg := err.Error()
	for _, s := range f.secrets {
		if len(s) >= 8 {
			msg = strings.ReplaceAll(msg, s, "REDACTED")
		}
	}
	return fmt.Errorf("%s", msg)
}

// redactURL drops credential query parameters from u for messages.
func redactURL(u string) string {
	p, err := url.Parse(u)
	if err != nil {
		return u
	}
	q := p.Query()
	for _, k := range []string{"key", "api_key", "access_token"} {
		if q.Has(k) {
			q.Set(k, "REDACTED")
		}
	}
	p.RawQuery = q.Encode()
	return p.String()
}

func endpoint(base, path string, query url.Values) string {
	u := strings.TrimRight(base, "/") + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	return u
}

// ---- OpenAI and OpenAI-compatible presets ----------------------------------

// openAIModel is one entry of GET /v1/models. Groq adds active,
// context_window and max_completion_tokens; Mistral adds capabilities,
// deprecation and type (base or fine-tuned); OpenRouter, Together and
// Fireworks add context_length, and OpenRouter expiration_date.
type openAIModel struct {
	ID                  string `json:"id"`
	Created             int64  `json:"created"`
	OwnedBy             string `json:"owned_by"`
	ShutdownDate        string `json:"shutdown_date"`
	Active              *bool  `json:"active"`
	ContextWindow       int    `json:"context_window"`
	ContextLength       int    `json:"context_length"`
	MaxCompletionTokens int    `json:"max_completion_tokens"`
	// Deprecation (Mistral) and ExpirationDate (OpenRouter) announce a
	// retirement date, as OpenAI's shutdown_date does.
	Deprecation    *string `json:"deprecation"`
	ExpirationDate *string `json:"expiration_date"`
	Type           string  `json:"type"`
	// Capabilities is Mistral's object of booleans. Decoded leniently:
	// another provider's differently shaped capabilities must not fail the
	// whole listing.
	Capabilities json.RawMessage `json:"capabilities"`
}

// OpenAILister lists GET {BaseURL}{Path} with a Bearer key: OpenAI's
// /v1/models, or an OpenAI-compatible preset's (Groq: /openai/v1/models).
// The API is not paginated. The body is OpenAI's {"data": [...]}, or a bare
// array of the same objects (Together).
type OpenAILister struct {
	BaseURL string
	Path    string
	APIKey  string
	Client  *http.Client
}

// List implements Lister.
func (l OpenAILister) List(ctx context.Context) ([]Model, error) {
	var raw json.RawMessage
	f := fetcher{client: l.Client, secrets: []string{l.APIKey}}
	u := endpoint(l.BaseURL, l.Path, nil)
	if err := f.getJSON(ctx, u, map[string]string{"Authorization": "Bearer " + l.APIKey}, nil, &raw); err != nil {
		return nil, err
	}
	var data []openAIModel
	if trimmed := strings.TrimSpace(string(raw)); strings.HasPrefix(trimmed, "[") {
		if err := json.Unmarshal(raw, &data); err != nil {
			return nil, fmt.Errorf("GET %s: decoding the body: %v", redactURL(u), err)
		}
	} else {
		var page struct {
			Data []openAIModel `json:"data"`
		}
		if err := json.Unmarshal(raw, &page); err != nil {
			return nil, fmt.Errorf("GET %s: decoding the body: %v", redactURL(u), err)
		}
		data = page.Data
	}
	out := make([]Model, 0, len(data))
	for _, m := range data {
		out = append(out, m.model())
	}
	return out, nil
}

func (m openAIModel) model() Model {
	mm := Model{ID: m.ID, OwnedBy: m.OwnedBy, Retires: m.ShutdownDate, ContextWindow: m.ContextWindow, MaxOutput: m.MaxCompletionTokens}
	if mm.ContextWindow == 0 {
		mm.ContextWindow = m.ContextLength
	}
	for _, d := range []*string{m.Deprecation, m.ExpirationDate} {
		if mm.Retires == "" && d != nil {
			mm.Retires = *d
		}
	}
	if m.Created > 0 {
		t := time.Unix(m.Created, 0).UTC()
		mm.Created = &t
	}
	if m.Type != "" {
		mm.Capabilities = append(mm.Capabilities, "type:"+m.Type)
	}
	var caps map[string]bool
	hasChatCap := false
	if len(m.Capabilities) > 0 && json.Unmarshal(m.Capabilities, &caps) == nil {
		names := make([]string, 0, len(caps))
		for name, on := range caps {
			if on {
				names = append(names, name)
			}
		}
		sort.Strings(names)
		mm.Capabilities = append(mm.Capabilities, names...)
		_, hasChatCap = caps["completion_chat"]
	}
	switch {
	case m.Active != nil && !*m.Active:
		mm.Lifecycle = "inactive"
		mm.AutoIgnore = "listed as inactive (active: false)"
	case mm.Retires != "":
		mm.Lifecycle = "deprecated"
		mm.AutoIgnore = "shutdown announced for " + mm.Retires
	case m.Type == "fine-tuned":
		mm.AutoIgnore = "fine-tuned model (type: fine-tuned): specific to the organisation that owns the key"
	case hasChatCap && !caps["completion_chat"] && !caps["ocr"]:
		// Mistral: embeddings, moderation, classifier and transcription
		// models. The mistral fixtures cover chat and OCR.
		mm.AutoIgnore = "capabilities has neither completion_chat nor ocr"
	}
	return mm
}

// ---- Anthropic ---------------------------------------------------------------

// AnthropicVersion is the anthropic-version header the lister sends.
const AnthropicVersion = "2023-06-01"

type anthropicModel struct {
	ID             string                     `json:"id"`
	DisplayName    string                     `json:"display_name"`
	CreatedAt      string                     `json:"created_at"`
	Lifecycle      string                     `json:"lifecycle"`
	Line           *string                    `json:"line"`
	MaxInputTokens *int                       `json:"max_input_tokens"`
	MaxTokens      *int                       `json:"max_tokens"`
	RetiresAt      *string                    `json:"retires_at"`
	Capabilities   map[string]json.RawMessage `json:"capabilities"`
}

// AnthropicLister lists GET {BaseURL}/v1/models, paginated with after_id.
type AnthropicLister struct {
	BaseURL string
	APIKey  string
	Client  *http.Client
	// PageSize is the limit per page; 0 means 1000, the API's maximum.
	PageSize int
}

// List implements Lister.
func (l AnthropicLister) List(ctx context.Context) ([]Model, error) {
	size := l.PageSize
	if size <= 0 {
		size = 1000
	}
	f := fetcher{client: l.Client, secrets: []string{l.APIKey}}
	headers := map[string]string{"x-api-key": l.APIKey, "anthropic-version": AnthropicVersion}
	var out []Model
	after := ""
	for pages := 0; ; pages++ {
		if pages == maxPages {
			return nil, fmt.Errorf("anthropic: more than %d pages; giving up", maxPages)
		}
		q := url.Values{"limit": {fmt.Sprint(size)}}
		if after != "" {
			q.Set("after_id", after)
		}
		var page struct {
			Data    []anthropicModel `json:"data"`
			HasMore bool             `json:"has_more"`
			LastID  *string          `json:"last_id"`
		}
		if err := f.getJSON(ctx, endpoint(l.BaseURL, "/v1/models", q), headers, nil, &page); err != nil {
			return nil, err
		}
		for _, m := range page.Data {
			out = append(out, m.model())
		}
		if !page.HasMore {
			return out, nil
		}
		if page.LastID == nil || *page.LastID == "" || *page.LastID == after {
			return nil, fmt.Errorf("anthropic: has_more is true but last_id does not advance (%v)", page.LastID)
		}
		after = *page.LastID
	}
}

func (m anthropicModel) model() Model {
	mm := Model{ID: m.ID, DisplayName: m.DisplayName, Lifecycle: m.Lifecycle}
	if t, err := time.Parse(time.RFC3339, m.CreatedAt); err == nil && t.Year() > 1970 {
		t = t.UTC()
		mm.Created = &t
	}
	if m.Line != nil {
		mm.Line = *m.Line
	}
	if m.MaxInputTokens != nil {
		mm.ContextWindow = *m.MaxInputTokens
	}
	if m.MaxTokens != nil {
		mm.MaxOutput = *m.MaxTokens
	}
	if m.RetiresAt != nil {
		mm.Retires = *m.RetiresAt
	}
	// Every capability is an object with "supported"; keep the supported
	// ones, and thinking's value either way.
	names := make([]string, 0, len(m.Capabilities))
	for name := range m.Capabilities {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		var c struct {
			Supported *bool `json:"supported"`
		}
		if json.Unmarshal(m.Capabilities[name], &c) != nil || c.Supported == nil {
			continue
		}
		if name == "thinking" {
			v := *c.Supported
			mm.Thinking = &v
		}
		if *c.Supported {
			mm.Capabilities = append(mm.Capabilities, name)
		}
	}
	switch m.Lifecycle {
	case "deprecated":
		mm.AutoIgnore = "lifecycle deprecated: closed to new adopters"
		if mm.Retires != "" {
			mm.AutoIgnore += ", retires " + mm.Retires
		}
	case "retired":
		mm.AutoIgnore = "lifecycle retired"
	}
	return mm
}

// ---- Gemini (Google AI Studio) -----------------------------------------------

type geminiModel struct {
	Name                       string   `json:"name"`
	BaseModelID                string   `json:"baseModelId"`
	Version                    string   `json:"version"`
	DisplayName                string   `json:"displayName"`
	InputTokenLimit            int      `json:"inputTokenLimit"`
	OutputTokenLimit           int      `json:"outputTokenLimit"`
	SupportedGenerationMethods []string `json:"supportedGenerationMethods"`
	Thinking                   *bool    `json:"thinking"`
}

// GeminiLister lists GET {BaseURL}/v1beta/models, paginated with pageToken.
type GeminiLister struct {
	BaseURL  string
	APIKey   string
	Client   *http.Client
	PageSize int // 0 means 1000, the API's maximum
}

// List implements Lister.
func (l GeminiLister) List(ctx context.Context) ([]Model, error) {
	size := l.PageSize
	if size <= 0 {
		size = 1000
	}
	f := fetcher{client: l.Client, secrets: []string{l.APIKey}}
	headers := map[string]string{"x-goog-api-key": l.APIKey}
	var out []Model
	token := ""
	seen := map[string]bool{}
	for pages := 0; ; pages++ {
		if pages == maxPages {
			return nil, fmt.Errorf("gemini: more than %d pages; giving up", maxPages)
		}
		q := url.Values{"pageSize": {fmt.Sprint(size)}}
		if token != "" {
			q.Set("pageToken", token)
		}
		var page struct {
			Models        []geminiModel `json:"models"`
			NextPageToken string        `json:"nextPageToken"`
		}
		if err := f.getJSON(ctx, endpoint(l.BaseURL, "/v1beta/models", q), headers, nil, &page); err != nil {
			return nil, err
		}
		for _, m := range page.Models {
			mm := Model{
				ID:            strings.TrimPrefix(m.Name, "models/"),
				DisplayName:   m.DisplayName,
				Thinking:      m.Thinking,
				Capabilities:  append([]string(nil), m.SupportedGenerationMethods...),
				ContextWindow: m.InputTokenLimit,
				MaxOutput:     m.OutputTokenLimit,
			}
			// The adapter calls generateContent (chat, speech) and
			// embedContent (embeddings); a model with neither (Imagen's
			// predict, Veo, AQA, Live-only) has nothing it parses.
			if !contains(m.SupportedGenerationMethods, "generateContent") && !contains(m.SupportedGenerationMethods, "embedContent") {
				mm.AutoIgnore = fmt.Sprintf("supportedGenerationMethods has neither generateContent nor embedContent %v", m.SupportedGenerationMethods)
			}
			out = append(out, mm)
		}
		if page.NextPageToken == "" {
			return out, nil
		}
		if seen[page.NextPageToken] {
			return nil, fmt.Errorf("gemini: nextPageToken repeats; giving up")
		}
		seen[page.NextPageToken] = true
		token = page.NextPageToken
	}
}

// ---- Cohere --------------------------------------------------------------------

type cohereModel struct {
	Name          string   `json:"name"`
	IsDeprecated  bool     `json:"is_deprecated"`
	Endpoints     []string `json:"endpoints"`
	Finetuned     bool     `json:"finetuned"`
	ContextLength float64  `json:"context_length"`
	Features      []string `json:"features"`
}

// CohereLister lists GET {BaseURL}/v1/models, paginated with page_token.
type CohereLister struct {
	BaseURL  string
	APIKey   string
	Client   *http.Client
	PageSize int // 0 means 1000, the API's maximum
}

// List implements Lister.
func (l CohereLister) List(ctx context.Context) ([]Model, error) {
	size := l.PageSize
	if size <= 0 {
		size = 1000
	}
	f := fetcher{client: l.Client, secrets: []string{l.APIKey}}
	headers := map[string]string{"Authorization": "Bearer " + l.APIKey}
	var out []Model
	token := ""
	seen := map[string]bool{}
	for pages := 0; ; pages++ {
		if pages == maxPages {
			return nil, fmt.Errorf("cohere: more than %d pages; giving up", maxPages)
		}
		q := url.Values{"page_size": {fmt.Sprint(size)}}
		if token != "" {
			q.Set("page_token", token)
		}
		var page struct {
			Models        []cohereModel `json:"models"`
			NextPageToken string        `json:"next_page_token"`
		}
		if err := f.getJSON(ctx, endpoint(l.BaseURL, "/v1/models", q), headers, nil, &page); err != nil {
			return nil, err
		}
		for _, m := range page.Models {
			mm := Model{ID: m.Name, ContextWindow: int(m.ContextLength)}
			for _, e := range m.Endpoints {
				mm.Capabilities = append(mm.Capabilities, "endpoint:"+e)
			}
			for _, feat := range m.Features {
				mm.Capabilities = append(mm.Capabilities, "feature:"+feat)
				// Cohere does not document the feature vocabulary; a
				// reasoning feature is taken as a yes, its absence as
				// "unknown", not as a no.
				if feat == "reasoning" || feat == "thinking" {
					yes := true
					mm.Thinking = &yes
				}
			}
			switch {
			case m.Finetuned:
				mm.AutoIgnore = "fine-tuned model (specific to one organisation)"
			case !contains(m.Endpoints, "chat") && !contains(m.Endpoints, "embed") && !contains(m.Endpoints, "rerank"):
				// The adapter serves chat, embed and rerank.
				mm.AutoIgnore = fmt.Sprintf("endpoints has none of chat, embed, rerank %v", m.Endpoints)
			case m.IsDeprecated:
				mm.Lifecycle = "deprecated"
				mm.AutoIgnore = "is_deprecated: true"
			}
			out = append(out, mm)
		}
		if page.NextPageToken == "" {
			return out, nil
		}
		if seen[page.NextPageToken] {
			return nil, fmt.Errorf("cohere: next_page_token repeats; giving up")
		}
		seen[page.NextPageToken] = true
		token = page.NextPageToken
	}
}

// ---- Static ---------------------------------------------------------------------

// StaticLister returns a fixed list: for a provider with no list API, the
// models a person copied from its docs.
type StaticLister struct{ Models []string }

// List implements Lister.
func (l StaticLister) List(context.Context) ([]Model, error) {
	out := make([]Model, 0, len(l.Models))
	for _, id := range l.Models {
		out = append(out, Model{ID: id})
	}
	return out, nil
}
