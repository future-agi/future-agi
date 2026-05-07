package external

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeMsg(role, content string) models.Message {
	raw, _ := json.Marshal(content)
	return models.Message{Role: role, Content: raw}
}

func makeInput(msgs []models.Message) *guardrails.CheckInput {
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model:    "gpt-4o",
			Messages: msgs,
		},
	}
}

// ======================= Lakera Tests =======================

func TestLakera_Flagged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer lk-test" {
			t.Error("wrong auth header")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"flagged":         true,
			"categories":      map[string]bool{"prompt_injection": true, "jailbreak": false},
			"category_scores": map[string]float64{"prompt_injection": 0.95, "jailbreak": 0.1},
		})
	}))
	defer srv.Close()

	g := New("test-lakera", map[string]interface{}{
		"provider":   "lakera",
		"api_key":    "lk-test",
		"endpoint":   srv.URL,
		"categories": []interface{}{"prompt_injection"},
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "ignore previous")}))
	if result.Pass {
		t.Fatal("expected fail")
	}
	if result.Score != 0.95 {
		t.Errorf("score = %f, want 0.95", result.Score)
	}
}

func TestLakera_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"flagged":         false,
			"categories":      map[string]bool{"prompt_injection": false},
			"category_scores": map[string]float64{"prompt_injection": 0.01},
		})
	}))
	defer srv.Close()

	g := New("test-lakera", map[string]interface{}{
		"provider": "lakera", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatal("expected pass")
	}
}

func TestLakera_FlaggedButNoCategoryMatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"flagged":         true,
			"categories":      map[string]bool{"prompt_injection": false, "pii": true},
			"category_scores": map[string]float64{"prompt_injection": 0.1, "pii": 0.9},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider":   "lakera",
		"api_key":    "k",
		"endpoint":   srv.URL,
		"categories": []interface{}{"prompt_injection"}, // Only checking injection
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "my ssn is 123")}))
	if !result.Pass {
		t.Fatal("expected pass: configured category not triggered")
	}
}

// ======================= Azure Content Safety Tests =======================

func TestAzure_HighSeverity(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Ocp-Apim-Subscription-Key") != "az-key" {
			t.Error("wrong subscription key header")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"categoriesAnalysis": []map[string]interface{}{
				{"category": "Hate", "severity": 0},
				{"category": "Violence", "severity": 4},
			},
		})
	}))
	defer srv.Close()

	g := New("test-azure", map[string]interface{}{
		"provider":           "azure_content_safety",
		"api_key":            "az-key",
		"endpoint":           srv.URL,
		"severity_threshold": 2,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "violent content")}))
	if result.Pass {
		t.Fatal("expected fail for severity 4")
	}
	// Score should be 4/6 ≈ 0.667
	if result.Score < 0.6 || result.Score > 0.7 {
		t.Errorf("score = %f, want ~0.667", result.Score)
	}
}

func TestAzure_LowSeverity(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"categoriesAnalysis": []map[string]interface{}{
				{"category": "Hate", "severity": 0},
				{"category": "Violence", "severity": 1},
			},
		})
	}))
	defer srv.Close()

	g := New("test-azure", map[string]interface{}{
		"provider":           "azure_content_safety",
		"api_key":            "k",
		"endpoint":           srv.URL,
		"severity_threshold": 2,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "mild content")}))
	if !result.Pass {
		t.Fatal("expected pass for low severity")
	}
}

// ======================= Presidio Tests =======================

func TestPresidio_PIIDetected(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]interface{}{
			{"entity_type": "PHONE_NUMBER", "score": 0.85, "start": 0, "end": 12},
			{"entity_type": "CREDIT_CARD", "score": 0.95, "start": 15, "end": 31},
		})
	}))
	defer srv.Close()

	g := New("test-presidio", map[string]interface{}{
		"provider":        "presidio",
		"endpoint":        srv.URL,
		"language":        "en",
		"score_threshold": 0.7,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "555-123-4567 4111111111111111")}))
	if result.Pass {
		t.Fatal("expected fail for PII")
	}
	if result.Score != 0.95 {
		t.Errorf("score = %f, want 0.95", result.Score)
	}
}

