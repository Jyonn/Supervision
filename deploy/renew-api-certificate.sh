#!/bin/sh
set -eu
case "${RENEWED_LINEAGE:-}" in
/etc/letsencrypt/live/api.abandon.qijiong.work|/etc/letsencrypt/live/api.supervision.qijiong.work|/etc/letsencrypt/live/supervision.qijiong.work)
    /usr/sbin/nginx -t
    /usr/bin/systemctl reload nginx
    ;;
esac
