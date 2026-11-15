# Upstream image is distroless (no shell, no wget). A static busybox is added
# solely to run the container healthcheck (wget on the readiness endpoint).
FROM busybox:1.37.0-musl AS busybox

FROM grafana/tempo:2.10.10
COPY --from=busybox /bin/busybox /bin/busybox
