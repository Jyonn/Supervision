#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/api.supervision.qijiong.work ]; then
    /usr/sbin/nginx -t
    /usr/bin/systemctl reload nginx
fi
