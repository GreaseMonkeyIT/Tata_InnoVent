# L2 aggregator (P3) — static Go binary on distroless.
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /aggregator .

FROM gcr.io/distroless/static-debian12
COPY --from=build /aggregator /aggregator
# queries.yaml is provided at runtime via a ConfigMap mounted at /etc/aggregator/
EXPOSE 9000
USER 65532:65532
ENTRYPOINT ["/aggregator"]
