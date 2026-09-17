package catalogwriter

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

// Mode is the mutually exclusive runtime transport for catalog envelopes.
// The zero value is disabled so an absent config cannot start catalog writes.
type Mode string

const (
	ModeDisabled Mode = "disabled"
	ModeDirect   Mode = "direct"
	ModeKafka    Mode = "kafka"
)

// RuntimeConfig belongs to collector-side staging and publishing. Direct mode
// carries a catalog-only ClickHouse identity; Kafka mode is producer-only and
// is rejected if any ClickHouse setting is present. The standalone consumer
// owns its own group and catalog-only delivery credentials.
type RuntimeConfig struct {
	Mode           Mode          `yaml:"mode"`
	Environment    string        `yaml:"environment"`
	CatalogEpoch   uint16        `yaml:"catalog_epoch"`
	ProducerStream string        `yaml:"producer_stream_id"`
	SpoolDir       string        `yaml:"spool_dir"`
	ReplayInterval time.Duration `yaml:"replay_interval"`

	ClickHouse ClickHouseSinkConfig `yaml:"clickhouse"`
	Kafka      KafkaRuntimeConfig   `yaml:"kafka"`
}

// KafkaRuntimeConfig intentionally contains no direct transport dependency;
// pkg/catalogkafka owns client construction. It lives here so root config can
// validate direct-vs-Kafka exclusivity before importing either runtime.
type KafkaRuntimeConfig struct {
	Brokers []string `yaml:"brokers"`
	Topic   string   `yaml:"topic"`
}

func (c RuntimeConfig) normalizedMode() Mode {
	if c.Mode == "" {
		return ModeDisabled
	}
	return Mode(strings.ToLower(strings.TrimSpace(string(c.Mode))))
}

// ValidateMode enforces a closed, mutually-exclusive switch. Non-disabled
// modes are dev-only in this rollout; production activation is a separate,
// reviewed change after dev evidence and replicated DDL wiring exist.
func (c RuntimeConfig) ValidateMode() error {
	mode := c.normalizedMode()
	switch mode {
	case ModeDisabled:
		return nil
	case ModeDirect, ModeKafka:
	default:
		return fmt.Errorf("catalogwriter: invalid catalog mode %q", c.Mode)
	}
	if c.Environment != "development" {
		return errors.New("catalogwriter: catalog ingestion modes are development-only until production review")
	}
	if c.CatalogEpoch == 0 || c.ProducerStream == "" || c.SpoolDir == "" {
		return errors.New("catalogwriter: enabled catalog mode requires epoch, producer stream, and spool directory")
	}
	if c.ReplayInterval < 0 || c.ReplayInterval > 30*time.Second {
		return errors.New("catalogwriter: replay interval must be at most 30s")
	}
	if mode == ModeDirect {
		if len(c.Kafka.Brokers) != 0 || c.Kafka.Topic != "" {
			return errors.New("catalogwriter: direct mode rejects Kafka settings")
		}
		if c.ClickHouse.RequestTimeout > 10*time.Second || c.ClickHouse.MaxExecutionTime > 10*time.Second {
			return errors.New("catalogwriter: catalog ClickHouse operations must be bounded to 10s")
		}
		if c.ClickHouse.URL == "" || c.ClickHouse.Database == "" || c.ClickHouse.Username == "" {
			return errors.New("catalogwriter: direct mode requires separate ClickHouse URL, database, and username")
		}
		return nil
	}
	if hasClickHouseSettings(c.ClickHouse) {
		return errors.New("catalogwriter: Kafka producer mode rejects ClickHouse settings")
	}
	if len(c.Kafka.Brokers) == 0 || c.Kafka.Topic == "" {
		return errors.New("catalogwriter: Kafka producer mode requires brokers and topic")
	}
	return nil
}

func hasClickHouseSettings(c ClickHouseSinkConfig) bool {
	return c.URL != "" || c.Database != "" || c.Username != "" || c.Password != "" ||
		c.RequestTimeout != 0 || c.MaxRequestBytes != 0 || c.MaxResponseBytes != 0 ||
		c.MaxExecutionTime != 0 || c.MaxMemoryUsage != 0 || c.MaxThreads != 0 || c.AsyncInsert
}

// SelectedMode returns the validated normalized mode.
func (c RuntimeConfig) SelectedMode() (Mode, error) {
	if err := c.ValidateMode(); err != nil {
		return ModeDisabled, err
	}
	return c.normalizedMode(), nil
}
