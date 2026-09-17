package auth

import (
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestEndpointFromEnvPreservesArbitraryCredentials(t *testing.T) {
	values := map[string]string{
		"FI_PG_WRITE_HOST": "postgres", "FI_PG_WRITE_PORT": "5432",
		"FI_PG_WRITE_DATABASE": "db%:'\\\" name", "FI_PG_WRITE_USER": "user%:'\\\" name",
		"FI_PG_WRITE_PASSWORD": "space and @:/?#ø\n\t$secret",
	}
	got := EndpointFromEnv(func(key string) string { return values[key] }, "FI_PG_WRITE")
	parsedConfig, err := pgxpool.ParseConfig(got)
	if err != nil {
		t.Fatalf("generated endpoint is not parseable: %v", err)
	}
	parsed := parsedConfig.ConnConfig
	if parsed.User != values["FI_PG_WRITE_USER"] || parsed.Password != values["FI_PG_WRITE_PASSWORD"] || parsed.Database != values["FI_PG_WRITE_DATABASE"] {
		t.Fatalf("credentials were not preserved: %#v", parsed)
	}
}

func TestEndpointFromEnvRequiresCompleteEndpoint(t *testing.T) {
	if got := EndpointFromEnv(func(key string) string {
		if key == "FI_PG_WRITE_HOST" {
			return "postgres"
		}
		return ""
	}, "FI_PG_WRITE"); got != "" {
		t.Fatalf("incomplete endpoint should fall back to legacy URI, got %q", got)
	}
}
