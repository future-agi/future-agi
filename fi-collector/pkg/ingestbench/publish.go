package ingestbench

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"
)

// A publication is the shareable form of one result: what was measured, under
// which disclosed conditions, and what was NOT measured. Its headline sustained
// throughput is printed only when the run earns it; otherwise the headline is
// withheld with every reason. Markdown is rendered from the same structure as
// the JSON, so the two cannot disagree.

const PublicationSchema = "fi.otlpbench.publication.v2"

// DefaultMinSustained is the shortest load window whose throughput may be
// published as sustained. The TH-8687 acceptance profile is 24 hours.
const DefaultMinSustained = time.Hour

type PublishOptions struct {
	// MinSustained: below this load duration no headline throughput.
	MinSustained time.Duration
	// Telemetry is the run's .telemetry.jsonl, when available; its digest,
	// run identity and per-line accounting identity are checked.
	Telemetry io.Reader
}

// Headline is the single throughput statement of a publication, or why there is
// none. It is a pass at the OFFERED rate, not a measured capacity: the rules
// require every offered span to be started, acknowledged and found stored, so
// a published headline always states the offered rate.
type Headline struct {
	Status          string   `json:"status"`
	WithheldReasons []string `json:"withheld_reasons"`
	Definition      string   `json:"definition"`
	Rule            string   `json:"rule"`
	// Published only: the offered rate, sustained and verified under the rule.
	SustainedOfferedSpansPerSecond *int64 `json:"sustained_offered_spans_per_second,omitempty"`
	// Published only: acknowledged spans over the measured load window (from
	// the start of the load to the completion of its last export), a measured
	// figure that is at most the offered rate.
	AchievedAcknowledgedSpansPerSecond *float64 `json:"achieved_acknowledged_spans_per_second_over_load_window,omitempty"`
	LoadWindowSeconds                  float64  `json:"load_window_seconds"`
	OfferedSpansPerSecond              int64    `json:"offered_spans_per_second"`
	SustainedSeconds                   float64  `json:"sustained_seconds"`
	MinSustainedSeconds                float64  `json:"min_sustained_seconds"`
}

// Stage is one of the five stages a span passes, with its count.
type Stage struct {
	Name    string `json:"name"`
	Spans   int64  `json:"spans"`
	Meaning string `json:"meaning"`
}

// Disclosure is one of the 24 disclosure fields a reproducible ingestion
// benchmark report needs, with what this result captured for it.
type Disclosure struct {
	Number int    `json:"number"`
	Field  string `json:"field"`
	Status string `json:"status"` // captured | partial | declared | not-captured
	Value  string `json:"value"`
}

type TelemetryCheck struct {
	SHA256         string `json:"sha256"`
	Bytes          int64  `json:"bytes"`
	Lines          int64  `json:"lines"`
	ProgressLines  int64  `json:"progress_lines"`
	CollectorLines int64  `json:"collector_lines"`
	// IdentityHeld counts progress lines on which attempted = acknowledged +
	// partially rejected + whole-request rejected + ambiguous + in flight.
	IdentityHeld       int64 `json:"progress_lines_identity_held"`
	IdentityViolations int64 `json:"progress_lines_identity_violated"`
}

type Publication struct {
	Schema       string           `json:"schema"`
	ResultSchema string           `json:"result_schema"`
	RunID        string           `json:"run_id"`
	RunStatus    string           `json:"run_status"`
	Started      time.Time        `json:"started_at"`
	Headline     Headline         `json:"headline"`
	Stages       []Stage          `json:"stages"`
	Outcomes     RequestOutcomes  `json:"request_outcomes"`
	Counters     Counters         `json:"counters"`
	Verification Verification     `json:"verification"`
	ReadVisible  LagResult        `json:"read_visible_lag"`
	Durable      DurableLagResult `json:"durable_lag"`
	// RequestLatency is from the actual send; FromScheduled from the
	// scheduled send (coordinated-omission corrected).
	RequestLatency     Percentiles        `json:"otlp_request_latency"`
	FromScheduled      Percentiles        `json:"otlp_request_latency_from_scheduled_send"`
	Schedule           ScheduleResult     `json:"schedule"`
	Backlog            BacklogResult      `json:"collector_backlog"`
	Recovery           *RecoveryResult    `json:"recovery,omitempty"`
	DirectReads        []DirectReadResult `json:"direct_clickhouse_reads"`
	ApplicationReads   []ProbeResult      `json:"application_reads"`
	Disclosures        []Disclosure       `json:"disclosures"`
	DisclosureCaptured int                `json:"disclosure_fields_captured"`
	DisclosurePartial  int                `json:"disclosure_fields_partial"`
	DisclosureMissing  int                `json:"disclosure_fields_not_captured"`
	NotMeasured        []string           `json:"not_measured"`
	Limitations        []string           `json:"limitations"`
	Warnings           []string           `json:"warnings"`
	Telemetry          *TelemetryCheck    `json:"telemetry,omitempty"`
}

