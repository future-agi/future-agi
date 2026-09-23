FROM futureagi/agentcc-gateway-base:v1.0.0 AS builder

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o agentcc-gateway ./cmd/agentcc/

FROM scratch

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /build/agentcc-gateway /app/agentcc-gateway

EXPOSE 8080

ENTRYPOINT ["/app/agentcc-gateway"]
CMD ["--config", "/app/config.yaml"]
