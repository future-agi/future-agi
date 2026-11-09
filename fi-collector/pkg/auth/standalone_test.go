package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"gopkg.in/yaml.v3"
)

// The standalone rig must supply real authentication, not bypass the same
// PostgreSQL-backed checks used by hosted collectors.
func TestStandaloneComposeAuth(t *testing.T) {
	data, err := os.ReadFile("../../docker-compose.standalone.yml")
	if err != nil {
		t.Fatal(err)
	}
	type service struct {
		Environment map[string]string `yaml:"environment"`
		Ports       []string          `yaml:"ports"`
		Volumes     []string          `yaml:"volumes"`
		DependsOn   map[string]struct {
			Condition string `yaml:"condition"`
		} `yaml:"depends_on"`
		Healthcheck struct {
			Test []string `yaml:"test"`
		} `yaml:"healthcheck"`
	}
	var compose struct {
		Services map[string]service `yaml:"services"`
	}
	if err := yaml.Unmarshal(data, &compose); err != nil {
		t.Fatal(err)
	}
	collector := compose.Services["fi-collector"]
	cfg := Config{PGWrite: collector.Environment["FI_PG_WRITE"], PGRead: collector.Environment["FI_PG_READ"]}
	if !cfg.IsEnabled() {
		t.Fatal("standalone compose must configure PostgreSQL authentication")
	}
	cfg.defaults()
	for _, endpoint := range []string{cfg.PGWrite, cfg.PGRead} {
		u, err := url.Parse(endpoint)
		if err != nil || u.Scheme != "postgres" || u.User == nil {
			t.Fatal("standalone auth must point to its seeded PostgreSQL service")
		}
		pg, ok := compose.Services[u.Hostname()]
		if !ok {
			t.Fatal("standalone PostgreSQL endpoint has no compose service")
		}
		password, _ := u.User.Password()
		if u.User.Username() != pg.Environment["POSTGRES_USER"] || password == "" ||
			password != pg.Environment["POSTGRES_PASSWORD"] ||
			strings.TrimPrefix(u.Path, "/") != pg.Environment["POSTGRES_DB"] {
			t.Fatal("collector endpoint does not match the standalone database")
		}
		if collector.DependsOn[u.Hostname()].Condition != "service_healthy" {
			t.Fatal("collector must wait for seeded PostgreSQL")
		}
		if !slices.Equal(pg.Healthcheck.Test, []string{"CMD", "pg_isready", "-h", "127.0.0.1", "-U", pg.Environment["POSTGRES_USER"], "-d", pg.Environment["POSTGRES_DB"]}) {
			t.Fatal("PostgreSQL readiness must use TCP, not the temporary initdb socket")
		}
		if len(pg.Ports) != 0 {
			t.Fatal("development PostgreSQL must not publish a host port")
		}
		seedMounted := false
		for _, mount := range pg.Volumes {
			parts := strings.Split(mount, ":")
			if len(parts) == 3 && parts[1] == "/docker-entrypoint-initdb.d/001-standalone.sql" && parts[2] == "ro" {
				if filepath.Clean(parts[0]) != "test/standalone/seed.sql" {
					t.Fatal("standalone compose must mount the tested seed")
				}
				if _, err := os.Stat(filepath.Join("../..", parts[0])); err != nil {
					t.Fatal(err)
				}
				seedMounted = true
			}
		}
		if !seedMounted {
			t.Fatal("standalone PostgreSQL must initialize the auth schema and key")
		}
	}
	for name, svc := range compose.Services {
		for _, port := range svc.Ports {
			if !strings.HasPrefix(port, "127.0.0.1:") {
				t.Fatalf("service %s exposes the development rig beyond loopback", name)
			}
		}
	}
	if collector.Environment["USAGE_EVENTS_ENABLED"] != "false" {
		t.Fatal("standalone has no consumer for billing events")
	}
}

