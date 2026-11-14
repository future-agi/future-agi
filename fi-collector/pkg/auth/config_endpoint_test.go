package auth

import (
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

func endpointFields(prefix string) map[string]string {
	return map[string]string{
		prefix + "_HOST": "postgres.example.test", prefix + "_PORT": "5432",
		prefix + "_DATABASE": "futureagi", prefix + "_USER": "collector",
	}
}

func isolatePGEnv(t *testing.T) {
	t.Helper()
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "PG") {
			t.Setenv(key, "")
		}
	}
	// Never consume the developer's password or service files in parser tests.
	t.Setenv("PGPASSFILE", filepath.Join(t.TempDir(), "absent.pgpass"))
	t.Setenv("PGSERVICEFILE", filepath.Join(t.TempDir(), "absent.pg_service.conf"))
}

func TestEndpointFromEnvPreservesArbitraryCredentials(t *testing.T) {
	isolatePGEnv(t)
	t.Setenv("PGSSLMODE", "verify-full")
	for _, prefix := range []string{"FI_PG_WRITE", "FI_PG_READ"} {
		for _, host := range []string{"postgres", "2001:db8::42"} {
			for _, database := range []string{"db%:'\\\" @:/?#&=+ ø\n\t", "/leading/slash?sslmode=disable&sslrootcert=/bad#fragment"} {
				t.Run(prefix+"/"+host+"/"+database, func(t *testing.T) {
					values := map[string]string{
						prefix + "_HOST": host, prefix + "_PORT": "5432",
						prefix + "_DATABASE": database, prefix + "_USER": "user%:'\\\" @:/?#&=+ ø\n\t",
						prefix + "_PASSWORD": "space and @:/?#ø\n\t$secret%&=+'\\\"",
					}
					got, err := EndpointFromEnv(func(key string) string { return values[key] }, prefix)
					if err != nil {
						t.Fatal(err)
					}
					parsedConfig, err := pgxpool.ParseConfig(got)
					if err != nil {
						t.Fatalf("generated endpoint is not parseable: %v", err)
					}
					parsed := parsedConfig.ConnConfig
					if parsed.Host != host || parsed.Port != 5432 || parsed.User != values[prefix+"_USER"] || parsed.Password != values[prefix+"_PASSWORD"] || parsed.Database != database {
						t.Fatal("host, port or credentials were not preserved")
					}
					if parsed.TLSConfig == nil || parsed.TLSConfig.InsecureSkipVerify || len(parsed.Fallbacks) != 0 {
						t.Fatal("reserved characters injected or downgraded TLS settings")
					}
				})
			}
		}
	}
}

func TestEndpointFromEnvUnset(t *testing.T) {
	got, err := EndpointFromEnv(func(string) string { return "" }, "FI_PG_WRITE")
	if err != nil || got != "" {
		t.Fatal("unset fields must preserve URI/YAML configuration")
	}
}

func TestEndpointFromEnvInvalidFieldsFailWithoutValues(t *testing.T) {
	for _, prefix := range []string{"FI_PG_WRITE", "FI_PG_READ"} {
		for _, tc := range []struct{ field, value string }{
			{"HOST", ""}, {"PORT", ""}, {"DATABASE", ""}, {"USER", ""},
			{"PORT", "not-a-port"}, {"PORT", "0"}, {"PORT", "-1"}, {"PORT", "65536"},
			{"PORT", "999999999999999999999999999999"},
			{"HOST", "host:5432"}, {"HOST", "[::1]"}, {"HOST", "/var/run/postgresql"},
			{"HOST", "bad host"}, {"HOST", "host,other"},
		} {
			t.Run(prefix+"/"+tc.field+"/"+tc.value, func(t *testing.T) {
				values := endpointFields(prefix)
				values[prefix+"_PASSWORD"] = "do-not-log-this-password"
				values[prefix+"_"+tc.field] = tc.value
				got, err := EndpointFromEnv(func(key string) string { return values[key] }, prefix)
				if err == nil || got != "" || !strings.Contains(err.Error(), prefix+"_"+tc.field) {
					t.Fatal("invalid fields must produce a named configuration error, not a fallback")
				}
				if strings.Contains(err.Error(), values[prefix+"_PASSWORD"]) {
					t.Fatal("configuration error leaked a password")
				}
			})
		}
		t.Run(prefix+"/password-only", func(t *testing.T) {
			_, err := EndpointFromEnv(func(key string) string {
				if key == prefix+"_PASSWORD" {
					return "password-only-secret"
				}
				return ""
			}, prefix)
			if err == nil || strings.Contains(err.Error(), "password-only-secret") {
				t.Fatal("password-only fields must fail without exposing their value")
			}
		})
	}
}

