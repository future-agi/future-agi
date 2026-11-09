// Command agentcc-cassette records a connector's exchanges with a test
// account into a scrubbed cassette, and replays cassettes offline (L2 of the
// connector conformance levels). Standard library only:
//
//	agentcc-cassette record --kind mcp --test-account <label> --url <endpoint>|--url-env <VAR> --out <file>
//	    [--calls <calls.json>] [--era auto|2026-07-28|2025-11-25|2025-06-18|2025-03-26]
//	    [--auth-env <VAR>] [--auth-header Authorization] [--auth-scheme Bearer]
//	    [--header-env <Header>=<VAR>]... [--timeout 60s] [scrub flags]
//	agentcc-cassette record --kind http --test-account <label> --upstream <https://origin> --out <file>
//	    [--listen 127.0.0.1:0] [--addr-file <file>] [--timeout 60s] [scrub flags]
//	agentcc-cassette replay --cassette <file> [--cassette <file>]... --addr <host:port>
//	    [--addr-file <file>] [--path /mcp]
//	agentcc-cassette check <file>...
//
// record refuses to run without --test-account: cassettes hold test-account
// data only, never a customer's. --kind mcp records initialize (or
// server/discover), every page of tools/list, resources/list and
// prompts/list, and each tools/call in --calls (a JSON array of
// {"name", "arguments"}). --kind http is a loopback proxy to --upstream that
// records each request/response pair until SIGINT or SIGTERM. Credentials
// come from environment variables (--auth-env, --header-env, and --url-env
// for an endpoint that carries one), never from the command line. Scrub
// flags, each repeatable: --secret-env <VAR> (a credential the recording
// uses, such as the one an HTTP client sends through the proxy: scrubbed
// wherever it appears), --secret-param <name> (a query or form parameter)
// and --secret-key <name> (a JSON member name) whose values are
// credentials, which the cassette keeps so replay blinds them too,
// --credential-header <name>, --redact <literal> (the test account's login,
// workspace or tenant name), --id-key <json key>.
//
// Nothing is written until the recording is scrubbed (tokens, emails and ids
// become stable placeholders) and verified: a cassette that still holds a
// credential-shaped value, an email or any scrubbed value is refused and no
// file is written (exit 1).
//
// replay serves MCP (Streamable HTTP at --path) or HTTP cassettes, keyed by
// (tool, normalized arguments) or (method, path, query, body sha256). It
// makes no network call. A request no cassette answers fails loudly (a
// JSON-RPC error or HTTP 501 marked X-Agentcc-Cassette: miss, a MISS line,
// /__cassette/misses), and replay then exits 3 when stopped. check re-runs
// the verification on cassettes already on disk. Logs go to stderr.
package main

import (
	"context"
	"io"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
)

// Exit codes.
const (
	exitOK     = 0
	exitFail   = 1 // a refusal to write, an upstream or file error
	exitUsage  = 2 // bad flags, including a missing test account
	exitMisses = 3 // replay answered a request it had no recording for
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stdout, os.Stderr)
	stop()
	os.Exit(code)
}

func run(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	logger := log.New(stderr, "agentcc-cassette: ", 0)
	if len(args) == 0 {
		logger.Print("usage: agentcc-cassette record|replay|check ... (see the package doc)")
		return exitUsage
	}
	switch args[0] {
	case "record":
		return runRecord(ctx, args[1:], stderr, logger)
	case "replay":
		return runReplay(ctx, args[1:], stderr, logger)
	case "check":
		return runCheck(args[1:], stdout, logger)
	}
	logger.Printf("unknown command %q: want record, replay or check", args[0])
	return exitUsage
}

// stringList is a repeatable string flag.
type stringList []string

func (l *stringList) String() string     { return strings.Join(*l, ",") }
func (l *stringList) Set(v string) error { *l = append(*l, v); return nil }

// writeAtomic writes path through a temporary file and a rename, so a
// script polling for it never reads it half-written.
func writeAtomic(path, content string) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(content), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
