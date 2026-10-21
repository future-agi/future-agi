// otlpbench sends bounded OTLP/HTTP traffic and independently verifies storage.
package main

import (
	"context"
	"encoding/json"
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

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := command(ctx, os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "otlpbench:", err)
		os.Exit(1)
	}
}

func command(ctx context.Context, args []string, stdout io.Writer) error {
	if len(args) > 0 && args[0] == "report" {
		if len(args) != 2 {
			return fmt.Errorf("usage: otlpbench report result.json")
		}
		var result ingestbench.Result
		if err := readJSON(args[1], &result); err != nil {
			return err
		}
		return ingestbench.Report(stdout, result)
	}
	if len(args) > 0 && args[0] == "size" {
		if len(args) != 2 {
			return fmt.Errorf("usage: otlpbench size measured-sizing-inputs.json")
		}
		var input ingestbench.SizingInput
		if err := readJSON(args[1], &input); err != nil {
			return err
		}
		result, err := ingestbench.Size(input)
		if err != nil {
			return err
		}
		return json.NewEncoder(stdout).Encode(result)
	}
	c := ingestbench.DefaultConfig()
	c.RunID = ingestbench.NewRunID()
	f := flag.NewFlagSet("otlpbench", flag.ContinueOnError)
	f.SetOutput(stdout)
	f.StringVar(&c.RunID, "run-id", c.RunID, "unique 16-lowercase-hex run namespace (reuse is refused)")
	f.StringVar(&c.Endpoint, "endpoint", c.Endpoint, "full collector OTLP/HTTP traces URL")
	f.StringVar(&c.ClickHouseURL, "ch-url", c.ClickHouseURL, "independent read-only ClickHouse HTTP endpoint")
	f.StringVar(&c.Database, "database", c.Database, "ClickHouse database")
	f.StringVar(&c.Table, "table", c.Table, "logical spans table (distributed table for a cluster)")
	f.StringVar(&c.ProjectID, "project-id", "", "dedicated test project UUID")
	f.StringVar(&c.ProjectName, "project-name", "", "project_name resolved by collector auth")
	f.StringVar(&c.Profile, "profile-label", c.Profile, "descriptive label only; never changes load settings")
	f.StringVar(&c.Payload, "payload", c.Payload, "small|medium|large|mixed synthetic text sizes")
	f.Int64Var(&c.Rate, "rate", c.Rate, "target SPANS per second, including children")
	f.DurationVar(&c.Duration, "duration", c.Duration, "load scheduling duration")
	f.DurationVar(&c.MaxDuration, "max-duration", c.MaxDuration, "hard load duration ceiling, max 24h")
	f.Int64Var(&c.MaxBytes, "max-bytes", c.MaxBytes, "hard cumulative uncompressed OTLP request body byte ceiling")
	f.IntVar(&c.BatchSize, "batch-size", c.BatchSize, "spans per OTLP request")
	f.IntVar(&c.Concurrency, "concurrency", c.Concurrency, "maximum in-flight OTLP requests, no unbounded queue")
	f.Int64Var(&c.SpansPerTrace, "spans-per-trace", c.SpansPerTrace, "one root plus N-1 children")
	f.Int64Var(&c.TracesPerSession, "traces-per-session", c.TracesPerSession, "consecutive traces per synthetic session")
	f.DurationVar(&c.Timeout, "request-timeout", c.Timeout, "per-request timeout, <= 1m")
	f.DurationVar(&c.Settle, "settle", c.Settle, "quiet settle window before independent verification, <= 10m")
	f.DurationVar(&c.PollInterval, "poll-interval", c.PollInterval, "sample visibility polling period, >= 100ms")
	f.Int64Var(&c.VerifyChunk, "verify-chunk", c.VerifyChunk, "exact distinct verification chunk size (bounded CH memory)")
	f.BoolVar(&c.Execute, "execute", false, "send traffic; omitted means offline dry run")
	f.BoolVar(&c.AllowUnauth, "allow-unauthenticated-fixture", false, "only for isolated test servers, not the production binary")
	f.StringVar(&c.ConfirmRemote, "confirm-remote", "", "must equal run-id for each non-loopback target")
	f.StringVar(&c.ConfirmLoad, "confirm-load", "", "must equal run-id above smoke rate/duration/byte ceilings")
	f.StringVar(&c.Environment, "environment", c.Environment, "e.g. isolated-local, provisioned-staging or mock; do not imply production")
	f.StringVar(&c.Topology, "topology", c.Topology, "e.g. single-node or 3-shards-2-replicas")
	f.StringVar(&c.Revision, "revision", "", "tested collector/backend git revision or immutable image digest")
	allowHosts := f.String("allow-hosts", "", "comma-separated exact non-loopback hostnames, no wildcard")
	containers := f.String("containers", "", "comma-separated explicit container names for read-only docker stats")
	nodes := f.String("nodes", "", "JSON array of named ClickHouse node HTTP URLs for resource measurements")
	probes := f.String("read-probes", "", "JSON array of actual authenticated read-only application API requests")
	otlpEnv := f.String("otlp-headers-env", "", "env variable containing JSON header name:value pairs, not written to artifacts")
	chEnv := f.String("ch-headers-env", "", "env variable containing read-only ClickHouse auth headers")
	out := f.String("out", "", "new result path; also writes .telemetry.jsonl; refuses overwriting")
	if err := f.Parse(args); err != nil {
		return err
	}
	if len(f.Args()) != 0 {
		return fmt.Errorf("unexpected positional arguments")
	}
	if *allowHosts != "" {
		c.AllowHosts = strings.Split(*allowHosts, ",")
	}
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
	options := ingestbench.Options{}
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
	var output, resources *os.File
	if c.Execute && *out == "" {
		return fmt.Errorf("--out is required for executed runs")
	}
	if *out != "" {
		var err error
		output, err = os.OpenFile(*out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return fmt.Errorf("create output (existing files are never overwritten): %w", err)
		}
		defer output.Close()
		if c.Execute {
			resources, err = os.OpenFile(*out+".telemetry.jsonl", os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				return err
			}
			defer resources.Close()
			options.ResourceWriter = resources
		}
	}
	// The context also bounds settle + verification + resource probes after the
	// load ends. In-flight requests cannot extend the load without limit.
	ctx, cancel := context.WithTimeout(ctx, c.MaxDuration+c.Settle+12*time.Minute)
	defer cancel()
	result, runErr := ingestbench.Run(ctx, c, options)
	if output != nil {
		if err := ingestbench.WriteResult(output, result); err != nil {
			return err
		}
		if err := output.Sync(); err != nil {
			return err
		}
		if resources != nil {
			if err := resources.Sync(); err != nil {
				return err
			}
		}
	}
	if err := ingestbench.WriteResult(stdout, result); err != nil {
		return err
	}
	if runErr != nil {
		return runErr
	}
	if c.Execute && result.Status != "measured-profile-complete" {
		return fmt.Errorf("benchmark did not complete the requested profile without errors; inspect the written result")
	}
	return nil
}

func readJSON(path string, out any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 16<<20))
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
