package mcpmock

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"
)

func echoParams(extra map[string]any) map[string]any {
	p := map[string]any{"name": "echo", "arguments": map[string]any{"text": "hi"}}
	for k, v := range extra {
		p[k] = v
	}
	return p
}

func charges(t *testing.T, base string) ([]Charge, int64) {
	t.Helper()
	var out struct {
		Charges     []Charge `json:"charges"`
		TotalMicros int64    `json:"total_micros"`
	}
	getJSON(t, base+"/__charges", &out)
	return out.Charges, out.TotalMicros
}

// TestChargesAtReceiptEvenWhenClientLeaves: the cost log has one entry per
// tools/call received, priced from the fixture or behavior.cost_usd, written
// at receipt; a call whose client went away before the result stays
// completed=false.
func TestChargesAtReceiptEvenWhenClientLeaves(t *testing.T) {
	_, srv := testMock(t, "2026", "none")
	url := srv.URL + "/mcp"
	r := call2026(t, url, "tools/call", echoParams(map[string]any{"_meta": map[string]any{"progressToken": "pt-1"}}))
	res := resultMap(t, r)
	if res["resultType"] != "complete" || !strings.Contains(string(r.msg.Result), `echo ok: {\"text\":\"hi\"}`) {
		t.Fatalf("echo = %s", r.body)
	}

	postControl(t, srv.URL, "/__control/behavior", `{"latency_ms":2000,"cost_usd":0.25}`)
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": "gone-1", "method": "tools/call", "params": meta2026(echoParams(nil))})
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	for _, h := range headers2026("tools/call", "echo") {
		name, value, _ := strings.Cut(h, ": ")
		req.Header.Set(name, value)
	}
	req.Header.Set("Accept", "application/json, text/event-stream")
	if _, err := http.DefaultClient.Do(req); err == nil {
		t.Fatal("the slow call answered within the client's deadline")
	}
	waitFor(t, "the abandoned call's charge", func() bool { c, _ := charges(t, srv.URL); return len(c) == 2 })
	time.Sleep(2200 * time.Millisecond) // the mock finishes the call; the client is gone
	got, total := charges(t, srv.URL)
	if got[0].Tool != "echo" || got[0].PriceUSD != 0.001 || !got[0].Completed || string(got[0].ProgressTokenSeen) != `"pt-1"` {
		t.Fatalf("first charge = %+v", got[0])
	}
	if got[1].PriceUSD != 0.25 || got[1].Completed || string(got[1].RequestIDSeen) != `"gone-1"` {
		t.Fatalf("abandoned charge = %+v, want price 0.25, completed false", got[1])
	}
	if total != 251000 {
		t.Fatalf("total_micros = %d, want 251000", total)
	}

	postControl(t, srv.URL, "/__control/behavior", `{"cost_usd":0.5,"is_error":true,"result_bytes":64}`)
	res = resultMap(t, call2026(t, url, "tools/call", echoParams(nil)))
	meta, _ := res["_meta"].(map[string]any)
	text := res["content"].([]any)[0].(map[string]any)["text"].(string)
	if res["isError"] != true || meta[metaCostUSD] != 0.5 || len(text) != 64 {
		t.Fatalf("armed result = %v", res)
	}
}

// TestFailNextAndUnknownTool: fail_next fails exactly the next n calls with
// HTTP 502 and -32603 (still charged: they were received); an unknown tool
// is -32602.
func TestFailNextAndUnknownTool(t *testing.T) {
	_, srv := testMock(t, V1125, "none")
	url := srv.URL + "/mcp"
	sid := initLegacy(t, url, V1125, nil)
	postControl(t, srv.URL, "/__control/behavior", `{"fail_next":2}`)
	for i := 0; i < 2; i++ {
		r := rpc(t, url, "tools/call", 10+i, echoParams(nil), "Mcp-Session-Id: "+sid)
		if r.status != http.StatusBadGateway || r.msg.Error == nil || r.msg.Error.Code != CodeInternal {
			t.Fatalf("armed failure %d = %d %s", i, r.status, r.body)
		}
	}
	if r := rpc(t, url, "tools/call", 12, echoParams(nil), "Mcp-Session-Id: "+sid); r.status != 200 || r.msg.Error != nil {
		t.Fatalf("the call after the armed failures = %d %s", r.status, r.body)
	}
	got, _ := charges(t, srv.URL)
	if len(got) != 3 || got[0].Completed || got[1].Completed || !got[2].Completed {
		t.Fatalf("charges = %+v", got)
	}
	if r := rpc(t, url, "tools/call", 13, map[string]any{"name": "nope"}, "Mcp-Session-Id: "+sid); r.msg.Error == nil || r.msg.Error.Code != CodeInvalidParams {
		t.Fatalf("unknown tool = %s", r.body)
	}
}

