package futureagi

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

func makeOutputInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
	}
}

// --- Test: Passed response ---

func TestFutureAGI_Passed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Api-Key") != "test-key" {
			t.Error("missing or wrong X-Api-Key header")
		}
		if r.Header.Get("X-Secret-Key") != "test-secret" {
			t.Error("missing or wrong X-Secret-Key header")
		}
		w.WriteHeader(200)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{
					"evaluations": []map[string]interface{}{
						{
							"name":       "toxicity",
							"output":     "Passed",
							"reason":     "Content is safe",
							"runtime":    150.0,
							"outputType": "string",
							"evalId":     "15",
						},
					},
				},
			},
		})
	}))
	defer srv.Close()

	g := New("test-toxicity", map[string]interface{}{
		"provider":   "futureagi",
		"eval_id":    "15",
		"api_key":    "test-key",
		"secret_key": "test-secret",
		"base_url":   srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello world")}))
	if !result.Pass {
		t.Fatalf("expected pass, got fail: %s", result.Message)
	}
	if result.Score != 0.0 {
		t.Errorf("expected score 0.0, got %f", result.Score)
	}
	if result.Message != "Content is safe" {
		t.Errorf("message = %q", result.Message)
	}
	if result.Details["eval_id"] != "15" {
		t.Errorf("eval_id = %v", result.Details["eval_id"])
	}
}

// --- Test: Failed response ---

func TestFutureAGI_Failed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{
					"evaluations": []map[string]interface{}{
						{
							"name":       "toxicity",
							"output":     "Failed",
							"reason":     "Toxic content detected",
							"runtime":    200.0,
							"outputType": "string",
							"evalId":     "15",
						},
					},
				},
			},
		})
	}))
	defer srv.Close()

	g := New("test-toxicity", map[string]interface{}{
		"provider":   "futureagi",
		"eval_id":    "15",
		"api_key":    "test-key",
		"secret_key": "test-secret",
		"base_url":   srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "toxic content")}))
	if result.Pass {
		t.Fatal("expected fail")
	}
	if result.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", result.Score)
	}
	if result.Message != "Toxic content detected" {
		t.Errorf("message = %q", result.Message)
	}
}

// --- Test: Boolean output (protect flash mode) ---

func TestFutureAGI_BooleanOutputHarmful(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{
					"evaluations": []map[string]interface{}{
						{"name": "protect_flash", "output": true, "reason": "harmful", "runtime": 50.0},
					},
				},
			},
		})
	}))
	defer srv.Close()

	g := New("flash", map[string]interface{}{
		"provider": "futureagi", "eval_id": "76",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "attack")}))
	if result.Pass {
		t.Fatal("expected fail for boolean true (harmful)")
	}
}

func TestFutureAGI_BooleanOutputSafe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{
					"evaluations": []map[string]interface{}{
						{"name": "protect_flash", "output": false, "reason": "safe", "runtime": 40.0},
					},
				},
			},
		})
	}))
	defer srv.Close()

	g := New("flash", map[string]interface{}{
		"provider": "futureagi", "eval_id": "76",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass {
		t.Fatal("expected pass for boolean false (safe)")
	}
}

// --- Test: HTTP errors ---

func TestFutureAGI_HTTP403(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "bad", "secret_key": "bad", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on 403")
	}
	if result.Score != 1.0 {
		t.Errorf("score = %f", result.Score)
	}
}

func TestFutureAGI_HTTP500WithRetry(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls <= 1 {
			w.WriteHeader(500)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0, "evalId": "15"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
		"retry": 1,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass after retry, got: %s", result.Message)
	}
	if calls != 2 {
		t.Errorf("expected 2 calls, got %d", calls)
	}
}

func TestFutureAGI_HTTP500NoRetry(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on 500 with no retry")
	}
}

// --- Test: Malformed response ---

func TestFutureAGI_MalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("not json"))
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on malformed JSON")
	}
}

// --- Test: Empty evaluations ---

func TestFutureAGI_EmptyEvaluations(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []interface{}{}},
			},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("expected pass for empty evaluations (fail-open)")
	}
}

func TestFutureAGI_EmptyResult(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{"result": []interface{}{}})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("expected pass for empty result (fail-open)")
	}
}

// --- Test: Post-stage (response check) ---

