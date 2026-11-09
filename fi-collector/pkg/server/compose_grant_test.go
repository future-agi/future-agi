package server

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
)

type composeGrantResolver struct {
	claims       *auth.GrantClaims
	kind, change string
	state, calls atomic.Int32
}

func (*composeGrantResolver) ValidateKey(context.Context, string, string) (*auth.ResolveResult, error) {
	return nil, errors.New("grant must not use personal persistence")
}
func (*composeGrantResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, errors.New("unexpected project lookup")
}
func (*composeGrantResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("unexpected create")
}
func (r *composeGrantResolver) ValidateGrant(_ context.Context, claims *auth.GrantClaims) (*auth.ResolveResult, error) {
	r.calls.Add(1)
	if *claims != *r.claims {
		return nil, nil
	}
	state := r.state.Load()
	if state != 0 && (r.change == "revoke" || r.change == "cancel") {
		return nil, nil
	}
	result := restartResult(claims.Org, claims.Workspace, r.kind == "sandbox")
	result.Grant = claims
	if r.kind == "platform" {
		result.Principal = auth.PrincipalPlatform
		result.KeyType = ""
	}
	if state != 0 && r.change == "remap" {
		result.Projects["customer-prod"], result.Projects[routeHiddenName] = calProject, calProject
	}
	return result, nil
}

