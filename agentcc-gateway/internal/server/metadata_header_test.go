package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	anthropictrans "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
	geminitrans "github.com/futureagi/agentcc-gateway/internal/translation/gemini"
)

func TestParseMetadataHeader(t *testing.T) {
	tests := []struct {
		name       string
		header     string
		wantMeta   map[string]string
		wantCustom []string
	}{
		{
			name:       "caller keys are stored and recorded",
			header:     `{"profile_id":"milestone-p1","business_id":"biz-42"}`,
			wantMeta:   map[string]string{"profile_id": "milestone-p1", "business_id": "biz-42"},
			wantCustom: []string{"business_id", "profile_id"},
		},
		{
			name:       "reserved keys are rejected, not recorded",
			header:     `{"cost":"0","org_id":"evil","auth_key_id":"evil","client_ip":"1.2.3.4","tenant":"ok"}`,
			wantMeta:   map[string]string{"tenant": "ok"},
			wantCustom: []string{"tenant"},
		},
		{
			name:       "malformed json is ignored",
			header:     `not json`,
			wantMeta:   map[string]string{},
			wantCustom: nil,
		},
		{
			// Strict string-only decoding threw the whole object away over one
			// numeric value, losing every dimension the caller sent.
			name:       "non-string values keep their JSON text",
			header:     `{"depth":3,"beta":true,"tenant":"ok"}`,
			wantMeta:   map[string]string{"depth": "3", "beta": "true", "tenant": "ok"},
			wantCustom: []string{"beta", "depth", "tenant"},
		},
		{
			name:       "a non-object body is ignored",
			header:     `["a","b"]`,
			wantMeta:   map[string]string{},
			wantCustom: nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rc := &models.RequestContext{Metadata: map[string]string{}}
			parseMetadataHeader(tt.header, rc)

			if len(rc.Metadata) != len(tt.wantMeta) {
				t.Fatalf("Metadata = %v, want %v", rc.Metadata, tt.wantMeta)
			}
			for k, v := range tt.wantMeta {
				if rc.Metadata[k] != v {
					t.Errorf("Metadata[%q] = %q, want %q", k, rc.Metadata[k], v)
				}
			}

			got := append([]string(nil), rc.CustomMetadataKeys...)
			sort.Strings(got)
			if len(got) != len(tt.wantCustom) {
				t.Fatalf("CustomMetadataKeys = %v, want %v", got, tt.wantCustom)
			}
			for i, k := range tt.wantCustom {
				if got[i] != k {
					t.Errorf("CustomMetadataKeys[%d] = %q, want %q", i, got[i], k)
				}
			}
		})
	}
}

// A blocked key must not become a telemetry dimension either — CustomMetadataKeys
// is what the OTel plugin exports, so recording one would re-open the injection
// the blocklist exists to prevent.
func TestParseMetadataHeaderDoesNotRecordBlockedKeys(t *testing.T) {
	rc := &models.RequestContext{Metadata: map[string]string{"cost": "0.5"}}
	parseMetadataHeader(`{"cost":"0.0"}`, rc)

	if rc.Metadata["cost"] != "0.5" {
		t.Errorf("caller overwrote a plugin-owned key: cost = %q", rc.Metadata["cost"])
	}
	if len(rc.CustomMetadataKeys) != 0 {
		t.Errorf("CustomMetadataKeys = %v, want empty", rc.CustomMetadataKeys)
	}
}

// The gateway's stand-in for span.SetAttribute() has two channels. Both must
// land on the same context, and the header must win: it is set by the calling
// infrastructure, which the payload it forwards should not be able to override.
func TestApplyCallerMetadata(t *testing.T) {
	tests := []struct {
		name     string
		header   string
		body     string
		wantMeta map[string]string
	}{
		{
			name:     "header only",
			header:   `{"tenant":"acme"}`,
			wantMeta: map[string]string{"tenant": "acme"},
		},
		{
			name:     "body metadata field only",
			body:     `{"tenant":"acme"}`,
			wantMeta: map[string]string{"tenant": "acme"},
		},
		{
			name:     "both channels merge",
			header:   `{"tenant":"acme"}`,
			body:     `{"workflow":"auto-reply"}`,
			wantMeta: map[string]string{"tenant": "acme", "workflow": "auto-reply"},
		},
		{
			name:     "header wins on conflict",
			header:   `{"tenant":"acme"}`,
			body:     `{"tenant":"spoofed"}`,
			wantMeta: map[string]string{"tenant": "acme"},
		},
		{
			name:     "body cannot set a reserved key either",
			body:     `{"org_id":"evil","tenant":"acme"}`,
			wantMeta: map[string]string{"tenant": "acme"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
			if tt.header != "" {
				r.Header.Set("x-agentcc-metadata", tt.header)
			}
			var body json.RawMessage
			if tt.body != "" {
				body = json.RawMessage(tt.body)
			}

			rc := &models.RequestContext{Metadata: map[string]string{}}
			applyCallerMetadata(rc, r, body)

			if len(rc.Metadata) != len(tt.wantMeta) {
				t.Fatalf("Metadata = %v, want %v", rc.Metadata, tt.wantMeta)
			}
			for k, v := range tt.wantMeta {
				if rc.Metadata[k] != v {
					t.Errorf("Metadata[%q] = %q, want %q", k, rc.Metadata[k], v)
				}
			}
		})
	}
}