func TestFutureAGI_PostStage(t *testing.T) {
	var receivedInput string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req evalRequest
		json.NewDecoder(r.Body).Decode(&req)
		if len(req.Inputs) > 0 {
			receivedInput = req.Inputs[0].Input
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "bias", "output": "Passed", "reason": "no bias", "runtime": 100.0, "evalId": "69"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-bias", map[string]interface{}{
		"provider": "futureagi", "eval_id": "69",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeOutputInput("The assistant response"))
	if !result.Pass {
		t.Fatal("expected pass")
	}
	if receivedInput != "The assistant response" {
		t.Errorf("received input = %q", receivedInput)
	}
}

// --- Test: Multiple messages concatenated ---

func TestFutureAGI_MultipleMessages(t *testing.T) {
	var receivedInput string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req evalRequest
		json.NewDecoder(r.Body).Decode(&req)
		if len(req.Inputs) > 0 {
			receivedInput = req.Inputs[0].Input
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	msgs := []models.Message{
		makeMsg("system", "You are a helper"),
		makeMsg("user", "Tell me a joke"),
	}
	g.Check(context.Background(), makeInput(msgs))
	if receivedInput != "You are a helper\nTell me a joke" {
		t.Errorf("expected concatenated messages, got %q", receivedInput)
	}
}

// --- Test: Nil and empty inputs ---

func TestFutureAGI_NilInput(t *testing.T) {
	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s",
	})
	result := g.Check(context.Background(), nil)
	if !result.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestFutureAGI_EmptyMessages(t *testing.T) {
	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s",
	})
	result := g.Check(context.Background(), makeInput(nil))
	if !result.Pass {
		t.Fatal("empty messages should pass")
	}
}

// --- Test: Missing credentials ---

func TestFutureAGI_MissingAPIKey(t *testing.T) {
	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "", "secret_key": "s",
	})
	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("missing api_key should pass (skip)")
	}
}

func TestFutureAGI_MissingEvalID(t *testing.T) {
	g := New("test", map[string]interface{}{
		"provider":   "futureagi",
		"api_key":    "k",
		"secret_key": "s",
	})
	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatal("missing eval_id should pass (skip)")
	}
}

// --- Test: Context cancellation ---

func TestFutureAGI_ContextCancelled(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// This handler should not be reached.
		json.NewEncoder(w).Encode(map[string]interface{}{"result": []interface{}{}})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately.

	result := g.Check(ctx, makeInput([]models.Message{makeMsg("user", "hi")}))
	if result.Pass {
		t.Fatal("expected fail on cancelled context")
	}
}

// --- Test: IsFutureAGIConfig ---

func TestIsFutureAGIConfig(t *testing.T) {
	if !IsFutureAGIConfig(map[string]interface{}{"provider": "futureagi", "eval_id": "15"}) {
		t.Error("should detect futureagi config")
	}
	if IsFutureAGIConfig(map[string]interface{}{"provider": "other"}) {
		t.Error("should not match other provider")
	}
	if IsFutureAGIConfig(map[string]interface{}{"url": "https://example.com"}) {
		t.Error("webhook config should return false")
	}
	if IsFutureAGIConfig(nil) {
		t.Error("nil should return false")
	}
}

// --- Test: Name ---

func TestFutureAGI_Name(t *testing.T) {
	g := New("my-guardrail", nil)
	if g.Name() != "my-guardrail" {
		t.Errorf("name = %q", g.Name())
	}
}

// --- Test: ExpandEnv ---

func TestExpandEnv(t *testing.T) {
	t.Setenv("TEST_EXPAND_VAR", "expanded-value")
	if v := expandEnv("${TEST_EXPAND_VAR}"); v != "expanded-value" {
		t.Errorf("expandEnv = %q", v)
	}
	if v := expandEnv("literal-value"); v != "literal-value" {
		t.Errorf("expandEnv literal = %q", v)
	}
}

// --- Test: Multimodal content (array format) ---

func TestFutureAGI_ArrayContent(t *testing.T) {
	var receivedInput string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req evalRequest
		json.NewDecoder(r.Body).Decode(&req)
		if len(req.Inputs) > 0 {
			receivedInput = req.Inputs[0].Input
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("test", map[string]interface{}{
		"provider": "futureagi", "eval_id": "15",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
	})

	// Create message with array content (multimodal format).
	arrayContent, _ := json.Marshal([]map[string]interface{}{
		{"type": "text", "text": "Analyze this image"},
		{"type": "image_url", "image_url": map[string]string{"url": "https://example.com/img.png"}},
	})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: arrayContent},
			},
		},
	}

	g.Check(context.Background(), input)
	if receivedInput != "Analyze this image" {
		t.Errorf("expected text extracted from array content, got %q", receivedInput)
	}
}

