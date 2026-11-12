FROM golang:1.26.10 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download all
COPY cmd/agentcc-mcpmock ./cmd/agentcc-mcpmock
COPY cmd/agentcc-otlpmock ./cmd/agentcc-otlpmock
RUN CGO_ENABLED=0 go build -o /out/mcpmock ./cmd/agentcc-mcpmock && CGO_ENABLED=0 go build -o /out/otlpmock ./cmd/agentcc-otlpmock
FROM alpine:3.22
COPY --from=build /out/ /usr/local/bin/
