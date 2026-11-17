package server

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Each fixture is authored in its own protocol, never through the production
// converters. Bytes are integer arrays (not Go's base64-encoded []byte).
func completionCacheLogprobsFixture(text string, native bool) *json.RawMessage {
	var value any
	if native {
		value = map[string]any{
			"tokens": []string{text}, "token_logprobs": []float64{-0.25},
			"top_logprobs": []map[string]float64{{text: -0.25}}, "text_offset": []int{0},
		}
	} else {
		bytes := make([]int, len(text))
		for i := range text {
			bytes[i] = int(text[i])
		}
		value = map[string]any{"content": []any{map[string]any{
			"token": text, "logprob": -0.25, "bytes": bytes,
			"top_logprobs": []any{map[string]any{"token": text, "logprob": -0.25, "bytes": bytes}},
		}}}
	}
	encoded, _ := json.Marshal(value)
	raw := json.RawMessage(encoded)
	return &raw
}

func completionCacheRequestBody(native, logprobs bool) string {
	if native {
		if logprobs {
			return completionCacheBody
		}
		return strings.Replace(completionCacheBody, `,"logprobs":1`, "", 1)
	}
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"cached prompt"}],"max_tokens":16,"temperature":0,"n":2`
	if logprobs {
		body += `,"logprobs":true,"top_logprobs":1`
	}
	return body + "}"
}

func assertCompletionCacheLogprobs(t *testing.T, w *httptest.ResponseRecorder, native, requested bool) string {
	t.Helper()
	var response struct {
		ID      string `json:"id"`
		Object  string `json:"object"`
		Choices []struct {
			Logprobs *json.RawMessage `json:"logprobs"`
		} `json:"choices"`
		Usage *models.Usage `json:"usage"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	object := "chat.completion"
	if native {
		object = "text_completion"
		decodeCachedCompletion(t, w)
	}
	if response.Object != object || len(response.Choices) != 2 || response.Usage == nil || response.Usage.TotalTokens != 9 {
		t.Fatalf("lost response envelope/choices/usage: %s", w.Body.String())
	}
	for i, choice := range response.Choices {
		if !requested {
			if choice.Logprobs != nil {
				t.Errorf("choice %d returned unrequested logprobs: %s", i, *choice.Logprobs)
			}
			continue
		}
		text := []string{"first cached answer", "second cached answer"}[i]
		var got, want any
		if choice.Logprobs == nil {
			t.Errorf("choice %d lost requested logprobs", i)
			continue
		}
		if err := json.Unmarshal(*choice.Logprobs, &got); err != nil {
			t.Fatal(err)
		}
		_ = json.Unmarshal(*completionCacheLogprobsFixture(text, native), &want)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("choice %d has wrong route logprobs: got %s, want %s", i, *choice.Logprobs, *completionCacheLogprobsFixture(text, native))
		}
	}
	return response.ID
}

func TestCompletionCacheCrossRouteLogprobs(t *testing.T) {
	for _, nativeFirst := range []bool{true, false} {
		for _, sourceLogs := range []bool{false, true} {
			for _, targetLogs := range []bool{false, true} {
				for _, custom := range []bool{false, true} {
					name := fmt.Sprintf("native_first=%t/source=%t/target=%t/custom=%t", nativeFirst, sourceLogs, targetLogs, custom)
					t.Run(name, func(t *testing.T) {
						srv, calls := newCompletionCacheGateway(t)
						paths := []string{"/v1/completions", "/v1/chat/completions"}
						if !nativeFirst {
							paths[0], paths[1] = paths[1], paths[0]
						}
						var headers map[string]string
						if custom {
							headers = map[string]string{"x-agentcc-cache-key": "shared-answer"}
						}
						first := postCompletionCache(t, srv, paths[0], completionCacheRequestBody(nativeFirst, sourceLogs), headers)
						assertCompletionCacheHeaders(t, first, "miss")
						assertCompletionCacheLogprobs(t, first, nativeFirst, sourceLogs)
						wantStatus, wantCalls := "hit_exact", int64(1)
						if sourceLogs || targetLogs {
							// Protocol-specific logprobs cannot be converted losslessly.
							wantStatus, wantCalls = "miss", 2
						}
						second := postCompletionCache(t, srv, paths[1], completionCacheRequestBody(!nativeFirst, targetLogs), headers)
						assertCompletionCacheHeaders(t, second, wantStatus)
						id := assertCompletionCacheLogprobs(t, second, !nativeFirst, targetLogs)
						if calls.Load() != wantCalls {
							t.Errorf("upstream calls=%d, want %d", calls.Load(), wantCalls)
						}
						// Each route still hits its own exact entry, with all logprobs
						// bytes retained and without charging or calling upstream again.
						for i, logs := range []bool{sourceLogs, targetLogs} {
							native := nativeFirst == (i == 0)
							hit := postCompletionCache(t, srv, paths[i], completionCacheRequestBody(native, logs), headers)
							assertCompletionCacheHeaders(t, hit, "hit_exact")
							gotID := assertCompletionCacheLogprobs(t, hit, native, logs)
							if i == 1 && gotID != id {
								t.Errorf("same-route hit changed identity: %q != %q", gotID, id)
							}
						}
						if calls.Load() != wantCalls {
							t.Errorf("repeat called upstream: %d, want %d", calls.Load(), wantCalls)
						}
					})
				}
			}
		}
	}
}

func TestCompletionCacheLogprobsOptions(t *testing.T) {
	for _, native := range []bool{true, false} {
		t.Run(fmt.Sprintf("native=%t", native), func(t *testing.T) {
			srv, calls := newCompletionCacheGateway(t)
			path, field := "/v1/chat/completions", `"top_logprobs":1`
			if native {
				path, field = "/v1/completions", `"logprobs":1`
			}
			body := completionCacheRequestBody(native, true)
			for i, count := range []int{0, 1, 2} {
				variant := strings.Replace(body, field, strings.TrimSuffix(field, "1")+fmt.Sprint(count), 1)
				for _, status := range []string{"miss", "hit_exact"} {
					w := postCompletionCache(t, srv, path, variant, nil)
					assertCompletionCacheHeaders(t, w, status)
					if calls.Load() != int64(i+1) {
						t.Errorf("count=%d calls=%d, want %d", count, calls.Load(), i+1)
					}
				}
			}
		})
	}
}
