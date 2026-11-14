package server

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type restartResolver struct {
	state       atomic.Int32
	validations atomic.Int32
	result      func(int32) *auth.ResolveResult
}

func (r *restartResolver) ValidateKey(context.Context, string, string) (*auth.ResolveResult, error) {
	r.validations.Add(1)
	return r.result(r.state.Load()), nil
}
func (*restartResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, errors.New("unexpected project lookup")
}
func (*restartResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("unexpected project create")
}

type restartRouter func(context.Context, string, ptrace.Traces, *auth.ResolveResult) (auth.RouteOutcome, error)

func (f restartRouter) RouteSimulation(ctx context.Context, key string, traces ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
	return f(ctx, key, traces, result)
}

type restartMeter struct {
	mu   sync.Mutex
	orgs []string
	deny string
}

func (m *restartMeter) CheckUsage(_ context.Context, org, _ string, _ int64) auth.CheckResult {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.orgs = append(m.orgs, org)
	return auth.CheckResult{Allowed: org != m.deny, Reason: "test quota"}
}

func (m *restartMeter) scopes() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.orgs...)
}

func restartResult(org, ws string, sandbox bool) *auth.ResolveResult {
	kind := "system"
	if sandbox {
		kind = auth.KeyTypeIngest
	}
	return &auth.ResolveResult{OrgID: org, WorkspaceID: ws, KeyType: kind,
		Projects: map[string]string{"customer-prod": routeProdID, "header-project": routeProdID, routeHiddenName: routeHiddenID}}
}

