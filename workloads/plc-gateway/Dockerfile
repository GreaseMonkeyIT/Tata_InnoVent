FROM golang:1.22-alpine AS build
WORKDIR /src
COPY . .
RUN go mod tidy && CGO_ENABLED=0 go build -o /plc .
FROM gcr.io/distroless/static
COPY --from=build /plc /plc
ENTRYPOINT ["/plc"]
