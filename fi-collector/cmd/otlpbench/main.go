// otlpbench sends bounded OTLP traffic and independently verifies storage.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

// warnings receives loud operator warnings; tests may replace it.
var warnings io.Writer = os.Stderr

func main() {
	ctx := context.Background()
	// Offline renderers may block in Write and do not observe a context. Leave
	// their signals alone. Graceful commands restore default termination after
	// the first cancellation so a second interrupt can stop blocked cleanup/I/O.
	offline := len(os.Args) > 1 && (os.Args[1] == "report" || os.Args[1] == "size" || os.Args[1] == "publish")
	if !offline {
		var stop context.CancelFunc
		ctx, stop = signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
		defer stop()
		go func() { <-ctx.Done(); stop() }()
	}
	if err := command(ctx, os.Args[1:], os.Stdout); err != nil && !errors.Is(err, flag.ErrHelp) {
		fmt.Fprintln(os.Stderr, "otlpbench:", err)
		os.Exit(1)
	}
}

func command(ctx context.Context, args []string, stdout io.Writer) error {
	if len(args) > 0 {
		switch args[0] {
		case "report":
			path, err := inputOperand("report", "result.json", args[1:], stdout)
			if err != nil {
				return err
			}
			var result ingestbench.Result
			if err := readJSON(path, &result); err != nil {
				return err
			}
			return ingestbench.Report(stdout, result)
		case "size":
			path, err := inputOperand("size", "measured-sizing-inputs.json", args[1:], stdout)
			if err != nil {
				return err
			}
			var input ingestbench.SizingInput
			if err := readJSON(path, &input); err != nil {
				return err
			}
			result, err := ingestbench.Size(input)
			if err != nil {
				return err
			}
			return json.NewEncoder(stdout).Encode(result)
		case "verify":
			return verifyCommand(ctx, args[1:], stdout)
		case "publish":
			return publishCommand(args[1:], stdout)
		case "collector-budget":
			return budgetCommand(ctx, args[1:], stdout)
		}
	}
	c := ingestbench.DefaultConfig()
	c.RunID = ingestbench.NewRunID()
	f := flag.NewFlagSet("otlpbench", flag.ContinueOnError)
	f.SetOutput(stdout)
	f.Usage = func() {
		fmt.Fprintln(stdout, "Usage: otlpbench --project-id UUID --project-name NAME [load flags] (offline plan unless --execute)")
		fmt.Fprintln(stdout, "Subcommands:")
		fmt.Fprintln(stdout, "  report result.json                         Render an offline diagnostic report")
		fmt.Fprintln(stdout, "  size measured-sizing-inputs.json           Extrapolate from supplied measurements")
		fmt.Fprintln(stdout, "  publish --markdown NEW.md --json NEW.json result.json  Publish validated evidence offline")
		fmt.Fprintln(stdout, "  verify --checkpoint PATH --out NEW.json    Resume read-only storage verification")
		fmt.Fprintln(stdout, "  collector-budget [--rate N --batch-size N --concurrency N]  Compute an admission heuristic")
		fmt.Fprintln(stdout, "Use <subcommand> --help for details. Load flags:")
		f.PrintDefaults()
	}
	f.StringVar(&c.RunID, "run-id", c.RunID, "unique 16-lowercase-hex run namespace (reuse is refused)")
	f.StringVar(&c.Endpoint, "endpoint", c.Endpoint, "collector OTLP endpoint: the full OTLP/HTTP traces URL, or http(s)://host:port for --encoding grpc")
	f.StringVar(&c.Encoding, "encoding", c.Encoding, "wire path: protobuf (OTLP/HTTP protobuf), json (OTLP/HTTP JSON) or grpc (OTLP/gRPC)")
	f.BoolVar(&c.Gzip, "gzip", c.Gzip, "gzip-compress request bodies (HTTP) or messages (gRPC), as SDK exporters usually do")
	f.StringVar(&c.ClickHouseURL, "ch-url", c.ClickHouseURL, "independent read-only ClickHouse HTTP endpoint")
	f.StringVar(&c.Database, "database", c.Database, "ClickHouse database")
	f.StringVar(&c.Table, "table", c.Table, "logical spans table (distributed table for a cluster)")
	f.StringVar(&c.ProjectID, "project-id", "", "dedicated test project UUID")
	f.StringVar(&c.ProjectName, "project-name", "", "project_name resolved by collector auth")
	f.StringVar(&c.Profile, "profile-label", c.Profile, "descriptive label only; never changes load settings")
	f.StringVar(&c.Payload, "payload", c.Payload, "small|medium|large|mixed: median synthetic text size class (lognormal sizes around it)")
	f.Int64Var(&c.Seed, "seed", c.Seed, "workload seed; the same seed gives the same span bytes for the same sequence and timestamp")
	f.Float64Var(&c.ErrorRate, "error-rate", c.ErrorRate, "fraction of spans with ERROR status, 0..1")
	f.IntVar(&c.TreeDepth, "tree-depth", c.TreeDepth, "maximum span depth below the root")
	f.IntVar(&c.FanOut, "fan-out", c.FanOut, "children per span in the k-ary trace layout")
	f.Float64Var(&c.CohortVariantFraction, "cohort-variant-fraction", c.CohortVariantFraction, "fraction of sessions in the variant cohort, 0..1")
	f.Float64Var(&c.BytesPerToken, "bytes-per-token", c.BytesPerToken, "text bytes per synthetic LLM token")
	f.IntVar(&c.TextPoolBlocks, "text-pool-blocks", c.TextPoolBlocks, "distinct text blocks in the seeded text pool")
	f.Int64Var(&c.Rate, "rate", c.Rate, "target SPANS per second, including children")
	f.DurationVar(&c.Duration, "duration", c.Duration, "load scheduling duration")
	f.DurationVar(&c.MaxDuration, "max-duration", c.MaxDuration, "hard load duration ceiling, max 24h")
	f.Int64Var(&c.MaxBytes, "max-bytes", c.MaxBytes, "hard cumulative UNCOMPRESSED OTLP request body byte ceiling")
	f.IntVar(&c.BatchSize, "batch-size", c.BatchSize, "spans per OTLP request")
	f.IntVar(&c.Concurrency, "concurrency", c.Concurrency, "maximum in-flight OTLP requests, no unbounded queue")
	f.Int64Var(&c.SpansPerTrace, "spans-per-trace", c.SpansPerTrace, "spans per trace, one root plus N-1 descendants")
	f.Int64Var(&c.TracesPerSession, "traces-per-session", c.TracesPerSession, "consecutive traces per synthetic session")
	f.DurationVar(&c.Timeout, "request-timeout", c.Timeout, "per-request timeout, <= 1m")
	f.DurationVar(&c.Settle, "settle", c.Settle, "quiet settle window before independent verification, <= 10m")
	f.DurationVar(&c.PollInterval, "poll-interval", c.PollInterval, "read-visible sentinel polling period, >= 100ms")
	f.IntVar(&c.MaxSentinels, "max-sentinels", c.MaxSentinels, "at most this many read-visible lag sentinels (the first span of every k-th request)")
	f.BoolVar(&c.DurableLag, "durable-lag", c.DurableLag, "measure created_at minus the scheduled send for every stored span (one extra query per verification chunk)")
	f.DurationVar(&c.CollectorPollInterval, "collector-poll-interval", c.CollectorPollInterval, "collector /healthz backlog sampling period (with --collector-health-url)")
	f.Int64Var(&c.VerifyChunk, "verify-chunk", c.VerifyChunk, "exact distinct verification chunk size in spans (bounded CH memory)")
	f.DurationVar(&c.VerifyTimeout, "verify-timeout", c.VerifyTimeout, "exact-ID verification budget; 0 derives one from the run size")
	f.DurationVar(&c.DurableLagTimeout, "durable-lag-timeout", c.DurableLagTimeout, "separate budget of the durable lag queries; 0 means the size of the exact-ID budget")
	f.DurationVar(&c.VerifyQueryTimeout, "verify-query-timeout", c.VerifyQueryTimeout, "per chunk query limit (server and client); 0 derives one from the chunk size")
	f.IntVar(&c.VerifyQueryRetries, "verify-query-retries", c.VerifyQueryRetries, "retries of a failed read-only verification query")
	f.Int64Var(&c.VerifyMaxMemory, "verify-max-memory", c.VerifyMaxMemory, "per-query max_memory_usage in bytes; 0 means 1 GiB")
	f.StringVar(&c.VerifyCheckpoint, "verify-checkpoint", "", "new file for resumable verification progress (see 'otlpbench verify')")
	f.StringVar(&c.CollectorHealthURL, "collector-health-url", "", "collector admin /healthz URL, read for its build revision and admission limits")
	f.Int64Var(&c.CollectorMaxPendingRows, "declared-collector-max-pending-rows", 0, "legacy row collector only; cannot declare current byte-budget sufficiency")
	f.BoolVar(&c.DirectProbes.Enabled, "direct-probes", c.DirectProbes.Enabled, "measure direct ClickHouse reads (trace, session, attribute filter) during load and after settle")
	f.DurationVar(&c.DirectProbes.Interval, "direct-probe-interval", c.DirectProbes.Interval, "direct probe round period during load, >= 500ms")
	f.DurationVar(&c.DirectProbes.MinAge, "direct-probe-min-age", c.DirectProbes.MinAge, "only probe traces acknowledged at least this long ago")
	f.IntVar(&c.DirectProbes.AfterSettleSamples, "direct-probe-after-settle", c.DirectProbes.AfterSettleSamples, "direct probe rounds after the settle window")
	f.DurationVar(&c.DirectProbes.QueryTimeout, "direct-probe-timeout", c.DirectProbes.QueryTimeout, "per direct probe query limit")
	f.BoolVar(&c.Execute, "execute", false, "send traffic; omitted means offline dry run")
	f.BoolVar(&c.AllowUnauth, "allow-unauthenticated-fixture", false, "only for isolated test servers, not the production binary")
	f.StringVar(&c.ConfirmRemote, "confirm-remote", "", "must equal run-id for each non-loopback target")
	f.StringVar(&c.ConfirmLoad, "confirm-load", "", "must equal run-id above smoke rate/duration/byte ceilings")
	f.StringVar(&c.Environment, "environment", c.Environment, "e.g. isolated-local, provisioned-staging or mock; do not imply production")
	f.StringVar(&c.Topology, "topology", c.Topology, "e.g. single-node or 3-shards-2-replicas")
	f.StringVar(&c.Revision, "revision", "", "git revision or image digest of the SYSTEM UNDER TEST (collector/backend), checked against /healthz when available")
	f.StringVar(&c.GeneratorRevision, "generator-revision", "", "git revision of this benchmark checkout, when the binary has no VCS stamp")
	f.StringVar(&c.SUTNotes, "sut-notes", "", "free-text description of the system under test (no secrets)")
	allowHosts := f.String("allow-hosts", "", "comma-separated exact non-loopback hostnames, no wildcard")
	containers := f.String("containers", "", "comma-separated explicit container names for read-only docker stats")
	nodes := f.String("nodes", "", "JSON array of named ClickHouse node HTTP URLs for resource measurements")
	probes := f.String("read-probes", "", "JSON array of actual authenticated read-only application API requests")
	otlpEnv := f.String("otlp-headers-env", "", "env variable containing JSON header name:value pairs, not written to artifacts")
	chEnv := f.String("ch-headers-env", "", "env variable containing read-only ClickHouse auth headers")
	out := f.String("out", "", "new result path; also writes .telemetry.jsonl; refuses overwriting")
	var declared faultFlags
	f.Var(&declared, "declare-fault", "name@offset+duration of a disruption YOU perform on an isolated stack during the load (e.g. clickhouse-stop@10m+60m); repeatable; recorded as declared, never performed by the tool")
	if err := f.Parse(args); err != nil {
		return err
	}
	if len(f.Args()) != 0 {
		return fmt.Errorf("unexpected positional arguments")
	}
	if *allowHosts != "" {
		c.AllowHosts = strings.Split(*allowHosts, ",")
	}
	c.DeclaredFaults = declared
	if *containers != "" {
		c.Containers = strings.Split(*containers, ",")
	}
	if *nodes != "" {
		if err := readJSON(*nodes, &c.Nodes); err != nil {
			return err
		}
	}
	if *probes != "" {
		if err := readJSON(*probes, &c.ReadProbes); err != nil {
			return err
		}
	}
	if err := c.Validate(); err != nil {
		return err
	}
	options := ingestbench.Options{CommandLine: append([]string{"otlpbench"}, args...), Warn: warnings}
	// An offline plan does not need credentials or contact any target.
	if c.Execute {
		var err error
		options.OTLPHeaders, err = ingestbench.HeadersFromEnv(*otlpEnv)
		if err != nil {
			return err
		}
		options.ClickHouseHeaders, err = ingestbench.HeadersFromEnv(*chEnv)
		if err != nil {
			return err
		}
	}
	var outputs *outputSet
	if c.Execute && *out == "" {
		return fmt.Errorf("--out is required for executed runs")
	}
	if *out != "" {
		paths := []string{*out}
		if c.Execute {
			paths = append(paths, *out+".telemetry.jsonl")
		}
		var err error
		outputs, err = reserveOutputs(paths...)
		if err != nil {
			return err
		}
		defer outputs.cleanup()
		if c.Execute {
			options.ResourceWriter = outputs.files[1]
		}
	}
	// The context also bounds settle, probes, verification and resource reads
	// after the load ends. In-flight requests cannot extend the load without limit.
	ctx, cancel := context.WithTimeout(ctx, c.MaxDuration+c.Settle+c.VerifyBudget()+c.DirectProbeBudget()+5*time.Minute)
	defer cancel()
	result, runErr := ingestbench.Run(ctx, c, options)
	if outputs != nil {
		if err := ingestbench.WriteResult(outputs.files[0], result); err != nil {
			return err
		}
		if err := outputs.commit(); err != nil {
			return err
		}
	}
	if err := ingestbench.WriteResult(stdout, result); err != nil {
		return err
	}
	if runErr != nil {
		return runErr
	}
	if c.Execute && result.Status != "measured-profile-complete" && !(len(c.DeclaredFaults) > 0 && result.Status == "measured-recovery-complete") {
		return fmt.Errorf("benchmark did not complete the requested profile without errors; inspect the written result")
	}
	return nil
}

