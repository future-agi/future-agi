package validation

// The validation plugin applies an org's Model Database
// overrides (Settings > Org Config > Model Database): token limits and
// capability switches, read through the tenant store (SetTenantStore).

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const mdbOrg = "org-mdb"

// newOrgPlugin is the validation plugin over buildTestDB with mdbOrg's overrides
// (raw is the org config's JSON, as Django pushes it).
func newOrgPlugin(t *testing.T, raw string) *Plugin {
	t.Helper()
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatalf("org config: %v", err)
	}
	store := tenant.NewStore()
	store.Set(mdbOrg, &cfg)
	db := buildTestDB()
	p := New(true, func() *modeldb.ModelDB { return db })
	p.SetTenantStore(store)
	return p
}

func orgRC(model string, req *models.ChatCompletionRequest) *models.RequestContext {
	rc := newRC(model, req)
	rc.Provider = "openai"
	rc.Metadata[tenant.MetadataKeyOrgID] = mdbOrg
	return rc
}

func chatReq(mutate func(*models.ChatCompletionRequest)) *models.ChatCompletionRequest {
	r := simpleRequest()
	if mutate != nil {
		mutate(r)
	}
	return r
}

func intp(n int) *int { return &n }

func wantRefused(t *testing.T, res pipeline.PluginResult, status int, code string, mentions ...string) {
	t.Helper()
	if res.Action != pipeline.ShortCircuit || res.Error == nil {
		t.Fatalf("result = %+v, want a refusal %d %s", res, status, code)
	}
	if res.Error.Status != status || res.Error.Code != code {
		t.Fatalf("refused %d %s (%s), want %d %s", res.Error.Status, res.Error.Code, res.Error.Message, status, code)
	}
	for _, m := range mentions {
		if !strings.Contains(res.Error.Message, m) {
			t.Fatalf("message %q does not name %q", res.Error.Message, m)
		}
	}
}

func wantContinue(t *testing.T, res pipeline.PluginResult) {
	t.Helper()
	if res.Action != pipeline.Continue || res.Error != nil {
		t.Fatalf("result = action %v, error %+v; want continue", res.Action, res.Error)
	}
}

// TestModelDBLimitsMaxOutput: max_output_tokens refuses a request asking
// for more with 400 naming the limit, and is sent as max_tokens when the
// request sets none.
func TestModelDBLimitsMaxOutput(t *testing.T) {
	p := newOrgPlugin(t, `{"model_database":{"overrides":{"test-model":{"max_output_tokens":5}}}}`)
	ctx := context.Background()

	res := p.ProcessRequest(ctx, orgRC("test-model", chatReq(func(r *models.ChatCompletionRequest) { r.MaxTokens = intp(500) })))
	wantRefused(t, res, http.StatusBadRequest, "max_tokens_exceeds_limit", "max_tokens 500", "max_output_tokens 5")
	if res.Error.Param == nil || *res.Error.Param != "max_tokens" {
		t.Fatalf("param = %v, want max_tokens", res.Error.Param)
	}

	res = p.ProcessRequest(ctx, orgRC("test-model", chatReq(func(r *models.ChatCompletionRequest) { r.MaxCompletionTokens = intp(6) })))
	wantRefused(t, res, http.StatusBadRequest, "max_tokens_exceeds_limit", "max_completion_tokens 6", "max_output_tokens 5")

	rc := orgRC("test-model", chatReq(func(r *models.ChatCompletionRequest) { r.MaxTokens = intp(5) }))
	wantContinue(t, p.ProcessRequest(ctx, rc))
	if *rc.Request.MaxTokens != 5 {
		t.Fatalf("max_tokens = %d, want the 5 the request asked for", *rc.Request.MaxTokens)
	}

	rc = orgRC("test-model", chatReq(func(r *models.ChatCompletionRequest) { r.MaxCompletionTokens = intp(3) }))
	wantContinue(t, p.ProcessRequest(ctx, rc))
	if rc.Request.MaxTokens != nil || *rc.Request.MaxCompletionTokens != 3 {
		t.Fatalf("a request within the limit was changed: max_tokens %v, max_completion_tokens %d", rc.Request.MaxTokens, *rc.Request.MaxCompletionTokens)
	}

	rc = orgRC("test-model", chatReq(nil))
	wantContinue(t, p.ProcessRequest(ctx, rc))
	if rc.Request.MaxTokens == nil || *rc.Request.MaxTokens != 5 {
		t.Fatalf("a request without max_tokens is sent with max_tokens %v, want the org's limit 5", rc.Request.MaxTokens)
	}
	if rc.Metadata["model_max_output_tokens"] != "5" {
		t.Fatalf("model_max_output_tokens = %q, want the org's 5", rc.Metadata["model_max_output_tokens"])
	}

	// Without the org (another org, or no org) the database's 2,048 stands
	// and nothing is refused or set.
	other := newRC("test-model", chatReq(func(r *models.ChatCompletionRequest) { r.MaxTokens = intp(500) }))
	other.Metadata[tenant.MetadataKeyOrgID] = "org-other"
	wantContinue(t, p.ProcessRequest(ctx, other))
	plain := newRC("test-model", chatReq(nil))
	wantContinue(t, p.ProcessRequest(ctx, plain))
	if plain.Request.MaxTokens != nil || plain.Metadata["model_max_output_tokens"] != "2048" {
		t.Fatalf("no-org request: max_tokens %v, model_max_output_tokens %q", plain.Request.MaxTokens, plain.Metadata["model_max_output_tokens"])
	}
}

