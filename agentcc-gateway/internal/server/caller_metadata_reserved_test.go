package server

import (
	"bufio"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Request metadata, the x-agentcc-metadata header and the body's metadata
// field, is the caller's dimension channel, and it lands in the same map the
// gateway writes its own billing, cost, telemetry, routing and identity keys
// into. Every name the gateway itself writes into or reads out of request
// metadata is the gateway's, unless callerSettableWant below lists it as one
// a caller may set, with the reason. A caller's value for a reserved name is
// dropped, never refused, so a request carrying one still runs.

// callerSettableWant are the names the gateway also writes or reads that stay
// the caller's, each with why that is safe. It is this test's own copy of the
// gateway's allow-list on purpose: a change to that list has to change this
// one too.
var callerSettableWant = map[string]string{
	// Caller requests the gateway reads by design; no plugin writes them.
	"provider_lock":     "the x-agentcc-provider-lock header's metadata form, checked against provider_lock's allow and deny lists like the header",
	"if_none_match":     "the edge cache's conditional request: a 304 only for the caller's own matching ETag",
	"edge_cache_opt_in": "the caller opting its own response into the edge cache, which cache.edge.require_opt_in asks for",
	// Ordinary caller dimensions the gateway writes only on one path, after
	// intake and unconditionally there, so its own value always wins.
	"customer_id":  "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"license_id":   "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"instance_id":  "written by the auth plugin (priority 20, after intake) from the license claims under license auth",
	"thread_id":    "written by the Assistants handlers from the URL after setupAssistantsRC",
	"message_id":   "written by the Assistants handlers from the URL after setupAssistantsRC",
	"assistant_id": "written by the Assistants handlers from the URL after setupAssistantsRC",
	// The speech relay's session evidence: written only by the relay
	// (handlers_speech.go), which reads no caller metadata, so on a relayed
	// session no caller value is there; elsewhere they are the caller's own.
	"voice":        "written only by the speech relay, which takes no caller metadata, when the session names a voice",
	"close_reason": "written only by the speech relay, which takes no caller metadata, when the session ends",
}

// reservedCallerKeys are gateway-owned names, grouped by what they record.
// Each is dropped from both channels in any letter case.
var reservedCallerKeys = []string{
	// Pricing inputs and the privacy mode.
	"audio_seconds", "input_characters", "images_generated", "quality", "model_alias", "privacy_mode",
	// Identity and policy.
	"access_groups", "access_group_matched", "x-guardrail-policy", "answer_unavailable", "request_unavailable",
	"a2a_task_id", "a2a_context_id",
	// Billing and usage evidence.
	"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
	"response_content_type", "response_size_bytes",
	// Routing evidence (several come back as x-agentcc-* response headers).
	"routing_strategy", "routing_strategy_applied", "routing_rule",
	"provider_locked", "provider_lock_target", "race_winner",
	"complexity_tier", "complexity_score", "complexity_source", "complexity_provider",
	"complexity_override", "complexity_original_model",
	"fallback_model", "fallback_provider", "failover_attempts", "failover_original_provider",
	"original_model", "original_provider", "model_map_matched", "model_map_provider",
	"model_deprecated", "model_max_output_tokens", "input_token_warning",
	"route_reason.strategy", "route_reason.conditional_rule", "route_reason.complexity_tier",
	// Telemetry.
	"endpoint_type", "endpoint_format", "translation_drops", "privacy_redacted",
	"tools_requested", "tools_filtered", "assistants_operation", "vector_stores_operation", "files_operation",
	"edge_cache", "edge_cache_control", "edge_cache_etag", "edge_cache_vary", "no_perf_disclosure",
}

// ordinaryCallerKeys are dimension names callers send (the e2e request-log
// tags flow sends application, service and team) or that sit next to a
// reserved family: none of them is the gateway's.
var ordinaryCallerKeys = []string{
	"tenant", "application", "service", "team", "user_id", "session_id", "environment",
	"cost_center", "cost_code", "costing_model", "model_version", "response_style",
	"routing_team", "audio_language", "image_style", "quality_tier", "privacy",
	"edge", "complexity", "fallback", "original", "provider", "endpoint",
}

func TestCallerCannotSetServerOwnedMetadata(t *testing.T) {
	for _, key := range reservedCallerKeys {
		for _, k := range []string{key, strings.ToUpper(key)} {
			if !isBlockedMetadataKey(k) {
				t.Errorf("caller metadata key %q is not reserved; the gateway owns it", k)
			}
		}
	}
	for key, why := range callerSettableWant {
		if isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is reserved; it stays the caller's (%s)", key, why)
		}
	}
	for _, key := range ordinaryCallerKeys {
		if isBlockedMetadataKey(key) {
			t.Errorf("caller metadata key %q is reserved; it is an ordinary dimension", key)
		}
	}
}