// faultFlags parses repeated --declare-fault name@offset+duration values.
type faultFlags []ingestbench.DeclaredFault

func (f *faultFlags) String() string { return fmt.Sprint(*f) }

func (f *faultFlags) Set(v string) error {
	name, window, ok := strings.Cut(v, "@")
	at, length, ok2 := strings.Cut(window, "+")
	if !ok || !ok2 {
		return fmt.Errorf("want name@offset+duration, e.g. clickhouse-stop@10m+60m")
	}
	offset, err := time.ParseDuration(at)
	if err != nil {
		return fmt.Errorf("fault offset: %w", err)
	}
	d, err := time.ParseDuration(length)
	if err != nil {
		return fmt.Errorf("fault duration: %w", err)
	}
	*f = append(*f, ingestbench.DeclaredFault{Name: name, At: offset, Duration: d})
	return nil
}

// publishCommand renders the shareable Markdown and JSON publication of a
// result. It never overwrites a file and makes no network request.
func publishCommand(args []string, stdout io.Writer) error {
	f := flag.NewFlagSet("otlpbench publish", flag.ContinueOnError)
	f.SetOutput(stdout)
	f.Usage = func() {
		fmt.Fprintln(stdout, "Usage: otlpbench publish --markdown new.md --json new.json [flags] result.json")
		f.PrintDefaults()
	}
	markdown := f.String("markdown", "", "new path for the Markdown publication")
	jsonOut := f.String("json", "", "new path for the JSON publication")
	telemetry := f.String("telemetry", "", "the run's .telemetry.jsonl: digested and checked line by line")
	minSustained := f.Duration("min-sustained", ingestbench.DefaultMinSustained, "shortest load duration whose throughput may be published as sustained")
	if err := f.Parse(args); err != nil {
		return err
	}
	if len(f.Args()) != 1 || *markdown == "" || *jsonOut == "" || *minSustained <= 0 {
		return fmt.Errorf("usage: otlpbench publish --markdown new.md --json new.json [--telemetry run.telemetry.jsonl] [--min-sustained 1h] result.json")
	}
	var result ingestbench.Result
	if err := readJSON(f.Arg(0), &result); err != nil {
		return err
	}
	options := ingestbench.PublishOptions{MinSustained: *minSustained}
	if *telemetry != "" {
		t, err := os.Open(*telemetry)
		if err != nil {
			return err
		}
		defer t.Close()
		options.Telemetry = t
	}
	p, err := ingestbench.Publish(result, options)
	if err != nil {
		return err
	}
	outputs, err := stageOutputs(*markdown, *jsonOut)
	if err != nil {
		return err
	}
	defer outputs.cleanup()
	if err := ingestbench.WritePublicationMarkdown(outputs.files[0], p); err != nil {
		return err
	}
	if err := ingestbench.WritePublicationJSON(outputs.files[1], p); err != nil {
		return err
	}
	if err := outputs.commit(); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "headline %s", p.Headline.Status)
	if len(p.Headline.WithheldReasons) > 0 {
		fmt.Fprintf(stdout, ": %s", strings.Join(p.Headline.WithheldReasons, "; "))
	}
	fmt.Fprintln(stdout)
	return nil
}

