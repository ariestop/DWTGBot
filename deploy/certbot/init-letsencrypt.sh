#!/usr/bin/env bash
# =====================================================================
# Bootstrap Let's Encrypt certificates for the media-plane nginx.
#
# First issue: drop a self-signed dummy cert so nginx can start with the
# HTTPS server block, then remove the dummy and ask certbot for the real
# one over HTTP-01, then reload nginx. Re-run with an existing lineage:
# force-renew it in place.
#
# Usage (from deploy/single or deploy/nl2):
#   DOMAIN=media.example.com EMAIL=admin@example.com \
#     bash ../certbot/init-letsencrypt.sh
# =====================================================================
set -Eeuo pipefail

DOMAIN="${DOMAIN:?DOMAIN must be set, e.g. media.example.com}"
EMAIL="${EMAIL:?EMAIL must be set}"
STAGING="${STAGING:-0}"          # 1 = use Let's Encrypt staging
ASSUME_YES="${ASSUME_YES:-0}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.yml --env-file .env}"

RSA_KEY_SIZE=4096
LIVE="/etc/letsencrypt/live/${DOMAIN}"

# Runs a command inside the certbot image with the letsencrypt volumes
# mounted. --no-deps: nginx is managed explicitly below.
certbot_run() {
  $COMPOSE run --rm --no-deps --entrypoint "$1" certbot
}

create_dummy_cert() {
  certbot_run "mkdir -p '${LIVE}'"
  certbot_run "openssl req -x509 -nodes -newkey rsa:${RSA_KEY_SIZE} -days 1 \
    -keyout '${LIVE}/privkey.pem' \
    -out    '${LIVE}/fullchain.pem' \
    -subj '/CN=localhost'"
}

wait_for_nginx() {
  local _
  for _ in $(seq 1 15); do
    if $COMPOSE exec -T nginx wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "nginx did not become ready on :80" >&2
  return 1
}

STAGING_FLAG=""
[[ "$STAGING" == "1" ]] && STAGING_FLAG="--staging"

request_cert() {
  certbot_run "certbot certonly --webroot -w /var/www/certbot \
    ${STAGING_FLAG} \
    --cert-name ${DOMAIN} \
    -d ${DOMAIN} \
    --email ${EMAIL} \
    --rsa-key-size ${RSA_KEY_SIZE} \
    --agree-tos \
    --no-eff-email \
    --non-interactive $*"
}

echo "Bootstrapping certs for ${DOMAIN}"

if certbot_run "test -f /etc/letsencrypt/renewal/${DOMAIN}.conf"; then
  # A lineage certbot manages already exists: renew it in place. Each
  # forced renewal counts against Let's Encrypt's duplicate-cert limit.
  if [[ "${ASSUME_YES}" != "1" ]]; then
    read -r -p "Certificate for ${DOMAIN} already exists. Force renewal? (y/N) " ans
    [[ "${ans,,}" == "y" ]] || exit 0
  fi
  $COMPOSE up -d nginx
  wait_for_nginx
  echo "Renewing existing cert"
  request_cert --force-renewal
else
  echo "Creating self-signed dummy cert"
  create_dummy_cert

  echo "Starting nginx with dummy cert"
  $COMPOSE up -d nginx
  wait_for_nginx

  # certbot refuses to create a lineage over a live/ directory it does not
  # manage ("live directory exists"). nginx already holds the dummy in
  # memory, so the files can go; on failure the dummy is recreated so a
  # later nginx restart still finds a certificate.
  certbot_run "rm -rf '${LIVE}' '/etc/letsencrypt/archive/${DOMAIN}'"
  echo "Requesting real cert from Let's Encrypt"
  if ! request_cert; then
    echo "certbot failed; restoring the dummy cert" >&2
    create_dummy_cert
    exit 1
  fi
fi

echo "Reloading nginx"
$COMPOSE exec -T nginx nginx -s reload || $COMPOSE restart nginx

echo "Done."
