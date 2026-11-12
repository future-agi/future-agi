package auth

import (
	"context"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgproto3"
	"github.com/jackc/pgx/v5/pgxpool"
)

// These focused tests run the real ValidateKey and write-eligibility code
// through pgx, with an in-memory wire fixture: no listener, database or service.
// Candidate rows model the landing query's ordered result; the database-backed
// test below separately checks that ordering and filtering against PostgreSQL.
func TestPersonalWorkspaceLanding(t *testing.T) {
	for _, tc := range []struct {
		name, pin, role, want string
		level                 int
	}{
		{"unpinned_research_only_member", "", "workspace_member", "research", 3},
		{"explicit_research_pin", "research", "workspace_member", "research", 3},
		{"inaccessible_pin_never_falls_back", "default", "workspace_member", "", 3},
		{"viewer_has_no_writable_workspace", "", "workspace_viewer", "", 1},
		{"level_overrides_stale_admin_role", "", "workspace_admin", "", 1},
		{"legacy_member_without_level", "", "workspace_member", "research", -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			cfg, err := pgxpool.ParseConfig("postgres://fixture@localhost/fixture?sslmode=disable")
			if err != nil {
				t.Fatal(err)
			}
			cfg.MaxConns = 1
			cfg.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol
			cfg.ConnConfig.DialFunc = func(context.Context, string, string) (net.Conn, error) {
				client, server := net.Pipe()
				go servePersonalWorkspaceFixture(t, server, tc.pin, tc.role, tc.level)
				return client, nil
			}
			pool, err := pgxpool.NewWithConfig(ctx, cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer pool.Close()
			peppers, err := ResolveSecretPeppers("", nil, "test", "synthetic-workspace-test-only")
			if err != nil {
				t.Fatal(err)
			}
			r := &PGResolver{write: pool, read: pool, peppers: peppers}
			result, err := r.ValidateKey(ctx, "synthetic-api", "synthetic-secret")
			if err != nil {
				t.Fatal(err)
			}
			if tc.want == "" {
				if result != nil {
					t.Fatalf("expected refusal, got workspace %s", result.WorkspaceID)
				}
				return
			}
			if result == nil {
				t.Fatalf("workspace-isolated member rejected; want %s", tc.want)
			}
			if result.WorkspaceID != tc.want || result.OrgID != "org" || result.UserID != "owner" {
				t.Fatal("resolved outside the key owner's organization or workspace")
			}
		})
	}
}

func servePersonalWorkspaceFixture(t *testing.T, conn net.Conn, pin, role string, level int) {
	t.Helper()
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	backend := pgproto3.NewBackend(conn, conn)
	if _, err := backend.ReceiveStartupMessage(); err != nil {
		return
	}
	backend.Send(&pgproto3.AuthenticationOk{})
	backend.Send(&pgproto3.ParameterStatus{Name: "server_version", Value: "16.0"})
	backend.Send(&pgproto3.ParameterStatus{Name: "client_encoding", Value: "UTF8"})
	backend.Send(&pgproto3.ParameterStatus{Name: "standard_conforming_strings", Value: "on"})
	backend.Send(&pgproto3.ReadyForQuery{TxStatus: 'I'})
	if backend.Flush() != nil {
		return
	}
	var wsLevel []byte
	if level >= 0 {
		wsLevel = []byte(fmt.Sprint(level))
	}
	for {
		message, err := backend.Receive()
		if err != nil {
			return
		}
		query, ok := message.(*pgproto3.Query)
		if !ok {
			return
		}
		var values [][][]byte
		var types []uint32
		switch {
		case strings.Contains(query.String, "FROM accounts_orgapikey k"):
			types = []uint32{25, 25, 25, 25, 25, 25}
			// Exact pin eligibility is still enforced before choosing a workspace.
			if pin != "default" {
				var ws []byte
				if pin != "" {
					ws = []byte(pin)
				}
				values = [][][]byte{{[]byte("org"), ws, []byte("owner"), []byte("user"), []byte("synthetic-secret"), []byte("")}}
			}
		case strings.Contains(query.String, "SELECT id FROM accounts_workspace"):
			types = []uint32{25}
			values = [][][]byte{{[]byte("default")}}
		case strings.Contains(query.String, "SELECT w.id, m.role, m.level, wm.role, wm.level"):
			types = []uint32{25, 25, 23, 25, 23}
			values = [][][]byte{
				{[]byte("default"), []byte("Member"), []byte("3"), nil, nil},
				{[]byte("research"), []byte("Member"), []byte("3"), []byte(role), wsLevel},
			}
		case strings.Contains(query.String, "SELECT m.role, m.level, wm.role, wm.level"):
			types = []uint32{25, 23, 25, 23}
			values = [][][]byte{{[]byte("Member"), []byte("3"), nil, nil}}
			if strings.Contains(query.String, "'research'") {
				values = [][][]byte{{[]byte("Member"), []byte("3"), []byte(role), wsLevel}}
			}
		default:
			t.Errorf("unexpected fixture query")
			return
		}
		fields := make([]pgproto3.FieldDescription, len(types))
		for i, oid := range types {
			fields[i] = pgproto3.FieldDescription{Name: []byte(fmt.Sprint(i)), DataTypeOID: oid, DataTypeSize: -1}
		}
		backend.Send(&pgproto3.RowDescription{Fields: fields})
		for _, row := range values {
			backend.Send(&pgproto3.DataRow{Values: row})
		}
		backend.Send(&pgproto3.CommandComplete{CommandTag: []byte(fmt.Sprintf("SELECT %d", len(values)))})
		backend.Send(&pgproto3.ReadyForQuery{TxStatus: 'I'})
		if backend.Flush() != nil {
			return
		}
	}
}
