package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/record"
)

type recordFlags struct {
	kind, account, out                  string
	url, urlEnv, calls, era             string
	authEnv, authHeader, authScheme     string
	upstream, listen, addrFile          string
	headerEnv, redact, idKeys, credHdrs stringList
	secretEnv, secretParams, secretKeys stringList
	timeout                             time.Duration
}

func parseRecordFlags(args []string, stderr io.Writer) (recordFlags, error) {
	var f recordFlags
	fs := flag.NewFlagSet("record", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&f.kind, "kind", "", "mcp or http")
	fs.StringVar(&f.account, "test-account", "", "the test account's label (required): cassettes record test accounts only")
	fs.StringVar(&f.out, "out", "", "the cassette file to write")
	fs.StringVar(&f.url, "url", "", "mcp: the server's Streamable HTTP endpoint")
	fs.StringVar(&f.urlEnv, "url-env", "", "mcp: the environment variable holding the endpoint, for a URL that carries a credential")
	fs.StringVar(&f.calls, "calls", "", `mcp: a JSON array of {"name", "arguments"} tool calls to record`)
	fs.StringVar(&f.era, "era", "auto", "mcp: auto, 2026-07-28, or a revision to initialize with")
	fs.StringVar(&f.authEnv, "auth-env", "", "mcp: the environment variable holding the credential")
	fs.StringVar(&f.authHeader, "auth-header", "Authorization", "mcp: the header --auth-env's credential goes in")
	fs.StringVar(&f.authScheme, "auth-scheme", "Bearer", "mcp: the scheme before the credential in Authorization")
	fs.Var(&f.headerEnv, "header-env", "mcp: Header=VAR, a credential header from an environment variable (repeatable)")
	fs.StringVar(&f.upstream, "upstream", "", "http: the origin to record (https://host[:port])")
	fs.StringVar(&f.listen, "listen", "127.0.0.1:0", "http: the proxy's loopback listen address")
	fs.StringVar(&f.addrFile, "addr-file", "", "http: write the bound address here once listening")
	fs.Var(&f.redact, "redact", "a literal to scrub everywhere, at least 4 characters (repeatable)")
	fs.Var(&f.idKeys, "id-key", "a JSON key whose values are ids (repeatable)")
	fs.Var(&f.credHdrs, "credential-header", "a header whose values are credentials (repeatable)")
	fs.Var(&f.secretEnv, "secret-env", "an environment variable holding a credential the recording uses (repeatable)")
	fs.Var(&f.secretParams, "secret-param", "a query or form parameter whose values are credentials (repeatable)")
	fs.Var(&f.secretKeys, "secret-key", "a JSON member name whose values are credentials (repeatable)")
	fs.DurationVar(&f.timeout, "timeout", 60*time.Second, "per request")
	if err := fs.Parse(args); err != nil {
		return f, err
	}
	if fs.NArg() > 0 {
		return f, fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	return f, nil
}

func runRecord(ctx context.Context, args []string, stderr io.Writer, logger *log.Logger) int {
	f, err := parseRecordFlags(args, stderr)
	if err != nil {
		return exitUsage
	}
	// First, before any network call or listener: no label, no recording.
	if err := cassette.ValidateTestAccount(f.account); err != nil {
		logger.Printf("record refused: %v", err)
		return exitUsage
	}
	if f.out == "" {
		logger.Print("record: --out is required")
		return exitUsage
	}
	cfg, err := scrubConfig(f)
	if err != nil {
		logger.Printf("record: %v", err)
		return exitUsage
	}
	switch f.kind {
	case "mcp":
		return recordMCP(ctx, f, cfg, logger)
	case "http":
		return recordHTTP(ctx, f, cfg, logger)
	}
	logger.Printf("record: --kind %q: want mcp or http", f.kind)
	return exitUsage
}

func recordMCP(ctx context.Context, f recordFlags, cfg cassette.ScrubConfig, logger *log.Logger) int {
	if f.upstream != "" || f.addrFile != "" {
		logger.Print("record --kind mcp: --upstream and --addr-file are --kind http flags")
		return exitUsage
	}
	creds, secrets, err := credentialsFromEnv(f)
	if err == nil {
		f.url, err = endpoint(f)
	}
	if err != nil {
		logger.Printf("record: %v", err)
		return exitUsage
	}
	secrets = append(secrets, urlSecrets(f.url)...)
	var calls []record.Call
	if f.calls != "" {
		if calls, err = readCalls(f.calls); err != nil {
			logger.Printf("record: --calls: %v", err)
			return exitUsage
		}
	}
	c, err := record.MCP(ctx, record.MCPOptions{URL: f.url, TestAccount: f.account, Era: f.era,
		Credentials: creds, Calls: calls, Timeout: f.timeout})
	if err != nil {
		logger.Printf("record: %v", err)
		return exitFail
	}
	for name := range creds {
		cfg.CredentialHeaders = append(cfg.CredentialHeaders, name)
	}
	cfg.KnownSecrets = append(cfg.KnownSecrets, secrets...)
	return save(f.out, c, cfg, logger)
}

// scrubConfig gathers the scrub flags, before any network call: the names
// must be names, the literals long enough and spelled in no credential name
// the cassette keeps, and every --secret-env variable must hold a value
// long enough to become a known secret, scrubbed wherever it appears.
func scrubConfig(f recordFlags) (cassette.ScrubConfig, error) {
	cfg := cassette.ScrubConfig{Literals: f.redact, IDKeys: f.idKeys, CredentialHeaders: f.credHdrs,
		SecretParams: f.secretParams, SecretKeys: f.secretKeys}
	if err := (&cassette.Rules{SecretParams: f.secretParams}).Validate(); err != nil {
		return cfg, fmt.Errorf("--secret-param: %w", err)
	}
	if err := (&cassette.Rules{SecretKeys: f.secretKeys}).Validate(); err != nil {
		return cfg, fmt.Errorf("--secret-key: %w", err)
	}
	for _, name := range f.secretEnv {
		v := os.Getenv(name)
		switch {
		case v == "":
			return cfg, fmt.Errorf("--secret-env: environment variable %s is empty or unset", name)
		case len(v) < cassette.MinKnownSecret:
			// never the value, only where it came from
			return cfg, fmt.Errorf("--secret-env: the value of %s is shorter than %d characters, too short to find wherever it appears; "+
				"name where it goes with --secret-param or --secret-key instead", name, cassette.MinKnownSecret)
		}
		cfg.KnownSecrets = append(cfg.KnownSecrets, v)
	}
	return cfg, cfg.Validate()
}

// credentialsFromEnv builds the credential headers from environment
// variables; the values are also the recording's known secrets, scrubbed
// wherever they appear.
func credentialsFromEnv(f recordFlags) (http.Header, []string, error) {
	h := http.Header{}
	var secrets []string
	read := func(name string) (string, error) {
		v := os.Getenv(name)
		if v == "" {
			return "", fmt.Errorf("environment variable %s is empty or unset", name)
		}
		return v, nil
	}
	if f.authEnv != "" {
		v, err := read(f.authEnv)
		if err != nil {
			return nil, nil, err
		}
		header := v
		if strings.EqualFold(f.authHeader, "Authorization") && f.authScheme != "" {
			header = f.authScheme + " " + v
		}
		h.Set(f.authHeader, header)
		secrets = append(secrets, v)
	}
	for _, spec := range f.headerEnv {
		name, env, ok := strings.Cut(spec, "=")
		if !ok || name == "" || env == "" {
			return nil, nil, fmt.Errorf("--header-env %q: want Header=VAR", spec)
		}
		v, err := read(env)
		if err != nil {
			return nil, nil, err
		}
		h.Set(name, v)
		secrets = append(secrets, v)
	}
	return h, secrets, nil
}

// endpoint is --url, or the URL in --url-env's variable.
func endpoint(f recordFlags) (string, error) {
	switch {
	case f.url != "" && f.urlEnv != "":
		return "", errors.New("--url and --url-env are exclusive")
	case f.urlEnv != "":
		v := os.Getenv(f.urlEnv)
		if v == "" {
			return "", fmt.Errorf("environment variable %s is empty or unset", f.urlEnv)
		}
		return v, nil
	}
	return f.url, nil
}

// urlSecrets are the parts of an endpoint that may be a credential (a
// URL-variable key in the path, a key in the query): they are scrubbed
// wherever the server echoes them. Short path segments are ordinary words.
func urlSecrets(raw string) []string {
	u, err := url.Parse(raw)
	if err != nil {
		return nil
	}
	var out []string
	for _, seg := range strings.Split(u.Path, "/") {
		if len(seg) >= 16 {
			out = append(out, seg)
		}
	}
	for _, vs := range u.Query() {
		for _, v := range vs {
			if v != "" {
				out = append(out, v)
			}
		}
	}
	return out
}

func readCalls(path string) ([]record.Call, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var calls []record.Call
	if err := dec.Decode(&calls); err != nil {
		return nil, err
	}
	return calls, nil
}

func recordHTTP(ctx context.Context, f recordFlags, cfg cassette.ScrubConfig, logger *log.Logger) int {
	if f.url != "" || f.urlEnv != "" || f.calls != "" || f.authEnv != "" || len(f.headerEnv) > 0 {
		logger.Print("record --kind http: --url, --url-env, --calls, --auth-env and --header-env are --kind mcp flags; the client sends its own credential through the proxy (name it with --secret-env)")
		return exitUsage
	}
	if err := record.CheckLoopback(f.listen); err != nil {
		logger.Printf("record: %v", err)
		return exitUsage
	}
	proxy, err := record.NewProxy(record.ProxyOptions{Upstream: f.upstream, TestAccount: f.account, Timeout: f.timeout})
	if err != nil {
		logger.Printf("record: %v", err)
		return exitUsage
	}
	ln, err := net.Listen("tcp", f.listen)
	if err != nil {
		logger.Printf("record: %v", err)
		return exitFail
	}
	srv := &http.Server{Handler: proxy, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	bound := ln.Addr().String()
	if f.addrFile != "" {
		if err := writeAtomic(f.addrFile, bound+"\n"); err != nil {
			logger.Printf("record: --addr-file: %v", err)
			_ = srv.Close()
			return exitFail
		}
	}
	logger.Printf("recording %s through http://%s for test account %s; stop with SIGINT or SIGTERM", f.upstream, bound, f.account)
	<-ctx.Done()
	shutdown(srv)
	c := proxy.Cassette()
	if len(c.HTTP.Exchanges) == 0 {
		logger.Print("record: nothing was recorded; no cassette written")
		return exitFail
	}
	return save(f.out, c, cfg, logger)
}

// save scrubs, verifies and writes; a refusal writes nothing.
func save(out string, c *cassette.Cassette, cfg cassette.ScrubConfig, logger *log.Logger) int {
	if err := cassette.Save(out, c, cfg); err != nil {
		var refused *cassette.RefusedError
		if errors.As(err, &refused) {
			logger.Printf("record: %v", refused)
		} else {
			logger.Printf("record: save: %v", err)
		}
		return exitFail
	}
	n := 0
	if c.MCP != nil {
		n = len(c.MCP.Exchanges)
	} else {
		n = len(c.HTTP.Exchanges)
	}
	logger.Printf("wrote %s: %d exchange(s), placeholders %v", out, n, c.Scrubbed)
	return exitOK
}

// shutdown lets requests in flight finish for a moment, then closes every
// connection: a client's spare connection that never carried a request
// would otherwise hold Shutdown for five seconds.
func shutdown(srv *http.Server) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		_ = srv.Close()
	}
}
