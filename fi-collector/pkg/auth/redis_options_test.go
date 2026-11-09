package auth

import (
	"crypto/tls"
	"testing"
)

// Configuration-only: no Dial, Redis server or network access.
func TestCollectorRedisOptions(t *testing.T) {
	for _, enabled := range []string{"true", "false"} {
		t.Run(enabled, func(t *testing.T) {
			env := map[string]string{
				"FI_AUTH_REDIS_ADDR":            "cache.example.invalid:6380",
				"FI_AUTH_REDIS_PASSWORD":        "synthetic-password",
				"FI_AUTH_REDIS_TLS":             enabled,
				"FI_AUTH_REDIS_TLS_SERVER_NAME": "cache.example.invalid",
			}
			var c Config
			if err := c.ApplyRedisEnv(func(key string) string { return env[key] }); err != nil {
				t.Fatal(err)
			}
			opts := c.RedisOptions()
			if opts.Addr != env["FI_AUTH_REDIS_ADDR"] || opts.Password != env["FI_AUTH_REDIS_PASSWORD"] {
				t.Fatal("TLS must preserve the address and password")
			}
			if enabled == "false" {
				if opts.TLSConfig != nil {
					t.Fatal("plain Redis unexpectedly uses TLS")
				}
				return
			}
			if opts.TLSConfig == nil || opts.TLSConfig.MinVersion < tls.VersionTLS12 || opts.TLSConfig.InsecureSkipVerify {
				t.Fatal("TLS must verify certificates and require TLS 1.2 or newer")
			}
			if opts.TLSConfig.ServerName != env["FI_AUTH_REDIS_TLS_SERVER_NAME"] {
				t.Fatal("TLS server identity was lost")
			}
		})
	}
}

func TestCollectorRedisRejectsMalformedTLS(t *testing.T) {
	var c Config
	if err := c.ApplyRedisEnv(func(key string) string {
		if key == "FI_AUTH_REDIS_TLS" {
			return "tru"
		}
		return ""
	}); err == nil {
		t.Fatal("invalid TLS configuration must not silently downgrade to plaintext")
	}
}