// TestModelDBLimitsMaxInput: max_input_tokens refuses an input estimated
// above it with 413 naming the limit, at the limit itself (the database's own
// limit keeps its 1.5x margin), and an org may raise its own limit.
func TestModelDBLimitsMaxInput(t *testing.T) {
	ctx := context.Background()
	long := func(chars int) *models.ChatCompletionRequest {
		b, _ := json.Marshal(strings.Repeat("a", chars))
		return &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: b}}}
	}

	p := newOrgPlugin(t, `{"model_database":{"overrides":{"test-model":{"max_input_tokens":100}}}}`)
	// 480 characters + "user" + 4 = 488 / 4 = 122 estimated tokens > 100.
	wantRefused(t, p.ProcessRequest(ctx, orgRC("test-model", long(480))), http.StatusRequestEntityTooLarge,
		"input_too_large", "about 122 tokens", "max_input_tokens 100")
	// 360 characters: 92 tokens, within 100.
	wantContinue(t, p.ProcessRequest(ctx, orgRC("test-model", long(360))))

	// Raised above the database's 4,096: 6,400 estimated tokens is past the
	// database's 1.5x margin (6,144) but within the org's 10,000.
	raised := newOrgPlugin(t, `{"model_database":{"overrides":{"test-model":{"max_input_tokens":10000}}}}`)
	wantContinue(t, raised.ProcessRequest(ctx, orgRC("test-model", long(25592))))
	db := buildTestDB()
	wantRefused(t, New(true, func() *modeldb.ModelDB { return db }).ProcessRequest(ctx, newRC("test-model", long(25592))),
		http.StatusRequestEntityTooLarge, "input_too_large")

	// A model the database does not know still gets the org's limit.
	unknown := newOrgPlugin(t, `{"model_database":{"overrides":{"my-llama-70b":{"max_input_tokens":100}}}}`)
	wantRefused(t, unknown.ProcessRequest(ctx, orgRC("my-llama-70b", long(480))), http.StatusRequestEntityTooLarge,
		"input_too_large", "max_input_tokens 100", `"my-llama-70b"`)
}