func TestEndpointFromEnvIPv6AndOptionalPassword(t *testing.T) {
	isolatePGEnv(t)
	for _, host := range []string{"postgres", "127.0.0.1", "::1", "2001:db8::42", "fe80::1%en0"} {
		t.Run(host, func(t *testing.T) {
			values := endpointFields("FI_PG_READ")
			values["FI_PG_READ_HOST"] = host
			values["FI_PG_READ_PORT"] = "6543"
			got, err := EndpointFromEnv(func(key string) string { return values[key] }, "FI_PG_READ")
			if err != nil {
				t.Fatal(err)
			}
			endpoint, err := url.Parse(got)
			if err != nil {
				t.Fatal(err)
			}
			if _, set := endpoint.User.Password(); set {
				t.Fatal("optional password must not override the driver's password resolution")
			}
			t.Setenv("PGPASSWORD", "driver-password")
			cfg, err := pgxpool.ParseConfig(got)
			if err != nil {
				t.Fatal(err)
			}
			if cfg.ConnConfig.Host != host || cfg.ConnConfig.Port != 6543 || cfg.ConnConfig.Password != "driver-password" {
				t.Fatal("host, port or optional password resolution changed")
			}
		})
	}
}

func TestEndpointFromEnvHonorsPGSSLMODE(t *testing.T) {
	isolatePGEnv(t)
	values := endpointFields("FI_PG_WRITE")
	got, err := EndpointFromEnv(func(key string) string { return values[key] }, "FI_PG_WRITE")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(got, "sslmode") {
		t.Fatal("generated endpoint must not override standard TLS configuration")
	}
	for _, mode := range []string{"require", "verify-ca", "verify-full", "disable", "prefer", ""} {
		t.Run(mode, func(t *testing.T) {
			t.Setenv("PGSSLMODE", mode)
			cfg, err := pgxpool.ParseConfig(got)
			if err != nil {
				t.Fatal(err)
			}
			pg := cfg.ConnConfig
			if mode == "disable" {
				if pg.TLSConfig != nil {
					t.Fatal("explicit local plaintext mode was not honored")
				}
				return
			}
			if pg.TLSConfig == nil {
				t.Fatal("TLS was silently disabled")
			}
			if mode == "require" || mode == "verify-ca" || mode == "verify-full" {
				for _, fallback := range pg.Fallbacks {
					if fallback.TLSConfig == nil {
						t.Fatal("strict TLS configuration gained a plaintext fallback")
					}
				}
			}
			if mode == "verify-full" && (pg.TLSConfig.InsecureSkipVerify || pg.TLSConfig.ServerName != values["FI_PG_WRITE_HOST"]) {
				t.Fatal("server identity verification was downgraded")
			}
		})
	}
}

func TestEndpointFromEnvInvalidTLSCannotFallBack(t *testing.T) {
	isolatePGEnv(t)
	values := endpointFields("FI_PG_WRITE")
	got, err := EndpointFromEnv(func(key string) string { return values[key] }, "FI_PG_WRITE")
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY"} {
		t.Run(key, func(t *testing.T) {
			t.Setenv("PGSSLMODE", "verify-full")
			t.Setenv(key, filepath.Join(t.TempDir(), "invalid-or-missing"))
			if _, err := pgxpool.ParseConfig(got); err == nil {
				t.Fatal("invalid TLS configuration was ignored")
			}
		})
	}
}