func TestPresidio_NoPII(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]interface{}{})
	}))
	defer srv.Close()

	g := New("test-presidio", map[string]interface{}{
		"provider": "presidio", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello world")}))
	if !result.Pass {
		t.Fatal("expected pass for no PII")
	}
}

func TestPresidio_BelowThreshold(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]interface{}{
			{"entity_type": "PERSON", "score": 0.3, "start": 0, "end": 5},
		})
	}))
	defer srv.Close()

	g := New("test-presidio", map[string]interface{}{
		"provider":        "presidio",
		"endpoint":        srv.URL,
		"score_threshold": 0.5,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "John said hi")}))
	if !result.Pass {
		t.Fatal("expected pass: entity score below threshold")
	}
}

// ======================= Llama Guard Tests =======================

func TestLlamaGuard_Unsafe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"content": "unsafe\nS6"}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-llamaguard", map[string]interface{}{
		"provider": "llama_guard",
		"endpoint": srv.URL,
		"api_key":  "k",
		"model":    "meta-llama/Llama-Guard-3-8B",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "dangerous content")}))
	if result.Pass {
		t.Fatal("expected fail for unsafe")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f", result.Score)
	}
}

func TestLlamaGuard_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"content": "safe"}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-llamaguard", map[string]interface{}{
		"provider": "llama_guard", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatal("expected pass")
	}
}

func TestLlamaGuard_EmptyChoices(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"choices": []interface{}{}})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "llama_guard", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("expected pass for empty choices")
	}
}

// ======================= Bedrock Tests =======================

func TestBedrock_Intervened(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"action": "GUARDRAIL_INTERVENED",
			"outputs": []map[string]interface{}{
				{"text": "Sorry, I can't help with that."},
			},
		})
	}))
	defer srv.Close()

	g := New("test-bedrock", map[string]interface{}{
		"provider":          "bedrock_guardrails",
		"endpoint":          srv.URL,
		"guardrail_id":      "abc123",
		"guardrail_version": "1",
		"access_key":        "ak",
		"secret_key":        "sk",
		"region":            "us-east-1",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "attack")}))
	if result.Pass {
		t.Fatal("expected fail for GUARDRAIL_INTERVENED")
	}
	if result.Message != "Sorry, I can't help with that." {
		t.Errorf("message = %q", result.Message)
	}
}

func TestBedrock_None(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"action": "NONE",
		})
	}))
	defer srv.Close()

	g := New("test-bedrock", map[string]interface{}{
		"provider":     "bedrock_guardrails",
		"endpoint":     srv.URL,
		"guardrail_id": "abc123",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatal("expected pass for NONE")
	}
}

// ======================= Generic External Tests =======================

func TestExternal_NilInput(t *testing.T) {
	g := New("test", map[string]interface{}{"provider": "lakera", "api_key": "k"})
	result := g.Check(context.Background(), nil)
	if !result.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestExternal_EmptyMessages(t *testing.T) {
	g := New("test", map[string]interface{}{"provider": "lakera", "api_key": "k", "endpoint": "http://unused"})
	result := g.Check(context.Background(), makeInput(nil))
	if !result.Pass {
		t.Fatal("empty messages should pass")
	}
}

func TestExternal_NoAdapter(t *testing.T) {
	g := New("test", nil)
	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("no adapter should pass")
	}
}

func TestExternal_HTTP500WithRetry(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls <= 1 {
			w.WriteHeader(500)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"flagged":         false,
			"categories":      map[string]bool{},
			"category_scores": map[string]float64{},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "lakera", "api_key": "k", "endpoint": srv.URL,
		"retry": 1,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass after retry: %s", result.Message)
	}
	if calls != 2 {
		t.Errorf("expected 2 calls, got %d", calls)
	}
}

func TestExternal_HTTP403NoRetry(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "lakera", "api_key": "bad", "endpoint": srv.URL,
		"retry": 2,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on 403")
	}
}

