#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/api.abandon.qijiong.work ]; then
    /usr/sbin/nginx -t
    /usr/bin/systemctl reload nginx
fi
