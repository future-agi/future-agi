package mock

import (
	"net/http"
	"strings"
	"testing"
)

func TestReceivedCapturesUpstreamRequests(t *testing.T) {
	m := newTestMock(t, "")
	m.do(t, "GET", "/api/v1/models", "", "Authorization", "Bearer key-cat")
	m.chat(t, "test/model-a", "", false, "X-Trace", "abc")
	m.do(t, "GET", "/__charges", "") // inspection calls are not upstream requests
	m.do(t, "POST", logWebhookPath, `{"logs":[]}`)

	_, b := m.do(t, "GET", "/__received", "")
	all := decode(t, b)["data"].([]any)
	if len(all) != 2 {
		t.Fatalf("want the catalog GET and the chat POST only, got %d: %s", len(all), b)
	}
	_, b = m.do(t, "GET", "/__received?last=1&path=/chat/completions", "")
	got := decode(t, b)["data"].([]any)[0].(map[string]any)
	headers := got["headers"].(map[string]any)
	body := got["body"].(map[string]any)
	if got["method"] != "POST" || got["path"] != "/v1/chat/completions" || headers["authorization"] != "Bearer key-1" ||
		headers["x-trace"] != "abc" || body["model"] != "test/model-a" {
		t.Fatalf("received = %v", got)
	}
	if resp, _ := m.do(t, "GET", "/__received?last=x", ""); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("a bad last gives %d", resp.StatusCode)
	}
}

func TestChargesSumEveryCallByBearer(t *testing.T) {
	m := newTestMock(t, "")
	m.chat(t, "test/model-a", "", false)                                  // 3,000 micros for key-1
	m.chat(t, "test/model-b", "", false, "Authorization", "Bearer key-2") // 1,000 x $1/M + 100 x $2/M
	m.chat(t, "test/model-a", "fail:500", false)                          // not charged
	m.do(t, "POST", "/v1/messages", `{"model":"test/model-b","messages":[]}`, "x-api-key", "key-3")

	total, count := charges(t, m)
	if total != 3000+1200+1200 || count != 3 {
		t.Fatalf("charges = %d micros over %d calls", total, count)
	}
	byBearer := m.srv.State().ChargesByBearer()
	if byBearer["key-1"] != 3000 || byBearer["key-2"] != 1200 || byBearer["key-3"] != 1200 || len(byBearer) != 3 {
		t.Fatalf("by bearer = %v", byBearer)
	}
	var sum int64
	for _, v := range byBearer {
		sum += v
	}
	if sum != total {
		t.Fatalf("per-bearer charges sum to %d, /__charges says %d", sum, total)
	}
}

func TestCreditsReportTheBalance(t *testing.T) {
	m := newTestMock(t, "")
	m.chat(t, "test/model-a", "", false)
	_, b := m.do(t, "GET", "/api/v1/credits", "")
	d := decode(t, b)["data"].(map[string]any)
	if d["total_credits"] != 10.0 || d["total_usage"] != 0.003 {
		t.Fatalf("credits = %v", d)
	}
	st := m.srv.State()
	if st.BalanceMicros() != 10_000_000-3000 {
		t.Fatalf("balance = %d", st.BalanceMicros())
	}
	st.SetBalanceMicros(500)
	if st.BalanceMicros() != 500 || st.CreditMicros() != 3500 {
		t.Fatalf("after SetBalanceMicros(500): balance %d credits %d", st.BalanceMicros(), st.CreditMicros())
	}
}

func TestLogsWebhookStoresEachEntry(t *testing.T) {
	m := newTestMock(t, "")
	m.do(t, "POST", logWebhookPath, `{"logs":[{"request_id":"r1"},{"request_id":"r2"}]}`)
	m.do(t, "POST", "/__logs", `{"request_id":"r3"}`)
	if resp, _ := m.do(t, "POST", "/__logs", `not json`); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("invalid log body gave %d", resp.StatusCode)
	}
	_, b := m.do(t, "GET", "/__logs", "")
	v := decode(t, b)
	if logs := v["logs"].([]any); len(logs) != 3 || logs[2].(map[string]any)["request_id"] != "r3" || v["batches"] != 2.0 {
		t.Fatalf("logs = %s", b)
	}
	_, b = m.do(t, "GET", "/__logs?last=1", "")
	if logs := decode(t, b)["logs"].([]any); len(logs) != 1 {
		t.Fatalf("last=1 gave %s", b)
	}
}

