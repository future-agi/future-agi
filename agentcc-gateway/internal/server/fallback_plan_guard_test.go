package server

import (
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
)

// cat2PlanResolverFile is the one non-test file in this package that may touch
// a model fallback chain source: the request's models[] (rc.Routing.Models),
// the org's chains with any plan auto installed (rc.OrgModelFallbacks,
// tenant.RoutingConfig.ModelFallbacks) and the node's config.yaml chains
// (Handlers.modelFallbacks, config.RoutingConfig.ModelFallbacks).
const cat2PlanResolverFile = "fallback_plan.go"

// cat2PlanResolver is the one function admission and dispatch resolve a plan
// with.
const cat2PlanResolver = "effectiveFallbackPlan"

// cat2PlanSourceSelectors are the fields and lookups that read a chain source
// directly. A package-qualified type (routing.ModelFallbacks) is not a read.
var cat2PlanSourceSelectors = []string{
	"OrgModelFallbacks", "ModelFallbacks", "modelFallbacks",
	"GetChain", "HasFallbacks", "LookupChain", "NewModelFallbacks",
}

// cat2PlanSourceIdents are the identity helpers only the resolver may use:
// fallbackChainModel picks the routed identity a chain is inherited under.
var cat2PlanSourceIdents = []string{"fallbackChainModel"}

// cat2PlanCallers are admission (mixedCataloguePlan, which pool admission and
// post-choice eligibility both reach through judge) and every chat dispatch
// path that walks a plan: streaming, non-streaming without and with provider
// failover, and resolution-time fallback. Each must take its plan from the
// resolver.
var cat2PlanCallers = []string{"mixedCataloguePlan", "openRoutedStream", "runNonStream", "runNonStreamWithFailover", "resolveProviderFor"}

// cat2RouteMaterializer is the one function that fixes a request's fallback
// route (the identity its plan is resolved for), once routing has chosen the
// request's first attempt. cat2RouteMaterializers call it just before every
// admission (checkEligibility) they make, and are the only functions that
// admit a route: chat's route and the Messages handler. cat2DispatchEntries
// call it as their first statement, before any attempt: a no-op for a routed
// request, so no attempt ever runs on an unfixed route. Nothing else calls it.
const cat2RouteMaterializer = "fixFallbackRoute"

var (
	cat2RouteMaterializers = []string{"resolveRoute", "AnthropicMessages", "AnthropicCountTokens"}
	cat2DispatchEntries    = []string{"runNonStream", "openRoutedStream"}
)

// cat2ProvisionalRoute is the route resolution-time fallback walks a plan on
// while routing is still choosing the first attempt, before the route is
// fixed. Only cat2ProvisionalCaller may build one.
const (
	cat2ProvisionalRoute  = "provisionalFallbackRoute"
	cat2ProvisionalCaller = "resolveProviderFor"
)

// cat2RouteAccessor reads a request's fixed route (rc.FallbackRoute()), and
// cat2RouteBuilders build (models.NewFallbackRoute) or fix
// (rc.FixFallbackRoute) one: only the resolver's file may call those.
const cat2RouteAccessor = "FallbackRoute"

var cat2RouteBuilders = []string{"NewFallbackRoute", "FixFallbackRoute"}

// cat2LiveIdentitySelectors are request fields that change after routing
// has fixed a request's route: provider failover and fallback hops rewrite
// rc.Request.Model, and routing rewrites rc.Model and the request's metadata
// on the way to its first attempt. The resolver and every helper it calls
// take the request's identity from its route instead.
var cat2LiveIdentitySelectors = []string{"Model", "Request", "Metadata"}