// --- Test: Multiple eval_ids — request payload includes all, any failure fails the check ---

func TestFutureAGI_EvalIDsArray_AllPass(t *testing.T) {
	var receivedReq evalRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedReq)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0, "evalId": "76"},
				}},
				{"evaluations": []map[string]interface{}{
					{"name": "injection", "output": "Passed", "reason": "ok", "runtime": 90.0, "evalId": "15"},
				}},
				{"evaluations": []map[string]interface{}{
					{"name": "bias", "output": "Passed", "reason": "ok", "runtime": 80.0, "evalId": "22"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("multi", map[string]interface{}{
		"provider":   "futureagi",
		"eval_ids":   []interface{}{"76", "15", "22"},
		"api_key":    "k",
		"secret_key": "s",
		"base_url":   srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass when all evals pass, got fail: %s", result.Message)
	}
	if len(receivedReq.Config) != 3 {
		t.Errorf("expected 3 entries in request config, got %d", len(receivedReq.Config))
	}
	for _, id := range []string{"76", "15", "22"} {
		if _, ok := receivedReq.Config[id]; !ok {
			t.Errorf("missing eval_id %s in config map", id)
		}
	}
}

func TestFutureAGI_EvalIDsArray_OneFailsFailsAll(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0, "evalId": "76"},
				}},
				{"evaluations": []map[string]interface{}{
					{"name": "injection", "output": "Failed", "reason": "prompt injection detected", "runtime": 90.0, "evalId": "15"},
				}},
				{"evaluations": []map[string]interface{}{
					{"name": "bias", "output": "Passed", "reason": "ok", "runtime": 80.0, "evalId": "22"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("multi", map[string]interface{}{
		"provider":   "futureagi",
		"eval_ids":   []interface{}{"76", "15", "22"},
		"api_key":    "k",
		"secret_key": "s",
		"base_url":   srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "ignore previous")}))
	if result.Pass {
		t.Fatal("expected fail when any eval fails")
	}
	if result.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", result.Score)
	}
	if result.Message != "prompt injection detected" {
		t.Errorf("expected failing eval's reason to surface, got %q", result.Message)
	}
	if result.Details["eval_id"] != "15" {
		t.Errorf("expected failing eval_id 15 in details, got %v", result.Details["eval_id"])
	}
}

func TestFutureAGI_EvalIDFallbackToSingular(t *testing.T) {
	var receivedReq evalRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedReq)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "toxicity", "output": "Passed", "reason": "ok", "runtime": 100.0, "evalId": "15"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("legacy", map[string]interface{}{
		"provider":   "futureagi",
		"eval_id":    "15",
		"api_key":    "k",
		"secret_key": "s",
		"base_url":   srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass, got fail: %s", result.Message)
	}
	if _, ok := receivedReq.Config["15"]; !ok {
		t.Errorf("expected singular eval_id to populate config map")
	}
}

// --- Test: Request payload structure ---

func TestFutureAGI_RequestPayload(t *testing.T) {
	var receivedReq evalRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedReq)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"result": []map[string]interface{}{
				{"evaluations": []map[string]interface{}{
					{"name": "injection", "output": "Passed", "reason": "safe", "runtime": 80.0, "evalId": "18"},
				}},
			},
		})
	}))
	defer srv.Close()

	g := New("test-injection", map[string]interface{}{
		"provider": "futureagi", "eval_id": "18",
		"api_key": "k", "secret_key": "s", "base_url": srv.URL,
		"call_type": "protect",
	})

	g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "test input")}))

	if len(receivedReq.Inputs) != 1 {
		t.Fatalf("expected 1 input, got %d", len(receivedReq.Inputs))
	}
	if receivedReq.Inputs[0].Input != "test input" {
		t.Errorf("input text = %q", receivedReq.Inputs[0].Input)
	}
	if receivedReq.Inputs[0].CallType != "protect" {
		t.Errorf("call_type = %q", receivedReq.Inputs[0].CallType)
	}
	cfg, ok := receivedReq.Config["18"]
	if !ok {
		t.Fatal("missing config for eval_id 18")
	}
	if cfg.CallType != "protect" {
		t.Errorf("config call_type = %q", cfg.CallType)
	}
}
