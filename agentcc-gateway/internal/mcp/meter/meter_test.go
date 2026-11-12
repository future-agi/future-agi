package meter

import (
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func TestResolveModeMostRestrictiveAndTerms(t *testing.T) {
	cases := []struct {
		name string
		in   ModeSources
		want string
	}{
		{"no org config is none", ModeSources{Org: "full", Default: "full"}, ModeNone},
		{"org loaded, nothing set is full", ModeSources{OrgLoaded: true}, ModeFull},
		{"org metadata", ModeSources{OrgLoaded: true, Org: "metadata"}, ModeMetadata},
		{"key none wins over org full", ModeSources{OrgLoaded: true, Org: "full", Key: "none"}, ModeNone},
		{"header tightens", ModeSources{OrgLoaded: true, Request: "metadata"}, ModeMetadata},
		{"header cannot loosen", ModeSources{OrgLoaded: true, Default: "none", Request: "full"}, ModeNone},
		{"misspelt is none", ModeSources{OrgLoaded: true, Key: "fulll"}, ModeNone},
		{"terms metadata_only tightens full", ModeSources{OrgLoaded: true, Retention: "metadata_only"}, ModeMetadata},
		{"terms hashed tightens full", ModeSources{OrgLoaded: true, Retention: "hashed"}, ModeMetadata},
		{"terms full keeps full", ModeSources{OrgLoaded: true, Retention: "full"}, ModeFull},
		{"terms never loosen none", ModeSources{OrgLoaded: true, Key: "none", Retention: "full"}, ModeNone},
		{"unknown terms tighten", ModeSources{OrgLoaded: true, Retention: "forever"}, ModeMetadata},
	}
	for _, c := range cases {
		if got := ResolveMode(c.in); got != c.want {
			t.Errorf("%s: ResolveMode(%+v) = %s, want %s", c.name, c.in, got, c.want)
		}
	}
}

func TestContentKeptOnlyUnderFull(t *testing.T) {
	raw := json.RawMessage(`{"q":"hello"}`)
	if got := Content(raw, ModeFull, nil); string(got) != `{"q":"hello"}` {
		t.Fatalf("full kept %s", got)
	}
	for _, mode := range []string{ModeMetadata, ModeNone, ""} {
		if got := Content(raw, mode, nil); got != nil {
			t.Fatalf("mode %q kept %s", mode, got)
		}
	}
	if got := Content(nil, ModeFull, nil); got != nil {
		t.Fatalf("empty content kept as %s", got)
	}
}

func TestContentRedactsEveryString(t *testing.T) {
	r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "email", Pattern: `[a-z]+@[a-z]+\.com`}})
	raw := json.RawMessage(`{"to":"bob@corp.com","n":12345678901234567890,"list":["x","amy@corp.com"],"nested":{"cc":"eve@corp.com"}}`)
	got := string(Content(raw, ModeFull, r))
	for _, leak := range []string{"bob@corp.com", "amy@corp.com", "eve@corp.com"} {
		if strings.Contains(got, leak) {
			t.Fatalf("redacted content still has %s: %s", leak, got)
		}
	}
	if !strings.Contains(got, `"n":12345678901234567890`) || !strings.Contains(got, "[REDACTED:email]") {
		t.Fatalf("redaction changed more than strings: %s", got)
	}
	if !json.Valid([]byte(got)) {
		t.Fatalf("redacted content is not JSON: %s", got)
	}

	full := privacy.New(privacy.ModeFull, nil)
	if got := string(Content(json.RawMessage(`{"a":"secret","b":["s2"]}`), ModeFull, full)); got != `{"a":"[REDACTED]","b":["[REDACTED]"]}` {
		t.Fatalf("full-mode redaction = %s", got)
	}
	none := privacy.New(privacy.ModeNone, nil)
	if got := string(Content(json.RawMessage(`{"a":"kept"}`), ModeFull, none)); got != `{"a":"kept"}` {
		t.Fatalf("a no-op redactor changed the content: %s", got)
	}
}

func TestContentCappedAt16KiB(t *testing.T) {
	big := `{"text":"` + strings.Repeat("é\"", 20000) + `"}`
	got := Content(json.RawMessage(big), ModeFull, nil)
	if len(got) > MaxContentBytes {
		t.Fatalf("capped content is %d bytes, cap %d", len(got), MaxContentBytes)
	}
	var tr truncatedContent
	if err := json.Unmarshal(got, &tr); err != nil {
		t.Fatalf("capped content is not the truncation object: %v", err)
	}
	if !tr.Truncated || tr.OriginalBytes != len(big) || !strings.HasPrefix(big, tr.Prefix) || len(tr.Prefix) < MaxContentBytes/4 {
		t.Fatalf("truncation = truncated %v, original %d (want %d), prefix %d bytes", tr.Truncated, tr.OriginalBytes, len(big), len(tr.Prefix))
	}

	exact := `"` + strings.Repeat("a", MaxContentBytes-2) + `"`
	if got := Content(json.RawMessage(exact), ModeFull, nil); string(got) != exact {
		t.Fatal("content of exactly the cap was changed")
	}
}

