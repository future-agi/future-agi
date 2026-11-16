package server

import (
	"bytes"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Python's full _build_payload -> GatewayClient serializer test checks this
// entire document, not just a hand-built GuardrailCheck. Cloud qualification can
// additionally supply the exact HTTPX request bytes via GUARDRAIL_R5_PAYLOAD_PATH.
const r5ProjectedOrgJSON = `{
  "providers": {},
  "guardrails": {
    "pipeline_mode": "parallel",
    "fail_open": false,
    "checks": {},
    "key_policies": [{
      "id": "00000000-0000-0000-0000-000000000001",
      "name": "numeric-transport",
      "mode": "enforce",
      "priority": 100,
      "key_ids": ["synthetic-key"],
      "checks": {
        "content-moderation": {
          "enabled": false,
          "action": "block",
          "confidence_threshold": 0.8,
          "configuration_error": "invalid_config",
          "config": {}
        },
        "pii-detection": {
          "enabled": true,
          "action": "block",
          "confidence_threshold": 0.5,
          "configuration_error": "invalid_config",
          "config": {}
        },
        "keyword-blocklist": {
          "enabled": true,
          "action": "block",
          "confidence_threshold": 0.5,
          "config": {"words": ["forbidden"], "future_option": [true, false, null]}
        }
      }
    }]
  },
  "cache": {"enabled": false, "default_ttl": 300, "max_entries": 10000}
}`

func r5SubmitOrgConfig(h *OrgConfigHandlers, body []byte, bulk bool) *httptest.ResponseRecorder {
	method, path := http.MethodPut, "/-/orgs/synthetic-org/config?org_id=synthetic-org"
	if bulk {
		method, path = http.MethodPost, "/-/orgs/configs/bulk"
		body = append(append([]byte(`{"synthetic-org":`), body...), '}')
	}
	r := httptest.NewRequest(method, path, bytes.NewReader(body))
	r.Header.Set("Authorization", "Bearer synthetic-admin")
	w := httptest.NewRecorder()
	if bulk {
		h.BulkLoadOrgConfigs(w, r)
	} else {
		h.SetOrgConfig(w, r)
	}
	return w
}

func r5AssertProjectedOrg(t *testing.T, cfg *tenant.OrgConfig) {
	t.Helper()
	if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.KeyPolicies) != 1 {
		t.Fatalf("selected policy lost in tenant config: %+v", cfg)
	}
	policy := cfg.Guardrails.KeyPolicies[0]
	if policy.Mode != "enforce" || policy.Priority != 100 || !reflect.DeepEqual(policy.KeyIDs, []string{"synthetic-key"}) {
		t.Fatalf("policy intent changed: %+v", policy)
	}
	if len(policy.Checks) != 3 {
		t.Fatalf("check count = %d, want 3", len(policy.Checks))
	}
	for _, name := range []string{"content-moderation", "pii-detection"} {
		check := policy.Checks[name]
		wantEnabled, wantThreshold := name == "pii-detection", 0.8
		if wantEnabled {
			wantThreshold = 0.5
		}
		if check == nil || check.Enabled != wantEnabled || check.Action != "block" || check.ConfidenceThreshold != wantThreshold || check.ConfigurationError != "invalid_config" || len(check.Config) != 0 {
			t.Fatalf("%s marker/intent changed: %+v", name, check)
		}
	}
	sibling := policy.Checks["keyword-blocklist"]
	if sibling == nil || !sibling.Enabled || sibling.Action != "block" || sibling.ConfidenceThreshold != 0.5 || sibling.ConfigurationError != "" {
		t.Fatalf("valid sibling is not live: %+v", sibling)
	}
	if !reflect.DeepEqual(sibling.Config["words"], []interface{}{"forbidden"}) || !reflect.DeepEqual(sibling.Config["future_option"], []interface{}{true, false, nil}) {
		t.Fatalf("opaque sibling config changed: %+v", sibling.Config)
	}
}

