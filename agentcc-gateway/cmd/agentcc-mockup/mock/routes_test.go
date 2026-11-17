package mock

import (
	"encoding/base64"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func TestMessagesReportAnthropicCacheUsage(t *testing.T) {
	m := newTestMock(t, "")
	body := `{"model":"test/model-a","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}`
	resp, b := m.do(t, "POST", "/v1/messages", body, BehaviorHeader, "cache_read:600,cache_creation:200")
	v := decode(t, b)
	if resp.StatusCode != 200 || v["type"] != "message" || v["stop_reason"] != "end_turn" {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
	u := v["usage"].(map[string]any)
	if u["cache_read_input_tokens"] != 600.0 || u["cache_creation_input_tokens"] != 200.0 || u["input_tokens"] != 200.0 || u["output_tokens"] != 100.0 {
		t.Fatalf("usage = %v (input_tokens excludes the 800 cache tokens)", u)
	}
	// 200 x $2/M + 600 x $0.5/M + 200 x $3/M + 100 x $10/M.
	if total, _ := charges(t, m); total != 2300 {
		t.Fatalf("charged %d micros, want 2300", total)
	}
}

func TestMessagesStreamCarriesCacheUsage(t *testing.T) {
	m := newTestMock(t, "")
	body := `{"model":"test/model-a","stream":true,"max_tokens":10,"messages":[]}`
	_, b := m.do(t, "POST", "/v1/messages", body, BehaviorHeader, "cache_read:600")
	events := parseSSE(t, b)
	var types []string
	var text strings.Builder
	for _, ev := range events {
		var v map[string]any
		json.Unmarshal([]byte(ev.data), &v)
		if v["type"] != ev.event {
			t.Fatalf("event %q carries type %v", ev.event, v["type"])
		}
		types = append(types, ev.event)
		switch ev.event {
		case "message_start":
			u := v["message"].(map[string]any)["usage"].(map[string]any)
			if u["cache_read_input_tokens"] != 600.0 || u["input_tokens"] != 400.0 {
				t.Fatalf("message_start usage = %v", u)
			}
		case "content_block_delta":
			text.WriteString(v["delta"].(map[string]any)["text"].(string))
		case "message_delta":
			if v["delta"].(map[string]any)["stop_reason"] != "end_turn" || v["usage"].(map[string]any)["output_tokens"] != 100.0 {
				t.Fatalf("message_delta = %v", v)
			}
		}
	}
	if types[0] != "message_start" || types[1] != "content_block_start" || types[len(types)-2] != "message_delta" || types[len(types)-1] != "message_stop" {
		t.Fatalf("event order = %v", types)
	}
	if text.String() != "(mock test/model-a) hello" {
		t.Fatalf("streamed text %q", text.String())
	}
}

func TestMessagesToolUseAndErrors(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.do(t, "POST", "/v1/messages", `{"model":"mock/tools","messages":[],"tools":[{"name":"search","input_schema":{}}]}`)
	v := decode(t, b)
	block := v["content"].([]any)[0].(map[string]any)
	if v["stop_reason"] != "tool_use" || block["type"] != "tool_use" || block["name"] != "search" || block["input"].(map[string]any)["query"] != "mock" {
		t.Fatalf("tool use = %s", b)
	}
	resp, b := m.do(t, "POST", "/v1/messages", `{"model":"mock/fail-529","messages":[]}`)
	if resp.StatusCode != 529 || decode(t, b)["type"] != "error" || decode(t, b)["error"].(map[string]any)["type"] != "api_error" {
		t.Fatalf("anthropic error: %d %s", resp.StatusCode, b)
	}
}

func TestResponsesShape(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.do(t, "POST", "/v1/responses", `{"model":"test/model-a","input":"hi"}`, BehaviorHeader, "cached_tokens:100,reasoning_tokens:20")
	v := decode(t, b)
	out := v["output"].([]any)[0].(map[string]any)
	text := out["content"].([]any)[0].(map[string]any)["text"]
	u := v["usage"].(map[string]any)
	if v["object"] != "response" || v["status"] != "completed" || text != "(mock test/model-a) hello" {
		t.Fatalf("response = %s", b)
	}
	if u["input_tokens"] != 1000.0 || u["input_tokens_details"].(map[string]any)["cached_tokens"] != 100.0 ||
		u["output_tokens_details"].(map[string]any)["reasoning_tokens"] != 20.0 || u["cost"] == nil {
		t.Fatalf("usage = %v", u)
	}
	_, b = m.do(t, "POST", "/v1/responses", `{"model":"test/model-a","input":"hi"}`, BehaviorHeader, "finish:length")
	if v := decode(t, b); v["status"] != "incomplete" || v["incomplete_details"].(map[string]any)["reason"] != "max_output_tokens" {
		t.Fatalf("finish:length = %s", b)
	}
}

func TestResponsesStreamEndsCompleted(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.do(t, "POST", "/v1/responses", `{"model":"mock/tools","input":"hi","stream":true}`)
	events := parseSSE(t, b)
	var args strings.Builder
	for i, ev := range events {
		var v map[string]any
		json.Unmarshal([]byte(ev.data), &v)
		if v["type"] != ev.event || v["sequence_number"] != float64(i) {
			t.Fatalf("event %d %q = %v", i, ev.event, v)
		}
		if ev.event == "response.function_call_arguments.delta" {
			args.WriteString(v["delta"].(string))
		}
	}
	last := events[len(events)-1]
	var done map[string]any
	json.Unmarshal([]byte(last.data), &done)
	r := done["response"].(map[string]any)
	if events[0].event != "response.created" || last.event != "response.completed" || r["usage"] == nil ||
		r["output"].([]any)[0].(map[string]any)["type"] != "function_call" || args.String() != `{"query":"mock"}` {
		t.Fatalf("stream %v ended with %s", events[0].event, last.data)
	}
}

func TestEmbeddingsImagesAndSpeech(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.do(t, "POST", "/v1/embeddings", `{"model":"test/model-b","input":["abcdefgh","x"],"dimensions":3}`)
	v := decode(t, b)
	data := v["data"].([]any)
	if len(data) != 2 || len(data[1].(map[string]any)["embedding"].([]any)) != 3 || v["usage"].(map[string]any)["prompt_tokens"] != 3.0 {
		t.Fatalf("embeddings = %s", b)
	}
	_, b = m.do(t, "POST", "/v1/embeddings", `{"model":"test/model-b","input":"abcd","encoding_format":"base64"}`)
	raw, err := base64.StdEncoding.DecodeString(decode(t, b)["data"].([]any)[0].(map[string]any)["embedding"].(string))
	if err != nil || len(raw) != 4*8 {
		t.Fatalf("base64 embedding: %v, %d bytes", err, len(raw))
	}

	_, b = m.do(t, "POST", "/v1/images/generations", `{"model":"img","prompt":"a cat","n":2,"response_format":"b64_json"}`)
	if imgs := decode(t, b)["data"].([]any); len(imgs) != 2 || imgs[0].(map[string]any)["b64_json"] != tinyPNG {
		t.Fatalf("images = %s", b)
	}

	resp, b := m.do(t, "POST", "/v1/audio/speech", `{"model":"tts","input":"hello","voice":"x"}`)
	if resp.Header.Get("Content-Type") != "audio/mpeg" || !strings.HasPrefix(string(b), "ID3") {
		t.Fatalf("speech: %s %q", resp.Header.Get("Content-Type"), b)
	}
	// embeddings 3 + 1 tokens x $1/M, two images x $0.04 (default), 5 chars x $0.000015.
	if total, n := charges(t, m); n != 4 || total != 3+1+80000+75 {
		t.Fatalf("charges = %d over %d", total, n)
	}
}

func TestCatalogRoutes(t *testing.T) {
	m := newTestMock(t, "")
	for _, path := range []string{"/v1/models", "/api/v1/models"} {
		_, b := m.do(t, "GET", path, "")
		if data := decode(t, b)["data"].([]any); len(data) != 2 {
			t.Fatalf("%s listed %d models", path, len(data))
		}
	}
	_, b := m.do(t, "GET", "/api/v1/models/test/model-a/endpoints", "")
	d := decode(t, b)["data"].(map[string]any)
	ep := d["endpoints"].([]any)[0].(map[string]any)
	if d["id"] != "test/model-a" || ep["tag"] != "test" || ep["max_completion_tokens"] != 500.0 || ep["pricing"].(map[string]any)["prompt"] != "0.000002" {
		t.Fatalf("endpoints = %s", b)
	}
	if resp, _ := m.do(t, "GET", "/api/v1/models/test/nope/endpoints", ""); resp.StatusCode != 404 {
		t.Fatalf("unknown model endpoints: %d", resp.StatusCode)
	}
	_, b = m.do(t, "GET", "/api/v1/endpoints/zdr", "")
	if !strings.Contains(string(b), `"data":[]`) {
		t.Fatalf("zdr without a fixture = %s", b)
	}
}

func TestFixtureEndpointsAndZDROverride(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "models.json"), testModels)
	writeFile(t, filepath.Join(dir, "zdr.json"), `{"data":[{"name":"z"}]}`)
	writeFile(t, filepath.Join(dir, "endpoints", "test", "model-a.json"), `{"data":{"id":"test/model-a","endpoints":[{"name":"fixture"}]}}`)
	m := serveFixtures(t, dir)
	_, b := m.do(t, "GET", "/api/v1/models/test/model-a/endpoints", "")
	if !strings.Contains(string(b), `"fixture"`) {
		t.Fatalf("fixture endpoints not served: %s", b)
	}
	_, b = m.do(t, "GET", "/api/v1/endpoints/zdr", "")
	if !strings.Contains(string(b), `"z"`) {
		t.Fatalf("zdr fixture not served: %s", b)
	}
}

