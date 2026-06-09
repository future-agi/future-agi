.PHONY: build run test test-race lint clean docker update-pricing

BINARY=agentcc-gateway
BUILD_DIR=bin
VERSION?=0.1.0
LDFLAGS=-ldflags "-s -w -X main.version=$(VERSION)"

build:
	go build $(LDFLAGS) -o $(BUILD_DIR)/$(BINARY) ./cmd/agentcc/

run: build
	$(BUILD_DIR)/$(BINARY) --config config.yaml

test:
	go test ./... -v -count=1

test-race:
	go test ./... -v -race -count=1

test-cover:
	go test ./... -coverprofile=coverage.out
	go tool cover -html=coverage.out -o coverage.html

bench:
	go test ./... -bench=. -benchmem

lint:
	golangci-lint run ./...

clean:
	rm -rf $(BUILD_DIR) coverage.out coverage.html

docker:
	docker build -t agentcc-gateway:$(VERSION) .

docker-run: docker
	docker run -p 8080:8080 -v $(PWD)/config.yaml:/app/config.yaml agentcc-gateway:$(VERSION)

update-pricing:
	bash scripts/update-litellm-pricing.sh