func r5AssertTransport(t *testing.T, body []byte) {
	t.Helper()
	for _, bulk := range []bool{false, true} {
		store := tenant.NewStore()
		h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
		w := r5SubmitOrgConfig(h, body, bulk)
		if w.Code != http.StatusOK {
			t.Fatalf("bulk=%t status=%d body=%s", bulk, w.Code, w.Body.String())
		}
		cfg := store.Get("synthetic-org")
		r5AssertProjectedOrg(t, cfg)
		encoded, err := json.Marshal(cfg)
		if err != nil {
			t.Fatal(err)
		}
		var restored tenant.OrgConfig
		if err := json.Unmarshal(encoded, &restored); err != nil {
			t.Fatal(err)
		}
		r5AssertProjectedOrg(t, &restored)
	}
}

func TestR5ConfigTransportProjectedOrg(t *testing.T) {
	r5AssertTransport(t, []byte(r5ProjectedOrgJSON))
}

func TestR5ConfigTransportPythonPayload(t *testing.T) {
	path := os.Getenv("GUARDRAIL_R5_PAYLOAD_PATH")
	if path == "" {
		t.Skip("exact Python request bytes require the cloud pytest export path")
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	r5AssertTransport(t, body)
}

func TestR5ConfigTransportRejectsOriginalNestedInteger(t *testing.T) {
	for _, name := range []string{"content-moderation", "pii-detection"} {
		t.Run(name, func(t *testing.T) {
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(r5ProjectedOrgJSON), &payload); err != nil {
				t.Fatal(err)
			}
			policies := payload["guardrails"].(map[string]interface{})["key_policies"].([]interface{})
			checks := policies[0].(map[string]interface{})["checks"].(map[string]interface{})
			check := checks[name].(map[string]interface{})
			// This is a JSON integer literal, not a float token or a string.
			huge := json.Number("1" + strings.Repeat("0", 400))
			config := map[string]interface{}{"max_entities": huge}
			if name == "content-moderation" {
				config = map[string]interface{}{"weights": map[string]interface{}{"toxicity": huge}}
			}
			check["config"] = config
			delete(check, "configuration_error")
			body, err := json.Marshal(payload)
			if err != nil {
				t.Fatal(err)
			}
			for _, bulk := range []bool{false, true} {
				store := tenant.NewStore()
				old := &tenant.OrgConfig{}
				store.Set("synthetic-org", old)
				h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
				w := r5SubmitOrgConfig(h, body, bulk)
				if w.Code != http.StatusBadRequest || store.Get("synthetic-org") != old {
					t.Fatalf("bulk=%t original numeric failure was not atomic: status=%d", bulk, w.Code)
				}
			}
		})
	}
}

func TestR5ConfigTransportFiniteOpaqueNumbers(t *testing.T) {
	// Go's interface{} boundary permits rounding; it must not impose an int64
	// limit or treat otherwise finite JSON numbers as unavailable.
	body := `{"guardrails":{"key_policies":[{"id":"finite","key_ids":["key"],"checks":{"custom-static":{"enabled":true,"action":"block","config":{"opaque":[1.7976931348623157e308,5e-324,-0.0,9007199254740993,9223372036854775808,null,true,false]}}}}]}}`
	store := tenant.NewStore()
	h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
	w := r5SubmitOrgConfig(h, []byte(body), false)
	if w.Code != http.StatusOK {
		t.Fatalf("finite opaque config rejected: %d %s", w.Code, w.Body.String())
	}
	values := store.Get("synthetic-org").Guardrails.KeyPolicies[0].Checks["custom-static"].Config["opaque"].([]interface{})
	want := []interface{}{math.MaxFloat64, math.SmallestNonzeroFloat64, math.Copysign(0, -1), float64(9007199254740993), float64(9223372036854775808), nil, true, false}
	if !reflect.DeepEqual(values, want) || !math.Signbit(values[2].(float64)) {
		t.Fatalf("opaque float64 values changed: %+v", values)
	}
}