func TestExternal_PostStage(t *testing.T) {
	var receivedBody string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req lakeraRequest
		json.NewDecoder(r.Body).Decode(&req)
		receivedBody = req.Input
		json.NewEncoder(w).Encode(map[string]interface{}{
			"flagged": false, "categories": map[string]bool{}, "category_scores": map[string]float64{},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "lakera", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), inputForPost("assistant response text"))
	if !result.Pass {
		t.Fatal("expected pass")
	}
	if receivedBody != "assistant response text" {
		t.Errorf("received = %q", receivedBody)
	}
}

func TestExternal_ContextCancelled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"flagged": false})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "lakera", "api_key": "k", "endpoint": srv.URL,
	})

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	result := g.Check(ctx, makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on cancelled context")
	}
}

// ======================= IsExternalProviderConfig Tests =======================

func TestIsExternalProviderConfig(t *testing.T) {
	tests := []struct {
		cfg    map[string]interface{}
		expect bool
	}{
		{map[string]interface{}{"provider": "lakera"}, true},
		{map[string]interface{}{"provider": "azure_content_safety"}, true},
		{map[string]interface{}{"provider": "presidio"}, true},
		{map[string]interface{}{"provider": "llama_guard"}, true},
		{map[string]interface{}{"provider": "bedrock_guardrails"}, true},
		{map[string]interface{}{"provider": "dynamoai"}, true},
		{map[string]interface{}{"provider": "enkrypt"}, true},
		{map[string]interface{}{"provider": "ibm_ai"}, true},
		{map[string]interface{}{"provider": "grayswan"}, true},
		{map[string]interface{}{"provider": "lasso"}, true},
		{map[string]interface{}{"provider": "futureagi"}, false},
		{map[string]interface{}{"provider": "unknown"}, false},
		{map[string]interface{}{"url": "https://example.com"}, false},
		{nil, false},
	}
	for _, tt := range tests {
		got := IsExternalProviderConfig(tt.cfg)
		if got != tt.expect {
			t.Errorf("IsExternalProviderConfig(%v) = %v, want %v", tt.cfg, got, tt.expect)
		}
	}
}

func TestExternal_Name(t *testing.T) {
	g := New("my-guard", map[string]interface{}{"provider": "lakera"})
	if g.Name() != "my-guard" {
		t.Errorf("name = %q", g.Name())
	}
}

// ======================= DynamoAI Tests =======================

func TestDynamoAI_Blocked(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer dyn-test" {
			t.Error("wrong auth header")
		}
		var req dynamoaiRequest
		json.NewDecoder(r.Body).Decode(&req)
		if len(req.Messages) == 0 || req.Messages[0].Role != "user" {
			t.Error("expected messages with user role")
		}
		if req.TextType != "MODEL_INPUT" {
			t.Errorf("textType = %q, want MODEL_INPUT", req.TextType)
		}
		if len(req.PolicyIDs) == 0 || req.PolicyIDs[0] != "pol-1" {
			t.Errorf("policyIds = %v, want [pol-1]", req.PolicyIDs)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"text":        req.Messages[0].Content,
			"finalAction": "BLOCK",
			"appliedPolicies": []map[string]interface{}{
				{
					"policy": map[string]string{"name": "Toxicity", "method": "PII", "action": "BLOCK"},
					"action": "BLOCK",
				},
			},
		})
	}))
	defer srv.Close()

	g := New("test-dynamoai", map[string]interface{}{
		"provider":   "dynamoai",
		"api_key":    "dyn-test",
		"endpoint":   srv.URL,
		"policy_ids": []interface{}{"pol-1"},
		"text_type":  "MODEL_INPUT",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "toxic content")}))
	if result.Pass {
		t.Fatal("expected fail for BLOCK")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f, want 1.0", result.Score)
	}
}

func TestDynamoAI_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"finalAction":     "NONE",
			"appliedPolicies": []interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-dynamoai", map[string]interface{}{
		"provider":   "dynamoai",
		"api_key":    "k",
		"endpoint":   srv.URL,
		"policy_ids": []interface{}{"pol-1"},
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