// Publish builds a publication after validating the imported evidence offline.
// Valid failed/incomplete results are rendered with their headline withheld.
func Publish(r Result, o PublishOptions) (Publication, error) {
	if err := ValidateResult(r); err != nil {
		return Publication{}, err
	}
	if o.MinSustained <= 0 {
		o.MinSustained = DefaultMinSustained
	}
	p := Publication{Schema: PublicationSchema, ResultSchema: r.SchemaVersion, RunID: r.Config.RunID, RunStatus: r.Status, Started: r.Started,
		Outcomes: r.Counters.RequestOutcomes, Counters: r.Counters, Verification: r.Verification, ReadVisible: r.Lag, Durable: r.Verification.DurableLag,
		RequestLatency: r.RequestLatency, FromScheduled: r.RequestLatencyFromScheduled, Schedule: r.Schedule, Backlog: r.Backlog, Recovery: r.Recovery,
		DirectReads: r.DirectReads, ApplicationReads: r.Reads, Limitations: r.Limitations, Warnings: r.Warnings}
	if o.Telemetry != nil {
		check, err := checkTelemetry(o.Telemetry, r.Config.RunID)
		if err != nil {
			return Publication{}, err
		}
		p.Telemetry = &check
	}
	p.Stages = stages(r)
	p.Headline = headline(r, o.MinSustained, p.Telemetry)
	p.Disclosures = disclosures(r, p.Telemetry)
	for _, d := range p.Disclosures {
		switch d.Status {
		case "captured":
			p.DisclosureCaptured++
		case "not-captured":
			p.DisclosureMissing++
		default:
			p.DisclosurePartial++
		}
	}
	p.NotMeasured = notMeasured(r)
	return p, nil
}

func stages(r Result) []Stage {
	v := r.Verification
	durable := "spans of this run the exact-ID verification found stored (distinct trace+span IDs), any outcome class"
	if !v.Complete {
		durable += "; verification INCOMPLETE, so a lower bound"
	}
	visible := int64(r.Lag.FromScheduled.Count)
	return []Stage{
		{"offered", r.PlannedSpans, "spans the profile calls for: target rate x duration"},
		{"scheduled", r.Schedule.ScheduledSpans, "spans whose scheduled send passed while the generator ran (open loop)"},
		{"attempted", r.Counters.Attempted, "spans in export requests the generator started"},
		{"accepted", r.Counters.Acknowledged, "spans the collector acknowledged (OTLP success minus partial rejections); NOT a durability claim"},
		{"durable", int64(v.DistinctExpected), durable},
		{"read-visible (sentinel sample)", visible, fmt.Sprintf("acknowledged sentinel spans seen by a read query, of %d acknowledged sentinels (a sample: one sentinel per %d requests)", r.Lag.Sampled, r.Lag.Stride)},
	}
}

