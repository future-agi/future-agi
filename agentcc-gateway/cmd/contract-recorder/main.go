// Command contract-recorder records real upstream responses for the provider
// contract tests (internal/providers/contracttest).
//
// For each selected case it sends the case's request.json through the
// gateway's own provider adapter, built by the provider registry, to the real
// upstream API, and saves the raw upstream response body byte for byte. What
// it records is exactly what the provider returns for exactly the request the
// gateway sends.
//
// Run it from agentcc-gateway/:
//
//	go run ./cmd/contract-recorder -provider anthropic -dry-run
//	go run ./cmd/contract-recorder -provider anthropic                    # in place, updates meta.json
//	go run ./cmd/contract-recorder -out /path/to/recordings               # every provider with a key
//	go run ./cmd/contract-recorder -provider gemini -from gemini-4-argon -as gemini-4.1-argon
//	go run ./cmd/contract-recorder -out /path/to/recordings -endpoint speech,images -max-models 1 -max-cases 1
//
// Each case is driven through the capability method its case.json
// "endpoint" names (chat when it has none). Cases case.json marks
// "recordable": false are never sent. An error case (case.json "status"
// 400-599) is saved only when upstream answers with exactly that status, and
// its case.json takes the response's retry and rate-limit headers.
//
// See internal/providers/contracttest/README.md ("Recording real fixtures").
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"slices"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

type options struct {
	Providers        []string
	Models           []string
	Cases            []string
	Endpoints        []string
	ExcludeEndpoints []string
	MaxCases         int
	MaxModels        int
	Rotate           int
	From, As         string
	Out              string
	DryRun           bool
	BaseURL          string
	Timeout          time.Duration
	ProvidersDir     string
}

func main() {
	// Info logs from the registry are noise here; unknown-shape warnings from
	// the adapters are worth seeing.
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})))
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, os.Getenv))
}

// run is main without the process: it returns the exit code. 0: every
// selected case was recorded (or its provider skipped for want of a key);
// 1: a case failed; 2: bad usage; 3: no recordable case matched the
// selection, so nothing was sent.
func run(args []string, stdout, stderr io.Writer, env func(string) string) int {
	opts, err := parseFlags(args, stderr)
	if err != nil {
		if err == flag.ErrHelp {
			return exitOK
		}
		_, _ = fmt.Fprintf(stderr, "contract-recorder: %v\n", err)
		return exitUsage
	}
	r := &recorder{opts: opts, out: stdout, env: env, now: time.Now}
	return r.run(context.Background())
}

