package observedcatalog

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

var deleteTestScope = Scope{
	OrganizationID: "00000000-0000-4000-8000-000000000010",
	WorkspaceID:    "00000000-0000-4000-8000-000000000020",
	ProjectID:      "00000000-0000-4000-8000-000000000030",
}

type deleteRequest struct {
	statement string
	query     map[string][]string
}

func deleteServer(t *testing.T, respond func(n int, w http.ResponseWriter)) (*ClickHouseSink, func() []deleteRequest) {
	t.Helper()
	var mu sync.Mutex
	var seen []deleteRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		seen = append(seen, deleteRequest{statement: string(body), query: r.URL.Query()})
		n := len(seen)
		mu.Unlock()
		respond(n, w)
	}))
	t.Cleanup(server.Close)
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Username: "writer", Password: "secret"})
	if err != nil {
		t.Fatal(err)
	}
	return sink, func() []deleteRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]deleteRequest(nil), seen...)
	}
}

func TestDeleteProjectRemovesBothTablesForOneProjectSynchronously(t *testing.T) {
	sink, requests := deleteServer(t, func(int, http.ResponseWriter) {})
	if err := sink.DeleteProject(context.Background(), deleteTestScope); err != nil {
		t.Fatal(err)
	}
	seen := requests()
	if len(seen) != 2 {
		t.Fatalf("%d statements, want one per table", len(seen))
	}
	for i, table := range []string{KeyTable, ValueTable} {
		r := seen[i]
		// All three clauses must hold for a row to go: any other connective
		// would delete other projects' rows too.
		want := "DELETE FROM " + table + " WHERE organization_id = {organization:String} AND workspace_id = {workspace:String} AND project_id = {project:String}"
		if got := strings.Join(strings.Fields(r.statement), " "); got != want {
			t.Fatalf("statement %d is not the scoped delete of %s:\n%s\nwant:\n%s", i, table, got, want)
		}
		// Identifiers reach the server only as bound parameters.
		for _, id := range []string{deleteTestScope.OrganizationID, deleteTestScope.WorkspaceID, deleteTestScope.ProjectID} {
			if strings.Contains(r.statement, id) {
				t.Fatalf("scope value built into the statement: %q", r.statement)
			}
		}
		if r.query["param_organization"][0] != deleteTestScope.OrganizationID || r.query["param_workspace"][0] != deleteTestScope.WorkspaceID || r.query["param_project"][0] != deleteTestScope.ProjectID {
			t.Fatalf("scope parameters wrong: %v", r.query)
		}
		if r.query["lightweight_deletes_sync"][0] != "2" || r.query["database"][0] != "catalog" || r.query["wait_end_of_query"][0] != "1" {
			t.Fatalf("delete is not synchronous on every replica: %v", r.query)
		}
	}
}

func TestDeleteProjectIsNotCutShortByTheInsertTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		time.Sleep(400 * time.Millisecond)
	}))
	t.Cleanup(server.Close)
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Timeout: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := sink.DeleteProject(context.Background(), deleteTestScope); err != nil {
		t.Fatalf("a delete slower than the insert timeout failed: %v", err)
	}
	// The caller's own deadline still ends it.
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if err := sink.DeleteProject(ctx, deleteTestScope); err == nil {
		t.Fatal("a delete outlived its caller's deadline")
	}
}

func TestDeleteProjectFailsOnEitherStatement(t *testing.T) {
	for _, failing := range []int{1, 2} {
		for _, mode := range []string{"status", "late-exception", "body"} {
			sink, requests := deleteServer(t, func(n int, w http.ResponseWriter) {
				if n != failing {
					return
				}
				switch mode {
				case "status":
					w.WriteHeader(http.StatusInternalServerError)
				case "late-exception":
					w.Header().Set("X-ClickHouse-Exception-Code", "497")
				case "body":
					w.Write([]byte("private-server-details"))
				}
			})
			err := sink.DeleteProject(context.Background(), deleteTestScope)
			if err == nil || strings.Contains(err.Error(), "private-server-details") {
				t.Fatalf("statement %d (%s) not reported, or leaked: %v", failing, mode, err)
			}
			if failing == 1 && len(requests()) != 1 {
				t.Fatal("values table deleted after the keys delete failed")
			}
		}
	}
}

func TestDeleteProjectRefusesAnUnscopedOrMalformedScope(t *testing.T) {
	for name, mutate := range map[string]func(*Scope){
		"organization": func(s *Scope) { s.OrganizationID = "" },
		"workspace":    func(s *Scope) { s.WorkspaceID = "" },
		"project":      func(s *Scope) { s.ProjectID = "" },
		"not-uuid":     func(s *Scope) { s.ProjectID = "x' OR 1=1 --" },
		"nil-uuid":     func(s *Scope) { s.ProjectID = "00000000-0000-0000-0000-000000000000" },
	} {
		t.Run(name, func(t *testing.T) {
			sink, requests := deleteServer(t, func(int, http.ResponseWriter) {})
			scope := deleteTestScope
			mutate(&scope)
			if err := sink.DeleteProject(context.Background(), scope); err == nil || len(requests()) != 0 {
				t.Fatal("delete sent without a complete scope", err)
			}
		})
	}
}