// Through both channels at once: every reserved name is dropped from the
// header and from the body, never recorded as the caller's, and the caller's
// other dimensions (allow-listed names included) still land.
func TestCallerReservedMetadataIsDroppedFromBothChannels(t *testing.T) {
	header := map[string]string{}
	body := map[string]string{}
	for _, key := range reservedCallerKeys {
		header[key] = "caller-header"
		body[strings.ToUpper(key)] = "caller-body"
	}
	want := map[string]string{"tenant": "acme", "application": "billing-bot"}
	for key := range callerSettableWant {
		want[key] = "caller-" + key
	}
	for k, v := range want {
		header[k] = v
	}
	hb, _ := json.Marshal(header)
	bb, _ := json.Marshal(body)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	r.Header.Set("x-agentcc-metadata", string(hb))
	applyCallerMetadata(rc, r, bb)

	for _, key := range reservedCallerKeys {
		for _, k := range []string{key, strings.ToUpper(key)} {
			if v, ok := rc.Metadata[k]; ok {
				t.Errorf("rc.Metadata[%q] = %q: a caller value for a reserved key landed", k, v)
			}
			if slices.Contains(rc.CustomMetadataKeys, k) {
				t.Errorf("reserved key %q recorded as a caller dimension", k)
			}
		}
	}
	for k, v := range want {
		if rc.Metadata[k] != v {
			t.Errorf("rc.Metadata[%q] = %q, want the caller's %q", k, rc.Metadata[k], v)
		}
		if !slices.Contains(rc.CustomMetadataKeys, k) {
			t.Errorf("caller dimension %q not recorded in CustomMetadataKeys", k)
		}
	}
}

// notRequestMetadata are the .Metadata maps the source audit finds that
// are not request metadata and hold a name request metadata leaves open: an
// API key's own metadata and a Pinecone match's. Matched on file, receiver
// and key, so a request-metadata use of the same name anywhere else is still
// audited. (API key metadata also holds org_id and access_groups, which are
// reserved in request metadata anyway.)
var notRequestMetadata = map[[3]string]string{
	{"internal/server/handlers.go", "apiKey", "log_content"}:   "the API key's log_content setting, copied into key_log_content",
	{"internal/server/hook_privacy.go", "key", "log_content"}:  "the API key's log_content setting",
	{"internal/cache/semantic_pinecone.go", "hit", "response"}: "a Pinecone match's stored response",
}

// TestEveryGatewayMetadataKeyIsReservedOrCallerSettable holds the reserved
// list complete against the gateway's own source: every constant key any
// non-test file of the module writes into or reads out of a .Metadata map
// (X.Metadata[K], X.SetMetadata(K, ...)), whether K is a literal or a
// constant of any package of the module, must be reserved, allow-listed as
// caller-settable, or named above as not request metadata. A new gateway key
// that is none of these fails here, so the list keeps up with the code.
//
// Keys built at run time (a loop copying one map into another) are copies of
// keys found elsewhere and are not audited.
func TestEveryGatewayMetadataKeyIsReservedOrCallerSettable(t *testing.T) {
	root, module := gatewayModuleRoot(t)
	sites := metadataKeySites(t, root, module)
	if len(sites) < 300 {
		t.Fatalf("the audit found %d metadata key sites; it is not reading the gateway's source", len(sites))
	}
	byKey := map[string][]string{}
	for _, s := range sites {
		if _, ok := notRequestMetadata[[3]string{s.file, s.recv, s.key}]; ok {
			continue
		}
		if isBlockedMetadataKey(s.key) {
			continue
		}
		if _, ok := callerSettableWant[strings.ToLower(s.key)]; ok {
			continue
		}
		byKey[s.key] = append(byKey[s.key], s.pos)
	}
	keys := make([]string, 0, len(byKey))
	for k := range byKey {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		t.Errorf("metadata key %q is the gateway's (%s) but a caller can set it: reserve it in isBlockedMetadataKey, "+
			"or allow-list it as caller-settable with the reason (the gateway's list and callerSettableWant), "+
			"or, if that map is not request metadata, name it in notRequestMetadata", k, strings.Join(byKey[k], ", "))
	}
}

type metadataKeySite struct{ key, recv, file, pos string }

// gatewayModuleRoot is the gateway module's directory and its module path.
func gatewayModuleRoot(t *testing.T) (string, string) {
	t.Helper()
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(filepath.Join(root, "go.mod"))
	if err != nil {
		t.Fatalf("go.mod: %v", err)
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if mod, ok := strings.CutPrefix(strings.TrimSpace(sc.Text()), "module "); ok {
			return root, strings.TrimSpace(mod)
		}
	}
	t.Fatal("go.mod names no module")
	return "", ""
}

type moduleFile struct {
	dir string // package directory, relative to the module root
	rel string // file path, relative to the module root
	f   *ast.File
}

