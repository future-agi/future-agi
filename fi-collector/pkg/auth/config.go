package auth

import "time"

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