// TestSSEAnswerCarriesProgressAndLogs: with sse armed, a tools/call answers
// as an SSE stream: the armed progress notifications (with the caller's
// token), the log messages, then the response. A JSON-only Accept gets 406.
func TestSSEAnswerCarriesProgressAndLogs(t *testing.T) {
	_, srv := testMock(t, V1125, "none")
	url := srv.URL + "/mcp"
	sid := initLegacy(t, url, V1125, nil)
	postControl(t, srv.URL, "/__control/behavior", `{"sse":true,"progress":2,"log_messages":1}`)
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
		"params": echoParams(map[string]any{"_meta": map[string]any{"progressToken": 42}})})
	req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Session-Id", sid)
	events, resp, cancel := sseStream(t, req)
	defer cancel()
	if resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("Content-Type = %q", resp.Header.Get("Content-Type"))
	}
	methods := []string{}
	for i := 0; i < 4; i++ {
		msg := decodeMessage(t, nextEvent(t, events, 5*time.Second).data)
		if msg.Method == "notifications/progress" && !strings.Contains(string(msg.Params), `"progressToken":42`) {
			t.Fatalf("progress without the caller's token: %s", msg.Params)
		}
		if msg.Method == "" {
			methods = append(methods, "result:"+string(msg.ID))
			continue
		}
		methods = append(methods, msg.Method)
	}
	want := "notifications/progress notifications/progress notifications/message result:7"
	if strings.Join(methods, " ") != want {
		t.Fatalf("stream = %v, want %s", methods, want)
	}
	if r := rpc(t, url, "tools/list", 8, nil, "Mcp-Session-Id: "+sid, "Accept: application/json"); r.status != http.StatusNotAcceptable {
		t.Fatalf("an SSE answer to a JSON-only client = %d, want 406", r.status)
	}
}

// TestLegacyElicitationRoundTrip: a 2025-06-18 client that declared
// elicitation gets elicitation/create on the call's SSE stream; its POSTed
// answer completes the call on the same stream.
func TestLegacyElicitationRoundTrip(t *testing.T) {
	_, srv := testMock(t, V0618, "none")
	url := srv.URL + "/mcp"
	sid := initLegacy(t, url, V0618, map[string]any{"elicitation": map[string]any{}})
	postControl(t, srv.URL, "/__control/behavior", `{"elicit":true}`)
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": echoParams(nil)})
	req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Session-Id", sid)
	events, _, cancel := sseStream(t, req)
	defer cancel()
	ask := decodeMessage(t, nextEvent(t, events, 5*time.Second).data)
	if ask.Method != "elicitation/create" || len(ask.ID) == 0 {
		t.Fatalf("first event = %+v, want an elicitation/create request", ask)
	}
	answer := map[string]any{"jsonrpc": "2.0", "id": json.RawMessage(ask.ID),
		"result": map[string]any{"action": "accept", "content": map[string]any{"confirm": true}}}
	data, _ := json.Marshal(answer)
	areq, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(data))
	areq.Header.Set("Content-Type", "application/json")
	areq.Header.Set("Mcp-Session-Id", sid)
	aresp, err := http.DefaultClient.Do(areq)
	if err != nil {
		t.Fatal(err)
	}
	aresp.Body.Close()
	if aresp.StatusCode != http.StatusAccepted {
		t.Fatalf("the answer POST = %d, want 202", aresp.StatusCode)
	}
	final := decodeMessage(t, nextEvent(t, events, 5*time.Second).data)
	if string(final.ID) != "5" || !strings.Contains(string(final.Result), `(input: {\"confirm\":true})`) {
		t.Fatalf("final = %+v %s", final, final.Result)
	}
	// Without the capability the mock refuses to elicit.
	sid2 := initLegacy(t, url, V0618, nil)
	if r := rpc(t, url, "tools/call", 6, echoParams(nil), "Mcp-Session-Id: "+sid2); !strings.Contains(string(r.body), "elicitation capability") {
		t.Fatalf("elicit without the capability = %s", r.body)
	}
}