func parseFlags(args []string, stderr io.Writer) (options, error) {
	// A FlagSet of its own: the contracttest package (imported for its
	// fixture layout) registers -update on the global one.
	fs := flag.NewFlagSet("contract-recorder", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var o options
	var providers, models, cases, endpoints, excluded string
	fs.StringVar(&providers, "provider", "", "comma-separated providers to record (default: every provider with fixtures)")
	fs.StringVar(&models, "model", "", "comma-separated model IDs or globs to record (default: all)")
	fs.StringVar(&cases, "case", "", "comma-separated case names or globs to record (default: all)")
	fs.StringVar(&endpoints, "endpoint", "", "comma-separated case.json endpoints to record (default: all; a case without case.json is chat)")
	fs.StringVar(&excluded, "exclude-endpoint", "", "comma-separated endpoints not to record")
	fs.IntVar(&o.MaxCases, "max-cases", 0, "record at most `N` cases per model and endpoint, text and tool cases first (0: no cap)")
	fs.IntVar(&o.MaxModels, "max-models", 0, "record at most `N` models per provider and endpoint (0: no cap)")
	fs.IntVar(&o.Rotate, "rotate", 0, "with -max-models, start the sorted model list at this `offset` (a day or week number), so successive runs reach every model")
	fs.StringVar(&o.From, "from", "", "clone this model's cases into a new model whose meta.json says cloned_from (needs -as and a single -provider)")
	fs.StringVar(&o.As, "as", "", "the new model ID to record the -from cases against")
	fs.StringVar(&o.Out, "out", "", "write <out>/<provider>/<model>/<case>/response.* (and case.json where the repo's no longer fits; the AGENTCC_CONTRACT_FIXTURES_DIR layout) instead of recording in place")
	fs.BoolVar(&o.DryRun, "dry-run", false, "print what would be recorded and where, without calling upstream or writing files")
	fs.StringVar(&o.BaseURL, "base-url", "", "override the upstream base URL (needs a single -provider)")
	fs.DurationVar(&o.Timeout, "timeout", 3*time.Minute, "per-case upstream timeout")
	fs.StringVar(&o.ProvidersDir, "providers-dir", "internal/providers", "the gateway's internal/providers directory")
	fs.Usage = func() {
		_, _ = fmt.Fprintf(fs.Output(), "usage: go run ./cmd/contract-recorder [flags]\n\n"+
			"Records real upstream responses for the provider contract tests through the\n"+
			"gateway's own adapters, each case through its case.json endpoint. Providers\n"+
			"whose credentials are not in the environment are skipped. Credentials:\n"+
			"ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, COHERE_API_KEY,\n"+
			"AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT, AWS_ACCESS_KEY_ID +\n"+
			"AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN, AWS_REGION), <PRESET>_API_KEY\n"+
			"(e.g. GROQ_API_KEY, MISTRAL_API_KEY) for openai fixtures keyed <preset>@<model>,\n"+
			"and VERTEX_PROJECT + VERTEX_ACCESS_TOKEN (+ VERTEX_LOCATION) for gemini\n"+
			"fixtures keyed vertex@<model>.\n\n"+
			"Exit status: 0 recorded (or skipped for want of a key), 1 a case failed,\n"+
			"2 bad usage, 3 no recordable case matched the selection.\n\nflags:\n")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if fs.NArg() > 0 {
		return o, fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	o.Providers, o.Models, o.Cases = splitList(providers), splitList(models), splitList(cases)
	o.Endpoints, o.ExcludeEndpoints = splitList(endpoints), splitList(excluded)
	for _, ep := range append(append([]string(nil), o.Endpoints...), o.ExcludeEndpoints...) {
		if !slices.Contains(contracttest.Endpoints, ep) {
			return o, fmt.Errorf("unknown endpoint %q (known: %s)", ep, strings.Join(contracttest.Endpoints, ", "))
		}
	}
	for _, ep := range o.Endpoints {
		if slices.Contains(o.ExcludeEndpoints, ep) {
			return o, fmt.Errorf("endpoint %q is in both -endpoint and -exclude-endpoint", ep)
		}
	}

	switch {
	case (o.From == "") != (o.As == ""):
		return o, fmt.Errorf("-from and -as go together")
	case o.From != "" && o.From == o.As:
		return o, fmt.Errorf("-as must name a different model than -from")
	case o.From != "" && len(o.Models) > 0:
		return o, fmt.Errorf("-model cannot be combined with -from/-as")
	case o.From != "" && len(o.Providers) != 1:
		return o, fmt.Errorf("-from/-as needs exactly one -provider")
	case o.BaseURL != "" && len(o.Providers) != 1:
		return o, fmt.Errorf("-base-url needs exactly one -provider")
	case o.Timeout <= 0:
		return o, fmt.Errorf("-timeout must be positive")
	case o.MaxCases < 0 || o.MaxModels < 0 || o.Rotate < 0:
		return o, fmt.Errorf("-max-cases, -max-models and -rotate cannot be negative")
	}
	for _, p := range o.Providers {
		if _, ok := providerSpecs[p]; !ok {
			return o, fmt.Errorf("unknown provider %q (known: %v)", p, knownProviders())
		}
	}
	if o.BaseURL != "" {
		if _, err := parseBaseURL(o.BaseURL); err != nil {
			return o, err
		}
	}
	if st, err := os.Stat(o.ProvidersDir); err != nil || !st.IsDir() {
		return o, fmt.Errorf("-providers-dir %q is not a directory; run from agentcc-gateway/ or pass -providers-dir", o.ProvidersDir)
	}
	return o, nil
}