// Exercise production HTTP middleware and gRPC stream ingestion. The router
// boundary forces publication at an exact point, after decoding and optionally
// mutating the old attempt. Persistence, key authentication and stamping use the
// real Authenticator. No client-side retry, sleeps or cache timing assumptions.
func TestProjectAuthRestartTransportParity(t *testing.T) {
	for _, transport := range []string{"http-proto", "http-json", "http-gzip", "http-basic", "grpc"} {
		for _, change := range []string{"replace", "org", "workspace", "to-sandbox", "from-sandbox", "partial-mutation", "all-dropped", "revoked", "invalid-scope", "churn", "quota"} {
			t.Run(transport+"/"+change, func(t *testing.T) {
				wantOrg, wantWS := routeOrg, routeWorkspace
				if change == "org" || change == "quota" {
					wantOrg = calOrg
				}
				if change == "workspace" {
					wantWS = calWS
				}
				r := &restartResolver{}
				r.result = func(state int32) *auth.ResolveResult {
					if state == 0 {
						return restartResult(routeOrg, routeWorkspace, change == "from-sandbox")
					}
					if change == "revoked" {
						return nil
					}
					if change == "invalid-scope" {
						return restartResult(wantOrg, "", false)
					}
					result := restartResult(wantOrg, wantWS, change == "to-sandbox")
					if change == "org" || change == "workspace" {
						result.Projects["customer-prod"] = calProject
						result.Projects["header-project"] = calProject
					}
					return result
				}
				a, err := auth.NewWithResolver(auth.Config{CacheTTL: time.Hour, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
				if err != nil {
					t.Fatal(err)
				}
				usage, meter := &routeUsage{}, &restartMeter{}
				if change == "quota" {
					meter.deny = wantOrg
				}
				var calls atomic.Int32
				var firstDeadline time.Time
				var budget *leaseBudget
				router := restartRouter(func(ctx context.Context, key string, traces ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
					n := calls.Add(1)
					deadline, ok := ctx.Deadline()
					if !ok {
						return auth.RouteOutcome{}, errors.New("request has no overall deadline")
					}
					if n == 1 {
						firstDeadline, budget = deadline, leaseBudgetFrom(ctx)
						if budget == nil {
							return auth.RouteOutcome{}, errors.New("request has no lease budget")
						}
					} else if !deadline.Equal(firstDeadline) || leaseBudgetFrom(ctx) != budget {
						return auth.RouteOutcome{}, errors.New("restart reset request deadline or lease budget")
					}
					var out auth.RouteOutcome
					if result.KeyType == auth.KeyTypeIngest {
						out, _ = (&routeFake{}).RouteSimulation(ctx, key, traces, result)
					}
					if n == 1 && change == "all-dropped" {
						out.Dropped = map[string]int{"test-drop": traces.SpanCount()}
						traces.ResourceSpans().RemoveIf(func(ptrace.ResourceSpans) bool { return true })
					}
					if n == 1 && change == "partial-mutation" {
						index := 0
						traces.ResourceSpans().RemoveIf(func(ptrace.ResourceSpans) bool { index++; return index == 1 })
						attrs := traces.ResourceSpans().At(0).Resource().Attributes()
						attrs.PutStr("project_name", routeHiddenName)
						attrs.PutStr("fi.org_id", "stale-org")
						attrs.PutStr("fi.project_id", "stale-project")
						attrs.PutStr("stale-attempt", "must-not-survive")
					}
					if n == 1 || change == "churn" {
						r.state.Add(1)
						a.InvalidateKey(key)
						if change == "replace" || change == "churn" {
							if _, err := a.Authenticate(ctx, "k", "s"); err != nil {
								return out, err
							}
						}
					}
					return out, nil
				})
				s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) {
					attachRealAuth(s, a)
					s.router, s.usage, s.metering = router, usage, meter
					s.propertyCatalog = nopCatalog{}
				})
				request := routeRequest("customer-prod", "")
				request.Traces().ResourceSpans().At(1).Resource().Attributes().Remove("project_name")
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				code, resp := sendRestartRequest(t, s, conn, ctx, transport, request)
				want := codes.OK
				switch change {
				case "revoked", "invalid-scope":
					want = codes.Unauthenticated
				case "churn":
					want = codes.Unavailable
				case "quota":
					want = codes.ResourceExhausted
				}
				if code != want {
					t.Fatalf("request after auth change: got %s want %s", code, want)
				}
				if r.validations.Load() < 2 {
					t.Fatal("request never reauthenticated")
				}
				var refusals int64
				for _, n := range s.IngestStats().Rejected {
					refusals += n
				}
				wantRefusals := int64(0)
				if want != codes.OK {
					wantRefusals = 1
				}
				if refusals != wantRefusals {
					t.Fatalf("refusal count=%d want %d", refusals, wantRefusals)
				}
				if calls.Load() > 2 {
					t.Fatalf("unbounded restart: %d routing attempts", calls.Load())
				}
				s.pendMu.Lock()
				pending, properties, leased := s.pend, s.pendProperty, s.pendLeased
				s.pendMu.Unlock()
				if want != codes.OK {
					if len(usage.snapshot()) != 0 || len(pending) != 0 {
						t.Fatal("refused request enqueued or billed")
					}
					if change == "churn" && calls.Load() != 2 {
						t.Fatal("did not exercise the retry bound")
					}
				} else {
					if resp.PartialSuccess().RejectedSpans() != 0 {
						t.Fatal("old attempt's dropped spans survived")
					}
					if calls.Load() != 2 {
						t.Fatalf("routing attempts=%d, want 2", calls.Load())
					}
					if got := meter.scopes(); !reflect.DeepEqual(got, []string{routeOrg, wantOrg}) {
						t.Fatalf("quota scopes=%v", got)
					}
					if got := usage.snapshot(); len(got) != 1 || got[0].org != wantOrg || got[0].spans != 4 {
						t.Fatalf("usage=%+v", got)
					}
					wantProject := routeProdID
					if change == "org" || change == "workspace" {
						wantProject = calProject
					}
					if change == "to-sandbox" {
						wantProject = routeHiddenID
					}
					expected := ptrace.NewTraces()
					request.Traces().CopyTo(expected)
					for i := 0; i < expected.ResourceSpans().Len(); i++ {
						attrs := expected.ResourceSpans().At(i).Resource().Attributes()
						if i == 1 {
							attrs.PutStr("project_name", "header-project")
						}
						if change == "to-sandbox" {
							attrs.PutStr("project_name", routeHiddenName)
							attrs.PutStr("fi.simulation.origin", "simulation")
						}
						attrs.PutStr("fi.org_id", wantOrg)
						attrs.PutStr("fi.project_id", wantProject)
					}
					if got, want := usage.snapshot()[0].payloadBytes, int64((&ptrace.ProtoMarshaler{}).TracesSize(expected)); got != want {
						t.Fatalf("billed bytes=%d want fresh accepted payload %d", got, want)
					}
					if len(pending) != 4 {
						t.Fatalf("pending rows=%d, want 4 original spans once", len(pending))
					}
					seen := map[any]bool{}
					for _, row := range pending {
						if row["project_id"] != wantProject {
							t.Fatalf("old project survived: %v", row["project_id"])
						}
						if seen[row["id"]] {
							t.Fatal("duplicate span enqueued")
						}
						seen[row["id"]] = true
						attrs := row["resource_attrs"].(map[string]any)
						if attrs["fi.org_id"] != wantOrg || attrs["stale-attempt"] != nil {
							t.Fatalf("stale attributes=%v", attrs)
						}
						if change == "from-sandbox" && attrs["fi.simulation.origin"] != nil {
							t.Fatal("old sandbox routing leaked into system-key retry")
						}
					}
					wantSidecars := 4
					if change == "to-sandbox" {
						wantSidecars = 0
					}
					if len(properties) != wantSidecars {
						t.Fatalf("sidecars=%d want %d", len(properties), wantSidecars)
					}
					for _, sidecar := range properties {
						if sidecar.OrganizationID != wantOrg || sidecar.WorkspaceID != wantWS || sidecar.ScopeError != "" {
							t.Fatalf("stale sidecar scope: %+v", sidecar)
						}
					}
				}
				if s.wire.inUse.Load() != 0 || s.work.inUse.Load() != leased {
					t.Fatal("handler leaked a wire/work lease")
				}
				s.drainNow(ctx)
				if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
					t.Fatal("drain leaked a lease")
				}
				ch.mu.Lock()
				bodies := append([][]byte(nil), ch.tables["spans"]...)
				ch.mu.Unlock()
				stored := map[string]bool{}
				for _, body := range bodies {
					dec := json.NewDecoder(bytes.NewReader(body))
					for dec.More() {
						var row struct {
							ID string `json:"id"`
						}
						if err := dec.Decode(&row); err != nil {
							t.Fatal(err)
						}
						if stored[row.ID] {
							t.Fatal("duplicate span reached storage")
						}
						stored[row.ID] = true
					}
				}
				if len(stored) != len(pending) {
					t.Fatalf("stored=%d, accepted=%d", len(stored), len(pending))
				}
				for _, row := range pending {
					if !stored[row["id"].(string)] {
						t.Fatal("accepted span missing from storage")
					}
				}
			})
		}
	}
}

