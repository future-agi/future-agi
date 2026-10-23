package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// mockRunWithTelemetry runs the 12-span mock pipeline and returns its result
// and telemetry stream.
func mockRunWithTelemetry(t *testing.T) (Result, []byte) {
	t.Helper()
	c := mockRunConfig()
	store := &mockStorage{cfg: c}
	ch := httptest.NewServer(http.HandlerFunc(store.serve))
	t.Cleanup(ch.Close)
	c.ClickHouseURL, c.Endpoint = ch.URL, startFixtureCollector(t, ch.URL)
	store.cfg = c
	var telemetry bytes.Buffer
	r, err := Run(context.Background(), c, Options{ResourceWriter: &telemetry})
	if err != nil {
		t.Fatal(err)
	}
	if r.Status != "measured-profile-complete" {
		t.Fatalf("mock run: %s %+v", r.Status, r.Verification)
	}
	return r, telemetry.Bytes()
}

// M3: a short run is published with every disclosure and an explicit "Not
// measured" section, but without a headline throughput; the Markdown and JSON
// come from one structure.
func TestPublicationOfAShortRunWithholdsTheHeadline(t *testing.T) {
	r, telemetry := mockRunWithTelemetry(t)
	p, err := Publish(r, PublishOptions{Telemetry: bytes.NewReader(telemetry)})
	if err != nil {
		t.Fatal(err)
	}
	if p.Headline.Status != "withheld" || p.Headline.VerifiedSpansPerSecond != nil || len(p.Headline.WithheldReasons) != 1 ||
		!strings.Contains(p.Headline.WithheldReasons[0], "below the configured minimum 1h0m0s") {
		t.Fatalf("headline of a 1 s run: %+v", p.Headline)
	}
	if len(p.Disclosures) != 24 || p.DisclosureCaptured+p.DisclosurePartial+p.DisclosureMissing != 24 {
		t.Fatalf("disclosures: %d listed, %d/%d/%d", len(p.Disclosures), p.DisclosureCaptured, p.DisclosurePartial, p.DisclosureMissing)
	}
	byField := map[string]Disclosure{}
	for i, d := range p.Disclosures {
		if d.Number != i+1 || d.Value == "" {
			t.Fatalf("disclosure %d: %+v", i, d)
		}
		byField[d.Field] = d
	}
	for field, want := range map[string]string{"ClickHouse version": "captured", "schema DDL hash": "captured", "storage bytes before and after": "captured",
		"clock offset": "captured", "telemetry stream hash": "captured", "network bandwidth and RTT": "not-captured", "warmup duration": "not-captured",
		"payload distribution, spans per trace, traces per session, seed": "captured", "ClickHouse insert settings (async insert, quorum, fsync, dedup window)": "partial"} {
		if byField[field].Status != want {
			t.Fatalf("disclosure %q = %+v, want %s", field, byField[field], want)
		}
	}
	if len(p.Stages) != 6 || p.Stages[0].Spans != 12 || p.Stages[2].Spans != 12 || p.Stages[3].Spans != 12 || p.Stages[4].Spans != 12 || p.Stages[5].Spans != 3 {
		t.Fatalf("stages: %+v", p.Stages)
	}
	tc := p.Telemetry
	if tc == nil || tc.ProgressLines == 0 || tc.IdentityHeld != tc.ProgressLines || tc.IdentityViolations != 0 || len(tc.SHA256) != 64 || tc.Lines == 0 {
		t.Fatalf("telemetry check: %+v", tc)
	}
	notMeasured := strings.Join(p.NotMeasured, "\n")
	for _, want := range []string{"Collector backlog over time", "Recovery from a collector or storage fault", "Application API read latency for trace-list", "Commit latency", "retry behaviour"} {
		if !strings.Contains(notMeasured, want) {
			t.Fatalf("not-measured list misses %q:\n%s", want, notMeasured)
		}
	}
	var md bytes.Buffer
	if err := WritePublicationMarkdown(&md, p); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"**No headline throughput.**", "## Not measured", "## Disclosures (", "| durable | 12 |", "Read-visible lag, upper bound, from the scheduled send", "identity held on"} {
		if !strings.Contains(md.String(), want) {
			t.Fatalf("markdown misses %q:\n%s", want, md.String())
		}
	}
	for _, banned := range []string{"10x", "faster than", "zero data loss", "verified sustained spans/s"} {
		if strings.Contains(strings.ToLower(md.String()), strings.ToLower(banned)) {
			t.Fatalf("markdown of a smoke run contains %q", banned)
		}
	}
	var js bytes.Buffer
	if err := WritePublicationJSON(&js, p); err != nil {
		t.Fatal(err)
	}
	var back Publication
	if err := json.Unmarshal(js.Bytes(), &back); err != nil || back.Headline.Status != "withheld" || back.Schema != PublicationSchema {
		t.Fatalf("JSON round trip: %v %+v", err, back.Headline)
	}
}

