package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

func r5AssertOpaqueDepth(t *testing.T, cfg *tenant.OrgConfig, depth int, kind string) {
	t.Helper()
	if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.KeyPolicies) != 1 {
		t.Fatal("opaque-depth policy was lost")
	}
	checks := cfg.Guardrails.KeyPolicies[0].Checks
	check := checks["pii-detection"]
	if len(checks) != 2 || check == nil || !check.Enabled || check.Action != "block" || check.ConfidenceThreshold != 0.8 || check.ConfigurationError != "" {
		t.Fatalf("valid opaque config became unavailable: %+v", check)
	}
	var cursor interface{} = check.Config
	for level := 0; level < depth-1; level++ {
		if kind == "array" && level > 0 {
			items, ok := cursor.([]interface{})
			if !ok || len(items) != 1 {
				t.Fatalf("lost array at level %d", level)
			}
			cursor = items[0]
		} else {
			item, ok := cursor.(map[string]interface{})
			if !ok || len(item) != 1 {
				t.Fatalf("lost object at level %d", level)
			}
			cursor = item["next"]
		}
	}
	if !reflect.DeepEqual(cursor, map[string]interface{}{"leaf": "kept"}) {
		t.Fatalf("opaque leaf changed: %+v", cursor)
	}
	sibling := checks["keyword-blocklist"]
	if sibling == nil || !sibling.Enabled || sibling.ConfigurationError != "" || !reflect.DeepEqual(sibling.Config["words"], []interface{}{"forbidden"}) {
		t.Fatal("opaque-depth sibling was lost")
	}
}

func r5AssertOpaqueDepthTransport(t *testing.T, body []byte, depth int, kind string) {
	t.Helper()
	var want tenant.OrgConfig
	if err := json.Unmarshal(body, &want); err != nil {
		t.Fatal(err)
	}
	for _, bulk := range []bool{false, true} {
		store := tenant.NewStore()
		h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
		w := r5SubmitOrgConfig(h, body, bulk)
		if w.Code != http.StatusOK {
			t.Fatalf("depth=%d kind=%s bulk=%t status=%d body=%s", depth, kind, bulk, w.Code, w.Body.String())
		}
		cfg := store.Get("synthetic-org")
		r5AssertOpaqueDepth(t, cfg, depth, kind)
		if !reflect.DeepEqual(cfg, &want) {
			t.Fatal("full opaque org changed during contract-to-tenant conversion")
		}
		encoded, err := json.Marshal(cfg)
		if err != nil {
			t.Fatal(err)
		}
		var restored tenant.OrgConfig
		if err := json.Unmarshal(encoded, &restored); err != nil {
			t.Fatal(err)
		}
		r5AssertOpaqueDepth(t, &restored, depth, kind)
		if !reflect.DeepEqual(cfg, &restored) {
			t.Fatal("full opaque org changed during tenant roundtrip")
		}
	}
}

func TestR5ConfigTransportOpaqueDepth(t *testing.T) {
	for _, depth := range []int{129, 200} {
		for _, kind := range []string{"object", "array"} {
			t.Run(fmt.Sprintf("%d-%s", depth, kind), func(t *testing.T) {
				var payload map[string]interface{}
				if err := json.Unmarshal([]byte(r5ProjectedOrgJSON), &payload); err != nil {
					t.Fatal(err)
				}
				policies := payload["guardrails"].(map[string]interface{})["key_policies"].([]interface{})
				checks := policies[0].(map[string]interface{})["checks"].(map[string]interface{})
				delete(checks, "content-moderation")
				delete(checks, "pii-detection")
				var config interface{} = map[string]interface{}{"leaf": "kept"}
				for level := 0; level < depth-1; level++ {
					if kind == "array" && level < depth-2 {
						config = []interface{}{config}
					} else {
						config = map[string]interface{}{"next": config}
					}
				}
				checks["pii-detection"] = map[string]interface{}{
					"enabled": true, "action": "block", "confidence_threshold": 0.8, "config": config,
				}
				body, err := json.Marshal(payload)
				if err != nil {
					t.Fatal(err)
				}
				r5AssertOpaqueDepthTransport(t, body, depth, kind)
			})
		}
	}
}

func TestR5ConfigTransportPythonOpaqueDepth(t *testing.T) {
	dir := os.Getenv("GUARDRAIL_R5_DEPTH_PAYLOAD_DIR")
	if dir == "" {
		t.Skip("exact Python HTTPX/DRF bytes require the cloud pytest export directory")
	}
	for _, depth := range []int{129, 200} {
		for _, kind := range []string{"object", "array"} {
			t.Run(fmt.Sprintf("%d-%s", depth, kind), func(t *testing.T) {
				body, err := os.ReadFile(filepath.Join(dir, fmt.Sprintf("r5-depth-%d-%s.json", depth, kind)))
				if err != nil {
					t.Fatal(err)
				}
				r5AssertOpaqueDepthTransport(t, body, depth, kind)
				bulk, err := os.ReadFile(filepath.Join(dir, fmt.Sprintf("r5-depth-%d-%s-bulk.json", depth, kind)))
				if err != nil {
					t.Fatal(err)
				}
				controlPlane := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.Write(bulk)
				}))
				defer controlPlane.Close()
				store := tenant.NewStore()
				if err := tenant.SyncFromControlPlane(context.Background(), controlPlane.URL, "synthetic-admin", store); err != nil {
					t.Fatal(err)
				}
				cfg := store.Get("00000000-0000-0000-0000-000000000003")
				r5AssertOpaqueDepth(t, cfg, depth, kind)
				var want tenant.OrgConfig
				if err := json.Unmarshal(body, &want); err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(cfg, &want) {
					t.Fatal("DRF startup-sync payload differs from HTTPX full org")
				}
			})
		}
	}
}

func TestR5ConfigTransportRejectsJSONNestingOverflow(t *testing.T) {
	// Exceed encoding/json's actual 10,000-container scanner limit, not a
	// conservative Python copy budget. Both handlers must keep the old store.
	config := strings.Repeat(`{"next":`, 10001) + `{"leaf":"kept"}` + strings.Repeat("}", 10001)
	body := []byte(`{"guardrails":{"key_policies":[{"id":"deep","key_ids":["key"],"checks":{"custom-static":{"enabled":true,"action":"block","config":` + config + `}}}]}}`)
	for _, bulk := range []bool{false, true} {
		store := tenant.NewStore()
		old := &tenant.OrgConfig{}
		store.Set("synthetic-org", old)
		h := NewOrgConfigHandlers(store, "synthetic-admin", nil)
		w := r5SubmitOrgConfig(h, body, bulk)
		if w.Code != http.StatusBadRequest || store.Get("synthetic-org") != old {
			t.Fatalf("bulk=%t nesting overflow was not atomic: status=%d", bulk, w.Code)
		}
	}
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