// ResumedVerification is the artifact of 'otlpbench verify'.
type ResumedVerification struct {
	SchemaVersion string                   `json:"schema_version"`
	Kind          string                   `json:"kind"`
	RunID         string                   `json:"run_id"`
	CommandLine   []string                 `json:"command_line"`
	Verification  ingestbench.Verification `json:"verification"`
}

func verifyCommand(ctx context.Context, args []string, stdout io.Writer) error {
	f := flag.NewFlagSet("otlpbench verify", flag.ContinueOnError)
	f.SetOutput(stdout)
	checkpoint := f.String("checkpoint", "", "verification checkpoint written by --verify-checkpoint (updated in place as chunks finish)")
	out := f.String("out", "", "new path for the verification result; refuses overwriting")
	chEnv := f.String("ch-headers-env", "", "env variable containing read-only ClickHouse auth headers")
	confirmRemote := f.String("confirm-remote", "", "must equal the run ID when the ClickHouse URL is not loopback")
	timeout := f.Duration("verify-timeout", 0, "exact-ID budget for this resume; 0 keeps the run's budget")
	durableTimeout := f.Duration("durable-lag-timeout", 0, "durable lag budget for this resume; 0 keeps the run's budget")
	queryTimeout := f.Duration("verify-query-timeout", 0, "per chunk query limit; 0 keeps the run's limit")
	retries := f.Int("verify-query-retries", -1, "retries per failed query; -1 keeps the run's setting")
	if err := f.Parse(args); err != nil {
		return err
	}
	if *checkpoint == "" || *out == "" || len(f.Args()) != 0 {
		return fmt.Errorf("usage: otlpbench verify --checkpoint path --out new-result.json [--ch-headers-env NAME]")
	}
	if *timeout < 0 || *timeout > 72*time.Hour || *durableTimeout < 0 || *durableTimeout > 72*time.Hour || *queryTimeout < 0 || *queryTimeout > time.Hour || *retries > 10 {
		return fmt.Errorf("verify-timeout and durable-lag-timeout <= 72h, verify-query-timeout <= 1h, verify-query-retries <= 10")
	}
	headers, err := ingestbench.HeadersFromEnv(*chEnv)
	if err != nil {
		return err
	}
	outputs, err := stageOutputs(*out)
	if err != nil {
		return err
	}
	defer outputs.cleanup()
	cfg, v, err := ingestbench.ResumeVerification(ctx, *checkpoint, headers, func(c *ingestbench.Config) {
		c.Execute, c.ConfirmRemote = true, *confirmRemote
		if *timeout > 0 {
			c.VerifyTimeout = *timeout
		}
		if *durableTimeout > 0 {
			c.DurableLagTimeout = *durableTimeout
		}
		if *queryTimeout > 0 {
			c.VerifyQueryTimeout = *queryTimeout
		}
		if *retries >= 0 {
			c.VerifyQueryRetries = *retries
		}
	})
	if err != nil {
		return err
	}
	doc := ResumedVerification{SchemaVersion: ingestbench.SchemaVersion, Kind: "resumed-verification", RunID: cfg.RunID,
		CommandLine: ingestbench.RedactArgs(append([]string{"otlpbench", "verify"}, args...)), Verification: v}
	enc := json.NewEncoder(outputs.files[0])
	enc.SetIndent("", "  ")
	if err := enc.Encode(doc); err != nil {
		return err
	}
	if err := outputs.commit(); err != nil {
		return err
	}
	enc = json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(doc); err != nil {
		return err
	}
	if !v.Complete {
		return fmt.Errorf("verification is still incomplete (%s); rerun 'otlpbench verify' with the same checkpoint to continue", v.Error)
	}
	return nil
}