// metadataKeySites parses every non-test Go file of the module and
// returns each constant metadata key it writes or reads.
func metadataKeySites(t *testing.T, root, module string) []metadataKeySite {
	t.Helper()
	fset := token.NewFileSet()
	var files []moduleFile
	err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if p != root && (d.Name() == "vendor" || d.Name() == "testdata" || strings.HasPrefix(d.Name(), ".")) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(fset, p, nil, parser.SkipObjectResolution)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, p)
		files = append(files, moduleFile{dir: filepath.ToSlash(filepath.Dir(rel)), rel: filepath.ToSlash(rel), f: f})
		return nil
	})
	if err != nil {
		t.Fatalf("reading the module source: %v", err)
	}

	// String constants of every package, keyed "<dir>.<Name>"; a constant
	// defined from another one resolves on a later pass.
	consts := map[string]string{}
	for pass := 0; pass < 4; pass++ {
		for _, x := range files {
			for _, decl := range x.f.Decls {
				gd, ok := decl.(*ast.GenDecl)
				if !ok || gd.Tok != token.CONST {
					continue
				}
				for _, spec := range gd.Specs {
					vs := spec.(*ast.ValueSpec)
					for i, name := range vs.Names {
						if i < len(vs.Values) {
							if v, ok := resolveConstKey(vs.Values[i], x, module, consts); ok {
								consts[x.dir+"."+name.Name] = v
							}
						}
					}
				}
			}
		}
	}

	var sites []metadataKeySite
	for _, x := range files {
		ast.Inspect(x.f, func(n ast.Node) bool {
			var key, recv ast.Expr
			switch e := n.(type) {
			case *ast.IndexExpr:
				if sel, ok := e.X.(*ast.SelectorExpr); ok && sel.Sel.Name == "Metadata" {
					key, recv = e.Index, sel.X
				}
			case *ast.CallExpr:
				if sel, ok := e.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "SetMetadata" && len(e.Args) > 0 {
					key, recv = e.Args[0], sel.X
				}
			}
			if key == nil {
				return true
			}
			if v, ok := resolveConstKey(key, x, module, consts); ok {
				pos := fset.Position(key.Pos())
				sites = append(sites, metadataKeySite{key: v, recv: metadataExprName(recv), file: x.rel, pos: x.rel + ":" + strconv.Itoa(pos.Line)})
			}
			return true
		})
	}
	return sites
}

// resolveConstKey is the string value of a literal or constant key expression.
func resolveConstKey(e ast.Expr, x moduleFile, module string, consts map[string]string) (string, bool) {
	switch t := e.(type) {
	case *ast.BasicLit:
		if t.Kind == token.STRING {
			s, err := strconv.Unquote(t.Value)
			return s, err == nil
		}
	case *ast.Ident:
		v, ok := consts[x.dir+"."+t.Name]
		return v, ok
	case *ast.SelectorExpr:
		pkg, ok := t.X.(*ast.Ident)
		if !ok {
			return "", false
		}
		for _, imp := range x.f.Imports {
			path, _ := strconv.Unquote(imp.Path.Value)
			name := filepath.Base(path)
			if imp.Name != nil {
				name = imp.Name.Name
			}
			if name == pkg.Name {
				dir, ok := strings.CutPrefix(path, module+"/")
				if !ok {
					return "", false
				}
				v, ok := consts[dir+"."+t.Sel.Name]
				return v, ok
			}
		}
	case *ast.BinaryExpr:
		if t.Op == token.ADD {
			a, okA := resolveConstKey(t.X, x, module, consts)
			b, okB := resolveConstKey(t.Y, x, module, consts)
			return a + b, okA && okB
		}
	case *ast.ParenExpr:
		return resolveConstKey(t.X, x, module, consts)
	}
	return "", false
}

// metadataExprName is a receiver expression as source-like text (rc, h.rc, f()).
func metadataExprName(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.Ident:
		return t.Name
	case *ast.SelectorExpr:
		return metadataExprName(t.X) + "." + t.Sel.Name
	case *ast.CallExpr:
		return metadataExprName(t.Fun) + "()"
	case *ast.IndexExpr:
		return metadataExprName(t.X) + "[]"
	case *ast.StarExpr:
		return "*" + metadataExprName(t.X)
	case *ast.ParenExpr:
		return metadataExprName(t.X)
	}
	return "?"
}

// The gateway's allow-list is this test's copy, name for name, and none of
// its names falls in a reserved family or the reserved names (the allow-list
// is checked first, so one that did would reopen a gateway key).
func TestCallerSettableMetadataKeysAreOutsideReservedFamilies(t *testing.T) {
	for key := range callerSettableMetadataKeys {
		if _, ok := callerSettableWant[key]; !ok {
			t.Errorf("the gateway allows caller key %q, which callerSettableWant does not list", key)
		}
		if key != strings.ToLower(key) {
			t.Errorf("allow-listed key %q is not lower case; isBlockedMetadataKey looks keys up lower-cased", key)
		}
		for _, prefix := range reservedMetadataPrefixes {
			if strings.HasPrefix(key, prefix) {
				t.Errorf("allow-listed key %q falls in the reserved family %q", key, prefix)
			}
		}
		if _, ok := reservedMetadataKeys[key]; ok {
			t.Errorf("allow-listed key %q is also reserved", key)
		}
	}
	for key := range callerSettableWant {
		if _, ok := callerSettableMetadataKeys[key]; !ok {
			t.Errorf("callerSettableWant lists %q, which the gateway does not allow", key)
		}
	}
}
