package keyscope

// CN22c fix round 1 (review R-CN22c-2 and R-CN22c-5): the reporter sends the
// control plane only events it accepts, whatever the refused request looked
// like, so one hostile request cannot get a batch of audited refusals
// refused; and one key cannot fill the audit log by varying the path.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"unicode"
	"unicode/utf8"
)

// The control plane's limits (futureagi/agentcc/views/ee_agent_key_policy.py
// RefusalEventSerializer): method at most 16 characters, path 1 to 300
// characters with no NUL, neither blank.
func acceptedByTheControlPlane(t *testing.T, e refusalEntry) {
	t.Helper()
	if e.Method == "" || utf8.RuneCountInString(e.Method) > 16 {
		t.Errorf("method %q", e.Method)
	}
	if strings.TrimSpace(e.Path) == "" || utf8.RuneCountInString(e.Path) > 300 || !utf8.ValidString(e.Path) {
		t.Errorf("path %q (%d runes)", e.Path, utf8.RuneCountInString(e.Path))
	}
	for _, r := range e.Path {
		if unicode.In(r, unicode.Cc, unicode.Cf) {
			t.Errorf("path %q keeps the control or format character %U", e.Path, r)
		}
	}
}

func TestReporterSendsOnlyWhatTheControlPlaneAccepts(t *testing.T) {
	s := &sink{status: http.StatusOK}
	srv := httptest.NewServer(s)
	defer srv.Close()
	r := NewReporter(srv.URL, "")

	report := func(method, path string) {
		r.Report(Refusal{KeyID: "key_a", Code: CodeScopeDenied, Method: method, Path: path})
	}
	report(strings.Repeat("X", 40), "/v1/keys")
	report(strings.Repeat("Y", 40), "/v1/keys") // another unknown method: the same entry
	report("GET", "/v1/models\x00x")
	report("GET", "/v1/‮evil\r\nforged: line")
	report("GET", "\x00\x01\x02")
	report("GET", "/v1/"+strings.Repeat("é", 400))
	report("GET", "/v1/\xff\xfe")
	r.Report(Refusal{KeyID: "key_a", Code: CodeBudgetExhausted, Method: "PIPELINE", Path: "chat endpoint (model m\x00)"})

	if err := r.Flush(context.Background()); err != nil {
		t.Fatalf("flush: %v", err)
	}
	got := s.batches[0]
	byPath := map[string]refusalEntry{}
	for _, e := range got {
		acceptedByTheControlPlane(t, e)
		byPath[e.Path] = e
	}
	if e := byPath["/v1/keys"]; e.Method != "OTHER" || e.Count != 2 {
		t.Fatalf("two refusals with unknown methods: %+v, want one OTHER entry counted 2 (batch %+v)", e, got)
	}
	if e, ok := byPath["/v1/modelsx"]; !ok || e.Method != "GET" {
		t.Fatalf("the NUL was not dropped from the path: %+v", got)
	}
	if _, ok := byPath["/v1/evilforged: line"]; !ok {
		t.Fatalf("control and format characters kept: %+v", got)
	}
	if _, ok := byPath["-"]; !ok {
		t.Fatalf("a path of control characters only was not sent as \"-\": %+v", got)
	}
	if e, ok := byPath["chat endpoint (model m)"]; !ok || e.Method != "PIPELINE" {
		t.Fatalf("the pipeline's refusal: %+v", got)
	}
	if len(got) != 7 {
		t.Fatalf("%d entries, want 7: %+v", len(got), got)
	}
}

func TestReporterBoundsEachKeysDistinctRefusals(t *testing.T) {
	r := NewReporter("http://127.0.0.1:9", "")
	const varied = 100
	for i := 0; i < varied; i++ {
		r.Report(Refusal{KeyID: "key_a", Code: CodeScopeDenied, Method: "GET", Path: "/v1/x/" + strings.Repeat("a", i)})
	}
	r.Report(Refusal{KeyID: "key_b", Code: CodeScopeDenied, Method: "GET", Path: "/v1/models"})

	entries, counted := 0, 0
	for _, e := range r.pending {
		if e.AgentKeyID == "key_a" {
			entries++
			counted += e.Count
		}
	}
	if entries > 20 {
		t.Fatalf("one key that varies the path made %d entries in one window, want at most 20", entries)
	}
	if counted != varied {
		t.Fatalf("the key's entries count %d refusals, want all %d", counted, varied)
	}
	if e, ok := r.pending[Refusal{KeyID: "key_b", Code: CodeScopeDenied, Method: "GET", Path: "/v1/models"}]; !ok || e.Count != 1 {
		t.Fatalf("another key's refusal was folded or lost: %+v", r.pending)
	}
}
