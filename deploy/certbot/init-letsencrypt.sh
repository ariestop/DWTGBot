#!/usr/bin/env bash
# =====================================================================
# Bootstrap Let's Encrypt certificates for the media-plane nginx.
#
# Pattern adapted from https://github.com/wmnnd/nginx-certbot — we drop a
# self-signed dummy cert first so nginx can start, then ask certbot for
# the real one over HTTP-01, then reload nginx.
#
# Usage (from deploy/single or deploy/nl2):
#   DOMAIN=media.example.com EMAIL=admin@example.com \
#     bash ../certbot/init-letsencrypt.sh
# =====================================================================
set -Eeuo pipefail

DOMAIN="${DOMAIN:?DOMAIN must be set, e.g. media.example.com}"
EMAIL="${EMAIL:?EMAIL must be set}"
STAGING="${STAGING:-0}"          # 1 = use Let's Encrypt staging
COMPOSE="${COMPOSE:-docker compose -f docker-compose.yml --env-file .env}"

DATA_PATH="${DATA_PATH:-./certbot}"
RSA_KEY_SIZE=4096

echo "Bootstrapping certs for ${DOMAIN}"

if [[ -d "${DATA_PATH}/conf/live/${DOMAIN}" ]]; then
  read -r -p "Existing data found for ${DOMAIN}. Continue and replace? (y/N) " ans
  [[ "${ans,,}" == "y" ]] || exit 0
fi

mkdir -p "${DATA_PATH}/conf/live/${DOMAIN}" "${DATA_PATH}/www"

# Pre-create the target directories inside the ``letsencrypt_conf`` volume
# before openssl tries to write into them. The certbot image runs openssl
# with a minimal shell, so mkdir has to happen in its own ``run`` call —
# piggy-backing on the openssl one-liner below would execute inside quoted
# context and no longer be a separate command. Without this step openssl
# fails with ``Can't open .../privkey.pem for writing, No such file``.
$COMPOSE run --rm --entrypoint "mkdir -p \
  '/etc/letsencrypt/live/${DOMAIN}' \
  '/etc/letsencrypt/archive/${DOMAIN}'" certbot

if [[ ! -f "${DATA_PATH}/conf/options-ssl-nginx.conf" ]]; then
  curl -sSL https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
    > "${DATA_PATH}/conf/options-ssl-nginx.conf"
fi
if [[ ! -f "${DATA_PATH}/conf/ssl-dhparams.pem" ]]; then
  curl -sSL https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
    > "${DATA_PATH}/conf/ssl-dhparams.pem"
fi

echo "Creating self-signed dummy cert"
$COMPOSE run --rm --entrypoint "\
  openssl req -x509 -nodes -newkey rsa:${RSA_KEY_SIZE} -days 1 \
    -keyout '/etc/letsencrypt/live/${DOMAIN}/privkey.pem' \
    -out    '/etc/letsencrypt/live/${DOMAIN}/fullchain.pem' \
    -subj '/CN=localhost'" certbot

echo "Starting nginx with dummy cert"
$COMPOSE up -d nginx

# Give nginx a couple of seconds to bind :80 before certbot tries to reach
# /.well-known/acme-challenge through it.
sleep 3

STAGING_FLAG=""
[[ "$STAGING" == "1" ]] && STAGING_FLAG="--staging"

# Note: we deliberately do NOT delete the dummy cert before calling certbot.
# The previous version removed it first, which left nginx with missing cert
# files — and on the next restart (healthcheck/live-restore/etc.) nginx
# crash-looped with ``cannot load certificate``, which then caused Let's
# Encrypt HTTP-01 to fail with Connection refused. ``--force-renewal`` makes
# certbot overwrite the dummy cert in-place, so nginx can be reloaded
# cleanly afterwards without any window where the cert files are absent.
echo "Requesting real cert from Let's Encrypt"
$COMPOSE run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    ${STAGING_FLAG} \
    --email ${EMAIL} \
    -d ${DOMAIN} \
    --rsa-key-size ${RSA_KEY_SIZE} \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    --non-interactive" certbot

echo "Reloading nginx"
$COMPOSE exec nginx nginx -s reload || $COMPOSE restart nginx

echo "Done."
