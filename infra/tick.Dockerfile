# The `tick` service: one hourly POST to /api/internal/tick, and nothing else.
#
# WHY THIS FILE EXISTS AT ALL. The tick used to be a stock `curlimages/curl` image plus
# `RAILWAY_RUN_COMMAND`, and Railway ignored that variable outright — the image's own entrypoint
# ran instead, `curl` was invoked with no arguments, printed its usage error, and the container
# restarted forever. `railway status` reported the service Online, then Completed. Nothing ticked,
# and the only symptom was that quiet households stopped finishing extraction, days later.
#
# A CMD in a Dockerfile cannot be ignored. That is the entire argument for this file: the tick's
# behaviour is now committed, reviewable and reproducible from a clone, instead of living in a
# dashboard field that one person set by hand and nobody can diff.
FROM alpine:3.21

# `--no-cache` keeps the layer honest; there is no package index left behind to go stale.
RUN apk add --no-cache curl

COPY infra/tick.sh /usr/local/bin/tick.sh
RUN chmod +x /usr/local/bin/tick.sh

# No ENTRYPOINT on purpose. An entrypoint is what made the previous image swallow its own start
# command; a bare CMD is overridable in the ordinary way if it ever needs to be.
CMD ["/usr/local/bin/tick.sh"]