func TestComposeGrantRevocationAndExpiryTransportParity(t *testing.T) {
	for _, kind := range []string{"sandbox", "platform"} {
		for _, transport := range []string{"http-json", "http-proto", "grpc"} {
			for _, phase := range []string{"routing", "final-price"} {
				for _, change := range []string{"revoke", "cancel", "expire", "remap", "live"} {
					t.Run(kind+"/"+transport+"/"+phase+"/"+change, func(t *testing.T) {
						exp := time.Now().Unix() + 60
						if change == "expire" {
							exp = time.Now().Unix() + 2
						}
						api, token, claims, cfg := composeSignedGrant(t, kind, exp)
						r := &composeGrantResolver{claims: claims, kind: kind, change: change}
						a, err := auth.NewWithResolver(cfg, r, nil, nil)
						if err != nil {
							t.Fatal(err)
						}
						usage, pricer := &routeUsage{}, &composePricer{}
						catalog, notifier := &propertyCatalogWriterStub{}, &routeNotifier{}
						reached, release := make(chan struct{}), make(chan struct{})
						var releaseOnce, reachedOnce sync.Once
						defer releaseOnce.Do(func() { close(release) })
						var blocker *lease
						var routes, classifierMode atomic.Int32
						classifierMode.Store(-1)
						var firstDeadline time.Time
						var firstBudget *leaseBudget
						s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) {
							attachRealAuth(s, a)
							s.grpcKnown = func(md metadata.MD) bool {
								if mode := classifierMode.Load(); mode >= 0 {
									return mode == 1
								}
								return a.KnownGRPCCredentials(md)
							}
							s.usage, s.pricer, s.propertyCatalog, s.traceNotifier = usage, pricer, catalog, notifier
							s.work.onBlock = func() { reachedOnce.Do(func() { close(reached) }) }
							s.router = restartRouter(func(ctx context.Context, key string, traces ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
								n := routes.Add(1)
								deadline, ok := ctx.Deadline()
								if !ok || leaseBudgetFrom(ctx) == nil {
									return auth.RouteOutcome{}, errors.New("missing original request budget")
								}
								if n == 1 {
									firstDeadline, firstBudget = deadline, leaseBudgetFrom(ctx)
								} else if !deadline.Equal(firstDeadline) || leaseBudgetFrom(ctx) != firstBudget {
									return auth.RouteOutcome{}, errors.New("grant restart reset original budget")
								}
								var out auth.RouteOutcome
								if result.Principal == auth.PrincipalSandbox {
									id, _ := result.GetProject(routeHiddenName)
									out.Hidden = id
									for i := 0; i < traces.ResourceSpans().Len(); i++ {
										attrs := traces.ResourceSpans().At(i).Resource().Attributes()
										attrs.PutStr("project_name", routeHiddenName)
										attrs.PutStr("fi.simulation.origin", "simulation")
									}
								}
								if n == 1 {
									if change == "remap" {
										traces.ResourceSpans().At(0).Resource().Attributes().PutStr("stale-attempt", "must-not-survive")
									}
									if phase == "final-price" {
										traces.ResourceSpans().At(0).Resource().Attributes().PutStr("unpriced_test_shape", strings.Repeat("x", 2048))
										var err error
										blocker, err = s.work.acquire(ctx, s.work.size-s.work.inUse.Load())
										if err != nil {
											return out, err
										}
									} else {
										reachedOnce.Do(func() { close(reached) })
										select {
										case <-release:
										case <-ctx.Done():
											return out, ctx.Err()
										}
									}
								}
								return out, nil
							})
						})
						ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
						defer cancel()
						done := make(chan composeTransportOutcome, 1)
						request := routeRequest("customer-prod")
						go func() { done <- composeSend(t, s, conn, ctx, transport, api, token, request, nil) }()
						select {
						case <-reached:
						case <-ctx.Done():
							t.Fatal("grant never reached held boundary")
						}
						if blocker != nil {
							defer blocker.release()
						}
						if change == "expire" {
							timer := time.NewTimer(time.Until(time.Unix(claims.Exp, 0)))
							defer timer.Stop()
							select {
							case <-timer.C:
							case <-ctx.Done():
								t.Fatal("original request expired before grant")
							}
						} else if change != "live" {
							r.state.Store(1)
							a.InvalidateKey(auth.CacheKey(api, token))
						}
						releaseOnce.Do(func() { close(release) })
						if blocker != nil {
							blocker.release()
						}
						var got composeTransportOutcome
						select {
						case got = <-done:
						case <-ctx.Done():
							t.Fatal("grant did not settle under original budget")
						}
						want := codes.Unauthenticated
						if change == "live" || change == "remap" {
							want = codes.OK
						}
						if got.code != want {
							t.Fatalf("grant result=%s want=%s", got.code, want)
						}
						if want != codes.OK {
							if transport != "grpc" && got.httpStatus != 401 {
								t.Fatal("revoked/expired grant not HTTP unauthorized")
							}
							composeNoAdmission(t, s, usage, pricer)
						} else {
							if got.response.PartialSuccess().RejectedSpans() != 0 || len(usage.snapshot()) != 1 || usage.snapshot()[0].org != routeOrg || usage.snapshot()[0].spans != 2 || pricer.calls.Load() != 2 {
								t.Fatal("valid same-tenant grant not billed/converted exactly once")
							}
							if change == "remap" && (routes.Load() != 2 || r.calls.Load() != 2) {
								t.Fatal("remap did not restart once from genuine credentials")
							}
							wantProject := routeProdID
							if kind == "sandbox" {
								wantProject = routeHiddenID
							}
							if change == "remap" {
								wantProject = calProject
							}
							s.pendMu.Lock()
							if len(s.pend) != 2 {
								t.Error("grant rows missing or duplicated")
							}
							for _, row := range s.pend {
								attrs := row["resource_attrs"].(map[string]any)
								if row["project_id"] != wantProject || row["org_id"] != routeOrg {
									t.Error("stale tenant/project reached storage")
								}
								if change == "remap" && (attrs["stale-attempt"] != nil || attrs["unpriced_test_shape"] != nil) {
									t.Error("old wire mutation survived restart")
								}
								if kind == "sandbox" && attrs["fi.simulation.origin"] != "simulation" {
									t.Error("sandbox origin lost")
								}
								if kind == "platform" && attrs["fi.simulation.origin"] != nil {
									t.Error("platform acquired sandbox origin")
								}
							}
							if kind == "sandbox" && (len(s.pendProperty) != 0 || len(s.pendRoots) != 0) {
								t.Error("hidden grant rows entered catalog/notifier queues")
							}
							s.pendMu.Unlock()
						}
						if routes.Load() > 2 {
							t.Fatal("unbounded grant retry")
						}
						s.drainNow(ctx)
						if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
							t.Fatal("grant left a high-water lease")
						}
						ch.mu.Lock()
						writes := len(ch.tables["spans"])
						ch.mu.Unlock()
						if (want == codes.OK) != (writes == 1) {
							t.Fatalf("storage writes=%d for %s", writes, want)
						}
						if kind == "sandbox" && (catalog.calls != 0 || notifier.calls != 0) {
							t.Fatal("hidden grant reached catalog or notifier")
						}
						if change == "live" {
							// A cached public jti is advisory only. Both classifier results
							// still traverse real token verification, never a bypass.
							bad := token + "x"
							md := metadata.Pairs("x-api-key", api, "x-secret-key", bad)
							if a.KnownGRPCCredentials(md) {
								t.Fatal("malformed same-jti token classified known")
							}
							if result, err := a.Authenticate(ctx, api, bad); result != nil || !errors.Is(err, auth.ErrUnauthenticated) {
								t.Fatal("malformed token bypassed Authenticate")
							}
							for _, known := range []bool{false, true} {
								if known {
									classifierMode.Store(1)
								} else {
									classifierMode.Store(0)
								}
								out := composeSend(t, s, conn, ctx, "grpc", api, bad, request, nil)
								if out.code != codes.Unauthenticated {
									t.Fatal("advisory classifier bypassed actual authentication")
								}
							}
							if len(usage.snapshot()) != 1 || pricer.calls.Load() != 2 {
								t.Fatal("malformed token caused conversion or billing")
							}
						}
					})
				}
			}
		}
	}
}
