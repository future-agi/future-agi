package keyscope

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestParseScope(t *testing.T) {
	if Parse(map[string]string{"org_id": "o"}).Restricted() {
		t.Fatal("a key with no scope entry is restricted")
	}
	cases := []struct {
		raw         string
		mcp, models bool
	}{
		{"mcp,models", true, true},
		{" models , mcp ", true, true},
		{"mcp", true, false},
		{"models", false, true},
		{"", false, false},
		{"mcp,", false, false},
		{"mcp,mcp", false, false},
		{"mcp,admin", false, false},
		{"MCP", false, false},
	}
	for _, c := range cases {
		s := Parse(map[string]string{MetaScopes: c.raw})
		if !s.Restricted() || s.mcp != c.mcp || s.models != c.models {
			t.Errorf("Parse(%q) = %+v, want restricted mcp=%v models=%v", c.raw, s, c.mcp, c.models)
		}
	}
}

func TestScopeAllows(t *testing.T) {
	both := Parse(map[string]string{MetaScopes: "mcp,models"})
	allowed := [][2]string{
		{"POST", "/v1/chat/completions"}, {"POST", "//v1/chat/completions"}, {"POST", "/v1/embeddings"},
		{"POST", "/v1/responses"}, {"POST", "/mcp"}, {"GET", "/mcp"}, {"DELETE", "/mcp/v/vs_x"}, {"POST", "/x/../mcp/v/a"},
	}
	for _, a := range allowed {
		if !both.Allows(a[0], a[1], "/mcp") {
			t.Errorf("mcp,models refuses %s %s", a[0], a[1])
		}
	}
	refused := [][2]string{
		{"GET", "/v1/chat/completions"}, {"POST", "/v1/completions"}, {"GET", "/v1/models"},
		{"GET", "/v1/responses/r1"}, {"POST", "/v1/messages"}, {"POST", "/mcpx"}, {"GET", "/-/keys"},
		{"POST", "/v1beta/models/gemini:generateContent"}, {"POST", "/a2a"}, {"GET", "/healthz"},
	}
	for _, r := range refused {
		if both.Allows(r[0], r[1], "/mcp") {
			t.Errorf("mcp,models allows %s %s", r[0], r[1])
		}
	}
	if both.Allows("POST", "/mcp", "") {
		t.Error("the mcp scope admits /mcp on a gateway whose MCP is off")
	}
	if Parse(map[string]string{MetaScopes: "models"}).Allows("POST", "/mcp", "/mcp") {
		t.Error("a models-only key reaches MCP")
	}
	if !(Scope{}).Allows("GET", "/-/keys", "/mcp") {
		t.Error("an unrestricted key is refused")
	}
}

func TestParseBudget(t *testing.T) {
	if _, ok := ParseBudget(map[string]string{"org_id": "o"}); ok {
		t.Fatal("a key with no budget entries has a budget")
	}
	b, ok := ParseBudget(map[string]string{MetaBudgetLimitUSD: "12.345", MetaBudgetPeriod: "weekly"})
	if !ok || !b.Valid() || b.LimitMicros != 12_345_000 || b.Period != PeriodWeekly {
		t.Fatalf("ParseBudget = %+v %v", b, ok)
	}
	for _, md := range []map[string]string{
		{MetaBudgetLimitUSD: "5"},
		{MetaBudgetPeriod: "daily"},
		{MetaBudgetLimitUSD: "five", MetaBudgetPeriod: "daily"},
		{MetaBudgetLimitUSD: "-0.01", MetaBudgetPeriod: "daily"},
		{MetaBudgetLimitUSD: "NaN", MetaBudgetPeriod: "daily"},
		{MetaBudgetLimitUSD: "1e12", MetaBudgetPeriod: "daily"},
		{MetaBudgetLimitUSD: "5", MetaBudgetPeriod: "total"},
	} {
		b, ok := ParseBudget(md)
		if !ok || b.Valid() || !b.Exhausted(0) {
			t.Errorf("ParseBudget(%v) = %+v %v, want an unreadable budget that refuses", md, b, ok)
		}
	}
	zero, _ := ParseBudget(map[string]string{MetaBudgetLimitUSD: "0", MetaBudgetPeriod: "daily"})
	if !zero.Valid() || !zero.Exhausted(0) {
		t.Error("a $0 budget lets a call through")
	}
}

func TestBudgetPeriods(t *testing.T) {
	now := time.Date(2026, 10, 21, 15, 4, 5, 0, time.UTC) // a Wednesday
	for _, c := range []struct{ period, start, end string }{
		{PeriodDaily, "2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z"},
		{PeriodWeekly, "2026-10-19T00:00:00Z", "2026-10-26T00:00:00Z"},
		{PeriodMonthly, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"},
	} {
		b := Budget{Period: c.period, valid: true}
		if got := b.PeriodStart(now).Format(time.RFC3339); got != c.start {
			t.Errorf("%s start %s, want %s", c.period, got, c.start)
		}
		if got := b.ResetsAt(now).Format(time.RFC3339); got != c.end {
			t.Errorf("%s end %s, want %s", c.period, got, c.end)
		}
	}
	sunday := time.Date(2026, 10, 25, 23, 0, 0, 0, time.UTC)
	if got := (Budget{Period: PeriodWeekly}).PeriodStart(sunday).Format(time.RFC3339); got != "2026-10-19T00:00:00Z" {
		t.Errorf("a Sunday's week starts %s", got)
	}
}

type sink struct {
	mu      sync.Mutex
	status  int
	batches [][]refusalEntry
	auth    []string
}

func (s *sink) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	var got struct {
		Events []refusalEntry `json:"events"`
	}
	_ = json.Unmarshal(body, &got)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.batches = append(s.batches, got.Events)
	s.auth = append(s.auth, r.Header.Get("Authorization"))
	w.WriteHeader(s.status)
}