func TestResetClearsEverything(t *testing.T) {
	m := newTestMock(t, "")
	hookRan := false
	m.srv.State().OnReset(func() { hookRan = true })
	m.chat(t, "test/model-a", "", false)
	m.do(t, "POST", "/__logs", `{"a":1}`)
	m.do(t, "POST", "/__behavior", `{"behavior":"fail:500"}`)
	m.srv.State().SetBalanceMicros(1)

	m.do(t, "POST", "/__reset", "")
	if total, n := charges(t, m); total != 0 || n != 0 {
		t.Fatalf("charges after reset: %d over %d", total, n)
	}
	_, b := m.do(t, "GET", "/__logs", "")
	if !strings.Contains(string(b), `"logs":[]`) {
		t.Fatalf("logs after reset: %s", b)
	}
	_, b = m.do(t, "GET", "/__received", "")
	if !strings.Contains(string(b), `"data":[]`) {
		t.Fatalf("received after reset: %s", b)
	}
	if m.srv.State().Armed() != nil || m.srv.State().BalanceMicros() != 10_000_000 || !hookRan {
		t.Fatalf("armed %v balance %d hook %v", m.srv.State().Armed(), m.srv.State().BalanceMicros(), hookRan)
	}
}

func TestArmedBehaviorReachesHeaderlessInferenceCalls(t *testing.T) {
	m := newTestMock(t, "")
	if resp, _ := m.do(t, "POST", "/__behavior", `{"behavior":""}`); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("an empty behaviour must be refused, got %d", resp.StatusCode)
	}
	m.do(t, "POST", "/__behavior", `{"behavior":"fail:503:n=1"}`)
	if resp, _ := m.do(t, "GET", "/api/v1/models", ""); resp.StatusCode != 200 {
		t.Fatalf("armed behaviour reached a catalog GET: %d", resp.StatusCode)
	}
	if resp, _ := m.chat(t, "test/model-a", "", false); resp.StatusCode != 503 {
		t.Fatalf("armed fail:503:n=1, first call: %d", resp.StatusCode)
	}
	if resp, _ := m.chat(t, "test/model-a", "", false); resp.StatusCode != 200 {
		t.Fatalf("armed fail:503:n=1, second call: %d", resp.StatusCode)
	}
	m.do(t, "POST", "/__behavior", `{"behavior":"fail:503:n=1"}`)
	if resp, _ := m.chat(t, "test/model-a", "", false); resp.StatusCode != 503 {
		t.Fatalf("re-arming restarts the count: %d", resp.StatusCode)
	}

	m.do(t, "POST", "/__behavior", `{"behavior":"fail:502","model":"test/model-b"}`)
	if resp, _ := m.chat(t, "test/model-a", "", false); resp.StatusCode != 200 {
		t.Fatalf("a model-scoped arm reached another model: %d", resp.StatusCode)
	}
	if resp, _ := m.chat(t, "test/model-b", "", false); resp.StatusCode != 502 {
		t.Fatalf("model-scoped arm missed its model: %d", resp.StatusCode)
	}
	if resp, _ := m.chat(t, "test/model-b", "latency:1", false); resp.StatusCode != 200 {
		t.Fatalf("a call's own header replaces the armed behaviour: %d", resp.StatusCode)
	}
	m.do(t, "DELETE", "/__behavior", "")
	if resp, _ := m.chat(t, "test/model-b", "", false); resp.StatusCode != 200 {
		t.Fatalf("after DELETE /__behavior: %d", resp.StatusCode)
	}
}

func TestUnknownRouteIs404(t *testing.T) {
	m := newTestMock(t, "")
	if resp, b := m.do(t, "GET", "/v2/nothing", ""); resp.StatusCode != http.StatusNotFound || !strings.Contains(string(b), "not_found") {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
}

func TestBalanceStaysConsistentUnderConcurrentCharges(t *testing.T) {
	st := NewState(1_000_000)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < 1000; i++ {
			st.RecordCharge(Charge{Bearer: "k", Micros: 1})
		}
	}()
	for i := 0; i < 1000; i++ {
		if b, c := st.BalanceMicros(), st.CreditMicros(); b > c || b < c-1000 {
			t.Fatalf("balance %d outside [credits-1000, credits] for credits %d", b, c)
		}
	}
	<-done
	st.SetBalanceMicros(10)
	if st.BalanceMicros() != 10 {
		t.Fatalf("balance = %d after SetBalanceMicros(10)", st.BalanceMicros())
	}
}
