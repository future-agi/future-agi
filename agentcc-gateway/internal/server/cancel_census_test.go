package server

import (
	"context"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// The provider record sites (recordProviderSignals, orgFailover.record and
// Failover's circuit breaker record) read a cancelled context as the
// client's own cancel and record nothing. So a context the gateway cancels
// for a reason of its own, such as a hedge that lost or an attempt it
// abandons, must never be the context one of those sites reads, nor an
// ancestor of it: a provider's real failure would then go unrecorded.
//
// Only code that creates a context holds its cancel, and the contexts above
// the record sites are made on the request path: the middleware, the
// server's handlers, routing and the pipeline. Providers sit below the
// record sites; their contexts are children, and cancelling a child never
// cancels its parent. This census pins every cancellable context those
// packages create. A new one fails the test until it is shown not to reach a
// record site (a child of the context the site reads, or work off the
// request path) and added below with why.

// censusDirs are the packages on the request path above the record sites,
// relative to this package.
var censusDirs = []string{".", "../routing", "../middleware", "../pipeline"}

// cancelCensus is every context the request path creates with a cancel:
// "cancel" (context.WithCancel, WithCancelCause) or "time limit"
// (WithTimeout, WithDeadline and their Cause forms, which end as
// DeadlineExceeded unless cancelled), by file and function, with its count.
var cancelCensus = map[string]int{
	// The request's own time limits. Each cancel runs when its handler
	// returns, after the handler's records; the limit itself ends the context
	// as DeadlineExceeded, which the record sites still count.
	"middleware/timeout.go TimeoutSkipping time limit":                              1,
	"server/handlers.go Handlers.ChatCompletion time limit":                         1,
	"server/handlers_completion.go Handlers.TextCompletion time limit":              1,
	"server/handlers_anthropic.go Handlers.AnthropicMessages time limit":            1,
	"server/handlers_anthropic.go Handlers.AnthropicCountTokens time limit":         1,
	"server/handlers_audio.go Handlers.CreateSpeech time limit":                     1,
	"server/handlers_audio.go Handlers.CreateTranscription time limit":              1,
	"server/handlers_audio.go Handlers.CreateTranslation time limit":                1,
	"server/handlers_audio.go Handlers.StreamSpeech time limit":                     1,
	"server/handlers_capability_echo.go Handlers.CapabilityEcho time limit":         1,
	"server/handlers_decision.go Handlers.serveDecision time limit":                 1,
	"server/handlers_embedding.go Handlers.CreateEmbedding time limit":              1,
	"server/handlers_genai.go Handlers.GenAIHandler time limit":                     1,
	"server/handlers_image.go Handlers.CreateImage time limit":                      1,
	"server/handlers_ocr.go Handlers.OCR time limit":                                1,
	"server/handlers_rerank.go Handlers.Rerank time limit":                          1,
	"server/handlers_responses.go Handlers.CreateResponse time limit":               1,
	"server/handlers_search.go Handlers.Search time limit":                          1,
	"server/handlers_speech.go Handlers.relaySpeech time limit":                     1,
	"server/handlers_vector_stores.go Handlers.proxyVectorStoresChecked time limit": 1,
	// The Assistants API proxy: one time limit per call, cancelled when the
	// call returns; it records nothing about a provider.
	"server/handlers_assistants.go Handlers.CancelRun time limit":          1,
	"server/handlers_assistants.go Handlers.CreateAssistant time limit":    1,
	"server/handlers_assistants.go Handlers.CreateMessage time limit":      1,
	"server/handlers_assistants.go Handlers.CreateRun time limit":          1,
	"server/handlers_assistants.go Handlers.CreateThread time limit":       1,
	"server/handlers_assistants.go Handlers.CreateThreadAndRun time limit": 1,
	"server/handlers_assistants.go Handlers.DeleteAssistant time limit":    1,
	"server/handlers_assistants.go Handlers.DeleteMessage time limit":      1,
	"server/handlers_assistants.go Handlers.DeleteThread time limit":       1,
	"server/handlers_assistants.go Handlers.GetAssistant time limit":       1,
	"server/handlers_assistants.go Handlers.GetMessage time limit":         1,
	"server/handlers_assistants.go Handlers.GetRun time limit":             1,
	"server/handlers_assistants.go Handlers.GetRunStep time limit":         1,
	"server/handlers_assistants.go Handlers.GetThread time limit":          1,
	"server/handlers_assistants.go Handlers.ListAssistants time limit":     1,
	"server/handlers_assistants.go Handlers.ListMessages time limit":       1,
	"server/handlers_assistants.go Handlers.ListRunSteps time limit":       1,
	"server/handlers_assistants.go Handlers.ListRuns time limit":           1,
	"server/handlers_assistants.go Handlers.SubmitToolOutputs time limit":  1,
	"server/handlers_assistants.go Handlers.UpdateAssistant time limit":    1,
	"server/handlers_assistants.go Handlers.UpdateMessage time limit":      1,
	"server/handlers_assistants.go Handlers.UpdateRun time limit":          1,
	"server/handlers_assistants.go Handlers.UpdateThread time limit":       1,
	// An attempt's own contexts: children of the context the record sites
	// read. Failover's per-attempt limit is cancelled after the attempt's
	// call returns, so orgFailover.record, inside the call, reads it live.
	"routing/failover.go Failover.callTarget time limit":       1,
	"server/attempt_budget.go attemptContext time limit":       1,
	"server/attempt_budget.go refusedContext time limit":       1,
	"server/attempt_budget.go withRequestDeadline time limit":  1,
	"server/attempt_budget.go streamAttemptContext time limit": 1,
	"server/attempt_budget.go streamAttemptContext cancel":     1,
	// Stream and race contexts: children made after the routing run that
	// records, or around calls that record nothing (a race keeps its winner
	// and records no loser).
	"routing/race.go RaceExecutor.Execute cancel":                                    1,
	"server/chat_stream.go Handlers.handleStream cancel":                             1,
	"server/handlers_completion.go Handlers.handleCompletionStream cancel":           1,
	"server/handlers_anthropic.go Handlers.handleAnthropicStreamViaCanonical cancel": 1,
	"server/handlers_genai.go Handlers.handleGenAIStreamViaCanonical cancel":         1,
	// Off the request path: background work, admin checks and start-up.
	"routing/mirror.go Mirror.ExecuteRuleAsync time limit":                 1,
	"server/admin_rotation.go rotationHooks time limit":                    1,
	"server/admin_rotation.go validateProviderKey time limit":              1,
	"server/hosted_readiness.go Server.hostedDependenciesReady time limit": 1,
	"server/key_scope.go keyScopeState.start cancel":                       1,
	"server/server.go New time limit":                                      1,
}

var cancelCtors = map[string]string{
	"WithCancel":        "cancel",
	"WithCancelCause":   "cancel",
	"WithTimeout":       "time limit",
	"WithTimeoutCause":  "time limit",
	"WithDeadline":      "time limit",
	"WithDeadlineCause": "time limit",
}

// requestPathCancels counts the cancellable contexts the census packages
// create, keyed as cancelCensus is.
func requestPathCancels(t *testing.T) map[string]int {
	t.Helper()
	found := map[string]int{}
	for _, dir := range censusDirs {
		files, err := filepath.Glob(filepath.Join(dir, "*.go"))
		if err != nil {
			t.Fatal(err)
		}
		for _, path := range files {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			src, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			file, err := parser.ParseFile(token.NewFileSet(), path, src, 0)
			if err != nil {
				t.Fatal(err)
			}
			pkg := filepath.Base(dir)
			if dir == "." {
				pkg = "server"
			}
			countCancels(file, pkg+"/"+filepath.Base(path), found)
		}
	}
	return found
}

// contextNames are the names file calls the standard "context" package by:
// its local name for each import of it ("context" unless renamed), and dot
// when it is imported with "." (its names are then used bare). A blank
// import adds none; an import of another path named context is not it.
func contextNames(file *ast.File) (names map[string]bool, dot bool) {
	names = map[string]bool{}
	for _, imp := range file.Imports {
		if imp.Path == nil || imp.Path.Value != `"context"` {
			continue
		}
		switch {
		case imp.Name == nil:
			names["context"] = true
		case imp.Name.Name == ".":
			dot = true
		case imp.Name.Name != "_":
			names[imp.Name.Name] = true
		}
	}
	return names, dot
}

// countCancels adds to found the cancellable contexts file creates, keyed
// "<name> <function> <kind>": every call of a cancelCtors constructor of the
// standard "context" package, by whatever name the file imports it under
// (contextNames).
func countCancels(file *ast.File, name string, found map[string]int) {
	names, dot := contextNames(file)
	if len(names) == 0 && !dot {
		return
	}
	for _, decl := range file.Decls {
		scope := "<file scope>"
		if fn, ok := decl.(*ast.FuncDecl); ok {
			scope = funcName(fn)
		}
		ast.Inspect(decl, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			var ctor string
			switch fun := call.Fun.(type) {
			case *ast.SelectorExpr:
				if pkgIdent, ok := fun.X.(*ast.Ident); ok && names[pkgIdent.Name] {
					ctor = fun.Sel.Name
				}
			case *ast.Ident:
				if dot {
					ctor = fun.Name
				}
			}
			if kind, ok := cancelCtors[ctor]; ok {
				found[fmt.Sprintf("%s %s %s", name, scope, kind)]++
			}
			return true
		})
	}
}