func headline(r Result, minSustained time.Duration, telemetry *TelemetryCheck) Headline {
	h := Headline{Status: "withheld", WithheldReasons: []string{}, OfferedSpansPerSecond: r.Config.Rate, SustainedSeconds: r.Config.Duration.Seconds(), MinSustainedSeconds: minSustained.Seconds(),
		LoadWindowSeconds: r.LoadSeconds,
		Definition:        "a pass at the offered rate, not a capacity: the offered spans/s is stated as sustained when every offered span was started, acknowledged and found stored by the exact-ID verification; the achieved figure is acknowledged spans / measured load window (load start to the completion of the last export)",
		Rule:              "printed only for an executed run of at least the minimum duration with clean completion status whose verification completed with no missing, visible duplicate or unexpected span and no stored span of a refused request, that started and acknowledged all offered load, saw every sentinel and had no fault"}
	why := func(format string, args ...any) {
		h.WithheldReasons = append(h.WithheldReasons, fmt.Sprintf(format, args...))
	}
	c, v := r.Counters, r.Verification
	if !r.Config.Execute || r.Status == "dry-run" {
		why("the run was not executed (dry run)")
	}
	if r.Status != "measured-profile-complete" {
		why("run status %q is not a clean measured-profile-complete result", r.Status)
	}
	if v.DuplicateRows != 0 {
		why("storage holds %d visible duplicate expected rows", v.DuplicateRows)
	}
	if !v.Complete {
		why("independent verification is incomplete (%s)", orUnknown(v.Error))
	}
	if r.AccountingError != "" {
		why("accounting error: %s", r.AccountingError)
	}
	if r.Config.Duration < minSustained {
		why("the sustained duration %s is below the configured minimum %s", r.Config.Duration, minSustained)
	}
	if r.StopReason != "planned-span-count" || c.Attempted != r.PlannedSpans {
		why("the offered load was not fully started: %d of %d spans attempted (stop reason %s)", c.Attempted, r.PlannedSpans, orUnknown(r.StopReason))
	}
	if c.Acknowledged != c.Attempted {
		why("the collector did not accept the whole offered load: %d of %d attempted spans not acknowledged", c.Attempted-c.Acknowledged, c.Attempted)
	}
	if v.Complete && v.Missing != 0 {
		why("%d attempted spans are missing from storage (%d acknowledged-but-missing)", v.Missing, v.MissingBy.AcknowledgedMissing)
	}
	if v.Complete && v.UnexpectedRows != 0 {
		why("storage holds %d rows that are not this run's identities", v.UnexpectedRows)
	}
	if v.Complete && v.ContractViolation {
		why("storage holds spans of requests the collector refused (%d whole-request rejected, %d beyond a partial rejection)", v.PersistedBy.RejectedButPersisted, v.PersistedBy.PartialExcess)
	}
	if r.Recovery != nil {
		why("the run injected or declared faults: a recovery case is not a throughput measurement")
	}
	if r.Lag.Unseen > 0 {
		why("%d acknowledged sentinels never became visible to reads", r.Lag.Unseen)
	}
	if telemetry != nil && telemetry.IdentityViolations > 0 {
		why("%d telemetry progress lines break the accounting identity", telemetry.IdentityViolations)
	}
	if len(h.WithheldReasons) == 0 && r.LoadSeconds <= 0 {
		why("the load window was not measured")
	}
	if len(h.WithheldReasons) == 0 {
		offered, achieved := r.Config.Rate, float64(c.Acknowledged)/r.LoadSeconds
		h.Status, h.SustainedOfferedSpansPerSecond, h.AchievedAcknowledgedSpansPerSecond = "published", &offered, &achieved
	}
	return h
}