func TestDynamoAI_APIError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "invalid policy ID",
		})
	}))
	defer srv.Close()

	g := New("test-dynamoai", map[string]interface{}{
		"provider": "dynamoai", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on API error")
	}
}

// ======================= Enkrypt AI Tests =======================

func TestEnkrypt_Flagged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("apikey") != "ek-test" {
			t.Errorf("wrong auth header: got %q", r.Header.Get("apikey"))
		}
		var req enkryptRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.Text == "" {
			t.Error("expected non-empty text")
		}
		toxDet, ok := req.Detectors["toxicity"].(map[string]interface{})
		if !ok || toxDet["enabled"] != true {
			t.Error("expected toxicity detector enabled")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"summary": map[string]interface{}{
				"toxicity":         float64(1),
				"injection_attack": float64(0),
			},
			"details": map[string]interface{}{
				"toxicity": map[string]interface{}{
					"toxicity": 0.92,
					"severe":   0.15,
				},
			},
		})
	}))
	defer srv.Close()

	g := New("test-enkrypt", map[string]interface{}{
		"provider": "enkrypt",
		"api_key":  "ek-test",
		"endpoint": srv.URL,
		"checks":   []interface{}{"toxicity", "injection_attack"},
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "toxic stuff")}))
	if result.Pass {
		t.Fatal("expected fail for toxicity")
	}
	if result.Score < 0.9 {
		t.Errorf("score = %f, want >= 0.9", result.Score)
	}
}

func TestEnkrypt_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"summary": map[string]interface{}{
				"toxicity":         float64(0),
				"injection_attack": float64(0),
			},
			"details": map[string]interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-enkrypt", map[string]interface{}{
		"provider": "enkrypt", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

func TestEnkrypt_ArraySummary(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"summary": map[string]interface{}{
				"toxicity": []interface{}{"toxicity", "insult"},
			},
			"details": map[string]interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-enkrypt", map[string]interface{}{
		"provider": "enkrypt", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "insult")}))
	if result.Pass {
		t.Fatal("expected fail for array toxicity summary")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f, want 1.0 for array-type summary", result.Score)
	}
}

// ======================= GraySwan Tests =======================

