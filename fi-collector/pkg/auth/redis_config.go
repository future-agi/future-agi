package auth

import (
	"crypto/tls"
	"fmt"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

// ApplyRedisEnv preserves YAML defaults unless an environment override is set.
// Invalid TLS configuration refuses startup; it never downgrades to plaintext.
func (c *Config) ApplyRedisEnv(getenv func(string) string) error {
	if v := getenv("FI_AUTH_REDIS_ADDR"); v != "" {
		c.RedisAddr = v
	}
	if v := getenv("FI_AUTH_REDIS_PASSWORD"); v != "" {
		c.RedisPass = v
	}
	if v := getenv("FI_AUTH_REDIS_TLS"); v != "" {
		enabled, err := strconv.ParseBool(strings.TrimSpace(v))
		if err != nil {
			return fmt.Errorf("FI_AUTH_REDIS_TLS must be a boolean")
		}
		c.RedisTLS = enabled
	}
	if v := getenv("FI_AUTH_REDIS_TLS_SERVER_NAME"); v != "" {
		c.RedisTLSServerName = v
	}
	return nil
}

// RedisOptions is shared by authentication, pause reads and usage emission.
// Go verifies the certificate against system roots (or SSL_CERT_FILE). With
// no server-name override, tls.Dial uses the hostname in RedisAddr for SNI and
// certificate verification. There is no plaintext or insecure TLS fallback.
func (c Config) RedisOptions() *redis.Options {
	opts := &redis.Options{Addr: c.RedisAddr, Password: c.RedisPass}
	if c.RedisTLS {
		opts.TLSConfig = &tls.Config{
			MinVersion: tls.VersionTLS12,
			ServerName: c.RedisTLSServerName,
		}
	}
	return opts
}