func disclosures(r Result, telemetry *TelemetryCheck) []Disclosure {
	pv, cfg := r.Provenance, r.Config
	ch, col := pv.ClickHouse, pv.Collector
	out := []Disclosure{}
	add := func(field, status, value string) {
		out = append(out, Disclosure{Number: len(out) + 1, Field: field, Status: status, Value: value})
	}
	have := func(ok bool, value, missing string) (string, string) {
		if ok {
			return "captured", value
		}
		return "not-captured", missing
	}
	st, val := have(pv.SUT.Revision != "", pv.SUT.Revision+" (source: "+pv.SUT.RevisionSource+")", "unknown")
	add("system-under-test revision or image digest", st, val)
	gen := pv.Generator.VCSRevision
	if gen == "" {
		gen = pv.Generator.DeclaredRevision
	}
	st, val = have(gen != "", gen+" "+pv.Generator.VCSModified, "unknown")
	add("generator revision", st, strings.TrimSpace(val))
	st, val = have(ch.Version != "", ch.Version, "unknown")
	add("ClickHouse version", st, val)
	settings := mapString(ch.SessionInsertSettings) + "; " + mapString(ch.MergeTreeSettings)
	switch {
	case len(ch.SessionInsertSettings) > 0 && len(ch.MergeTreeSettings) > 0:
		add("ClickHouse insert settings (async insert, quorum, fsync, dedup window)", "partial", settings+" (verification user's session and server MergeTree defaults; the writer's own profile is not read)")
	case len(ch.SessionInsertSettings)+len(ch.MergeTreeSettings) > 0:
		add("ClickHouse insert settings (async insert, quorum, fsync, dedup window)", "partial", settings)
	default:
		add("ClickHouse insert settings (async insert, quorum, fsync, dedup window)", "not-captured", orUnknown(ch.SettingsError))
	}
	if col.Writer != nil {
		add("writer mode and retry policy", "captured", fmt.Sprintf("%s; %d retries, backoff %d-%d ms, request timeout %d ms, dead-letter after retries %t",
			col.Writer.InsertSettings, col.Writer.MaxRetries, col.Writer.InitialBackoffMS, col.Writer.MaxBackoffMS, col.Writer.RequestTimeoutMS, col.Writer.DeadLetter))
	} else {
		add("writer mode and retry policy", "not-captured", "collector /healthz writer block not read (no --collector-health-url)")
	}
	if col.Limits != nil {
		l := col.Limits
		add("collector limits from /healthz", "captured", fmt.Sprintf("max_pending_rows %d, batch %d rows / %d ms, HTTP body %d bytes, gRPC %d MiB", l.MaxPendingRows, l.BatchMaxRows, l.BatchMaxAgeMS, l.MaxHTTPBodyBytes, l.GRPCMaxRecvMiB))
	} else {
		add("collector limits from /healthz", "not-captured", "budget source: "+col.Budget.Source)
	}
	clusters := []string{}
	for _, c := range ch.Clusters {
		clusters = append(clusters, fmt.Sprintf("%s %dx%d", c.Cluster, c.Shards, c.Replicas))
	}
	add("topology (shards x replicas, Distributed mode)", "partial", fmt.Sprintf("declared %q; system.clusters: %s; Distributed insert mode not recorded", cfg.Topology, orUnknown(strings.Join(clusters, ", "))))
	hw := ch.Hardware
	if hw.Error == "" && hw.OSMemoryBytes > 0 {
		runtime := "not reported"
		if col.Runtime != nil {
			runtime = fmt.Sprintf("num_cpu %v, gomaxprocs %v", col.Runtime["num_cpu"], col.Runtime["gomaxprocs"])
		}
		add("CPU, RAM, disk type and IOPS of each node", "partial", fmt.Sprintf("ClickHouse server (one node): memory %d bytes, cgroup CPU %s, max_threads %s; collector runtime %s; disk type and IOPS not captured",
			hw.OSMemoryBytes, reportedNumber(hw.CGroupMaxCPU), orUnknown(hw.MaxThreads), runtime))
	} else {
		add("CPU, RAM, disk type and IOPS of each node", "not-captured", orUnknown(hw.Error))
	}
	add("network bandwidth and RTT", "not-captured", "not measured by the tool")
	h := pv.Host
	add("generator host resources and utilisation", "partial", fmt.Sprintf("%s/%s %s, %d logical CPUs, GOMAXPROCS %d, memory %d bytes; build workers %d; utilisation not sampled",
		h.OS, h.Arch, orUnknown(h.CPUModel), h.LogicalCPUs, h.GOMAXPROCS, h.MemoryBytes, r.Workload.BuildWorkers))
	add("encoding and compression", "captured", fmt.Sprintf("%s, gzip %t; %d wire bytes for %d uncompressed", cfg.Encoding, cfg.Gzip, r.Counters.Bytes, r.Counters.UncompressedBytes))
	wl := r.Workload
	add("payload distribution, spans per trace, traces per session, seed", "captured", fmt.Sprintf("payload %s, seed %d, %d spans/trace, %d traces/session; realised input bytes p50/p99 %d/%d, output bytes p50/p99 %d/%d over %d spans",
		cfg.Payload, cfg.Seed, cfg.SpansPerTrace, cfg.TracesPerSession, wl.InputBytes.P50, wl.InputBytes.P99, wl.OutputBytes.P50, wl.OutputBytes.P99, wl.Spans))
	add("load model (open/closed, rate, concurrency, batch)", "captured", fmt.Sprintf("open loop, %d spans/s, concurrency %d, %d spans per request", cfg.Rate, cfg.Concurrency, cfg.BatchSize))
	add("warmup duration", "not-captured", "no warmup phase: the whole load window is measured")
	add("measured duration", "captured", fmt.Sprintf("scheduled %s, load %.3fs incl. final export completion, settle %s", cfg.Duration, r.LoadSeconds, cfg.Settle))
	add("repetitions", "not-captured", "one result per run; publish each run, including failed ones")
	if r.Clock.Start.Measured {
		add("clock offset", "captured", fmt.Sprintf("server minus generator %.3f ms +/- %.3f ms before load, %.3f ms after settle; limited %t", r.Clock.Start.OffsetMS, r.Clock.Start.UncertaintyMS, r.Clock.End.OffsetMS, r.Clock.Limited))
	} else {
		add("clock offset", "not-captured", orUnknown(r.Clock.Start.Error))
	}
	add("raw histogram buckets", "captured", fmt.Sprintf("%d read-visible upper-bound buckets, %d durable lag buckets in the JSON", len(r.Lag.Buckets), len(r.Verification.DurableLag.Buckets)))
	if telemetry != nil {
		add("telemetry stream hash", "captured", fmt.Sprintf("sha256 %s, %d lines", telemetry.SHA256, telemetry.Lines))
	} else {
		add("telemetry stream hash", "not-captured", "no telemetry stream given to the publisher")
	}
	add("failed or aborted runs", "partial", "this run's status is "+r.Status+"; other runs are not known to one result")
	add("identified limiting component", "not-captured", "not inferred by the tool; read the backlog, schedule and resource series")
	st, val = have(ch.CreateTableSHA256 != "", ch.CreateTableSHA256, "create_table_query not read")
	add("schema DDL hash", st, val)
	st, val = have(ch.TableEngine != "", ch.TableEngine+" ORDER BY "+ch.SortingKey, "unknown")
	add("table engine and ORDER BY", st, val)
	if b, a := ch.StorageBefore, ch.StorageAfter; b != nil && a != nil && b.Error == "" && a.Error == "" {
		add("storage bytes before and after", "captured", fmt.Sprintf("table-wide on one server: %d -> %d bytes on disk, %d -> %d rows (includes other data; synthetic payloads, not a storage-cost figure)", b.BytesOnDisk, a.BytesOnDisk, b.Rows, a.Rows))
	} else {
		add("storage bytes before and after", "not-captured", "system.parts not read")
	}
	return out
}

