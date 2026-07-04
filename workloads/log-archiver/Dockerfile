FROM alpine:3.20
RUN apk add --no-cache fio
COPY archive.sh /archive.sh
ENTRYPOINT ["/bin/sh", "/archive.sh"]
