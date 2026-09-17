// fi-collector — OTLP→ClickHouse 25.3 typed-Map translator.
//
// Purpose: replace the row-EXPANDING `spans_mv` (the OOM source) by doing the
// attribute-split work in this Go process BEFORE the row reaches CH. The
// payload that lands in CH is already fully-typed: typed Maps populated,
// hot LLM columns extracted, JSON overflow serialized.
//
// At 1B spans/day (≈12K avg / 100K peak per second) the goal is to keep
// per-pod memory bounded — the old CH MV ran JSON-shred on multi-MB
// attribute blobs and held the whole batch in RAM during the merge.
module github.com/future-agi/future-agi/fi-collector

go 1.24.0

toolchain go1.24.3

require (
	github.com/alicebob/miniredis/v2 v2.38.0
	github.com/google/uuid v1.6.0
	github.com/jackc/pgx/v5 v5.7.4
	github.com/redis/go-redis/v9 v9.7.3
	github.com/twmb/franz-go v1.20.7
	go.opentelemetry.io/collector/pdata v1.20.0
	golang.org/x/sync v0.19.0
	golang.org/x/sys v0.41.0
	google.golang.org/grpc v1.67.1
	gopkg.in/yaml.v3 v3.0.1
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/dgryski/go-rendezvous v0.0.0-20200823014737-9f7001d12a5f // indirect
	github.com/gogo/protobuf v1.3.2 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	github.com/json-iterator/go v1.1.12 // indirect
	github.com/klauspost/compress v1.18.4 // indirect
	github.com/kr/text v0.2.0 // indirect
	github.com/modern-go/concurrent v0.0.0-20180306012644-bacd9c7ef1dd // indirect
	github.com/modern-go/reflect2 v1.0.2 // indirect
	github.com/pierrec/lz4/v4 v4.1.25 // indirect
	github.com/twmb/franz-go/pkg/kmsg v1.12.0 // indirect
	github.com/yuin/gopher-lua v1.1.1 // indirect
	go.uber.org/multierr v1.11.0 // indirect
	golang.org/x/crypto v0.48.0 // indirect
	golang.org/x/net v0.49.0 // indirect
	golang.org/x/text v0.34.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240814211410-ddb44dafa142 // indirect
	google.golang.org/protobuf v1.35.1 // indirect
)