func sendRestartRequest(t *testing.T, s *Server, conn *grpc.ClientConn, ctx context.Context, transport string, req ptraceotlp.ExportRequest) (codes.Code, ptraceotlp.ExportResponse) {
	t.Helper()
	resp := ptraceotlp.NewExportResponse()
	body, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	if transport == "grpc" {
		ctx = metadata.AppendToOutgoingContext(withKeys(ctx), "x-project-name", "header-project")
		var out []byte
		err := conn.Invoke(ctx, exportMethod, &body, &out, grpc.ForceCodec(rawProtoCodec{}))
		if err == nil {
			if err := resp.UnmarshalProto(out); err != nil {
				t.Fatal(err)
			}
		}
		return status.Code(err), resp
	}
	ct := "application/x-protobuf"
	if transport == "http-json" {
		ct = "application/json"
		body, err = req.MarshalJSON()
		if err != nil {
			t.Fatal(err)
		}
	}
	if transport == "http-gzip" {
		var buf bytes.Buffer
		gz := gzip.NewWriter(&buf)
		if _, err := gz.Write(body); err != nil {
			t.Fatal(err)
		}
		if err := gz.Close(); err != nil {
			t.Fatal(err)
		}
		body = buf.Bytes()
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body)).WithContext(ctx)
	r.Header.Set("Content-Type", ct)
	r.Header.Set("X-Project-Name", "header-project")
	if transport == "http-basic" {
		r.SetBasicAuth("k", "s")
	} else {
		r.Header.Set("X-Api-Key", "k")
		r.Header.Set("X-Secret-Key", "s")
	}
	if transport == "http-gzip" {
		r.Header.Set("Content-Encoding", "gzip")
	}
	rec := httptest.NewRecorder()
	s.countAuthRefusals(s.auth.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces))).ServeHTTP(rec, r)
	switch rec.Code {
	case 200:
		if ct == "application/json" {
			err = resp.UnmarshalJSON(rec.Body.Bytes())
		} else {
			err = resp.UnmarshalProto(rec.Body.Bytes())
		}
		if err != nil {
			t.Fatal(err)
		}
		return codes.OK, resp
	case 401:
		return codes.Unauthenticated, resp
	case 429:
		if rec.Header().Get("Retry-After") != "" {
			t.Fatal("quota has short retry hint")
		}
		return codes.ResourceExhausted, resp
	case 503:
		if rec.Header().Get("Retry-After") != "1" {
			t.Fatal("project failure lost retry hint")
		}
		return codes.Unavailable, resp
	default:
		t.Fatalf("HTTP %d: %s", rec.Code, rec.Body.String())
		return codes.Unknown, resp
	}
}