// Uses the existing isolated PostgreSQL harness, never an operator database.
// Loading the shipped seed (not routingSchema) catches drift in the SQL that
// authenticates keys and auto-creates projects, including its conflict index.
func TestStandaloneSeedAuthenticationAndStamping(t *testing.T) {
	seed, err := os.ReadFile("../../test/standalone/seed.sql")
	if err != nil {
		t.Fatal(err)
	}
	pool := routingDatabase(t)
	ctx := context.Background()
	if _, err := pool.Exec(ctx, "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, string(seed)); err != nil {
		t.Fatal(err)
	}
	resolver := routingResolver(pool, columnsMissing)
	a, err := NewWithResolver(Config{}, resolver, nil, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	const apiKey, secretKey = "standalone-api-key", "standalone-secret-key"
	res, err := a.Authenticate(ctx, apiKey, secretKey)
	if err != nil {
		t.Fatal(err)
	}
	for name, id := range map[string]string{"org": res.OrgID, "workspace": res.WorkspaceID, "user": res.UserID} {
		if parsed, err := uuid.Parse(id); err != nil || parsed == uuid.Nil {
			t.Fatalf("seed %s must be a nonzero UUID", name)
		}
	}
	workspace, err := resolver.resolveDefaultWorkspace(ctx, res.OrgID)
	if err != nil || workspace != res.WorkspaceID {
		t.Fatalf("default workspace = %q, %v", workspace, err)
	}
	for _, pair := range [][2]string{{"", ""}, {apiKey, "wrong-secret"}, {"wrong-key", secretKey}} {
		if _, err := a.Authenticate(ctx, pair[0], pair[1]); !errors.Is(err, ErrUnauthenticated) {
			t.Fatalf("invalid standalone credentials must be refused: %v", err)
		}
	}

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("standalone-smoke")
	if _, err := StampResourceAttrs(ctx, a, CacheKey(apiKey, secretKey), traces, res); err == nil {
		t.Fatal("missing project_name must still fail closed")
	}
	rs.Resource().Attributes().PutStr("project_name", "standalone-smoke")
	if dropped, err := StampResourceAttrs(ctx, a, CacheKey(apiKey, secretKey), traces, res); err != nil || dropped != 0 {
		t.Fatalf("stamp seeded standalone span: dropped=%d, err=%v", dropped, err)
	}
	projectID := getStrAttr(rs.Resource().Attributes(), "fi.project_id")
	if parsed, err := uuid.Parse(projectID); err != nil || parsed == uuid.Nil {
		t.Fatal("auto-created project must be a nonzero UUID")
	}
	if getStrAttr(rs.Resource().Attributes(), "fi.org_id") != res.OrgID {
		t.Fatal("stamp must use the authenticated organization")
	}
	// Exercise ON CONFLICT, not just the in-process project cache.
	again, err := resolver.GetOrCreateProject(ctx, res.OrgID, res.WorkspaceID, "standalone-smoke", ProjectTypeObserve)
	if err != nil || again != projectID {
		t.Fatalf("repeated project creation = %q, %v; want %q", again, err, projectID)
	}
	resolved, err := resolver.ResolveProjectsWithTypes(ctx, res.OrgID, res.WorkspaceID, []string{"standalone-smoke"})
	if err != nil || resolved["standalone-smoke"].ID != projectID || resolved["standalone-smoke"].TraceType != ProjectTypeObserve {
		t.Fatalf("resolve persisted standalone project: %v, %v", resolved, err)
	}
	var plan, mode string
	if err := pool.QueryRow(ctx, `SELECT plan, tracing_billing_mode FROM usage_organizationsubscription
		WHERE organization_id = $1 AND deleted = false`, res.OrgID).Scan(&plan, &mode); err != nil || plan == "" || mode != "storage" {
		t.Fatalf("standalone subscription: plan=%q, mode=%q, err=%v", plan, mode, err)
	}
}
