// Command k2drive is scenario 94's driver (scripts/scenarios/connectors/
// 94_cn_rest_runtime.sh). It has two modes:
//
//	k2drive witness --upstream http://127.0.0.1:<port> --addr 127.0.0.1:0 --addr-file <file> --out <file.jsonl>
//	k2drive drive --url <runtime app URL> --era 2025-11-25|2026-07-28 --calls <calls.json>
//	    --conn-token-env <VAR> --call-token-env <VAR> [--call-token-env <VAR>]... --out <results.json>
//
// witness is a loopback reverse proxy in front of a vendor stand-in (an
// agentcc-cassette replay). For each request it appends one JSON line to
// --out: the method, path and query, and the SHA-256 of the Authorization
// and X-API-Key values (never a value), so a scenario can tell which
// credential reached the vendor. It stops on SIGINT or SIGTERM.
//
// drive connects to one app of the runtime with the gateway's own MCP
// client (internal/mcp) and the bearer in --conn-token-env, lists the
// tools, then makes each call of --calls (a JSON array of {"tool",
// "arguments"}) with a per-call bearer, CallOptions.Auth, the way the gateway sends
// a user's token per call: call i uses the i-th --call-token-env, round
// robin. It writes the listed tools and every result to --out. Tokens come
// from the environment, never from the command line, and are never written.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func main() {
	if len(os.Args) < 2 {
		fail("usage: k2drive witness|drive ... (see the package doc)")
	}
	var err error
	switch os.Args[1] {
	case "witness":
		err = witness(os.Args[2:])
	case "drive":
		err = drive(os.Args[2:])
	default:
		err = fmt.Errorf("unknown mode %q", os.Args[1])
	}
	if err != nil {
		fail(err.Error())
	}
}

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "k2drive: "+msg)
	os.Exit(1)
}

func digest(v string) string {
	if v == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(v))
	return hex.EncodeToString(sum[:])
}

// seen is one request the witness forwarded.
type seen struct {
	Method       string `json:"method"`
	Path         string `json:"path"`
	Query        string `json:"query,omitempty"`
	AuthSHA256   string `json:"authorization_sha256,omitempty"`
	APIKeySHA256 string `json:"x_api_key_sha256,omitempty"`
}

func witness(args []string) error {
	fs := flag.NewFlagSet("witness", flag.ContinueOnError)
	upstream := fs.String("upstream", "", "the vendor stand-in's base URL (loopback)")
	addr := fs.String("addr", "127.0.0.1:0", "listen address (loopback)")
	addrFile := fs.String("addr-file", "", "write the bound address here")
	out := fs.String("out", "", "the JSON lines file to append to")
	if err := fs.Parse(args); err != nil {
		return err
	}
	u, err := url.Parse(*upstream)
	listenHost, _, splitErr := net.SplitHostPort(*addr)
	if err != nil || splitErr != nil || u.Scheme != "http" || !loopback(u.Hostname()) || !loopback(listenHost) || *out == "" {
		return errors.New("witness needs --upstream http://<loopback>:<port>, a loopback --addr and --out")
	}
	f, err := os.OpenFile(*out, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	var mu sync.Mutex
	proxy := httputil.NewSingleHostReverseProxy(u)
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		line, _ := json.Marshal(seen{Method: r.Method, Path: r.URL.EscapedPath(), Query: r.URL.RawQuery,
			AuthSHA256: digest(r.Header.Get("Authorization")), APIKeySHA256: digest(r.Header.Get("X-API-Key"))})
		mu.Lock()
		_, _ = f.Write(append(line, '\n'))
		mu.Unlock()
		proxy.ServeHTTP(w, r)
	})
	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		return err
	}
	srv := &http.Server{Handler: h, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	if *addrFile != "" {
		if err := os.WriteFile(*addrFile+".tmp", []byte(ln.Addr().String()+"\n"), 0o600); err != nil {
			return err
		}
		if err := os.Rename(*addrFile+".tmp", *addrFile); err != nil {
			return err
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	sctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return srv.Shutdown(sctx)
}

func loopback(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// call is one entry of --calls.
type call struct {
	Tool      string          `json:"tool"`
	Arguments json.RawMessage `json:"arguments"`
}

// results is drive's --out.
type results struct {
	Era   string            `json:"era"`
	Tools []json.RawMessage `json:"tools"`
	Calls []callResult      `json:"calls"`
}

type callResult struct {
	Tool       string          `json:"tool"`
	TokenIndex int             `json:"token_index"`
	Result     json.RawMessage `json:"result,omitempty"`
	Error      string          `json:"error,omitempty"`
}

type envList []string

func (l *envList) String() string     { return strings.Join(*l, ",") }
func (l *envList) Set(v string) error { *l = append(*l, v); return nil }

func token(name string) (string, error) {
	v := os.Getenv(name)
	if name == "" || v == "" {
		return "", fmt.Errorf("environment variable %q is not set", name)
	}
	return v, nil
}

func drive(args []string) error {
	fs := flag.NewFlagSet("drive", flag.ContinueOnError)
	target := fs.String("url", "", "the runtime app's MCP URL, http://127.0.0.1:<port>/<app>/mcp")
	era := fs.String("era", "2025-11-25", "the protocol era to pin")
	callsFile := fs.String("calls", "", "a JSON array of {tool, arguments}")
	connEnv := fs.String("conn-token-env", "", "the connection's bearer, by environment variable")
	var callEnv envList
	fs.Var(&callEnv, "call-token-env", "a per-call bearer, by environment variable (repeatable; round robin)")
	out := fs.String("out", "", "where to write the results")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *target == "" || *callsFile == "" || *out == "" || len(callEnv) == 0 {
		return errors.New("drive needs --url, --calls, --call-token-env and --out")
	}
	conn, err := token(*connEnv)
	if err != nil {
		return err
	}
	perCall := make([]string, len(callEnv))
	for i, name := range callEnv {
		if perCall[i], err = token(name); err != nil {
			return err
		}
	}
	data, err := os.ReadFile(*callsFile)
	if err != nil {
		return err
	}
	var calls []call
	if err := json.Unmarshal(data, &calls); err != nil || len(calls) == 0 {
		return fmt.Errorf("--calls: want a non-empty JSON array of {tool, arguments}: %v", err)
	}
	c := mcp.NewClient(mcp.ClientConfig{ServerID: "k2drive", URL: *target, TransportType: "http",
		Auth: mcp.AuthConfig{Type: "bearer", Token: conn}, Era: *era})
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer c.Close()
	res := results{Era: c.Era()}
	for _, t := range c.Tools() {
		raw, _ := json.Marshal(t)
		res.Tools = append(res.Tools, raw)
	}
	for i, cl := range calls {
		idx := i % len(perCall)
		auth := &mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer " + perCall[idx]}}}
		raw, err := c.CallToolRaw(ctx, cl.Tool, cl.Arguments, mcp.CallOptions{Auth: auth})
		r := callResult{Tool: cl.Tool, TokenIndex: idx, Result: raw}
		if err != nil {
			r.Error = err.Error()
		}
		res.Calls = append(res.Calls, r)
	}
	encoded, err := json.MarshalIndent(res, "", "  ")
	if err != nil {
		return err
	}
	for _, secret := range append([]string{conn}, perCall...) {
		if strings.Contains(string(encoded), secret) {
			return errors.New("a result holds a token; not written")
		}
	}
	return os.WriteFile(*out, append(encoded, '\n'), 0o600)
}