func TestClassify(t *testing.T) {
	cases := []struct {
		name string
		in   Input
		want Class
	}{
		{"success", Input{HasResult: true}, Class{Outcome: OutcomeSuccess}},
		{"isError is never success", Input{HasResult: true, IsError: true}, Class{OutcomeIsError, "unknown", OriginUpstream, ""}},
		{"ACL denial", Input{Denied: true, Code: "tool_denied"}, Class{OutcomeDenied, "denied", OriginGateway, "tool_denied"}},
		{"unknown tool", Input{Denied: true, Code: "unknown_tool"}, Class{OutcomeDenied, "denied", OriginGateway, CodeUnknownTool}},
		{"PIN's tool_not_found", Input{Denied: true, Code: "tool_not_found"}, Class{OutcomeDenied, "denied", OriginGateway, CodeUnknownTool}},
		{"credits", Input{Denied: true, Code: "managed_credits_exhausted"}, Class{OutcomeDenied, "insufficient_credits", OriginGateway, "managed_credits_exhausted"}},
		{"quarantine", Input{Denied: true, Code: "tool_quarantined"}, Class{OutcomeDenied, "quarantined", OriginGateway, "tool_quarantined"}},
		{"upstream timeout attribution", Input{Denied: true, Code: "upstream_error", Attribution: "timeout"}, Class{OutcomeTimeout, "timeout", OriginUpstream, "upstream_error"}},
		{"upstream time budget used", Input{Denied: true, Code: "upstream_error", Attribution: "upstream", TimedOut: true}, Class{OutcomeTimeout, "timeout", OriginUpstream, "upstream_error"}},
		{"client went away", Input{Denied: true, Code: "upstream_error", Cancelled: true, TimedOut: true}, Class{OutcomeCancelled, "cancelled", OriginClient, "upstream_error"}},
		{"network", Input{Denied: true, Code: "upstream_error", Attribution: "network"}, Class{OutcomeError, "network", OriginNetwork, "upstream_error"}},
		{"upstream failure", Input{Denied: true, Code: "upstream_error", Attribution: "upstream"}, Class{OutcomeError, "unknown", OriginUpstream, "upstream_error"}},
		{"cancelled before any answer", Input{Cancelled: true}, Class{OutcomeCancelled, "cancelled", OriginClient, ""}},
		{"no result, no denial", Input{}, Class{OutcomeError, "unknown", OriginGateway, ""}},
	}
	for _, c := range cases {
		if got := Classify(c.in); got != c.want {
			t.Errorf("%s: Classify(%+v) = %+v, want %+v", c.name, c.in, got, c.want)
		}
	}
}

func TestErrorMessageScrubbedAndCut(t *testing.T) {
	scrub := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "sk", Pattern: `sk-[a-z0-9]+`}}).Redact
	got := ErrorMessage("upstream said: api_key=sk-fakevalue0000 "+strings.Repeat("é", 600), scrub)
	if strings.Contains(got, "sk-fakevalue0000") || !strings.Contains(got, "[REDACTED:sk]") {
		t.Fatalf("credentials kept: %s", got)
	}
	if n := len([]rune(got)); n != MaxErrorMessage {
		t.Fatalf("message is %d characters, want %d", n, MaxErrorMessage)
	}
	if got := ErrorMessage("short", nil); got != "short" {
		t.Fatalf("unscrubbed short message = %q", got)
	}
	if ErrorMessage("", scrub) != "" {
		t.Fatal("an empty message became non-empty")
	}
}

// TestCountersSurviveReRegistration: counters are keyed by name and owned by
// the meter, so a server registered again keeps counting from where it was.
func TestCountersSurviveReRegistration(t *testing.T) {
	c := NewCounters()
	k := Key{OrgID: "org-a", ServerID: "github", Tool: "github_search"}
	c.Observe(k, OutcomeSuccess, 10, 1000)
	c.Observe(k, OutcomeDenied, 1, 2000)
	// A re-registration builds a new registry entry; the meter's key is the
	// same names, so nothing resets.
	c.Observe(Key{OrgID: "org-a", ServerID: "github", Tool: "github_search"}, OutcomeIsError, 5, 1500)
	got, ok := c.Get(k)
	if !ok || got.Calls != 3 || got.Outcomes[OutcomeSuccess] != 1 || got.Outcomes[OutcomeDenied] != 1 ||
		got.Outcomes[OutcomeIsError] != 1 || got.LatencyMsTotal != 16 || got.LastCalledMs != 2000 {
		t.Fatalf("counters = %+v, want 3 calls across outcomes", got)
	}
	got.Outcomes[OutcomeSuccess] = 99
	if again, _ := c.Get(k); again.Outcomes[OutcomeSuccess] != 1 {
		t.Fatal("Get returned the live map, not a copy")
	}
	c.Observe(Key{OrgID: "org-b", ServerID: "github", Tool: "github_search"}, OutcomeSuccess, 1, 1)
	if snap := c.Snapshot(); len(snap) != 2 || snap[0].OrgID != "org-a" || snap[1].OrgID != "org-b" {
		t.Fatalf("snapshot = %+v, want org-a then org-b", snap)
	}
}