func mapString(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+m[k])
	}
	return strings.Join(parts, ", ")
}

// notMeasured lists what this result does not measure: from its own blocks,
// then what the tool never measures.
func notMeasured(r Result) []string {
	out := []string{}
	for _, p := range r.Reads {
		if p.Latency.Count == 0 {
			out = append(out, fmt.Sprintf("Application API read latency for %s (%s).", p.Name, p.Status))
		}
	}
	for _, d := range r.DirectReads {
		if d.Latency.Count == 0 {
			out = append(out, fmt.Sprintf("Direct ClickHouse %s read latency %s (%s).", d.Name, d.Phase, d.Status))
		}
	}
	if r.Lag.FromScheduled.Count == 0 {
		out = append(out, "Read-visible lag (no acknowledged sentinel was seen).")
	}
	if r.Verification.DurableLag.Status != "measured" {
		out = append(out, fmt.Sprintf("Durable (server insert) lag of every stored span (%s).", orUnknown(r.Verification.DurableLag.Status)))
	}
	if r.Backlog.Status != "measured" && r.Backlog.Status != "measured-with-gaps" {
		out = append(out, "Collector backlog over time ("+orUnknown(r.Backlog.Status)+": pass --collector-health-url).")
	}
	if r.Recovery == nil {
		out = append(out, "Recovery from a collector or storage fault (no fault in this run).")
	}
	if !r.Clock.Start.Measured {
		out = append(out, "Generator-to-ClickHouse clock offset.")
	}
	if len(r.Config.Nodes) == 0 && len(r.Config.Containers) == 0 {
		out = append(out, "Database and collector CPU and memory utilisation over time (no --nodes or --containers).")
	}
	if r.Config.Duration < 24*time.Hour || r.Config.Rate < 15000 {
		out = append(out, "The TH-8687 acceptance profiles (15,000 spans/s for 24 h; 60,000 spans/s for 60 s) unless this run is one of them.")
	}
	return append(out,
		"Commit latency: created_at is taken before the part is committed, so durable lag is a lower bound on it.",
		"Duplicates removed by MergeTree merges before verification (only visible duplicates are counted).",
		"A real OpenTelemetry SDK or Collector exporter's retry behaviour: the generator never retries.",
		"Replica consistency, failover and multi-shard scaling: one verification connection reads one view.",
		"Storage, compression or cost per span: the payload is synthetic.",
		"Network bandwidth and round-trip time between hosts; disk type and IOPS.",
		"Behaviour beyond this run's duration (merges, TTL, retention limit, disk fill).",
	)
}