// TestCAT2FallbackPlanSourceGuard keeps admission and dispatch on one plan by
// construction. A second reader of a chain source is how admission and
// dispatch disagreed in rounds 9 to 12 (the node chain under the upstream
// identity, the org chain under it, the auto alias, explicit-empty entries):
// every non-test file but the resolver's must reach a chain only through
// effectiveFallbackPlan, and each admission and dispatch path must call it.
func TestCAT2FallbackPlanSourceGuard(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	fset := token.NewFileSet()
	var violations []string
	callers := map[string]bool{}
	resolverDeclared := false
	seen := map[token.Pos]bool{}
	parsed := map[string]*ast.File{}
	flag := func(pos token.Pos, what string) {
		if !seen[pos] {
			seen[pos] = true
			violations = append(violations, fset.Position(pos).String()+": "+what)
		}
	}
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(fset, name, nil, parser.SkipObjectResolution)
		if err != nil {
			t.Fatal(err)
		}
		parsed[name] = f
		imports := map[string]bool{}
		for _, spec := range f.Imports {
			p, err := strconv.Unquote(spec.Path.Value)
			if err != nil {
				t.Fatal(err)
			}
			local := path.Base(p)
			if spec.Name != nil {
				local = spec.Name.Name
			}
			imports[local] = true
		}
		for _, decl := range f.Decls {
			fn := ""
			if d, ok := decl.(*ast.FuncDecl); ok {
				fn = d.Name.Name
				if name == cat2PlanResolverFile && fn == cat2PlanResolver && d.Recv != nil {
					resolverDeclared = true
				}
			}
			ast.Inspect(decl, func(n ast.Node) bool {
				switch n := n.(type) {
				case *ast.SelectorExpr:
					if n.Sel.Name == cat2PlanResolver {
						callers[fn] = true
					}
					if name == cat2PlanResolverFile {
						return true
					}
					if id, ok := n.X.(*ast.Ident); ok && imports[id.Name] && n.Sel.Name != "NewModelFallbacks" {
						return true // a package-qualified type or function
					}
					if slices.Contains(cat2PlanSourceSelectors, n.Sel.Name) {
						flag(n.Sel.Pos(), fn+" reads ."+n.Sel.Name)
					}
					if inner, ok := n.X.(*ast.SelectorExpr); ok && inner.Sel.Name == "Routing" && n.Sel.Name == "Models" {
						flag(n.Sel.Pos(), fn+" reads .Routing.Models")
					}
				case *ast.Ident:
					if name != cat2PlanResolverFile && slices.Contains(cat2PlanSourceIdents, n.Name) {
						flag(n.Pos(), fn+" uses "+n.Name)
					}
				}
				return true
			})
		}
	}
	if !resolverDeclared {
		t.Errorf("%s does not declare the (*Handlers).%s resolver", cat2PlanResolverFile, cat2PlanResolver)
	}
	for _, v := range violations {
		t.Errorf("chain source read outside %s: %s", cat2PlanResolverFile, v)
	}
	for _, fn := range cat2PlanCallers {
		if !callers[fn] {
			t.Errorf("%s does not take its plan from %s", fn, cat2PlanResolver)
		}
	}
	checkCat2FixedRoute(t, fset, parsed)
}