// budgetCommand compares a profile's heuristic row budget with the collector.
// Without --collector-health-url it makes no network request.
func budgetCommand(ctx context.Context, args []string, stdout io.Writer) error {
	c := ingestbench.DefaultConfig()
	f := flag.NewFlagSet("otlpbench collector-budget", flag.ContinueOnError)
	f.SetOutput(stdout)
	f.Int64Var(&c.Rate, "rate", c.Rate, "target spans per second")
	f.IntVar(&c.BatchSize, "batch-size", c.BatchSize, "spans per request")
	f.IntVar(&c.Concurrency, "concurrency", c.Concurrency, "in-flight requests")
	health := f.String("collector-health-url", "", "collector admin /healthz URL (read-only GET); omitted means no network request")
	declared := f.Int64("declared-collector-max-pending-rows", 0, "legacy row collector only; cannot declare current byte-budget sufficiency")
	allowHosts := f.String("allow-hosts", "", "comma-separated exact non-loopback hostnames")
	if err := f.Parse(args); err != nil {
		return err
	}
	if err := ingestbench.ValidateLoadBounds(c.Rate, c.BatchSize, c.Concurrency); err != nil {
		return err
	}
	if len(f.Args()) != 0 || *declared < 0 {
		return fmt.Errorf("usage: otlpbench collector-budget --rate N --batch-size N --concurrency N [--collector-health-url URL]")
	}
	var info ingestbench.CollectorInfo
	if *health != "" {
		c.RunID = ingestbench.NewRunID()
		if *allowHosts != "" {
			c.AllowHosts = strings.Split(*allowHosts, ",")
		}
		if err := c.CheckURL(*health); err != nil {
			return err
		}
		client := c.HTTPClient()
		defer client.CloseIdleConnections()
		info = ingestbench.FetchCollectorHealth(ctx, client, *health)
		if info.Error != "" {
			fmt.Fprintf(warnings, "\n*** WARNING: collector health: %s ***\n", info.Error)
		}
	}
	info.Budget = ingestbench.CheckBudget(c.Rate, c.BatchSize, c.Concurrency, info.Limits, *declared)
	if info.Budget.Error != "" {
		return fmt.Errorf("collector-budget: %s", info.Budget.Error)
	}
	for _, w := range info.Budget.Warnings() {
		fmt.Fprintf(warnings, "\n*** WARNING: %s ***\n\n", w)
	}
	enc := json.NewEncoder(stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(info)
}

func inputOperand(name, operand string, args []string, stdout io.Writer) (string, error) {
	f := flag.NewFlagSet("otlpbench "+name, flag.ContinueOnError)
	f.SetOutput(stdout)
	usage := "Usage: otlpbench " + name + " " + operand
	f.Usage = func() { fmt.Fprintln(stdout, usage) }
	if err := f.Parse(args); err != nil {
		return "", err
	}
	if f.NArg() != 1 {
		return "", fmt.Errorf("%s", usage)
	}
	return f.Arg(0), nil
}

func readJSON(path string, out any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, (16<<20)+1))
	if err != nil {
		return err
	}
	if len(raw) > 16<<20 {
		return fmt.Errorf("input JSON exceeds 16 MiB")
	}
	if _, ok := out.(*ingestbench.Result); ok {
		if err := ingestbench.ValidateResultJSON(raw); err != nil {
			return fmt.Errorf("invalid input JSON: %w", err)
		}
	}
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.DisallowUnknownFields()
	if err := d.Decode(out); err != nil {
		return fmt.Errorf("invalid input JSON: %w", err)
	}
	var trailing any
	if err := d.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("trailing JSON input")
	}
	return nil
}