// progressIdentityHolds checks one progress line. The in-flight counts are
// tracked separately from the outcome classes (counted when a request starts
// and when it finishes), so a request that finished without landing in exactly
// one class breaks the equation. After the load (final) nothing is in flight.
func progressIdentityHolds(k Counters, inFlightSpans, inFlightRequests int64, final bool) bool {
	if inFlightSpans < 0 || inFlightRequests < 0 || (final && (inFlightSpans != 0 || inFlightRequests != 0)) {
		return false
	}
	return k.Attempted == k.Acknowledged+k.PartialRejected+k.WholeRequestRejected+k.Ambiguous+inFlightSpans &&
		k.Requests == k.RequestOutcomes.total()+inFlightRequests &&
		k.WholeRequestRejected == k.RejectedRetryable+k.RejectedNonRetryable && k.Unacknowledged == k.WholeRequestRejected+k.Ambiguous
}

// checkTelemetry digests a telemetry stream and checks that every line belongs
// to the run and every progress line keeps the accounting identity, against
// in-flight counts tracked independently of the outcome classes.
func checkTelemetry(r io.Reader, runID string) (TelemetryCheck, error) {
	h := sha256.New()
	var c TelemetryCheck
	sc := bufio.NewScanner(io.TeeReader(r, h))
	sc.Buffer(make([]byte, 1<<20), 16<<20)
	for sc.Scan() {
		c.Lines++
		c.Bytes += int64(len(sc.Bytes())) + 1
		var e telemetryEvent
		if err := json.Unmarshal(sc.Bytes(), &e); err != nil {
			return c, fmt.Errorf("telemetry line %d is not JSON", c.Lines)
		}
		if e.SchemaVersion != SchemaVersion || e.RunID != runID {
			return c, fmt.Errorf("telemetry line %d belongs to run %q schema %q, not run %q", c.Lines, e.RunID, e.SchemaVersion, runID)
		}
		switch e.Kind {
		case "progress", "progress-final":
			c.ProgressLines++
			if e.Counters == nil || e.InFlightSpans == nil || e.InFlightRequests == nil {
				c.IdentityViolations++
				continue
			}
			if progressIdentityHolds(*e.Counters, *e.InFlightSpans, *e.InFlightRequests, e.Kind == "progress-final") {
				c.IdentityHeld++
			} else {
				c.IdentityViolations++
			}
		case "collector":
			c.CollectorLines++
		}
	}
	if err := sc.Err(); err != nil {
		return c, fmt.Errorf("read telemetry: %w", err)
	}
	c.SHA256 = hex.EncodeToString(h.Sum(nil))
	return c, nil
}

// WritePublicationJSON writes the publication as indented JSON.
func WritePublicationJSON(w io.Writer, p Publication) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(p)
}

func pct(p Percentiles) string {
	if p.Count == 0 {
		return "not measured"
	}
	// %g preserves the stored float rather than adding presentation rounding
	// that could turn a lower bound into an overstatement (or an upper into an
	// understatement). The histogram already supplies bounded quantization.
	return fmt.Sprintf("%g / %g / %g / %g / %g ms (n=%d)", p.P50, p.P95, p.P99, p.P999, p.Max, p.Count)
}