func funcName(fn *ast.FuncDecl) string {
	if fn.Recv == nil || len(fn.Recv.List) == 0 {
		return fn.Name.Name
	}
	typ := fn.Recv.List[0].Type
	if star, ok := typ.(*ast.StarExpr); ok {
		typ = star.X
	}
	if index, ok := typ.(*ast.IndexExpr); ok {
		typ = index.X
	}
	if ident, ok := typ.(*ast.Ident); ok {
		return ident.Name + "." + fn.Name.Name
	}
	return fn.Name.Name
}

func TestNoGatewayOwnedCancelReachesAProviderRecordSite(t *testing.T) {
	found := requestPathCancels(t)
	var added, gone []string
	for key, n := range found {
		if cancelCensus[key] != n {
			added = append(added, fmt.Sprintf("%s (%d, census %d)", key, n, cancelCensus[key]))
		}
	}
	for key, n := range cancelCensus {
		if found[key] == 0 {
			gone = append(gone, fmt.Sprintf("%s (census %d)", key, n))
		}
	}
	sort.Strings(added)
	sort.Strings(gone)
	if len(added) > 0 {
		t.Errorf("cancellable contexts the census does not list: show each never becomes the context, or an ancestor of the context, "+
			"that recordProviderSignals, orgFailover.record or Failover's breaker record reads (they read a cancel as the client's), "+
			"then list it in cancelCensus with why:\n  %s", strings.Join(added, "\n  "))
	}
	if len(gone) > 0 {
		t.Errorf("census entries the code no longer has; remove them:\n  %s", strings.Join(gone, "\n  "))
	}
}