// A key set by both channels must be recorded once, or it counts twice against
// the export cap and appears twice while the blob is built.
func TestApplyCallerMetadataRecordsEachKeyOnce(t *testing.T) {
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	r.Header.Set("x-agentcc-metadata", `{"tenant":"acme"}`)

	rc := &models.RequestContext{Metadata: map[string]string{}}
	applyCallerMetadata(rc, r, json.RawMessage(`{"tenant":"spoofed"}`))

	if len(rc.CustomMetadataKeys) != 1 {
		t.Errorf("CustomMetadataKeys = %v, want one entry", rc.CustomMetadataKeys)
	}
}

// Body fields the request struct has no field for. Not exotic: every OpenAI
// parameter newer than the `known` list in UnmarshalJSON arrives this way.
func TestApplyCallerExtras(t *testing.T) {
	tests := []struct {
		name        string
		body        string
		wantExtras  map[string]any
		wantDropped int
	}{
		{
			name:       "scalars keep their types",
			body:       `{"reasoning_effort":"high","parallel_tool_calls":true,"routing_weight":3,"threshold":0.75}`,
			wantExtras: map[string]any{"reasoning_effort": "high", "parallel_tool_calls": true, "routing_weight": int64(3), "threshold": 0.75},
		},
		{
			name:        "nested objects and arrays are skipped, not stringified",
			body:        `{"routing_hint":{"pool":"east"},"tags":["a","b"],"nothing":null,"ok":"yes"}`,
			wantExtras:  map[string]any{"ok": "yes"},
			wantDropped: 3,
		},
		{
			// metadata is the curated dimension channel; it has its own path.
			name:       "metadata is not duplicated into extras",
			body:       `{"metadata":{"tenant":"acme"},"store":false}`,
			wantExtras: map[string]any{"store": false},
		},
		{
			name:        "credential-shaped keys are dropped",
			body:        `{"vertex_credentials":"blob","x_auth_token":"t","upstream_secret":"s","store":true}`,
			wantExtras:  map[string]any{"store": true},
			wantDropped: 3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var req models.ChatCompletionRequest
			if err := json.Unmarshal([]byte(tt.body), &req); err != nil {
				t.Fatalf("unmarshal: %v", err)
			}

			rc := &models.RequestContext{Metadata: map[string]string{}}
			applyCallerExtras(rc, req.Extra)

			if len(rc.CallerExtras) != len(tt.wantExtras) {
				t.Fatalf("CallerExtras = %v, want %v", rc.CallerExtras, tt.wantExtras)
			}
			for k, v := range tt.wantExtras {
				if rc.CallerExtras[k] != v {
					t.Errorf("CallerExtras[%q] = %#v, want %#v", k, rc.CallerExtras[k], v)
				}
			}
			if rc.CallerExtrasDropped != tt.wantDropped {
				t.Errorf("CallerExtrasDropped = %d, want %d", rc.CallerExtrasDropped, tt.wantDropped)
			}
		})
	}
}

// Guards the claim that decides the default: these are ordinary OpenAI
// parameters, not exotic passthrough, and the request struct has no field for
// any of them. If one is ever promoted to a real field, this test says so.
func TestModernOpenAIParamsLandInExtra(t *testing.T) {
	var req models.ChatCompletionRequest
	body := `{"model":"gpt-4o","messages":[],"reasoning_effort":"high",
	          "parallel_tool_calls":true,"store":true,"web_search_options":1}`
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	for _, k := range []string{"reasoning_effort", "parallel_tool_calls", "store", "web_search_options"} {
		if _, ok := req.Extra[k]; !ok {
			t.Errorf("%q is no longer captured in Extra — give it a body-attribute path of its own", k)
		}
	}
}

// The reserved-key list is a prefix match, and both halves of the cost family
// were wrong: "cost" as a prefix swallowed ordinary business dimensions, while
// "credit_" missed the keys the credits plugin actually writes.
func TestIsBlockedMetadataKeyCostFamily(t *testing.T) {
	blocked := []string{
		"cost", "cost_source", "cost_SOURCE",
		"credits_used", "credits_remaining", "credit_balance",
		"budget_remaining", "ratelimit_limit", "org_id", "authorization",
	}
	for _, k := range blocked {
		if !isBlockedMetadataKey(k) {
			t.Errorf("%q is writable by callers but plugins own it", k)
		}
	}

	allowed := []string{"cost_center", "cost_code", "costing_model", "customer_id"}
	for _, k := range allowed {
		if isBlockedMetadataKey(k) {
			t.Errorf("%q is a caller dimension and must not be reserved", k)
		}
	}
}

