package auth

import (
	"net/url"
	"strconv"
	"time"
)

// Config controls the auth extension. Auth is always active when PGWrite
// is set — without it, spans land with empty project_id (unusable).
type Config struct {
	PGWrite     string        `yaml:"pg_write"`
	PGRead      string        `yaml:"pg_read"`
	RedisAddr   string        `yaml:"redis_addr"`
	CacheTTL    time.Duration `yaml:"cache_ttl"`
	WarmTTL     time.Duration `yaml:"warm_ttl"`
	PGPoolRead  int           `yaml:"pg_pool_read"`
	PGPoolWrite int           `yaml:"pg_pool_write"`
}

// EndpointFromEnv returns a password-safe PostgreSQL URL when all endpoint
// fields are supplied separately. It deliberately returns an empty string when
// no fields are configured so existing PGWrite/PGRead URI configuration remains
// compatible. The password is encoded by net/url, never interpolated into a
// URI by a deployment template.
func EndpointFromEnv(getenv func(string) string, prefix string) string {
	host, port, database, user := getenv(prefix+"_HOST"), getenv(prefix+"_PORT"), getenv(prefix+"_DATABASE"), getenv(prefix+"_USER")
	if host == "" && port == "" && database == "" && user == "" && getenv(prefix+"_PASSWORD") == "" {
		return ""
	}
	if host == "" || port == "" || database == "" || user == "" {
		return ""
	}
	parsedPort, err := strconv.Atoi(port)
	if err != nil || parsedPort < 1 || parsedPort > 65535 {
		return ""
	}
	return (&url.URL{
		Scheme:   "postgres",
		Host:     host + ":" + strconv.Itoa(parsedPort),
		Path:     "/" + database,
		User:     url.UserPassword(user, getenv(prefix+"_PASSWORD")),
		RawQuery: "sslmode=disable",
	}).String()
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
