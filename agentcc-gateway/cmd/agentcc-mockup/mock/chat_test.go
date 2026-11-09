package mock

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestChatNonStreamIsPricedFromTheFixtures(t *testing.T) {
	m := newTestMock(t, "")
	resp, b := m.chat(t, "test/model-a", "", false)
	v := decode(t, b)
	if resp.StatusCode != 200 || v["object"] != "chat.completion" || v["model"] != "test/model-a" {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
	choice := v["choices"].([]any)[0].(map[string]any)
	if choice["finish_reason"] != "stop" || choice["message"].(map[string]any)["content"] != "(mock test/model-a) hello" {
		t.Errorf("choice = %v", choice)
	}
	u := v["usage"].(map[string]any)
	// 1,000 prompt x $2/M + 100 completion x $10/M.
	if u["prompt_tokens"] != 1000.0 || u["completion_tokens"] != 100.0 || u["total_tokens"] != 1100.0 || u["cost"] != 0.003 {
		t.Errorf("usage = %v", u)
	}
	if _, ok := u["prompt_tokens_details"]; ok {
		t.Errorf("details are reported only when non-zero: %v", u)
	}
	if _, ok := v["provider"]; ok {
		t.Errorf("no served provider was asked for: %v", v["provider"])
	}
}

func TestUsageCostToggle(t *testing.T) {
	quiet := newTestMock(t, `{"report_cost": false, "prompt_tokens": 1000, "completion_tokens": 100}`)
	_, b := quiet.chat(t, "test/model-a", "", false)
	if _, ok := decode(t, b)["usage"].(map[string]any)["cost"]; ok {
		t.Fatalf("report_cost false still reported usage.cost: %s", b)
	}
	if total, _ := charges(t, quiet); total != 3000 {
		t.Fatalf("an unreported cost is still charged at the table price: %d micros", total)
	}

	_, b = quiet.chat(t, "test/model-a", "usage_cost:0.0123", false)
	if got := decode(t, b)["usage"].(map[string]any)["cost"]; got != 0.0123 {
		t.Fatalf("usage_cost:0.0123 reported %v", got)
	}
	if total, _ := charges(t, quiet); total != 3000+12300 {
		t.Fatalf("usage_cost is what is charged: total %d micros", total)
	}

	_, b = quiet.chat(t, "test/model-a", "no_usage", false)
	if _, ok := decode(t, b)["usage"]; ok {
		t.Fatalf("no_usage still sent usage: %s", b)
	}
}

func TestCacheAndReasoningTokensArePricedAndReported(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.chat(t, "test/model-a", "cached_tokens:400,cache_write_tokens:100,reasoning_tokens:60", false)
	u := decode(t, b)["usage"].(map[string]any)
	if d := u["prompt_tokens_details"].(map[string]any); d["cached_tokens"] != 400.0 || d["cache_write_tokens"] != 100.0 {
		t.Errorf("prompt_tokens_details = %v", d)
	}
	if d := u["completion_tokens_details"].(map[string]any); d["reasoning_tokens"] != 60.0 {
		t.Errorf("completion_tokens_details = %v", d)
	}
	// 500 uncached x $2/M + 400 cached x $0.5/M + 100 written x $3/M + 100 x $10/M.
	if u["cost"] != 0.0025 {
		t.Errorf("cost = %v, want 0.0025", u["cost"])
	}
}

func TestUncataloguedModelUsesTheDefaultPrice(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.chat(t, "vendor/anything", "", false)
	// 1,000 x $3/M + 100 x $4/M.
	if got := decode(t, b)["usage"].(map[string]any)["cost"]; got != 0.0034 {
		t.Fatalf("cost = %v, want 0.0034", got)
	}
}

func TestServedProviderField(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.chat(t, "test/model-a", "served:acme-cloud", false)
	if got := decode(t, b)["provider"]; got != "acme-cloud" {
		t.Fatalf("served:acme-cloud gave provider %v", got)
	}

	body := `{"model":"test/model-a","messages":[],"provider":{"order":["first-host","second-host"],"allow_fallbacks":false}}`
	_, b = m.do(t, "POST", "/api/v1/chat/completions", body)
	if got := decode(t, b)["provider"]; got != "first-host" {
		t.Fatalf("without served, provider = %v, want provider.order[0]", got)
	}
	_, b = m.do(t, "POST", "/api/v1/chat/completions", body, BehaviorHeader, "served:override")
	if got := decode(t, b)["provider"]; got != "override" {
		t.Fatalf("served beats provider.order: provider = %v", got)
	}
}

func TestChatStreamFraming(t *testing.T) {
	m := newTestMock(t, "")
	resp, b := m.chat(t, "test/model-a", "served:acme", true)
	if resp.StatusCode != 200 || !strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		t.Fatalf("status %d type %q", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	events := parseSSE(t, b)
	if last := events[len(events)-1]; last.data != "[DONE]" {
		t.Fatalf("stream must end with [DONE], ended with %q", last.data)
	}
	var content strings.Builder
	var finish any
	var usage map[string]any
	for i, ev := range events[:len(events)-1] {
		var c map[string]any
		if err := json.Unmarshal([]byte(ev.data), &c); err != nil {
			t.Fatalf("event %d is not JSON: %q", i, ev.data)
		}
		if c["object"] != "chat.completion.chunk" || c["provider"] != "acme" {
			t.Fatalf("event %d = %v", i, c)
		}
		choices := c["choices"].([]any)
		if u, ok := c["usage"].(map[string]any); ok {
			if len(choices) != 0 || i != len(events)-2 {
				t.Fatalf("usage must come in the last chunk, with no choices: event %d of %d", i, len(events))
			}
			usage = u
			continue
		}
		ch := choices[0].(map[string]any)
		if s, ok := ch["delta"].(map[string]any)["content"].(string); ok {
			content.WriteString(s)
		}
		if ch["finish_reason"] != nil {
			finish = ch["finish_reason"]
		}
	}
	if content.String() != "(mock test/model-a) hello" || finish != "stop" {
		t.Fatalf("streamed content %q finish %v", content.String(), finish)
	}
	if usage == nil || usage["cost"] != 0.003 {
		t.Fatalf("usage chunk = %v", usage)
	}
	// role, 4 content pieces (stream_chunks), finish, usage, [DONE].
	if len(events) != 8 {
		t.Fatalf("got %d events, want 8", len(events))
	}
}

func TestChatStreamToolCallsAreArgumentDeltas(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.chat(t, "mock/tools", "", true)
	var args strings.Builder
	var name any
	var finish any
	for _, ev := range parseSSE(t, b) {
		if ev.data == "[DONE]" {
			continue
		}
		var c map[string]any
		json.Unmarshal([]byte(ev.data), &c)
		choices, _ := c["choices"].([]any)
		if len(choices) == 0 {
			continue
		}
		ch := choices[0].(map[string]any)
		if ch["finish_reason"] != nil {
			finish = ch["finish_reason"]
		}
		calls, _ := ch["delta"].(map[string]any)["tool_calls"].([]any)
		for _, call := range calls {
			fn := call.(map[string]any)["function"].(map[string]any)
			if n, ok := fn["name"]; ok {
				name = n
			}
			args.WriteString(fn["arguments"].(string))
		}
	}
	if name != "get_weather" || args.String() != `{"query":"mock"}` || finish != "tool_calls" {
		t.Fatalf("tool call %v(%s), finish %v", name, args.String(), finish)
	}
}

func TestStreamAbortDropsTheConnection(t *testing.T) {
	m := newTestMock(t, "")
	body := `{"model":"test/model-a","stream":true,"messages":[]}`
	req, _ := http.NewRequest("POST", m.URL+"/v1/chat/completions", strings.NewReader(body))
	req.Header.Set(BehaviorHeader, "stream_abort_after:2")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	got, err := io.ReadAll(resp.Body)
	if err == nil {
		t.Fatalf("the stream ended cleanly; want a dropped connection. Body:\n%s", got)
	}
	if n := strings.Count(string(got), "data: "); n != 2 || strings.Contains(string(got), "[DONE]") {
		t.Fatalf("want exactly 2 events before the drop, got %d:\n%s", n, got)
	}
	if _, n := charges(t, m); n != 1 {
		t.Fatalf("an aborted stream is still charged once, got %d", n)
	}
}

func TestChunkDelaySpacesTheEvents(t *testing.T) {
	m := newTestMock(t, "")
	start := time.Now()
	_, b := m.chat(t, "test/model-a", "chunk_delay:30", true)
	events := len(parseSSE(t, b))
	if min := time.Duration(events-1) * 30 * time.Millisecond; time.Since(start) < min {
		t.Fatalf("%d events with chunk_delay:30 took %v, want at least %v", events, time.Since(start), min)
	}
}

func TestModelShortcutsOverHTTP(t *testing.T) {
	m := newTestMock(t, "")
	_, b := m.chat(t, "mock/empty", "", false)
	ch := decode(t, b)["choices"].([]any)[0].(map[string]any)
	if ch["message"].(map[string]any)["content"] != "" || decode(t, b)["usage"].(map[string]any)["completion_tokens"] != 0.0 {
		t.Errorf("mock/empty = %s", b)
	}
	_, b = m.chat(t, "mock/filter", "", false)
	if ch := decode(t, b)["choices"].([]any)[0].(map[string]any); ch["finish_reason"] != "content_filter" {
		t.Errorf("mock/filter finish = %v", ch["finish_reason"])
	}
	_, b = m.chat(t, "mock/tools", "", false)
	msg := decode(t, b)["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)
	if msg["content"] != nil || len(msg["tool_calls"].([]any)) != 1 {
		t.Errorf("mock/tools message = %v", msg)
	}
	if resp, _ := m.chat(t, "mock/ok-7", "", false); resp.StatusCode != 200 {
		t.Errorf("mock/ok-7 status %d", resp.StatusCode)
	}
}
