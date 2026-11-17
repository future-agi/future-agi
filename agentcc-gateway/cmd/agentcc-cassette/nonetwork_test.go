package main

import (
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

const module = "github.com/futureagi/agentcc-gateway/"

// clientSelectors are the standard library's ways to call out: an HTTP
// client or transport, a dial, a DNS lookup.
var clientSelectors = map[string]map[string]bool{
	"net/http": {"Get": true, "Head": true, "Post": true, "PostForm": true, "NewRequest": true,
		"NewRequestWithContext": true, "DefaultClient": true, "DefaultTransport": true, "Client": true, "Transport": true},
	"net": {"Dialer": true, "Resolver": true, "DefaultResolver": true},
}

var forbiddenImports = map[string]bool{"net/http/httputil": true, "net/rpc": true, "net/smtp": true,
	"os/exec": true, "crypto/tls": true, "syscall": true, "unsafe": true}

// TestReplayMakesNoNetworkCall proves replay cannot call out, two ways.
// Statically: the replay package, and the only package beyond the standard
// library it may import (cassette), hold no HTTP client, transport, dial,
// lookup or raw syscall.
// Dynamically: replay runs as its own process with every proxy variable
// pointed at a canary listener, answers recorded requests and misses for
// both kinds, and the canary never sees a connection.
func TestReplayMakesNoNetworkCall(t *testing.T) {
	t.Run("static: no client code in replay or cassette", func(t *testing.T) {
		allowed := map[string]map[string]bool{
			"replay":   {module + "cmd/agentcc-cassette/cassette": true},
			"cassette": {},
		}
		for dir, inModule := range allowed {
			checkNoClientCode(t, dir, inModule)
		}
	})
	t.Run("dynamic: a replay process never connects out", func(t *testing.T) {
		canary, accepted := canaryListener(t)
		dir := t.TempDir()
		mcpFile := saveCassette(t, dir, mcpCassette())
		httpFile := saveCassette(t, dir, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{
			Exchanges: []cassette.HTTPExchange{{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/ok"},
				Response: cassette.HTTPResponse{Status: 200, Body: &cassette.Body{Text: "ok"}}}}}})
		for _, tc := range []struct {
			file  string
			drive func(base string)
		}{
			{mcpFile, func(base string) {
				for _, body := range []string{
					`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}`,
					`{"jsonrpc":"2.0","id":2,"method":"tools/list"}`,
					`{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_weather","arguments":{"city":"Paris"}}}`,
					`{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"fetch","arguments":{"url":"https://example.com/"}}}`,
					`{"jsonrpc":"2.0","id":5,"method":"resources/read","params":{"uri":"https://example.com/doc"}}`,
				} {
					rawRPC(t, base+"/mcp", body)
				}
			}},
			{httpFile, func(base string) {
				for _, path := range []string{"/v1/ok", "/v1/missing", "/http://example.com/"} {
					send(t, "GET", base+path, "any", "")
				}
			}},
		} {
			code := runReplayProcess(t, tc.file, canary, tc.drive)
			if code != exitMisses {
				t.Errorf("%s: replay exited %d, want %d (it answered misses)", filepath.Base(tc.file), code, exitMisses)
			}
		}
		time.Sleep(100 * time.Millisecond) // let a late dial land, if one was ever coming
		if n := accepted.Load(); n != 0 {
			t.Fatalf("the canary saw %d connection(s) from replay", n)
		}
	})
}

func checkNoClientCode(t *testing.T, dir string, inModule map[string]bool) {
	t.Helper()
	fset := token.NewFileSet()
	files, err := filepath.Glob(filepath.Join(dir, "*.go"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no Go files in %s", dir)
	}
	for _, path := range files {
		if strings.HasSuffix(path, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			t.Fatal(err)
		}
		names := map[string]string{} // local name -> import path
		for _, imp := range f.Imports {
			p, _ := strconv.Unquote(imp.Path.Value)
			stdlib := !strings.Contains(strings.SplitN(p, "/", 2)[0], ".")
			switch {
			case forbiddenImports[p]:
				t.Errorf("%s imports %s", path, p)
			case !stdlib && !inModule[p]:
				t.Errorf("%s imports %s; beyond the standard library only %v may be imported", path, p, inModule)
			}
			name := p[strings.LastIndex(p, "/")+1:]
			if imp.Name != nil {
				name = imp.Name.Name
			}
			names[name] = p
		}
		ast.Inspect(f, func(n ast.Node) bool {
			sel, ok := n.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			pkg, ok := sel.X.(*ast.Ident)
			if !ok {
				return true
			}
			imp := names[pkg.Name]
			if clientSelectors[imp][sel.Sel.Name] || imp == "net" && (strings.HasPrefix(sel.Sel.Name, "Dial") || strings.HasPrefix(sel.Sel.Name, "Lookup")) {
				t.Errorf("%s: %s uses %s.%s", fset.Position(sel.Pos()), filepath.Base(path), imp, sel.Sel.Name)
			}
			return true
		})
	}
}

// canaryListener counts every connection anything makes to it.
func canaryListener(t *testing.T) (string, *atomic.Int64) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	var n atomic.Int64
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			n.Add(1)
			c.Close()
		}
	}()
	return ln.Addr().String(), &n
}

// runReplayProcess runs `agentcc-cassette replay` as a child process with
// every proxy variable set to the canary, drives it, stops it with SIGTERM
// and returns its exit code.
func runReplayProcess(t *testing.T, file, canary string, drive func(base string)) int {
	t.Helper()
	addrFile := filepath.Join(t.TempDir(), "addr")
	cmd := exec.Command(os.Args[0], "replay", "--cassette", file, "--addr", "127.0.0.1:0", "--addr-file", addrFile)
	cmd.Env = append(os.Environ(), "CASSETTE_RUN_MAIN=1", "NO_PROXY=", "no_proxy=",
		"HTTP_PROXY=http://"+canary, "HTTPS_PROXY=http://"+canary, "ALL_PROXY=socks5://"+canary,
		"http_proxy=http://"+canary, "https_proxy=http://"+canary, "all_proxy=socks5://"+canary)
	var stderr syncBuffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	deadline := time.Now().Add(15 * time.Second)
	var base string
	for base == "" {
		if data, err := os.ReadFile(addrFile); err == nil && len(data) > 0 {
			base = "http://" + strings.TrimSpace(string(data))
		} else if time.Now().After(deadline) {
			t.Fatalf("replay never listened: %s", stderr.String())
		} else {
			time.Sleep(20 * time.Millisecond)
		}
	}
	drive(base)
	resp, err := http.Get(base + "/__cassette/health")
	if err != nil {
		t.Fatal(err)
	}
	var health map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&health)
	resp.Body.Close()
	if health["misses"].(float64) == 0 {
		t.Errorf("the drive caused no miss: %v", health)
	}
	_ = cmd.Process.Signal(syscall.SIGTERM)
	select {
	case err := <-done:
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode()
		}
		if err != nil {
			t.Fatal(err)
		}
		return 0
	case <-time.After(15 * time.Second):
		t.Fatalf("replay did not stop on SIGTERM: %s", stderr.String())
	}
	return -1
}
