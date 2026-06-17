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

go 1.24

require (
	github.com/google/uuid v1.6.0
	go.opentelemetry.io/collector/pdata v1.20.0
	google.golang.org/grpc v1.67.1
	gopkg.in/yaml.v3 v3.0.1
)

require (
	github.com/gogo/protobuf v1.3.2 // indirect
	github.com/json-iterator/go v1.1.12 // indirect
	github.com/kr/text v0.2.0 // indirect
	github.com/modern-go/concurrent v0.0.0-20180306012644-bacd9c7ef1dd // indirect
	github.com/modern-go/reflect2 v1.0.2 // indirect
	go.uber.org/multierr v1.11.0 // indirect
	golang.org/x/net v0.28.0 // indirect
	golang.org/x/sys v0.24.0 // indirect
	golang.org/x/text v0.17.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240814211410-ddb44dafa142 // indirect
	google.golang.org/protobuf v1.35.1 // indirect
)