func TestReporterGroupsRefusalsAndKeepsThemWhenTheControlPlaneIsDown(t *testing.T) {
	s := &sink{status: http.StatusServiceUnavailable}
	srv := httptest.NewServer(s)
	defer srv.Close()
	r := NewReporter(srv.URL, "admin-tok")
	for i := 0; i < 3; i++ {
		r.Report(Refusal{KeyID: "key_a", Code: CodeScopeDenied, Method: "GET", Path: "/v1/models"})
	}
	if len(r.pending) != 1 {
		t.Fatalf("three identical refusals made %d entries, want 1", len(r.pending))
	}
	r.Report(Refusal{KeyID: "key_a", Code: CodeBudgetExhausted, Method: "PIPELINE", Path: "chat endpoint (model m)"})

	if err := r.Flush(context.Background()); err == nil {
		t.Fatal("a 503 flush reported success")
	}
	s.status = http.StatusOK
	r.Report(Refusal{KeyID: "key_a", Code: CodeScopeDenied, Method: "GET", Path: "/v1/models"})
	if err := r.Flush(context.Background()); err != nil {
		t.Fatalf("flush: %v", err)
	}
	last := s.batches[len(s.batches)-1]
	if len(last) != 2 {
		t.Fatalf("second flush sent %d entries, want 2: %+v", len(last), last)
	}
	for _, e := range last {
		if e.Code == CodeScopeDenied && e.Count != 4 {
			t.Fatalf("scope refusals counted %d, want 4 (3 kept + 1)", e.Count)
		}
	}
	if s.auth[0] != "Bearer admin-tok" {
		t.Fatalf("Authorization %q", s.auth[0])
	}
	if err := r.Flush(context.Background()); err != nil || len(s.batches) != 2 {
		t.Fatalf("an empty flush posted (%d batches, %v)", len(s.batches), err)
	}
}

func TestReporterDropsABatchTheControlPlaneRefuses(t *testing.T) {
	s := &sink{status: http.StatusBadRequest}
	srv := httptest.NewServer(s)
	defer srv.Close()
	r := NewReporter(srv.URL, "")
	r.Report(Refusal{KeyID: "key_a", Code: CodeScopeDenied, Method: "GET", Path: "/x"})
	if err := r.Flush(context.Background()); err == nil {
		t.Fatal("a 400 flush reported success")
	}
	s.status = http.StatusOK
	if err := r.Flush(context.Background()); err != nil || len(s.batches) != 1 {
		t.Fatalf("a refused batch was sent again (%d batches)", len(s.batches))
	}
}

func TestReporterBoundsDistinctRefusals(t *testing.T) {
	r := NewReporter("http://127.0.0.1:9", "")
	// Distinct keys, each under its own bound (maxEntriesPerKey), fill the
	// reporter's.
	for i := 0; i < maxPending+5; i++ {
		r.Report(Refusal{KeyID: "key_" + time.Duration(i).String(), Code: CodeScopeDenied, Method: "GET", Path: "/p/" + string(rune('a'+i%26))})
	}
	if len(r.pending) != maxPending || r.dropped != 5 {
		t.Fatalf("pending %d dropped %d", len(r.pending), r.dropped)
	}
	var nilReporter *Reporter
	nilReporter.Report(Refusal{KeyID: "k"}) // no control plane: logged only
	if err := nilReporter.Flush(context.Background()); err != nil {
		t.Fatal(err)
	}
	if NewReporter("", "tok") != nil {
		t.Fatal("a reporter with no control plane")
	}
}

// The contract's golden fixtures for the two gateway refusals
// (omega-ee-attach/v1, CN22c) are what the gateway writes, body for body.
func TestRefusalsMatchTheOmegaEEAttachFixtures(t *testing.T) {
	dir := "../../scripts/scenarios/fixtures/omega-ee-attach/protocol/"
	both := Parse(map[string]string{MetaScopes: "mcp,models"})
	monthly, _ := ParseBudget(map[string]string{MetaBudgetLimitUSD: "50", MetaBudgetPeriod: PeriodMonthly})
	cases := []struct {
		file string
		err  *models.APIError
	}{
		{"403-key-scope-denied.json", ErrScopeDenied(both, "GET", "/v1/models")},
		{"402-agent-key-budget-exhausted.json", ErrBudgetExhausted("key_3c9f1a7e5b2d8c4f6a0e1b9d", monthly,
			60_000_000, time.Date(2026, 10, 21, 23, 0, 0, 0, time.UTC))},
	}
	for _, c := range cases {
		raw, err := os.ReadFile(dir + c.file)
		if err != nil {
			t.Fatal(err)
		}
		var fixture struct {
			HTTP struct {
				Status int `json:"status"`
			} `json:"http"`
			Body json.RawMessage `json:"body"`
		}
		if err := json.Unmarshal(raw, &fixture); err != nil {
			t.Fatal(err)
		}
		w := httptest.NewRecorder()
		models.WriteError(w, c.err)
		var got, want any
		_ = json.Unmarshal(w.Body.Bytes(), &got)
		_ = json.Unmarshal(fixture.Body, &want)
		if w.Code != fixture.HTTP.Status || !reflect.DeepEqual(got, want) {
			t.Errorf("%s: the gateway writes %d %s, the fixture says %d %s", c.file, w.Code, w.Body.String(),
				fixture.HTTP.Status, fixture.Body)
		}
		if ct := w.Header().Get("Content-Type"); ct != "application/json" {
			t.Errorf("%s: content-type %q", c.file, ct)
		}
	}
}