// The Anthropic and Gemini endpoints never unmarshal the body into a canonical
// struct on their native pass-through path, so extras are read from the raw
// bytes against each dialect's own field set.
func TestApplyCallerExtrasFromBody(t *testing.T) {
	tests := []struct {
		name       string
		body       string
		known      map[string]struct{}
		wantExtras map[string]any
	}{
		{
			name: "anthropic spec fields are not reported as caller additions",
			body: `{"model":"claude-3","messages":[],"max_tokens":100,"top_k":5,
			        "thinking":{"type":"enabled"},"stop_sequences":["x"],
			        "tenant":"acme","routing_weight":3}`,
			known:      anthropictrans.KnownRequestFields(),
			wantExtras: map[string]any{"tenant": "acme", "routing_weight": int64(3)},
		},
		{
			name: "gemini spec fields are not reported as caller additions",
			body: `{"contents":[],"generationConfig":{"temperature":1},
			        "safetySettings":[],"cachedContent":"c","systemInstruction":{},
			        "tenant":"acme","beta_flag":true}`,
			known:      geminitrans.KnownRequestFields(),
			wantExtras: map[string]any{"tenant": "acme", "beta_flag": true},
		},
		{
			name:       "a malformed body is ignored",
			body:       `not json`,
			known:      anthropictrans.KnownRequestFields(),
			wantExtras: map[string]any{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rc := &models.RequestContext{Metadata: map[string]string{}}
			applyCallerExtrasFromBody(rc, []byte(tt.body), tt.known)

			if len(rc.CallerExtras) != len(tt.wantExtras) {
				t.Fatalf("CallerExtras = %v, want %v", rc.CallerExtras, tt.wantExtras)
			}
			for k, v := range tt.wantExtras {
				if rc.CallerExtras[k] != v {
					t.Errorf("CallerExtras[%q] = %#v, want %#v", k, rc.CallerExtras[k], v)
				}
			}
		})
	}
}

// The field sets come from the structs themselves, so adding a field to a
// dialect's request type cannot silently start reporting it as caller data.
func TestKnownRequestFieldsCoverEachDialect(t *testing.T) {
	anth := anthropictrans.KnownRequestFields()
	for _, f := range []string{"model", "messages", "max_tokens", "system", "tools",
		"tool_choice", "stream", "temperature", "top_p", "top_k", "stop_sequences",
		"thinking", "metadata"} {
		if _, ok := anth[f]; !ok {
			t.Errorf("anthropic field %q missing from KnownRequestFields", f)
		}
	}

	gem := geminitrans.KnownRequestFields()
	for _, f := range []string{"contents", "systemInstruction", "generationConfig",
		"tools", "toolConfig", "safetySettings", "cachedContent"} {
		if _, ok := gem[f]; !ok {
			t.Errorf("gemini field %q missing from KnownRequestFields", f)
		}
	}
}

// The credential filter over-blocks on purpose — a missing attribute costs
// nothing, a leaked one costs a lot — but it must not swallow ordinary words
// that merely start the same way.
func TestIsCredentialShapedKey(t *testing.T) {
	dropped := []string{
		"vertex_credentials", "api_key", "apiKey", "auth_token", "auth-token",
		"authtoken", "Authorization", "upstream_secret", "user_password",
		"private_key", "bearer_token", "request_signature",
	}
	for _, k := range dropped {
		if !isCredentialShapedKey(k) {
			t.Errorf("%q reaches the span but looks like a credential", k)
		}
	}

	kept := []string{"author", "authority", "author_id", "authored_at", "routing_weight", "store"}
	for _, k := range kept {
		if isCredentialShapedKey(k) {
			t.Errorf("%q is an ordinary field and must not be dropped", k)
		}
	}
}

// The dialect endpoints capture caller extras from their own raw body bytes,
// since they never build a canonical request the OTel plugin could read them
// from. That capture only reaches a span if it happens before the pipeline
// runs — TH-7619 wired h.engine.Process onto these two handlers, so the calls
// that were inert when they were written are now live.
//
// Asserted against the source for the same reason TestBillableHandlersRunThePipeline
// is: the failure mode is an omission or a reordering, not a wrong value.
func TestDialectHandlersCaptureCallerExtrasBeforeThePipeline(t *testing.T) {
	for _, file := range []string{"handlers_anthropic.go", "handlers_genai.go"} {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		text := string(src)

		capture := strings.Index(text, "applyCallerExtrasFromBody(")
		if capture < 0 {
			t.Errorf("%s never calls applyCallerExtrasFromBody — caller body fields "+
				"reach no span, and this path builds no canonical request to recover them from", file)
			continue
		}
		process := strings.Index(text, "h.engine.Process(")
		if process < 0 {
			t.Errorf("%s never calls h.engine.Process", file)
			continue
		}
		if capture > process {
			t.Errorf("%s captures caller extras after the pipeline runs; the OTel "+
				"plugin reads rc.CallerExtras inside it, so they would be exported empty", file)
		}
	}
}
