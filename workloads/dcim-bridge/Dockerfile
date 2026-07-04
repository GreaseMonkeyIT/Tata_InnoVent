FROM golang:1.22-alpine AS build
WORKDIR /src
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o /app .
FROM gcr.io/distroless/static
COPY --from=build /app /app
ENTRYPOINT ["/app"]