func TestLoadFixtures(t *testing.T) {
	common, err := LoadFixtures(filepath.Join("..", "..", "..", "scripts", "scenarios", "fixtures", "common"))
	if err != nil {
		t.Fatalf("the scenarios' common fixtures must load: %v", err)
	}
	if p := common.PriceOf("anthropic/claude-sonnet-5.5"); p.Prompt != 0.000002 || p.Completion != 0.00001 {
		t.Fatalf("common fixture price = %+v", p)
	}
	if !common.Config.ReportCost || common.Config.PromptTokens != 1200 || common.Config.CompletionTokens != 300 {
		t.Fatalf("common config = %+v", common.Config)
	}

	empty, err := LoadFixtures("")
	if err != nil || empty.Config.PromptTokens != 1200 || len(empty.models) != 0 {
		t.Fatalf("no fixtures dir: %+v, %v", empty.Config, err)
	}
	if p := empty.PriceOf("x/y"); p.CacheRead != p.Prompt || p.Prompt != 0.000001 {
		t.Fatalf("default price = %+v", p)
	}

	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "config.json"), `{"balance_usd": "not-a-number"}`)
	if _, err := LoadFixtures(dir); err == nil {
		t.Fatal("a bad decimal must fail the load")
	}
	writeFile(t, filepath.Join(dir, "config.json"), `{}`)
	writeFile(t, filepath.Join(dir, "models.json"), `{"data":[{"name":"no id"}]}`)
	if _, err := LoadFixtures(dir); err == nil {
		t.Fatal("a catalog model without an id must fail the load")
	}
}
