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
	"time"
)

type options struct {
	Providers    []string
	Models       []string
	Cases        []string
	From, As     string
	Out          string
	DryRun       bool
	BaseURL      string
	Timeout      time.Duration
	ProvidersDir string
}

func main() {
	// Info logs from the registry are noise here; unknown-shape warnings from
	// the adapters are worth seeing.
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})))
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr, os.Getenv))
}

// run is main without the process: it returns the exit code. 0: every
// selected case was recorded (or its provider skipped for want of a key);
// 1: a case failed; 2: bad usage.
func run(args []string, stdout, stderr io.Writer, env func(string) string) int {
	opts, err := parseFlags(args, stderr)
	if err != nil {
		if err == flag.ErrHelp {
			return 0
		}
		_, _ = fmt.Fprintf(stderr, "contract-recorder: %v\n", err)
		return 2
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
	var providers, models, cases string
	fs.StringVar(&providers, "provider", "", "comma-separated providers to record (default: every provider with fixtures)")
	fs.StringVar(&models, "model", "", "comma-separated model IDs or globs to record (default: all)")
	fs.StringVar(&cases, "case", "", "comma-separated case names or globs to record (default: all)")
	fs.StringVar(&o.From, "from", "", "clone this model's cases into a new model (needs -as and a single -provider)")
	fs.StringVar(&o.As, "as", "", "the new model ID to record the -from cases against")
	fs.StringVar(&o.Out, "out", "", "write <out>/<provider>/<model>/<case>/response.* (the AGENTCC_CONTRACT_FIXTURES_DIR layout) instead of recording in place")
	fs.BoolVar(&o.DryRun, "dry-run", false, "print what would be recorded and where, without calling upstream or writing files")
	fs.StringVar(&o.BaseURL, "base-url", "", "override the upstream base URL (needs a single -provider)")
	fs.DurationVar(&o.Timeout, "timeout", 3*time.Minute, "per-case upstream timeout")
	fs.StringVar(&o.ProvidersDir, "providers-dir", "internal/providers", "the gateway's internal/providers directory")
	fs.Usage = func() {
		_, _ = fmt.Fprintf(fs.Output(), "usage: go run ./cmd/contract-recorder [flags]\n\n"+
			"Records real upstream responses for the provider contract tests through the\n"+
			"gateway's own adapters. Providers whose credentials are not in the environment\n"+
			"are skipped. Credentials: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,\n"+
			"COHERE_API_KEY, AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT, AWS_ACCESS_KEY_ID +\n"+
			"AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN, AWS_REGION), and <PRESET>_API_KEY\n"+
			"(e.g. GROQ_API_KEY) for openai fixtures keyed <preset>@<model>.\n\nflags:\n")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if fs.NArg() > 0 {
		return o, fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	o.Providers, o.Models, o.Cases = splitList(providers), splitList(models), splitList(cases)

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