func TestCountersConcurrent(t *testing.T) {
	c := NewCounters()
	k := Key{OrgID: "o", ServerID: "s", Tool: "t"}
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.Observe(k, OutcomeSuccess, 1, 1)
			_ = c.Snapshot()
		}()
	}
	wg.Wait()
	if got, _ := c.Get(k); got.Calls != 50 {
		t.Fatalf("calls = %d, want 50", got.Calls)
	}
}

func TestInstallAndRestore(t *testing.T) {
	before := Default()
	m := New(Options{KeyLogContent: func(id, raw string) string { return id + "|" + raw }})
	restore := Install(m)
	if Default() != m || Default().KeyLogContent("k1", "") != "k1|" {
		t.Fatal("Install did not make the meter current")
	}
	if Default().KeyLogContent("", "") != "" {
		t.Fatal("a lookup with no key ran")
	}
	if Default().Redactor("org") != nil {
		t.Fatal("a meter without a redactor lookup returned one")
	}
	restore()
	if Default() != before {
		t.Fatal("restore did not put the previous meter back")
	}
	if Install(nil)(); Default() != before {
		t.Fatal("Install(nil) changed the meter")
	}
}

// TestCapRunesOnRuneBoundary: names are cut by characters, never inside one,
// and NUL is replaced, so the value fits the record's varchar column and Postgres
// takes it.
func TestCapRunesOnRuneBoundary(t *testing.T) {
	two := strings.Repeat("é", 300) // 2 bytes each
	got := CapName(two)
	if n := utf8.RuneCountInString(got); n != MaxNameRunes || len(got) != 2*MaxNameRunes || !utf8.ValidString(got) {
		t.Fatalf("CapName of 300 two-byte runes = %d runes, %d bytes, valid %v", n, len(got), utf8.ValidString(got))
	}
	four := "t_" + strings.Repeat("\U0001F600", 300) // 4 bytes each
	if got := CapName(four); utf8.RuneCountInString(got) != MaxNameRunes || !utf8.ValidString(got) || !strings.HasPrefix(got, "t_") {
		t.Fatalf("CapName of four-byte runes = %d runes, valid %v", utf8.RuneCountInString(got), utf8.ValidString(got))
	}
	ascii := strings.Repeat("a", MaxNameRunes)
	if CapName(ascii) != ascii || CapName("short") != "short" || CapName("") != "" {
		t.Fatal("CapName changed a name that fits")
	}
	if got := CapRunes("abécd", 3); got != "abé" {
		t.Fatalf("CapRunes(3) = %q", got)
	}
	if got := Column("a\x00b", 16); got != "a�b" {
		t.Fatalf("Column kept a NUL: %q", got)
	}
	if got := ErrorMessage("tool not found: x\x00y", nil); strings.Contains(got, "\x00") {
		t.Fatalf("ErrorMessage kept a NUL: %q", got)
	}
}

// TestCountersCapNameAndOverflow: a counter key's tool is cut to
// MaxNameRunes, and once maxKeys keys exist a new key is counted under the
// one OverflowTool key, so the counters stay bounded whatever names arrive.
func TestCountersCapNameAndOverflow(t *testing.T) {
	c := NewCounters()
	long := Key{OrgID: "o", ServerID: "s", Tool: strings.Repeat("x", 300)}
	c.Observe(long, OutcomeSuccess, 1, 1)
	snap := c.Snapshot()
	if len(snap) != 1 || len(snap[0].Tool) != MaxNameRunes {
		t.Fatalf("snapshot = %d keys, tool of %d bytes; want 1 key of %d", len(snap), len(snap[0].Tool), MaxNameRunes)
	}
	if _, ok := c.Get(Key{OrgID: "o", ServerID: "s", Tool: strings.Repeat("x", MaxNameRunes)}); !ok {
		t.Fatal("the cut key is not the stored one")
	}

	c = NewCounters()
	c.maxKeys = 2
	for i, tool := range []string{"a", "b", "c", "d", "a"} {
		c.Observe(Key{OrgID: "o", ServerID: "s", Tool: tool}, OutcomeSuccess, 1, int64(i))
	}
	snap = c.Snapshot()
	if len(snap) != 3 {
		t.Fatalf("snapshot has %d keys, want a, b and the overflow key: %+v", len(snap), snap)
	}
	over, ok := c.Get(Key{Tool: OverflowTool})
	if !ok || over.Calls != 2 {
		t.Fatalf("overflow key = %+v (found %v), want the 2 calls of c and d", over, ok)
	}
	if a, _ := c.Get(Key{OrgID: "o", ServerID: "s", Tool: "a"}); a.Calls != 2 {
		t.Fatalf("an existing key stopped counting past the bound: %+v", a)
	}
}