// checkCat2FixedRoute keeps every plan on the route routing fixed, by
// construction (round 14). Provider failover rewrites rc.Request.Model before
// a dispatch path resolves its plan, and a resolver that read it rebuilt a
// declared plan around the failover target's model and resurrected the
// request's first hop (CAT-2 r13 close, Sol P2). So:
//   - the resolver, and every function in its file it calls, reads no live
//     identity (cat2LiveIdentitySelectors) and passes rc to nothing else;
//   - every other caller passes the request's fixed route,
//     rc.FallbackRoute(), except resolution-time fallback, which walks a
//     provisional route while routing is still choosing the first attempt;
//   - one function fixes the route, called only by chat's route and the
//     Messages handler, once just before each admission there (no other
//     function admits a route), and first thing by each dispatch entry; only
//     the resolver's file builds or fixes a route.
func checkCat2FixedRoute(t *testing.T, fset *token.FileSet, files map[string]*ast.File) {
	t.Helper()
	at := func(pos token.Pos) string { return fset.Position(pos).String() }
	resolverFile := files[cat2PlanResolverFile]
	if resolverFile == nil {
		t.Fatalf("%s was not parsed", cat2PlanResolverFile)
	}
	decls := map[string]*ast.FuncDecl{}
	for _, d := range resolverFile.Decls {
		if fd, ok := d.(*ast.FuncDecl); ok {
			decls[fd.Name.Name] = fd
		}
	}
	for _, name := range []string{cat2RouteMaterializer, cat2ProvisionalRoute} {
		if decls[name] == nil {
			t.Errorf("%s does not declare %s", cat2PlanResolverFile, name)
		}
	}

	// The resolver and its helpers: every function of its file that
	// effectiveFallbackPlan reaches through calls.
	reached := map[string]bool{}
	queue := []string{cat2PlanResolver}
	for len(queue) > 0 {
		name := queue[0]
		queue = queue[1:]
		if reached[name] || decls[name] == nil {
			continue
		}
		reached[name] = true
		ast.Inspect(decls[name], func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok && decls[cat2CalleeName(call)] != nil {
				queue = append(queue, cat2CalleeName(call))
			}
			return true
		})
	}
	for name := range reached {
		ast.Inspect(decls[name], func(n ast.Node) bool {
			switch n := n.(type) {
			case *ast.SelectorExpr:
				if slices.Contains(cat2LiveIdentitySelectors, n.Sel.Name) {
					t.Errorf("%s: %s reads .%s; a plan's identity is its route's", at(n.Sel.Pos()), name, n.Sel.Name)
				}
			case *ast.CallExpr:
				callee := cat2CalleeName(n)
				for _, arg := range n.Args {
					if id, ok := arg.(*ast.Ident); ok && id.Name == "rc" && decls[callee] == nil {
						t.Errorf("%s: %s passes rc to %s, outside the resolver", at(n.Pos()), name, callee)
					}
				}
			}
			return true
		})
	}

	provisional := false
	for file, f := range files {
		for _, d := range f.Decls {
			fd, ok := d.(*ast.FuncDecl)
			if !ok {
				continue
			}
			fn := fd.Name.Name
			var fixes, admissions []token.Pos
			ast.Inspect(fd, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				switch callee := cat2CalleeName(call); {
				case callee == cat2PlanResolver && file != cat2PlanResolverFile:
					if len(call.Args) != 2 {
						t.Errorf("%s: %s calls %s with %d arguments, want rc and its route", at(call.Pos()), fn, callee, len(call.Args))
						break
					}
					route := call.Args[1]
					if fn == cat2ProvisionalCaller {
						if c, ok := route.(*ast.CallExpr); !ok || cat2CalleeName(c) != cat2ProvisionalRoute {
							t.Errorf("%s: %s walks a plan on %s, not %s", at(route.Pos()), fn, types.ExprString(route), cat2ProvisionalRoute)
						}
					} else if !cat2IsFixedRoute(route) {
						t.Errorf("%s: %s resolves its plan on %s, not the request's fixed route rc.%s()", at(route.Pos()), fn, types.ExprString(route), cat2RouteAccessor)
					}
				case callee == cat2RouteMaterializer:
					fixes = append(fixes, call.Pos())
					if !slices.Contains(cat2RouteMaterializers, fn) && !slices.Contains(cat2DispatchEntries, fn) {
						t.Errorf("%s: %s fixes a fallback route; only %v and %v may", at(call.Pos()), fn, cat2RouteMaterializers, cat2DispatchEntries)
					}
				case callee == cat2ProvisionalRoute:
					provisional = provisional || fn == cat2ProvisionalCaller
					if fn != cat2ProvisionalCaller {
						t.Errorf("%s: %s builds a provisional route; only %s may", at(call.Pos()), fn, cat2ProvisionalCaller)
					}
				case slices.Contains(cat2RouteBuilders, callee) && file != cat2PlanResolverFile:
					t.Errorf("%s: %s calls %s outside %s", at(call.Pos()), fn, callee, cat2PlanResolverFile)
				case callee == "checkEligibility":
					admissions = append(admissions, call.Pos())
					if !slices.Contains(cat2RouteMaterializers, fn) {
						t.Errorf("%s: %s admits a route without fixing it; only %v may", at(call.Pos()), fn, cat2RouteMaterializers)
					}
				}
				return true
			})
			if slices.Contains(cat2DispatchEntries, fn) && !cat2FixesFirst(fd) {
				t.Errorf("%s does not fix the request's fallback route as its first statement, before any attempt", fn)
			}
			if !slices.Contains(cat2RouteMaterializers, fn) {
				continue
			}
			// One fix just before each admission: fix, admit, fix, admit.
			slices.Sort(fixes)
			slices.Sort(admissions)
			ordered := len(fixes) > 0 && len(fixes) == len(admissions)
			for i := 0; ordered && i < len(fixes); i++ {
				ordered = fixes[i] < admissions[i] && (i == 0 || fixes[i] > admissions[i-1])
			}
			if !ordered {
				t.Errorf("%s fixes the request's fallback route %d times for %d admissions; want once just before each", fn, len(fixes), len(admissions))
			}
		}
	}
	if !provisional {
		t.Errorf("%s does not walk resolution-time fallback on %s", cat2ProvisionalCaller, cat2ProvisionalRoute)
	}
}

// cat2CalleeName is the name a call calls: f for f(...), m for x.m(...).
func cat2CalleeName(call *ast.CallExpr) string {
	switch fn := call.Fun.(type) {
	case *ast.Ident:
		return fn.Name
	case *ast.SelectorExpr:
		return fn.Sel.Name
	}
	return ""
}

// cat2FixesFirst reports whether fd's first statement is
// fixFallbackRoute(rc).
func cat2FixesFirst(fd *ast.FuncDecl) bool {
	if fd.Body == nil || len(fd.Body.List) == 0 {
		return false
	}
	stmt, ok := fd.Body.List[0].(*ast.ExprStmt)
	if !ok {
		return false
	}
	call, ok := stmt.X.(*ast.CallExpr)
	if !ok || cat2CalleeName(call) != cat2RouteMaterializer || len(call.Args) != 1 {
		return false
	}
	rc, ok := call.Args[0].(*ast.Ident)
	return ok && rc.Name == "rc"
}

// cat2IsFixedRoute reports whether e is rc.FallbackRoute().
func cat2IsFixedRoute(e ast.Expr) bool {
	call, ok := e.(*ast.CallExpr)
	if !ok || len(call.Args) != 0 {
		return false
	}
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || sel.Sel.Name != cat2RouteAccessor {
		return false
	}
	rc, ok := sel.X.(*ast.Ident)
	return ok && rc.Name == "rc"
}
