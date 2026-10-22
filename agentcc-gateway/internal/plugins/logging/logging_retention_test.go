package logging

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The plugin's content mode decides what its webhook
// entry and its trace line keep. A chat request resolved the mode at step 5
// (rc.Privacy); every other endpoint arrives unresolved, and the plugin
// resolves it from the same sources, after the auth plugin copied the key's
// log_content to key_log_content.

const contentOrg = "org-content"

// The caller's error text, which no mode but full may keep.
const callerText = "upstream said: <the user's prompt>"

// A guardrail message naming a tool, which none may not keep.
const guardrailText = "tool permission deny violation: transfer_funds"

// contentModePlugin is a logging plugin that keeps bodies, with orgs from
// tenants, sending to a recorder.
func contentModePlugin(t *testing.T, tenants *tenant.Store) (*Plugin, *LogFlusher, *batchRecorder) {
	t.Helper()
	wh := newBatchRecorder(t)
	f := NewLogFlusher(wh.URL, "", time.Hour, 100)
	p := New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: true, BufferSize: 16, Workers: 1}, tenants)
	t.Cleanup(p.Close)
	p.SetFlusher(f)
	return p, f, wh
}

// contentRequest is a request with bodies, headers, a failed attempt with
// error text, a caller metadata key and a gateway one.
func contentRequest(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = "req_content"
	rc.Model, rc.Provider = "gpt-4o", "openai"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"my secret prompt"`)}}}
	rc.Response = &models.ChatCompletionResponse{Model: "gpt-4o"}
	rc.RequestHeaders = http.Header{"X-Title": {"Deep Research"}}
	rc.Metadata[tenant.MetadataKeyOrgID] = contentOrg
	rc.Metadata["team"] = "caller-dimension"
	rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, "team")
	rc.Metadata["outcome_class"] = "ok"
	rc.Errors = append(rc.Errors, &models.APIError{Status: http.StatusBadGateway, Code: "upstream_error", Message: callerText})
	rc.Flags.GuardrailTriggered = true
	rc.GuardrailResults = append(rc.GuardrailResults, models.GuardrailResult{
		Name: "tool-permissions", Score: 1, Threshold: 0.5, Action: "log", Message: guardrailText})
	// A warn-action guardrail's message, as the guardrail plugin joins it
	// into gateway-owned metadata.
	rc.Metadata["guardrail_warnings"] = "tool-permissions: " + guardrailText
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openai", Model: "gpt-4o",
		CredentialType: models.CredentialBYOK, StatusCode: http.StatusTooManyRequests, Outcome: models.OutcomeError,
		ErrorClass: models.ErrClassRateLimited, ErrorMessage: callerText, StartedAt: time.Now()})
	return rc
}

// sendEntry runs the plugin on rc and returns the one entry the webhook got.
func sendEntry(t *testing.T, p *Plugin, f *LogFlusher, wh *batchRecorder, rc *models.RequestContext) map[string]any {
	t.Helper()
	p.ProcessResponse(context.Background(), rc)
	f.flush()
	batches := wh.all()
	if len(batches) != 1 || len(batches[0]) != 1 {
		t.Fatalf("webhook batches = %v; want one entry", batches)
	}
	entry := batches[0][0]
	if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
		t.Errorf("entry fails log-entry-v2: %v", errs)
	}
	return entry
}

// guardrailResult is the entry's one guardrail result.
func guardrailResult(t *testing.T, entry map[string]any) map[string]any {
	t.Helper()
	results, _ := entry["guardrail_results"].([]any)
	if len(results) != 1 {
		t.Fatalf("guardrail_results = %v, want one", entry["guardrail_results"])
	}
	g, _ := results[0].(map[string]any)
	return g
}

func assertNoContent(t *testing.T, label string, entry map[string]any) {
	t.Helper()
	for _, k := range []string{"request_body", "response_body", "request_headers"} {
		if _, ok := entry[k]; ok {
			t.Errorf("%s: the entry kept %s", label, k)
		}
	}
}

func orgStore(org *tenant.OrgConfig) *tenant.Store {
	s := tenant.NewStore()
	s.Set(contentOrg, org)
	return s
}

// store_bodies=false is metadata: no bodies or headers, whether the chat hook
// resolved the mode or the plugin resolves it for another endpoint.
func TestStoreBodiesFalse(t *testing.T) {
	noBodies := false
	tenants := orgStore(&tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{
		Retention: &tenant.PrivacyRetentionConfig{StoreBodies: &noBodies}}})

	t.Run("resolved by the plugin", func(t *testing.T) {
		p, f, wh := contentModePlugin(t, tenants)
		entry := sendEntry(t, p, f, wh, contentRequest(t))
		assertNoContent(t, "store_bodies=false", entry)
		if entry["content_mode"] != "metadata" || entry["error_message"] != callerText {
			t.Errorf("content_mode = %v, error_message = %v; want metadata, with the error text kept", entry["content_mode"], entry["error_message"])
		}
		if g := guardrailResult(t, entry); g["message"] != guardrailText {
			t.Errorf("metadata guardrail result = %v; want its message kept", g)
		}
		if meta, _ := entry["metadata"].(map[string]any); meta["guardrail_warnings"] != "tool-permissions: "+guardrailText {
			t.Errorf("metadata guardrail_warnings = %v; want the warning kept", meta["guardrail_warnings"])
		}
	})
	t.Run("resolved at step 5", func(t *testing.T) {
		p, f, wh := contentModePlugin(t, nil)
		rc := contentRequest(t)
		rc.Privacy = models.Privacy{ContentMode: models.ContentModeMetadata, Source: models.PrivacySourceOrg}
		entry := sendEntry(t, p, f, wh, rc)
		assertNoContent(t, "rc.Privacy metadata", entry)
		if entry["content_mode"] != "metadata" {
			t.Errorf("content_mode = %v, want metadata", entry["content_mode"])
		}
	})
}

// none drops the bodies, the request headers, the error text (also from the
// trace line and the attempts, which keep class and status) and the caller's
// metadata keys, and keeps the gateway's.
func TestContentModeNoneOmitsBodiesHeadersAndErrorText(t *testing.T) {
	handler, cleanup := installCapturingLogger()
	defer cleanup()
	p, f, wh := contentModePlugin(t, nil)
	rc := contentRequest(t)
	rc.Metadata[privacy.KeyLogContentRequestMetadata] = "none" // as the auth plugin copies it
	entry := sendEntry(t, p, f, wh, rc)

	assertNoContent(t, "key none", entry)
	if _, ok := entry["error_message"]; ok {
		t.Errorf("none kept the error text: %v", entry["error_message"])
	}
	meta, _ := entry["metadata"].(map[string]any)
	if _, ok := meta["team"]; ok || meta["outcome_class"] != "ok" {
		t.Errorf("metadata = %v; want the caller's key gone and the gateway's kept", meta)
	}
	// The guardrail warnings are gateway-owned but quote guardrail messages,
	// which can name tools or carry a judge's reading of the prompt.
	if v, ok := meta["guardrail_warnings"]; ok {
		t.Errorf("none kept guardrail_warnings: %v", v)
	}
	a0 := entry["attempts"].([]any)[0].(map[string]any)
	if _, ok := a0["error_message"]; ok || a0["error_class"] != "rate_limited" || a0["status_code"] != 429.0 {
		t.Errorf("attempt = %v; want class and status only", a0)
	}
	if entry["content_mode"] != "none" || entry["status_code"] != 502.0 {
		t.Errorf("content_mode = %v, status_code = %v; want none, 502", entry["content_mode"], entry["status_code"])
	}
	if g := guardrailResult(t, entry); g["message"] != "" || g["name"] != "tool-permissions" || g["action"] != "log" {
		t.Errorf("guardrail result = %v; want its name and action, and no message", g)
	}

	p.Close() // drains the trace emitter
	traced := 0
	for i := 0; i < handler.count(); i++ {
		rec := handler.get(i)
		if rec.Message != "request.trace" {
			continue
		}
		traced++
		if v, ok := findAttr(rec, "error_message"); ok {
			t.Errorf("the trace line kept the error text: %q", v.String())
		}
		if v, ok := findAttr(rec, "guardrail_results"); !ok || strings.Contains(v.String(), "transfer_funds") {
			t.Errorf("the trace line's guardrail results = %q (present %v); want them without the message", v.String(), ok)
		}
	}
	if traced != 1 {
		t.Errorf("trace lines = %d, want 1", traced)
	}
}

// Every entry names its content mode: the one step 5 resolved, or the one the
// plugin resolves (full from the global default when nothing restricts).
func TestContentModeFieldOnEntry(t *testing.T) {
	for _, tt := range []struct {
		name     string
		resolved models.ContentMode // rc.Privacy, "" for an endpoint without step 5
		org      string
		key      string
		header   string
		dflt     string
		want     string
		bodies   bool
	}{
		{name: "resolved full", resolved: models.ContentModeFull, want: "full", bodies: true},
		{name: "resolved none", resolved: models.ContentModeNone, want: "none"},
		{name: "unresolved, nothing set", want: "full", bodies: true},
		{name: "unresolved, org none", org: "none", want: "none"},
		{name: "unresolved, key metadata", key: "metadata", want: "metadata"},
		{name: "unresolved, header none", header: "none", want: "none"},
		{name: "unresolved, header cannot loosen", org: "metadata", header: "full", want: "metadata"},
		{name: "unresolved, global default metadata", dflt: "metadata", want: "metadata"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if tt.dflt != "" {
				restore, err := privacy.SetDefaultContentMode(tt.dflt)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(restore)
			}
			p, f, wh := contentModePlugin(t, orgStore(&tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{ContentMode: tt.org}}))
			rc := contentRequest(t)
			rc.Privacy = models.Privacy{ContentMode: tt.resolved}
			if tt.key != "" {
				rc.Metadata[privacy.KeyLogContentRequestMetadata] = tt.key
			}
			if tt.header != "" {
				rc.RequestHeaders.Set(privacy.LogContentHeader, tt.header)
			}
			entry := sendEntry(t, p, f, wh, rc)
			if entry["content_mode"] != tt.want {
				t.Errorf("content_mode = %v, want %s", entry["content_mode"], tt.want)
			}
			if _, has := entry["request_body"]; has != tt.bodies {
				t.Errorf("request_body present = %v, want %v", has, tt.bodies)
			}
		})
	}
}

// Internal service keys stay out of the request log: Django bills their
// callers at the activity level, and ingesting
// them would also emit gateway usage events for the same calls.
func TestInternalKeyRequestNotLogged(t *testing.T) {
	p, f, wh := contentModePlugin(t, nil)
	rc := contentRequest(t)
	rc.Metadata["key_type"] = "internal"
	p.ProcessResponse(context.Background(), rc)
	f.flush()
	if batches := wh.all(); len(batches) != 0 {
		t.Errorf("an internal-key request reached the webhook: %v", batches)
	}
}

// An empty key_log_content only says the key has no content mode: the early
// key peek copies it on every endpoint, and only
// the chat pipeline's step 5 removes it, so the plugin keeps it out of the
// log. A set value names the key's mode and stays.
func TestEmptyKeyLogContentNotLogged(t *testing.T) {
	for _, tt := range []struct {
		value string
		kept  bool
	}{
		{value: "", kept: false},
		{value: "metadata", kept: true},
	} {
		p, f, wh := contentModePlugin(t, nil)
		rc := contentRequest(t)
		rc.Metadata[privacy.KeyLogContentRequestMetadata] = tt.value
		entry := sendEntry(t, p, f, wh, rc)
		meta, _ := entry["metadata"].(map[string]any)
		v, ok := meta[privacy.KeyLogContentRequestMetadata]
		if ok != tt.kept || (ok && v != tt.value) {
			t.Errorf("key_log_content %q: entry metadata has %v (present %v); want present %v", tt.value, v, ok, tt.kept)
		}
		if rc.Metadata[privacy.KeyLogContentRequestMetadata] != tt.value {
			t.Errorf("key_log_content %q: the plugin changed rc.Metadata", tt.value)
		}
	}
}

// The non-chat fallback reads the org's stored config, not the rollout
// candidate step 4 would pick. That is the same content mode: a
// candidate replaces only routing and managed_models (tenant.buildCandidate)
// and shares stable's privacy. If a rollout ever carries its own privacy,
// this fails, and contentMode's fallback must resolve the effective config.
func TestRolloutCandidateSharesContentMode(t *testing.T) {
	for _, mode := range []string{"", "metadata", "none"} {
		org := &tenant.OrgConfig{
			Privacy: &tenant.PrivacyConfig{ContentMode: mode},
			Routing: &tenant.RoutingConfig{Rollout: &tenant.RolloutConfig{
				Version: "v2", Percent: 100, StickyBy: "request", Routing: &tenant.RoutingConfig{}}},
		}
		if got, want := org.Candidate().ContentMode(), org.ContentMode(); got != want {
			t.Errorf("content mode %q: candidate %q, stable %q; want the same", mode, got, want)
		}
	}
}