func mdCell(s string) string { return strings.ReplaceAll(strings.ReplaceAll(s, "|", "\\|"), "\n", " ") }

// WritePublicationMarkdown renders the publication.
func WritePublicationMarkdown(w io.Writer, p Publication) error {
	var b strings.Builder
	f := func(format string, args ...any) { fmt.Fprintf(&b, format, args...) }
	f("# OTLP ingestion benchmark result %s\n\n", p.RunID)
	f("Result schema `%s`, publication schema `%s`. Run status: **%s**. Started %s.\n\n", p.ResultSchema, p.Schema, p.RunStatus, p.Started.UTC().Format(time.RFC3339))
	f("## Headline\n\n")
	if p.Headline.Status == "published" {
		f("**Sustained the offered %d spans/s for %.0f s (verified):** every offered span was started, acknowledged and found stored. Achieved %.1f acknowledged spans/s over the measured %.3f s load window. This is a pass at the offered rate, not a capacity limit.\n\n",
			*p.Headline.SustainedOfferedSpansPerSecond, p.Headline.SustainedSeconds, *p.Headline.AchievedAcknowledgedSpansPerSecond, p.Headline.LoadWindowSeconds)
		f("Definition: %s; %s (minimum %g s).\n\n", p.Headline.Definition, p.Headline.Rule, p.Headline.MinSustainedSeconds)
	} else {
		f("**No headline throughput.** It is withheld because:\n\n")
		for _, reason := range p.Headline.WithheldReasons {
			f("- %s\n", reason)
		}
		f("\nDefinition: %s; %s (minimum %g s).\n\n", p.Headline.Definition, p.Headline.Rule, p.Headline.MinSustainedSeconds)
	}
	f("## Stages\n\n| Stage | Spans | Meaning |\n|---|---:|---|\n")
	for _, s := range p.Stages {
		f("| %s | %d | %s |\n", s.Name, s.Spans, mdCell(s.Meaning))
	}
	o, c := p.Outcomes, p.Counters
	f("\nRequests: %d (acknowledged %d, partial %d, rejected retryable %d [with retry hint %d], rejected non-retryable %d, ambiguous %d). Spans: partial-rejected %d, whole-request rejected %d, ambiguous %d.\n\n",
		c.Requests, o.Acknowledged, o.PartialRejection, o.RejectedRetryable, o.RejectedWithRetryHint, o.RejectedNonRetryable, o.Ambiguous, c.PartialRejected, c.WholeRequestRejected, c.Ambiguous)
	v := p.Verification
	f("## Verification\n\nComplete: **%t**. Distinct stored attempted spans %d, missing %d (acknowledged-but-missing %d), visible duplicate rows %d, unexpected rows %d; %d chunks, %d rechecked, %.1f s.\n\n",
		v.Complete, v.DistinctExpected, v.Missing, v.MissingBy.AcknowledgedMissing, v.DuplicateRows, v.UnexpectedRows, v.Chunks, v.RecheckedChunks, v.ElapsedSeconds)
	if len(v.MissingRanges) > 0 {
		f("| Requests | Class | Scheduled at (s) | Missing spans |\n|---|---|---|---:|\n")
		for i, m := range v.MissingRanges {
			if i == 50 {
				f("| ... | %d more ranges in the JSON | | |\n", len(v.MissingRanges)-50)
				break
			}
			f("| %d-%d | %s | %.3f-%.3f | %d |\n", m.FirstRequest, m.LastRequest, m.Class, m.FirstScheduledS, m.LastScheduledS, m.MissingSpans)
		}
		f("\n")
	}
	f("## Latency (p50 / p95 / p99 / p99.9 / max)\n\n| Measure | Value |\n|---|---|\n")
	l := p.ReadVisible
	f("| Read-visible lag, upper bound, from the scheduled send | %s |\n", pct(l.FromScheduled))
	f("| Read-visible lag, lower bound, from the scheduled send | %s |\n", pct(l.LowerFromScheduled))
	f("| Durable (server insert) lag from the scheduled send, %s | %s |\n", mdCell(orUnknown(p.Durable.Status)), pct(p.Durable.Lag))
	f("| OTLP request latency from the actual send | %s |\n", pct(p.RequestLatency))
	f("| OTLP request latency from the scheduled send | %s |\n", pct(p.FromScheduled))
	f("| Start delay after the scheduled send | %s |\n", pct(p.Schedule.StartDelay))
	f("\nSentinels: %d registered, %d acknowledged, %d never seen, poll every %.0f ms. Durable lag covers %d stored spans (%d inserted before their scheduled send); clock offset applied %.3f ms +/- %.3f ms. Schedule: %d spans scheduled, %d never started, %d late starts.\n\n",
		l.Registered, l.Sampled, l.Unseen, l.PollIntervalMS, p.Durable.Rows, p.Durable.NegativeRows, p.Durable.ClockOffsetMS, p.Durable.ClockUncertaintyMS, p.Schedule.ScheduledSpans, p.Schedule.NotStartedSpans, p.Schedule.LateStartRequests)
	bl := p.Backlog
	f("## Collector backlog\n\n")
	if bl.Status == "measured" || bl.Status == "measured-with-gaps" {
		f("%s over %d samples (%d failed). Buffered rows p50/p99/max %d/%d/%d of a %d-row budget; slope %.2f rows/s over the load, %.2f rows/s in its second half; drained after the load: %t (%.3f s); collector restarts seen %d; insert stalls %d.\n\n",
			bl.Status, bl.Samples, bl.FailedSamples, bl.BufferedRows.P50, bl.BufferedRows.P99, bl.MaxBufferedRows, bl.MaxPendingRows, bl.SlopeRowsPerSecond, bl.SecondHalfSlopeRowsPerSecond, bl.Drained, bl.DrainSeconds, bl.CounterResets, len(bl.InsertStalls))
	} else {
		f("%s.\n\n", orUnknown(bl.Status))
	}
	if rec := p.Recovery; rec != nil {
		f("## Recovery\n\nVerification complete %t; acknowledged-but-missing %d; visible duplicates %d; ambiguous-but-persisted %d.\n\n| Fault | Source | Window (s) | Recovered | Recovery (s) | Requests scheduled during it |\n|---|---|---|---|---:|---|\n",
			rec.Complete, rec.AcknowledgedMissing, rec.VisibleDuplicates, rec.AmbiguousPersisted)
		for _, fr := range rec.Faults {
			seconds := "-"
			if fr.RecoverySeconds != nil {
				seconds = fmt.Sprintf("%.3f", *fr.RecoverySeconds)
			}
			recovered := "yes"
			if !fr.Recovered {
				recovered = "no: " + fr.NotRecoveredReason
			}
			f("| %s | %s | %.3f-%.3f | %s | %s | %s |\n", fr.Name, fr.Source, fr.StartS, fr.EndS, mdCell(recovered), seconds, mdCell(fmt.Sprintf("%+v", fr.RequestsScheduledDuring)))
		}
		f("\n")
	}
	f("## Disclosures (%d captured, %d partial, %d not captured of %d)\n\n| # | Field | Status | Value |\n|---:|---|---|---|\n", p.DisclosureCaptured, p.DisclosurePartial, p.DisclosureMissing, len(p.Disclosures))
	for _, d := range p.Disclosures {
		f("| %d | %s | %s | %s |\n", d.Number, mdCell(d.Field), d.Status, mdCell(d.Value))
	}
	if t := p.Telemetry; t != nil {
		f("\nTelemetry: sha256 `%s`, %d lines (%d progress, %d collector); the accounting identity held on %d progress lines and broke on %d.\n", t.SHA256, t.Lines, t.ProgressLines, t.CollectorLines, t.IdentityHeld, t.IdentityViolations)
	}
	f("\n## Not measured\n\n")
	for _, n := range p.NotMeasured {
		f("- %s\n", n)
	}
	f("\n## Limitations\n\n")
	for _, n := range p.Limitations {
		f("- %s\n", n)
	}
	if len(p.Warnings) > 0 {
		f("\n## Warnings\n\n")
		for _, n := range p.Warnings {
			f("- %s\n", n)
		}
	}
	_, err := io.WriteString(w, b.String())
	return err
}
