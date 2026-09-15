package observedcatalog

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

type RuntimeConfig struct {
	Mode           string        `yaml:"mode"`
	Kafka          KafkaConfig   `yaml:"kafka"`
	Spool          SpoolConfig   `yaml:"spool"`
	Limits         Limits        `yaml:"limits"`
	ReplayInterval time.Duration `yaml:"replay_interval"`
}

const envPrefix = "FI_OBSERVED_CATALOG_"

func KafkaConfigFromEnv(getenv func(string) string) (KafkaConfig, error) {
	c := KafkaConfig{}
	return kafkaFromEnv(c, getenv)
}

func kafkaFromEnv(c KafkaConfig, getenv func(string) string) (KafkaConfig, error) {
	if raw := getenv(envPrefix + "KAFKA_BROKERS"); raw != "" {
		c.Brokers = nil
		for _, broker := range strings.Split(raw, ",") {
			c.Brokers = append(c.Brokers, strings.TrimSpace(broker))
		}
	}
	if v := getenv(envPrefix + "KAFKA_TOPIC"); v != "" {
		c.Topic = v
	}
	if v := getenv(envPrefix + "KAFKA_GROUP"); v != "" {
		c.Group = v
	}
	if v := getenv(envPrefix + "KAFKA_CLIENT_ID"); v != "" {
		c.ClientID = v
	}
	if err := durationEnv(getenv, "KAFKA_TIMEOUT", &c.Timeout, 30*time.Second); err != nil {
		return c, err
	}
	return c.normalized()
}

// LimitsFromEnv is shared by live ingestion and backfill. Eligibility does
// not depend on independently configured transport or page byte limits.
func LimitsFromEnv(getenv func(string) string) (Limits, error) {
	limits := DefaultLimits()
	for suffix, target := range map[string]*int{"MAX_KEYS_PER_SPAN": &limits.MaxKeysPerSpan, "MAX_ARRAY_MEMBERS_PER_SPAN": &limits.MaxArrayMembersPerSpan} {
		if value := getenv(envPrefix + suffix); value != "" {
			n, err := strconv.Atoi(value)
			if err != nil || n <= 0 {
				return limits, fmt.Errorf("%s%s must be positive", envPrefix, suffix)
			}
			*target = n
		}
	}
	if limits.MaxKeysPerSpan > 4096 || limits.MaxArrayMembersPerSpan > 16384 {
		return limits, fmt.Errorf("observedcatalog: extraction limits exceed ceilings")
	}
	return limits, nil
}

func ClickHouseConfigFromEnv(getenv func(string) string) (ClickHouseConfig, error) {
	c := ClickHouseConfig{URL: getenv(envPrefix + "CH_URL"), Database: getenv(envPrefix + "CH_DATABASE"), Username: getenv(envPrefix + "CH_USERNAME"), Password: getenv(envPrefix + "CH_PASSWORD")}
	if err := durationEnv(getenv, "CH_TIMEOUT", &c.Timeout, maxClickHouseRequestTimeout); err != nil {
		return c, err
	}
	if c.Timeout == 0 {
		c.Timeout = 10 * time.Second
	}
	return c, nil
}

func RuntimeFromEnv(c RuntimeConfig, getenv func(string) string) (RuntimeConfig, error) {
	for _, name := range []string{"FI_CATALOG_MODE", "FI_PROPERTY_CATALOG_MODE"} {
		if value := getenv(name); value != "" && value != "disabled" {
			return c, fmt.Errorf("%s is obsolete; configure FI_OBSERVED_CATALOG_MODE", name)
		}
	}
	if value := getenv(envPrefix + "MODE"); value != "" {
		c.Mode = value
	}
	if c.Mode == "" {
		c.Mode = "disabled"
	}
	if c.Mode != "disabled" && c.Mode != "kafka" {
		return c, fmt.Errorf("FI_OBSERVED_CATALOG_MODE must be disabled or kafka")
	}
	if c.Mode == "disabled" {
		return c, nil
	}
	var err error
	c.Kafka, err = kafkaFromEnv(c.Kafka, getenv)
	if err != nil {
		return c, err
	}
	if value := getenv(envPrefix + "SPOOL_DIR"); value != "" {
		c.Spool.Directory = value
	}
	if c.Spool.Directory == "" {
		c.Spool.Directory = "/var/lib/fi-collector/observed-catalog"
	}
	if c.Spool.MaxFiles == 0 {
		c.Spool.MaxFiles = 10000
	}
	if c.Spool.MaxBytes == 0 {
		c.Spool.MaxBytes = 512 << 20
	}
	if c.ReplayInterval == 0 {
		c.ReplayInterval = time.Second
	}
	c.Limits, err = LimitsFromEnv(getenv)
	if err != nil {
		return c, err
	}
	for suffix, target := range map[string]*int{"MAX_SPOOL_FILES": &c.Spool.MaxFiles} {
		if value := getenv(envPrefix + suffix); value != "" {
			n, err := strconv.Atoi(value)
			if err != nil || n <= 0 {
				return c, fmt.Errorf("%s%s must be a positive integer", envPrefix, suffix)
			}
			*target = n
		}
	}
	if value := getenv(envPrefix + "MAX_SPOOL_BYTES"); value != "" {
		n, err := strconv.ParseInt(value, 10, 64)
		if err != nil || n <= 0 {
			return c, fmt.Errorf("FI_OBSERVED_CATALOG_MAX_SPOOL_BYTES must be positive")
		}
		c.Spool.MaxBytes = n
	}
	if err := durationEnv(getenv, "REPLAY_INTERVAL", &c.ReplayInterval, time.Minute); err != nil {
		return c, err
	}
	if c.ReplayInterval <= 0 || c.ReplayInterval > time.Minute || c.Limits.MaxKeysPerSpan <= 0 || c.Limits.MaxKeysPerSpan > 4096 || c.Limits.MaxArrayMembersPerSpan <= 0 || c.Limits.MaxArrayMembersPerSpan > 16384 {
		return c, fmt.Errorf("observedcatalog: invalid runtime limits")
	}
	return c, nil
}

func durationEnv(getenv func(string) string, suffix string, target *time.Duration, max time.Duration) error {
	if value := getenv(envPrefix + suffix); value != "" {
		d, err := time.ParseDuration(value)
		if err != nil || d <= 0 || d > max {
			return fmt.Errorf("%s%s must be a positive duration no greater than %s", envPrefix, suffix, max)
		}
		*target = d
	}
	return nil
}