// TestInputRequiredRoundTrip: a 2026 call with elicit armed answers
// input_required with the mock's sealed requestState; the retry with
// inputResponses completes; a tampered or foreign requestState is -32602.
func TestInputRequiredRoundTrip(t *testing.T) {
	_, srv := testMock(t, "2026", "none")
	url := srv.URL + "/mcp"
	postControl(t, srv.URL, "/__control/behavior", `{"elicit":true}`)
	first := resultMap(t, call2026(t, url, "tools/call", echoParams(nil)))
	state, _ := first["requestState"].(string)
	if first["resultType"] != "input_required" || state == "" || first["inputRequests"].(map[string]any)["confirm"] == nil {
		t.Fatalf("first call = %v", first)
	}
	accept := map[string]any{"confirm": map[string]any{"action": "accept", "content": map[string]any{"confirm": true}}}
	final := resultMap(t, call2026(t, url, "tools/call", echoParams(map[string]any{"inputResponses": accept, "requestState": state})))
	if final["resultType"] != "complete" || !strings.Contains(final["content"].([]any)[0].(map[string]any)["text"].(string), "input:") {
		t.Fatalf("second call = %v", final)
	}
	// The money oracle: the call is charged once (echo is $0.001), on the
	// retry that completed it; the input_required round is logged at price 0,
	// not completed, and left out of total_micros and billable_count.
	var log struct {
		Charges       []Charge `json:"charges"`
		TotalMicros   int64    `json:"total_micros"`
		Count         int      `json:"count"`
		BillableCount int      `json:"billable_count"`
	}
	getJSON(t, srv.URL+"/__charges", &log)
	if log.Count != 2 || log.BillableCount != 1 || log.TotalMicros != 1000 {
		t.Fatalf("/__charges = count %d, billable %d, total %d; want 2, 1, 1000", log.Count, log.BillableCount, log.TotalMicros)
	}
	if r1, r2 := log.Charges[0], log.Charges[1]; !r1.InputRequired || r1.PriceUSD != 0 || r1.Completed ||
		r2.InputRequired || r2.PriceUSD != 0.001 || !r2.Completed {
		t.Fatalf("charges = %+v: the first call is input_required (price 0, not completed), the second the billable completion", log.Charges)
	}
	last := state[len(state)-1:] // the HMAC is hex: change its last digit
	tampered := state[:len(state)-1] + map[bool]string{true: "1", false: "0"}[last == "0"]
	for name, params := range map[string]map[string]any{
		"tampered":   echoParams(map[string]any{"inputResponses": accept, "requestState": tampered}),
		"other args": {"name": "echo", "arguments": map[string]any{"text": "other"}, "inputResponses": accept, "requestState": state},
		"other tool": {"name": "get_weather", "arguments": map[string]any{"text": "hi"}, "inputResponses": accept, "requestState": state},
		"no state":   echoParams(map[string]any{"inputResponses": accept}),
		"declined":   echoParams(map[string]any{"inputResponses": map[string]any{"confirm": map[string]any{"action": "decline"}}, "requestState": state}),
	} {
		r := call2026(t, url, "tools/call", params)
		if r.msg.Error == nil || r.msg.Error.Code != CodeInvalidParams {
			t.Fatalf("%s retry = %s, want -32602", name, r.body)
		}
	}
}

// TestRugPullReachesListenAndLegacyStreams: /__control/tools replaces the
// tool set and sends tools list_changed to a 2026 subscriptions/listen
// stream (tagged with its subscriptionId) and to a 2024-11-05 /sse stream.
func TestRugPullReachesListenAndLegacyStreams(t *testing.T) {
	m, srv := testMock(t, "dual", "none")
	url := srv.URL + "/mcp"
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "subscriptions/listen",
		"params": meta2026(map[string]any{"notifications": []string{"notifications/tools/list_changed"}})})
	req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	req.Header.Set("Accept", "application/json, text/event-stream")
	for _, h := range headers2026("subscriptions/listen", "") {
		name, value, _ := strings.Cut(h, ": ")
		req.Header.Set(name, value)
	}
	listen, _, cancel := sseStream(t, req)
	defer cancel()
	waitFor(t, "the listen stream", func() bool { return m.listenerCount() == 1 })

	sreq, _ := http.NewRequest(http.MethodGet, srv.URL+"/sse", nil)
	sreq.Header.Set("Accept", "text/event-stream")
	sse, _, cancel2 := sseStream(t, sreq)
	defer cancel2()
	nextEvent(t, sse, 5*time.Second) // the endpoint event

	postControl(t, srv.URL, "/__control/behavior", `{"notify":["prompts/list_changed"]}`) // not subscribed
	postControl(t, srv.URL, "/__control/tools", `{"tools":[{"name":"new_tool","price_usd":0.002}]}`)
	got := decodeMessage(t, nextEvent(t, listen, 5*time.Second).data)
	if got.Method != "notifications/tools/list_changed" || !strings.Contains(string(got.Params), "subscriptionId") {
		t.Fatalf("listen got %+v %s, want only the subscribed list_changed with its subscriptionId", got, got.Params)
	}
	ev1 := decodeMessage(t, nextEvent(t, sse, 5*time.Second).data)
	ev2 := decodeMessage(t, nextEvent(t, sse, 5*time.Second).data)
	if ev1.Method != "notifications/prompts/list_changed" || ev2.Method != "notifications/tools/list_changed" {
		t.Fatalf("/sse got %s then %s", ev1.Method, ev2.Method)
	}
	tools := resultMap(t, call2026(t, url, "tools/list", nil))["tools"].([]any)
	if len(tools) != 1 || tools[0].(map[string]any)["name"] != "new_tool" {
		t.Fatalf("after the tool set replacement tools/list = %v", tools)
	}
	resultMap(t, call2026(t, url, "tools/call", map[string]any{"name": "new_tool"}))
	if got, _ := charges(t, srv.URL); got[0].PriceUSD != 0.002 {
		t.Fatalf("the new tool's price = %v", got[0].PriceUSD)
	}
}