func TestGraySwan_Violation(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer gs-test" {
			t.Errorf("wrong Authorization header")
		}
		if r.Header.Get("grayswan-api-key") != "gs-test" {
			t.Errorf("missing grayswan-api-key header")
		}
		var req grayswanRequest
		json.NewDecoder(r.Body).Decode(&req)
		if len(req.Messages) == 0 || req.Messages[0].Role != "user" {
			t.Error("expected messages with user role")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violation":      0.92,
			"violated_rules": []int{2, 3},
			"mutation":       false,
			"ipi":            true,
			"violated_rule_descriptions": []map[string]interface{}{
				{"rule": 2, "name": "No jailbreaks", "description": "desc"},
			},
		})
	}))
	defer srv.Close()

	g := New("test-grayswan", map[string]interface{}{
		"provider": "grayswan",
		"api_key":  "gs-test",
		"endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "bypass safety")}))
	if result.Pass {
		t.Fatal("expected fail for high violation score")
	}
	if result.Score != 0.92 {
		t.Errorf("score = %f, want 0.92", result.Score)
	}
}

func TestGraySwan_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violation":      0.05,
			"violated_rules": []int{},
			"mutation":       false,
			"ipi":            false,
		})
	}))
	defer srv.Close()

	g := New("test-grayswan", map[string]interface{}{
		"provider": "grayswan", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
	if result.Score != 0.05 {
		t.Errorf("score = %f, want 0.05", result.Score)
	}
}

func TestGraySwan_IPIOnly(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violation":      0.3,
			"violated_rules": []int{},
			"mutation":       false,
			"ipi":            true,
		})
	}))
	defer srv.Close()

	g := New("test-grayswan", map[string]interface{}{
		"provider": "grayswan", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hidden injection")}))
	if result.Pass {
		t.Fatal("expected fail for IPI detection even with low violation score")
	}
}

// ======================= Lasso Tests =======================

func TestLasso_Flagged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("lasso-api-key") != "ls-test" {
			t.Errorf("wrong auth header: got %q", r.Header.Get("lasso-api-key"))
		}
		if r.Header.Get("Authorization") != "" {
			t.Error("should not have Authorization header")
		}
		var req lassoRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.MessageType != "PROMPT" {
			t.Errorf("messageType = %q, want PROMPT", req.MessageType)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violations_detected": true,
			"deputies": map[string]bool{
				"jailbreak":         true,
				"pattern-detection": false,
			},
			"findings": map[string]interface{}{
				"jailbreak": []map[string]interface{}{
					{"action": "BLOCK", "severity": "HIGH"},
				},
			},
		})
	}))
	defer srv.Close()

	g := New("test-lasso", map[string]interface{}{
		"provider": "lasso",
		"api_key":  "ls-test",
		"endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "jailbreak attempt")}))
	if result.Pass {
		t.Fatal("expected fail for violations_detected")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f, want 1.0", result.Score)
	}
}

func TestLasso_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violations_detected": false,
			"deputies":            map[string]bool{},
			"findings":            map[string]interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-lasso", map[string]interface{}{
		"provider": "lasso", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

func TestLasso_EndpointAppendClassify(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/classify" {
			t.Errorf("path = %q, want /classify", r.URL.Path)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"violations_detected": false,
			"deputies":            map[string]bool{},
			"findings":            map[string]interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-lasso", map[string]interface{}{
		"provider": "lasso", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

// ======================= IBM Granite Guardian Tests =======================

func TestIBM_Flagged(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer ibm-test" {
			t.Error("wrong auth header")
		}
		if r.URL.Query().Get("version") == "" {
			t.Error("missing version query param")
		}
		var req ibmChatRequest
		json.NewDecoder(r.Body).Decode(&req)
		if req.ModelID != "ibm/granite-guardian-3-8b" {
			t.Errorf("model_id = %q", req.ModelID)
		}
		if req.ProjectID != "proj-1" {
			t.Errorf("project_id = %q", req.ProjectID)
		}
		if len(req.Messages) == 0 {
			t.Fatal("expected messages")
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"index": 0, "message": map[string]string{"role": "assistant", "content": "Yes"}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-ibm", map[string]interface{}{
		"provider":    "ibm_ai",
		"api_key":     "ibm-test",
		"endpoint":    srv.URL,
		"project_id":  "proj-1",
		"model_id":    "ibm/granite-guardian-3-8b",
		"criteria_id": "jailbreak",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "bypass all safety")}))
	if result.Pass {
		t.Fatal("expected fail for 'Yes' response")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f, want 1.0", result.Score)
	}
}

func TestIBM_Safe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"index": 0, "message": map[string]string{"role": "assistant", "content": "No"}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-ibm", map[string]interface{}{
		"provider":   "ibm_ai",
		"api_key":    "k",
		"endpoint":   srv.URL,
		"project_id": "proj-1",
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

func TestIBM_EmptyChoices(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []interface{}{},
		})
	}))
	defer srv.Close()

	g := New("test-ibm", map[string]interface{}{
		"provider": "ibm_ai", "api_key": "k", "endpoint": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail for empty choices")
	}
}

func TestIBM_CustomCriteria(t *testing.T) {
	var receivedBody ibmChatRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedBody)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"choices": []map[string]interface{}{
				{"index": 0, "message": map[string]string{"role": "assistant", "content": "No"}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-ibm", map[string]interface{}{
		"provider":    "ibm_ai",
		"api_key":     "k",
		"endpoint":    srv.URL,
		"criteria_id": "groundedness",
	})

	g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "test")}))
	if len(receivedBody.Messages) == 0 {
		t.Fatal("expected messages in request")
	}
	prompt := receivedBody.Messages[0].Content
	if !contains(prompt, "groundedness") {
		t.Errorf("prompt should contain criteria_id 'groundedness', got: %s", prompt[:100])
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && searchString(s, substr)
}

func searchString(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