// The census counts the standard "context" package's constructors by the
// name each file imports it under: an aliased or dot import is counted as a
// plain one, and another package imported under the name context is not.
func TestCancelCensusCountsAliasedContextImports(t *testing.T) {
	for _, tc := range []struct {
		name string
		src  string
		want map[string]int
	}{
		{"plain", `package x
import "context"
func f() { _, c := context.WithCancel(context.Background()); c() }`,
			map[string]int{"x/plain.go f cancel": 1}},
		{"aliased", `package x
import stdctx "context"
func f() {
	_, c := stdctx.WithCancel(stdctx.Background())
	c()
	_, d := stdctx.WithTimeout(stdctx.Background(), 0)
	d()
}`,
			map[string]int{"x/aliased.go f cancel": 1, "x/aliased.go f time limit": 1}},
		{"dot", `package x
import . "context"
type T struct{}
func (T) g() { _, c := WithDeadline(Background(), zero); c() }`,
			map[string]int{"x/dot.go T.g time limit": 1}},
		{"two names", `package x
import (
	"context"
	cx "context"
)
func f() { _, c := context.WithCancelCause(cx.Background()); c(nil); _, d := cx.WithCancel(context.Background()); d() }`,
			map[string]int{"x/two names.go f cancel": 2}},
		{"another package named context", `package x
import context "example.com/notcontext"
func f() { context.WithCancel(nil) }`,
			map[string]int{}},
		{"blank import", `package x
import _ "context"
func WithCancel() {}
func f() { WithCancel() }`,
			map[string]int{}},
	} {
		file, err := parser.ParseFile(token.NewFileSet(), tc.name+".go", tc.src, 0)
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		got := map[string]int{}
		countCancels(file, "x/"+tc.name+".go", got)
		if fmt.Sprint(got) != fmt.Sprint(tc.want) {
			t.Errorf("%s: counted %v, want %v", tc.name, got, tc.want)
		}
	}
}

// orgFailover.record, the record site of an org's own strategies, reads the
// attempt's context: an attempt the client cancelled records nothing, and an
// attempt a time limit ended, or one that failed with the client connected,
// still counts.
func TestOrgFailoverRecordSkipsOnlyAClientCancel(t *testing.T) {
	newOF := func() *orgFailover {
		latency := routing.NewLatencyTracker(0.3)
		return &orgFailover{latency: latency, health: routing.NewHealthMonitor(latency, nil)}
	}
	failure := errors.New("upstream failed")
	started := time.Now().Add(-20 * time.Millisecond)

	of := newOF()
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	of.record(cancelled, "t1", started, failure)
	of.record(cancelled, "t1", started, nil)
	if h := of.health.GetHealth("t1"); h.RequestCount != 0 || h.ErrorCount != 0 {
		t.Errorf("org health after attempts the client cancelled: %d requests, %d errors; want none", h.RequestCount, h.ErrorCount)
	}
	if got := of.latency.Get("t1"); got != 0 {
		t.Errorf("org latency after an attempt the client cancelled = %v ms; want none", got)
	}

	of = newOF()
	expired, stop := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer stop()
	of.record(expired, "t1", started, failure)
	of.record(context.Background(), "t1", started, failure)
	of.record(context.Background(), "t1", started, nil)
	if h := of.health.GetHealth("t1"); h.RequestCount != 3 || h.ErrorCount != 2 {
		t.Errorf("org health: %d requests, %d errors; want a time limit's and a provider's failure counted, and the success", h.RequestCount, h.ErrorCount)
	}
	if got := of.latency.Get("t1"); got <= 0 {
		t.Errorf("org latency after a served attempt = %v ms; want it recorded", got)
	}
}
