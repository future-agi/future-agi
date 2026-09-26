package auth

import (
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Config controls the auth extension. Auth is always active when PGWrite
// is set — without it, spans land with empty project_id (unusable).
type Config struct {
	PGWrite     string        `yaml:"pg_write"`
	PGRead      string        `yaml:"pg_read"`
	RedisAddr   string        `yaml:"redis_addr"`
	RedisPass   string        `yaml:"redis_password"`
	CacheTTL    time.Duration `yaml:"cache_ttl"`
	WarmTTL     time.Duration `yaml:"warm_ttl"`
	PGPoolRead  int           `yaml:"pg_pool_read"`
	PGPoolWrite int           `yaml:"pg_pool_write"`
}

// EndpointFromEnv builds a PostgreSQL URL from separate endpoint fields. No
// fields returns ("", nil); partial or invalid fields are errors, never a silent
// fallback. PASSWORD is optional; absent passwords use pgx's normal resolution.
// TLS options are deliberately not set here: pgx honors the same PGSSLMODE and
// PGSSL* certificate environment as Python/libpq instead of forcing plaintext.
func EndpointFromEnv(getenv func(string) string, prefix string) (string, error) {
	host, port, database, user := getenv(prefix+"_HOST"), getenv(prefix+"_PORT"), getenv(prefix+"_DATABASE"), getenv(prefix+"_USER")
	password := getenv(prefix + "_PASSWORD")
	if host == "" && port == "" && database == "" && user == "" && password == "" {
		return "", nil
	}
	for _, field := range []struct{ name, value string }{
		{"HOST", host}, {"PORT", port}, {"DATABASE", database}, {"USER", user},
	} {
		if field.value == "" {
			return "", fmt.Errorf("%s endpoint fields require %s_%s", prefix, prefix, field.name)
		}
	}
	if strings.Contains(host, ":") {
		if _, err := netip.ParseAddr(host); err != nil {
			return "", fmt.Errorf("%s_HOST must be a hostname or an unbracketed IP address (port is separate)", prefix)
		}
	} else if strings.ContainsAny(host, " \t\r\n/?#@,\\[]%") {
		return "", fmt.Errorf("%s_HOST must be a hostname or an IP address", prefix)
	}
	parsedPort, err := strconv.Atoi(port)
	if err != nil || parsedPort < 1 || parsedPort > 65535 {
		return "", fmt.Errorf("%s_PORT must be an integer from 1 to 65535", prefix)
	}
	credentials := url.User(user)
	if password != "" {
		credentials = url.UserPassword(user, password)
	}
	endpoint := &url.URL{
		Scheme: "postgres",
		Host:   net.JoinHostPort(host, strconv.Itoa(parsedPort)),
		Path:   "/" + database,
		User:   credentials,
	}
	if strings.HasPrefix(database, "/") {
		// pgx trims all leading slashes from URL paths. A query parameter
		// preserves these valid database names without interpolating options.
		endpoint.Path = ""
		endpoint.RawQuery = url.Values{"dbname": {database}}.Encode()
	}
	return endpoint.String(), nil
}

func (c *Config) IsEnabled() bool {
	return c.PGWrite != ""
}

func (c *Config) defaults() {
	if c.CacheTTL == 0 {
		c.CacheTTL = 5 * time.Minute
	}
	if c.WarmTTL == 0 {
		c.WarmTTL = 1 * time.Hour
	}
	if c.PGPoolRead == 0 {
		c.PGPoolRead = 5
	}
	if c.PGPoolWrite == 0 {
		c.PGPoolWrite = 2
	}
	if c.PGRead == "" {
		c.PGRead = c.PGWrite
	}
}
