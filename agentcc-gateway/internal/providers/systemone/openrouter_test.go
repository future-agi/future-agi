package systemone

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
)

func TestOpenRouterDecisionRoutingPolicy(t *testing.T) {
	for _, preferences := range []string{
		"",
		`,"provider":null`,
		`,"provider":{"allow_fallbacks":false,"require_parameters":false,"only":["TypeSafe"],"zdr":true}`,
	} {
		t.Run(preferences, func(t *testing.T) {
			body := `{"model":"~typesafe/jev-latest","state":{"count":9007199254740993},"questions":{"q":{"type":"noul","instructions":"private-instructions"}},"future":{"keep":true}` + preferences + `}`
			encoded, err := openRouterDecisionBody([]byte(body))
			if err != nil {
				t.Fatal(err)
			}
			var before, after map[string]json.RawMessage
			if err := json.Unmarshal([]byte(body), &before); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(encoded, &after); err != nil {
				t.Fatal(err)
			}
			for key, raw := range before {
				if key != "provider" && string(raw) != string(after[key]) {
					t.Errorf("field %q changed", key)
				}
			}
			var policy map[string]json.RawMessage
			if err := json.Unmarshal(after["provider"], &policy); err != nil {
				t.Fatal(err)
			}
			if string(policy["allow_fallbacks"]) != "true" || string(policy["require_parameters"]) != "true" {
				t.Fatal("OpenRouter routing policy was not enforced")
			}
			if strings.Contains(preferences, `"only"`) && (string(policy["only"]) != `["TypeSafe"]` || string(policy["zdr"]) != "true") {
				t.Fatal("caller provider restrictions were lost")
			}
		})
	}
}

func TestOpenRouterRejectsUnsupportedRoutingBeforeSending(t *testing.T) {
	for _, tc := range []struct {
		name, extra, code string
	}{
		{"provider_string", `"provider":"private-state"`, "invalid_provider_preferences"},
		{"provider_array", `"provider":[]`, "invalid_provider_preferences"},
		{"fallback_models", `"models":["another/model"]`, "decision_fallback_not_allowed"},
		{"fallback_route", `"route":"fallback"`, "decision_fallback_not_allowed"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			upstream := testhttp.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				calls.Add(1)
			}))
			defer upstream.Close()
			p := newProvider(t, "openrouter", config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext})
			body := `{"model":"typesafe/jev-1.14","state":"private-state","questions":{"q":{"type":"noul"}},` + tc.extra + `}`
			_, err := p.SystemOne(context.Background(), request(t, body))
			checkError(t, err, http.StatusBadRequest, tc.code)
			if calls.Load() != 0 {
				t.Fatal("invalid routing reached the provider")
			}
		})
	}
}

func TestOpenRouterDecisionResponsePreservesCanonicalModelAndRefusal(t *testing.T) {
	const body = `{"model":"typesafe/jev-1.13","state":"private-state","questions":{"q":{"type":"noul"}}}`
	const answer = `{"model":"typesafe/jev-1.13-20260917","provider":"TypeSafe","answers":{"q":{"type":"refusal"}},"usage":{"input_tokens":17,"output_tokens":3,"cost":0.002}}`
	var calls atomic.Int32
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodPost || r.URL.Path != "/api/alpha/decisions" {
			t.Errorf("request = %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer "+testKey || r.Header.Get(models.ProviderKeyHeader) != "" {
			t.Error("provider did not receive only the selected bearer credential")
		}
		var sent struct {
			Model    string          `json:"model"`
			Provider map[string]bool `json:"provider"`
		}
		if err := json.NewDecoder(r.Body).Decode(&sent); err != nil {
			t.Error(err)
		}
		if sent.Model != "typesafe/jev-1.13" || !reflect.DeepEqual(sent.Provider, map[string]bool{"allow_fallbacks": true, "require_parameters": true}) {
			t.Error("wrong model or routing policy")
		}
		io.WriteString(w, answer)
	}))
	defer upstream.Close()
	p := newProvider(t, "openrouter", config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext})
	resp, err := p.SystemOne(context.Background(), request(t, body))
	if err != nil {
		t.Fatal(err)
	}
	if string(resp.Body) != answer || resp.Model != "typesafe/jev-1.13-20260917" || resp.Usage != (models.DecisionUsage{InputTokens: 17, OutputTokens: 3}) {
		t.Fatal("canonical model, refusal, or usage was changed")
	}
	if calls.Load() != 1 {
		t.Fatalf("upstream calls = %d, want one without retries", calls.Load())
	}
}

func TestOpenRouterRequiresOwnKey(t *testing.T) {
	p := newProvider(t, "openrouter", config.ProviderConfig{})
	_, err := p.SystemOne(context.Background(), request(t, testBody))
	checkError(t, err, http.StatusForbidden, "provider_key_missing")
}