// TestModelDBCapabilitiesSwitchesOff: every switch the org turns off
// refuses a request using that capability with 400 model_capability_error
// naming the switch; the same request passes when the switch is not set.
func TestModelDBCapabilitiesSwitchesOff(t *testing.T) {
	ctx := context.Background()
	img := json.RawMessage(`[{"type":"text","text":"see"},{"type":"image_url","image_url":{"url":"https://example.invalid/a.png"}}]`)
	audio := json.RawMessage(`[{"type":"input_audio","input_audio":{"data":"AAAA","format":"wav"}}]`)
	file := json.RawMessage(`[{"type":"file","file":{"file_id":"file-1"}}]`)
	cases := []struct {
		name  string
		model string // test-vision-model supports vision and json_schema in the database
		use   func(*models.ChatCompletionRequest)
	}{
		{"function_calling", "test-model", func(r *models.ChatCompletionRequest) {
			r.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "f"}}}
		}},
		{"parallel_tool_calls", "test-model", func(r *models.ChatCompletionRequest) {
			r.Extra = map[string]json.RawMessage{"parallel_tool_calls": json.RawMessage(`true`)}
		}},
		{"vision", "test-vision-model", func(r *models.ChatCompletionRequest) { r.Messages[0].Content = img }},
		{"audio_input", "test-model", func(r *models.ChatCompletionRequest) { r.Messages[0].Content = audio }},
		{"audio_output", "test-model", func(r *models.ChatCompletionRequest) { r.Modalities = []string{"text", "audio"} }},
		{"pdf_input", "test-model", func(r *models.ChatCompletionRequest) { r.Messages[0].Content = file }},
		{"streaming", "test-model", func(r *models.ChatCompletionRequest) { r.Stream = true }},
		{"response_schema", "test-vision-model", func(r *models.ChatCompletionRequest) {
			r.ResponseFormat = &models.ResponseFormat{Type: "json_schema"}
		}},
		{"system_messages", "test-model", func(r *models.ChatCompletionRequest) {
			r.Messages = append([]models.Message{{Role: "system", Content: json.RawMessage(`"be brief"`)}}, r.Messages...)
		}},
		{"reasoning", "test-model", func(r *models.ChatCompletionRequest) {
			r.Extra = map[string]json.RawMessage{"reasoning_effort": json.RawMessage(`"high"`)}
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			off := newOrgPlugin(t, `{"model_database":{"overrides":{"`+c.model+`":{"capabilities":{"`+c.name+`":false}}}}}`)
			wantRefused(t, off.ProcessRequest(ctx, orgRC(c.model, chatReq(c.use))), http.StatusBadRequest,
				"model_capability_error", "turns off "+c.name, `"`+c.model+`"`)
			// The plain request is not refused by the switch.
			wantContinue(t, off.ProcessRequest(ctx, orgRC(c.model, chatReq(nil))))
			// Not set (another switch set): the request passes as before.
			unset := newOrgPlugin(t, `{"model_database":{"overrides":{"`+c.model+`":{"capabilities":{"prompt_caching":false}}}}}`)
			res := unset.ProcessRequest(ctx, orgRC(c.model, chatReq(c.use)))
			if res.Error != nil && strings.Contains(res.Error.Message, "turns off") {
				t.Fatalf("an unset switch refused: %s", res.Error.Message)
			}
		})
	}
}

// TestModelDBCapabilitiesSwitchOnAndMatching: a switch turned on lets
// through what the database refuses; an override keyed by the bare model
// applies to a provider-prefixed request and to a model the database does
// not know; without the tenant store nothing of the org's applies.
func TestModelDBCapabilitiesSwitchOnAndMatching(t *testing.T) {
	ctx := context.Background()
	img := json.RawMessage(`[{"type":"image_url","image_url":{"url":"https://example.invalid/a.png"}}]`)
	withImage := func(r *models.ChatCompletionRequest) { r.Messages[0].Content = img }
	withTools := func(r *models.ChatCompletionRequest) {
		r.Tools = []models.Tool{{Type: "function", Function: models.ToolFunction{Name: "f"}}}
	}

	// test-model has no vision in the database: refused without the org...
	db := buildTestDB()
	plain := New(true, func() *modeldb.ModelDB { return db })
	wantRefused(t, plain.ProcessRequest(ctx, orgRC("test-model", chatReq(withImage))), http.StatusBadRequest, "model_capability_error")
	// ...and allowed for the org that turned vision on.
	on := newOrgPlugin(t, `{"model_database":{"overrides":{"test-model":{"capabilities":{"vision":true}}}}}`)
	wantContinue(t, on.ProcessRequest(ctx, orgRC("test-model", chatReq(withImage))))

	off := newOrgPlugin(t, `{"model_database":{"overrides":{"test-model":{"capabilities":{"function_calling":false}}}}}`)
	wantRefused(t, off.ProcessRequest(ctx, orgRC("openai/test-model", chatReq(withTools))), http.StatusBadRequest,
		"model_capability_error", "turns off function_calling")

	unknown := newOrgPlugin(t, `{"model_database":{"overrides":{"my-own":{"capabilities":{"function_calling":false}}}}}`)
	wantRefused(t, unknown.ProcessRequest(ctx, orgRC("my-own", chatReq(withTools))), http.StatusBadRequest,
		"model_capability_error", "turns off function_calling", `"my-own"`)

	// SetTenantStore is what makes the org's view apply.
	var cfg tenant.OrgConfig
	_ = json.Unmarshal([]byte(`{"model_database":{"overrides":{"test-model":{"capabilities":{"function_calling":false},"max_output_tokens":5}}}}`), &cfg)
	store := tenant.NewStore()
	store.Set(mdbOrg, &cfg)
	noStore := New(true, func() *modeldb.ModelDB { return db })
	wantContinue(t, noStore.ProcessRequest(ctx, orgRC("test-model", chatReq(withTools))))
	noStore.SetTenantStore(store)
	wantRefused(t, noStore.ProcessRequest(ctx, orgRC("test-model", chatReq(withTools))), http.StatusBadRequest,
		"model_capability_error", "turns off function_calling")
}