// M3: the headline is printed only for a complete, loss-free run of at least the
// minimum duration that accepted all offered load; each failed condition alone
// withholds it, with its reason.
func TestPublicationHeadlineNeedsAnEarnedRun(t *testing.T) {
	base, telemetry := mockRunWithTelemetry(t)
	opts := PublishOptions{MinSustained: 500 * time.Millisecond}
	p, err := Publish(base, opts)
	if err != nil {
		t.Fatal(err)
	}
	if p.Headline.Status != "published" || p.Headline.VerifiedSpansPerSecond == nil || *p.Headline.VerifiedSpansPerSecond != 12 || len(p.Headline.WithheldReasons) != 0 {
		t.Fatalf("an earned 1 s run with a 500 ms minimum: %+v", p.Headline)
	}
	var md bytes.Buffer
	_ = WritePublicationMarkdown(&md, p)
	if !strings.Contains(md.String(), "**12.0 verified sustained spans/s** for 1 s at an offered 12 spans/s") {
		t.Fatalf("published headline:\n%s", md.String())
	}
	tampered := bytes.Replace(telemetry, []byte(`"in_flight_spans":0`), []byte(`"in_flight_spans":1`), 1)
	for name, tc := range map[string]struct {
		mutate func(*Result)
		opts   PublishOptions
		reason string
	}{
		"too-short":        {func(*Result) {}, PublishOptions{}, "below the configured minimum"},
		"incomplete":       {func(r *Result) { r.Verification.Complete, r.Verification.Error = false, "verification budget exhausted" }, opts, "verification is incomplete (verification budget exhausted)"},
		"missing":          {func(r *Result) { r.Verification.Missing, r.Verification.MissingBy.AcknowledgedMissing = 1, 1 }, opts, "1 attempted spans are missing"},
		"not-all-accepted": {func(r *Result) { r.Counters.Acknowledged -= 4 }, opts, "4 of 12 attempted spans not acknowledged"},
		"not-all-started":  {func(r *Result) { r.StopReason = "byte-budget" }, opts, "stop reason byte-budget"},
		"recovery":         {func(r *Result) { r.Recovery = &RecoveryResult{} }, opts, "not a throughput measurement"},
		"unseen-sentinel":  {func(r *Result) { r.Lag.Unseen = 1 }, opts, "1 acknowledged sentinels never became visible"},
		"accounting":       {func(r *Result) { r.AccountingError = "span accounting identity broken" }, opts, "accounting error"},
		"dry-run":          {func(r *Result) { r.Config.Execute = false }, opts, "not executed"},
		"telemetry":        {func(*Result) {}, PublishOptions{MinSustained: opts.MinSustained, Telemetry: bytes.NewReader(tampered)}, "telemetry progress lines break the accounting identity"},
	} {
		t.Run(name, func(t *testing.T) {
			if name == "telemetry" && bytes.Equal(tampered, telemetry) {
				t.Fatal("telemetry fixture has no line to tamper with")
			}
			r := base
			tc.mutate(&r)
			p, err := Publish(r, tc.opts)
			if err != nil {
				t.Fatal(err)
			}
			if p.Headline.Status != "withheld" || p.Headline.VerifiedSpansPerSecond != nil || !strings.Contains(strings.Join(p.Headline.WithheldReasons, "; "), tc.reason) {
				t.Fatalf("headline %+v, want withheld because %q", p.Headline, tc.reason)
			}
		})
	}
	// A telemetry stream of another run, or another schema, is refused.
	other := bytes.ReplaceAll(telemetry, []byte(base.Config.RunID), []byte("fedcba9876543210"))
	if _, err := Publish(base, PublishOptions{Telemetry: bytes.NewReader(other)}); err == nil || !strings.Contains(err.Error(), "belongs to run") {
		t.Fatalf("another run's telemetry accepted: %v", err)
	}
	old := base
	old.SchemaVersion = "fi.otlpbench.v2"
	if _, err := Publish(old, opts); err == nil {
		t.Fatal("published a result of an older schema")
	}
}
