package server

import (
	"context"
	"fmt"
	"net/http"
	"reflect"
	"sort"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
)

type composeBulkResolver struct {
	result         *auth.ResolveResult
	validations    atomic.Int32
	reads          int
	org, workspace string
	found          map[string]string
	created        []string
}

func (r *composeBulkResolver) ValidateKey(_ context.Context, api, secret string) (*auth.ResolveResult, error) {
	r.validations.Add(1)
	if api != "k" || secret != "s" {
		return nil, nil
	}
	return r.result, nil
}
func (r *composeBulkResolver) ResolveProjects(_ context.Context, org, workspace string, names []string) (map[string]string, error) {
	r.reads++
	r.org, r.workspace = org, workspace
	out := make(map[string]string)
	for _, name := range names {
		if id, ok := r.found[name]; ok {
			out[name] = id
		}
	}
	return out, nil
}
func (r *composeBulkResolver) GetOrCreateProject(_ context.Context, org, workspace, name, kind string) (string, error) {
	r.created = append(r.created, name)
	return expectedProject, nil
}

func TestComposeExpectedTargetBulkNamesTransportParity(t *testing.T) {
	for _, transport := range []string{"http-json", "http-proto", "grpc"} {
		for _, mode := range []string{"unknown", "matching", "ordinary"} {
			t.Run(transport+"/"+mode, func(t *testing.T) {
				names := make([]string, auth.MaxNewProjectsPerRequest+1)
				for i := range names {
					names[i] = fmt.Sprintf("name-%02d", i)
				}
				r := &composeBulkResolver{result: &auth.ResolveResult{OrgID: expectedOrg, WorkspaceID: expectedWorkspace, Principal: auth.PrincipalPersonal, KeyType: "user", Projects: map[string]string{}}, found: map[string]string{}}
				if mode == "matching" {
					for _, name := range names {
						r.found[name] = expectedProject
					}
				}
				a, err := auth.NewWithResolver(auth.Config{}, r, nil, nil)
				if err != nil {
					t.Fatal(err)
				}
				usage, pricer := &routeUsage{}, &composePricer{}
				var routes atomic.Int32
				s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) {
					attachRealAuth(s, a)
					s.usage, s.pricer, s.propertyCatalog = usage, pricer, nopCatalog{}
					s.router = restartRouter(func(context.Context, string, ptrace.Traces, *auth.ResolveResult) (auth.RouteOutcome, error) {
						routes.Add(1)
						return auth.RouteOutcome{}, nil
					})
				})
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				headers := targetHeaders()
				if mode == "ordinary" {
					headers = http.Header{}
				}
				request := ptraceotlp.NewExportRequestFromTraces(targetTraces(names...))
				out := composeSend(t, s, conn, ctx, transport, "k", "s", request, headers)
				want := codes.PermissionDenied
				if mode == "matching" {
					want = codes.OK
				}
				if mode == "ordinary" {
					want = codes.Unavailable
				}
				if out.code != want {
					t.Fatalf("bulk transport code=%s want %s", out.code, want)
				}
				if mode == "unknown" && transport != "grpc" && out.httpStatus != http.StatusForbidden {
					t.Fatal("expected-target denial is not HTTP 403")
				}
				if r.validations.Load() != 1 || routes.Load() != 1 || r.reads != 1 || r.org != expectedOrg || r.workspace != expectedWorkspace {
					t.Fatalf("unexpected auth restart or unscoped/missing read: validations=%d routes=%d reads=%d", r.validations.Load(), routes.Load(), r.reads)
				}
				if mode != "ordinary" && len(r.created) != 0 {
					t.Fatal("read-only expected-target request created projects")
				}
				if mode != "matching" {
					composeNoAdmission(t, s, usage, pricer)
					s.drainNow(ctx)
					ch.mu.Lock()
					writes := len(ch.tables)
					ch.mu.Unlock()
					if writes != 0 || out.response.PartialSuccess().RejectedSpans() != 0 {
						t.Fatal("denial wrote storage or acknowledged partial success")
					}
				}
				if mode == "unknown" {
					if len(r.result.Projects) != 0 {
						t.Fatal("unknown names entered project map")
					}
					return
				}
				if mode == "ordinary" {
					wantNames := append([]string(nil), names...)
					sort.Strings(wantNames)
					if !reflect.DeepEqual(r.created, wantNames[:auth.MaxNewProjectsPerRequest]) {
						t.Fatalf("bounded first create set=%v", r.created)
					}
					out = composeSend(t, s, conn, ctx, transport, "k", "s", request, headers)
					if out.code != codes.OK || len(r.created) != len(names) || r.validations.Load() != 1 {
						t.Fatal("ordinary retry did not make bounded progress")
					}
				}
				if out.response.PartialSuccess().RejectedSpans() != 0 || len(usage.snapshot()) != 1 || pricer.calls.Load() != int32(len(names)) {
					t.Fatal("positive control did not convert and bill once")
				}
				s.pendMu.Lock()
				if len(s.pend) != len(names) {
					t.Error("positive control missing rows")
				}
				for _, row := range s.pend {
					if row["project_id"] != expectedProject || row["org_id"] != expectedOrg {
						t.Error("positive control retained forged tenant")
					}
				}
				s.pendMu.Unlock()
				s.drainNow(ctx)
				if s.work.inUse.Load() != 0 || s.wire.inUse.Load() != 0 {
					t.Fatal("positive control leaked leases")
				}
				ch.mu.Lock()
				writes := len(ch.tables["spans"])
				ch.mu.Unlock()
				if writes == 0 {
					t.Fatal("matching transport control did not reach storage")
				}
			})
		}
	}
}
