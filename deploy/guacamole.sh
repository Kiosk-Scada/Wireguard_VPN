#!/usr/bin/env bash
# =============================================================================
#  guacamole.sh — qasje nga browser-i (SSH / VNC / RDP) te router-at e VPN-së
#
#  Instalon në një server LOKAL (p.sh. i njëjti server me Frappe):
#    * Apache Guacamole 1.6.0 (guacd + webapp) me extension-in guacamole-auth-json
#      → hyrja bëhet VETËM përmes Frappe (butoni "Lidhu"), s'ka login tjetër
#    * nginx me HTTPS, i hapur vetëm për rrjetin lokal (LAN)
#    * Tailscale i lidhur me Headscale-in tënd → arrin router-at 10.50.x.x
#
#  Përdorimi:   sudo bash ./guacamole.sh               (instalim / përditësim)
#               sudo bash ./guacamole.sh --reconfigure (ndrysho përgjigjet)
#               sudo bash ./guacamole.sh --status      (gjendja)
# =============================================================================
set -Eeuo pipefail
[[ -n "${BASH_VERSION:-}" ]] || { echo "Xhiroje me bash:  sudo bash $0"; exit 1; }
if grep -q $'\r' "$0" 2>/dev/null; then
  echo "GABIM: file-i ka fund-rreshta Windows (CRLF). Rregullo:  sed -i 's/\r\$//' $0"; exit 1
fi

GUAC_VERSION="1.6.0"          # përdoret te docker-compose.yml (përmes okremote.env)
export GUAC_VERSION
NGINX_IMAGE="nginx:1.27-alpine"
BASE=/opt/okremote
ENV_FILE="$BASE/okremote.env"
CERT_DIR="$BASE/certs"
REC_DIR="$BASE/recordings"
HUB_TEST_IP="10.50.0.1"

# ---------------------------- funksione të përbashkëta ------------------------
die()  { echo -e "\n\033[1;31mGABIM:\033[0m $*\n" >&2; exit 1; }
say()  { echo -e "\n\033[1;36m== $*\033[0m"; }
note() { echo -e "   $*"; }
ok()   { echo -e "   \033[1;32m✓\033[0m $*"; }
warn() { echo -e "\033[1;33mKUJDES:\033[0m $*" >&2; }
trap 'die "Dështoi rreshti $LINENO: $BASH_COMMAND"' ERR

trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

ask() {  # ask VAR "pyetja" "default" "regex" "ndihmë"
  local __var="$1" q="$2" def="${3:-}" re="${4:-}" hint="${5:-}" ans
  while true; do
    if [[ -n "$def" ]]; then
      read -r -p "   $q [$def]: " ans || die "Input-i u ndërpre."
      ans="$(trim "$ans")"; ans="${ans:-$def}"
    else
      read -r -p "   $q: " ans || die "Input-i u ndërpre."
      ans="$(trim "$ans")"
    fi
    if [[ -z "$re" || "$ans" =~ $re ]]; then printf -v "$__var" '%s' "$ans"; return 0; fi
    echo "   ✗ vlerë e pavlefshme${hint:+ — $hint}"
  done
}

ask_secret() {  # ask_secret VAR "pyetja" "regex" "ndihmë" [keep]
  local __var="$1" q="$2" re="${3:-}" hint="${4:-}" keep="${5:-0}" ans
  while true; do
    if [[ "$keep" == 1 && -n "${!__var:-}" ]]; then
      read -r -s -p "   $q (s'shfaqet; Enter = mbaj ekzistuesin): " ans || die "Input-i u ndërpre."
      echo; ans="$(trim "$ans")"
      [[ -z "$ans" ]] && return 0
    else
      read -r -s -p "   $q (s'shfaqet kur shkruan/ngjit): " ans || die "Input-i u ndërpre."
      echo; ans="$(trim "$ans")"
    fi
    if [[ -n "$ans" && ( -z "$re" || "$ans" =~ $re ) ]]; then printf -v "$__var" '%s' "$ans"; return 0; fi
    echo "   ✗ vlerë e pavlefshme${hint:+ — $hint}"
  done
}

ask_yn() {  # ask_yn VAR "pyetja" y|n  → true/false
  local __var="$1" q="$2" def="${3:-n}" ans
  while true; do
    read -r -p "   $q [$( [[ $def == y ]] && echo 'P/j' || echo 'p/J' )]: " ans || die "Input-i u ndërpre."
    ans="$(trim "${ans:-$def}")"
    case "${ans,,}" in
      p|po|y|yes) printf -v "$__var" 'true';  return 0 ;;
      j|jo|n|no)  printf -v "$__var" 'false'; return 0 ;;
    esac
    echo "   ✗ shkruaj p (po) ose j (jo)"
  done
}

valid_cidrs() {
  python3 - "$1" <<'PY' >/dev/null 2>&1
import ipaddress, sys
items = sys.argv[1].replace(",", " ").split()
assert items
for c in items:
    ipaddress.ip_network(c, strict=False)
PY
}

write_env() {  # write_env FILE VAR1 VAR2 ...
  local file="$1" v; shift
  ( umask 077
    { echo "# gjeneruar nga $(basename "$0") më $(date '+%Y-%m-%d %H:%M') — përmban sekrete, mos e shpërndaj"
      for v in "$@"; do printf '%s=%q\n' "$v" "${!v-}"; done
    } > "${file}.tmp" )
  mv -f "${file}.tmp" "$file"; chmod 600 "$file"
}

env_get() {  # env_get FILE VAR
  [[ -f "$1" ]] || return 0
  bash -c 'set +u; source "$1" >/dev/null 2>&1; printf "%s" "${!2-}"' _ "$1" "$2" 2>/dev/null || true
}

port_busy() { ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .; }

lan_iface() { { ip -4 route get 1.1.1.1 2>/dev/null || true; } | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}'; }
lan_ip()    { { ip -4 route get 1.1.1.1 2>/dev/null || true; } | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}'; }
lan_cidr()  {
  local dev cidr; dev="$(lan_iface)"; [[ -n "$dev" ]] || return 0
  cidr="$( { ip -4 -o addr show dev "$dev" 2>/dev/null || true; } | awk '{print $4; exit}')"
  [[ -n "$cidr" ]] && python3 -c "import ipaddress,sys;print(ipaddress.ip_interface(sys.argv[1]).network)" "$cidr" || true
}

compose() { docker compose --project-directory "$BASE" --env-file "$ENV_FILE" -f "$BASE/docker-compose.yml" "$@"; }

# ---------------------------- kontrollet -------------------------------------
preflight() {
  [[ $EUID -eq 0 ]] || die "Xhiroje me sudo:   sudo bash $0"
  if [[ -r /etc/os-release ]]; then
    local ID="" PRETTY_NAME=""
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "$ID" == "ubuntu" || "$ID" == "debian" ]] || warn "Testuar për Ubuntu 22.04/24.04 (ky server: ${PRETTY_NAME:-?})."
  fi
  command -v apt-get >/dev/null || die "Kërkohet Ubuntu/Debian (apt-get)."
  [[ -t 0 ]] || die "Wizard-i kërkon terminal interaktiv. Hyr me SSH dhe xhiroje: sudo bash $0"
}

# ---------------------------- wizard -----------------------------------------
wizard() {
  local def_ip def_cidr v
  def_ip="$(lan_ip)"; def_cidr="$(lan_cidr)"
  say "Konfigurimi (Enter = vlera në kllapa)"

  note "1) Adresa që hapin punëtorët në browser (brenda zyrës)."
  ask DOMAIN "Domain-i" "${DOMAIN:-remote.pikapetrol.com}" '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' "p.sh. remote.pikapetrol.com"
  ask SERVER_LAN_IP "IP-ja lokale e këtij serveri" "${SERVER_LAN_IP:-$def_ip}" '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'

  note ""
  note "2) Kush lejohet ta hapë (rrjeti i zyrës). Disa me presje."
  while true; do
    ask ALLOW_CIDRS "Rrjetet e lejuara" "${ALLOW_CIDRS:-${def_cidr:-192.168.0.0/16}}"
    valid_cidrs "$ALLOW_CIDRS" && break
    echo "   ✗ shkruaj CIDR, p.sh. 192.168.10.0/24"
  done

  note ""
  note "3) Portet."
  local def_https="${HTTPS_PORT:-443}"
  if [[ -z "${HTTPS_PORT:-}" ]] && port_busy 443; then
    warn "Porti 443 është i zënë në këtë server (ndoshta nginx i Frappe) → propozoj 8443."
    def_https=8443
  fi
  ask HTTPS_PORT "Porti HTTPS për punëtorët" "$def_https" '^[0-9]{2,5}$'
  ask LOCAL_PORT "Porti lokal për Frappe (vetëm 127.0.0.1)" "${LOCAL_PORT:-8085}" '^[0-9]{2,5}$'

  note ""
  note "4) Certifikata HTTPS:"
  note "   1 = self-signed (punon menjëherë; browser-i jep paralajmërim herën e parë)"
  note "   2 = Let's Encrypt me Cloudflare DNS (pa hapur porte; kërkon API token)"
  note "   3 = Let's Encrypt me ofrues tjetër DNS (plugin i acme.sh, p.sh. dns_hetzner)"
  note "   4 = certifikata ime (fullchain.pem + privkey.pem)"
  ask TLS_MODE "Zgjedhja" "${TLS_MODE:-1}" '^[1-4]$'
  case "$TLS_MODE" in
    2)
      ask ACME_EMAIL "Email për Let's Encrypt" "${ACME_EMAIL:-}" '^[^@ ]+@[^@ ]+\.[^@ ]+$'
      note "Cloudflare → My Profile → API Tokens → 'Edit zone DNS' për zonën e domain-it."
      ask_secret CF_TOKEN "Cloudflare API Token" '^[A-Za-z0-9_-]{20,}$' "token i pavlefshëm" 1
      ;;
    3)
      ask ACME_EMAIL "Email për Let's Encrypt" "${ACME_EMAIL:-}" '^[^@ ]+@[^@ ]+\.[^@ ]+$'
      note "Plugin-at: https://github.com/acmesh-official/acme.sh/wiki/dnsapi"
      ask ACME_DNS_PLUGIN "Plugin-i i acme.sh" "${ACME_DNS_PLUGIN:-dns_hetzner}" '^dns_[a-z0-9_]+$'
      note "Variablat që kërkon plugin-i, si VAR=vlera, të ndara me hapësirë (p.sh. HETZNER_Token=abc123)"
      ask_secret ACME_DNS_ENV "Variablat" '^[A-Za-z_][A-Za-z0-9_]*=[^ ]+( +[A-Za-z_][A-Za-z0-9_]*=[^ ]+)*$' "format: VAR=vlera" 1
      ;;
    4)
      ask OWN_FULLCHAIN "Path i fullchain.pem" "${OWN_FULLCHAIN:-}" '^/.+'
      ask OWN_PRIVKEY "Path i privkey.pem" "${OWN_PRIVKEY:-}" '^/.+'
      [[ -r "$OWN_FULLCHAIN" && -r "$OWN_PRIVKEY" ]] || die "S'i gjej certifikatat: $OWN_FULLCHAIN / $OWN_PRIVKEY"
      ;;
  esac

  note ""
  note "5) Lidhja me VPN (Headscale). Guacamole duhet t'i arrijë router-at 10.50.x.x."
  ask LOGIN_SERVER "Headscale URL" "${LOGIN_SERVER:-https://vpn.pikapetrol.com}" '^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$'
  if ts_running; then
    ok "Tailscale është i lidhur tashmë në këtë server — s'kërkohet auth key."
    TS_AUTHKEY=""
  else
    note "Te serveri Headscale krijo një key:   sudo hs-authkey support-team 1h"
    ask_secret TS_AUTHKEY "Auth key" '^[A-Za-z0-9_-]{20,}$' "key i pavlefshëm"
  fi

  note ""
  note "6) Sesionet."
  ask SESSION_TIMEOUT "Mbyll sesionin pas sa minutash pa aktivitet" "${SESSION_TIMEOUT:-60}" '^[0-9]{1,4}$'
  ask_yn v "Incizo sesionet (ekranet ruhen në $REC_DIR)?" "$([[ "${RECORDING:-false}" == true ]] && echo y || echo n)"
  RECORDING="$v"
  if [[ "$RECORDING" == true ]]; then
    ask RECORDING_DAYS "Mbaji incizimet sa ditë" "${RECORDING_DAYS:-30}" '^[0-9]{1,4}$'
  fi

  if [[ -z "${JSON_SECRET_KEY:-}" ]]; then
    JSON_SECRET_KEY="$(openssl rand -hex 16)"
  else
    ask_yn v "Mbaj JSON Secret Key ekzistues (përndryshe duhet ndryshuar edhe te Frappe)?" y
    [[ "$v" == true ]] || JSON_SECRET_KEY="$(openssl rand -hex 16)"
  fi

  save_env
  ok "Konfigurimi u ruajt: $ENV_FILE"
}

save_env() {
  write_env "$ENV_FILE" DOMAIN SERVER_LAN_IP ALLOW_CIDRS HTTPS_PORT LOCAL_PORT TLS_MODE ACME_EMAIL CF_TOKEN \
    ACME_DNS_PLUGIN ACME_DNS_ENV OWN_FULLCHAIN OWN_PRIVKEY LOGIN_SERVER SESSION_TIMEOUT RECORDING \
    RECORDING_DAYS JSON_SECRET_KEY GUAC_VERSION
}

load_previous() {
  local v
  for v in DOMAIN SERVER_LAN_IP ALLOW_CIDRS HTTPS_PORT LOCAL_PORT TLS_MODE ACME_EMAIL CF_TOKEN ACME_DNS_PLUGIN \
           ACME_DNS_ENV OWN_FULLCHAIN OWN_PRIVKEY LOGIN_SERVER SESSION_TIMEOUT RECORDING RECORDING_DAYS \
           JSON_SECRET_KEY; do
    printf -v "$v" '%s' "$(env_get "$ENV_FILE" "$v")"
  done
}

# ---------------------------- instalimi --------------------------------------
install_packages() {
  say "Paketat (Docker, openssl, curl)..."
  local miss=()
  command -v curl >/dev/null || miss+=(curl)
  command -v openssl >/dev/null || miss+=(openssl)
  command -v python3 >/dev/null || miss+=(python3)
  command -v ss >/dev/null || miss+=(iproute2)
  command -v docker >/dev/null || miss+=(docker.io)
  if (( ${#miss[@]} )); then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${miss[@]}" ca-certificates >/dev/null
  fi
  if ! docker compose version >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
      || die "S'u instalua 'docker compose'. Instalo Docker sipas https://docs.docker.com/engine/install/ubuntu/"
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || die "Docker s'po punon (systemctl status docker)."
  ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) · $(docker compose version --short 2>/dev/null)"
}

ts_running() {
  command -v tailscale >/dev/null 2>&1 || return 1
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("BackendState")=="Running" else 1)' 2>/dev/null
}

# LAN-i i zyrës brenda 100.64.0.0/10 (p.sh. 100.100.100.0/24)? Atëherë rregulli anti-spoofing i Tailscale
# (iptables: DROP për 100.64.0.0/10 që s'vjen nga tailscale0) e bllokon krejt LAN-in → SSH/web s'punojnë.
lan_in_cgnat() {
  python3 - "$SERVER_LAN_IP ${ALLOW_CIDRS//,/ }" <<'PY' >/dev/null 2>&1
import ipaddress, sys
cg = ipaddress.ip_network("100.64.0.0/10")
items = [x for x in sys.argv[1].split() if x]
sys.exit(0 if any(ipaddress.ip_network(x, strict=False).overlaps(cg) for x in items) else 1)
PY
}

setup_tailscale() {
  say "Tailscale → Headscale"
  if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh >/dev/null || die "Instalimi i Tailscale dështoi."
  fi
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
  local host nf=()
  host="okremote-$(hostname -s | tr -cd 'a-zA-Z0-9-' | cut -c1-40)"
  if lan_in_cgnat; then
    warn "LAN-i i këtij serveri (${SERVER_LAN_IP}) është brenda 100.64.0.0/10 — i njëjti range si Tailscale."
    warn "Përdor --netfilter-mode=off, përndryshe Tailscale e bllokon SSH/HTTPS nga zyra."
    nf=(--netfilter-mode=off)
  fi
  if ts_running; then
    if (( ${#nf[@]} )); then
      tailscale up --reset --login-server="${LOGIN_SERVER%/}" --accept-routes --accept-dns=false "${nf[@]}" \
        --hostname="$host" || die "tailscale up --netfilter-mode=off dështoi"
      ok "Tailscale: --accept-routes, --netfilter-mode=off"
    else
      # --accept-routes: rrugët 10.50.0.0/15 nga hub-et; --accept-dns=false: mos e prek DNS-in e serverit
      tailscale set --accept-routes=true --accept-dns=false >/dev/null 2>&1 \
        || warn "S'u vendos --accept-routes me 'tailscale set' — kontrollo me: tailscale debug prefs"
      ok "Tailscale ishte i lidhur; u sigurua --accept-routes"
    fi
  else
    [[ -n "${TS_AUTHKEY:-}" ]] || die "Tailscale s'është i lidhur dhe s'ka auth key — xhiroje me --reconfigure."
    tailscale up --login-server="${LOGIN_SERVER%/}" --authkey="$TS_AUTHKEY" --accept-routes --accept-dns=false \
      "${nf[@]}" --hostname="$host" \
      || die "tailscale up dështoi (key i skaduar? krijo të ri: sudo hs-authkey support-team 1h)"
    ok "Tailscale u lidh me ${LOGIN_SERVER}"
  fi
  for _ in 1 2 3 4 5 6; do
    if ping -c1 -W2 "$HUB_TEST_IP" >/dev/null 2>&1; then ok "Hub-i ($HUB_TEST_IP) arrihet përmes VPN-së"; return 0; fi
    sleep 2
  done
  warn "Hub-i ($HUB_TEST_IP) s'u arrit. Kontrollo te Headscale: 'sudo hs-status' (rrugët 10.50.0.0/15 të aprovuara)"
  warn "dhe që ky server është në user-in support-team (ACL group:admins)."
}

make_certs() {
  say "Certifikata HTTPS"
  install -d -m 700 "$CERT_DIR"
  case "$TLS_MODE" in
    1)
      if [[ -s "$CERT_DIR/fullchain.pem" && -s "$CERT_DIR/.selfsigned" ]] \
         && [[ "$(cat "$CERT_DIR/.selfsigned")" == "$DOMAIN $SERVER_LAN_IP" ]] \
         && openssl x509 -checkend 2592000 -noout -in "$CERT_DIR/fullchain.pem" >/dev/null 2>&1; then
        ok "Certifikata self-signed ekzistuese vlen ende"; return 0
      fi
      openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
        -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" \
        -subj "/CN=$DOMAIN/O=okremote" \
        -addext "subjectAltName=DNS:$DOMAIN,IP:$SERVER_LAN_IP" >/dev/null 2>&1 \
        || die "openssl s'krijoi certifikatën"
      echo "$DOMAIN $SERVER_LAN_IP" > "$CERT_DIR/.selfsigned"
      ok "Self-signed për $DOMAIN / $SERVER_LAN_IP (vlen 825 ditë)"
      ;;
    2|3)
      rm -f "$CERT_DIR/.selfsigned"
      local acme_home="$BASE/acme" plugin env_args=()
      if [[ ! -x "$acme_home/acme.sh" ]]; then
        rm -rf /tmp/okremote-acme && mkdir -p /tmp/okremote-acme
        curl -fsSL https://github.com/acmesh-official/acme.sh/archive/refs/heads/master.tar.gz \
          | tar -xz -C /tmp/okremote-acme --strip-components=1 || die "S'u shkarkua acme.sh"
        ( cd /tmp/okremote-acme && ./acme.sh --install --home "$acme_home" --accountemail "$ACME_EMAIL" >/dev/null ) \
          || die "Instalimi i acme.sh dështoi"
      fi
      if [[ "$TLS_MODE" == 2 ]]; then
        plugin=dns_cf; env_args=("CF_Token=$CF_TOKEN")
      else
        plugin="$ACME_DNS_PLUGIN"; read -r -a env_args <<< "$ACME_DNS_ENV"
      fi
      env "${env_args[@]}" "$acme_home/acme.sh" --home "$acme_home" --issue --server letsencrypt \
        --dns "$plugin" -d "$DOMAIN" --keylength ec-256 >/tmp/okremote-acme.log 2>&1 \
        || grep -q "Domains not changed\|Skipping. Next renewal" /tmp/okremote-acme.log \
        || die "Let's Encrypt dështoi — shih /tmp/okremote-acme.log"
      "$acme_home/acme.sh" --home "$acme_home" --install-cert -d "$DOMAIN" --ecc \
        --key-file "$CERT_DIR/privkey.pem" --fullchain-file "$CERT_DIR/fullchain.pem" \
        --reloadcmd "docker exec okremote-nginx-1 nginx -s reload >/dev/null 2>&1 || true" >/dev/null \
        || die "S'u instalua certifikata"
      ok "Let's Encrypt për $DOMAIN (rinovohet vetë nga acme.sh)"
      ;;
    4)
      rm -f "$CERT_DIR/.selfsigned"
      install -m 644 "$OWN_FULLCHAIN" "$CERT_DIR/fullchain.pem"
      install -m 600 "$OWN_PRIVKEY" "$CERT_DIR/privkey.pem"
      ok "Certifikata jote u kopjua"
      ;;
  esac
  chmod 600 "$CERT_DIR/privkey.pem"
}

write_files() {
  say "Konfigurimi i Guacamole / nginx"
  install -d -m 750 "$BASE"
  install -d -m 700 "$REC_DIR"
  cat > "$BASE/docker-compose.yml" <<'YAML'
# gjeneruar nga guacamole.sh — ndryshimet: sudo bash guacamole.sh --reconfigure
name: okremote
services:
  guacd:
    image: guacamole/guacd:${GUAC_VERSION}
    restart: unless-stopped
    volumes:
      - ./recordings:/recordings
  guacamole:
    image: guacamole/guacamole:${GUAC_VERSION}
    restart: unless-stopped
    depends_on: [guacd]
    environment:
      GUACD_HOSTNAME: guacd
      GUACD_PORT: "4822"
      JSON_SECRET_KEY: ${JSON_SECRET_KEY}
      WEBAPP_CONTEXT: ROOT
      API_SESSION_TIMEOUT: "${SESSION_TIMEOUT}"
    ports:
      - "127.0.0.1:${LOCAL_PORT}:8080"
  nginx:
    image: NGINX_IMAGE_PLACEHOLDER
    restart: unless-stopped
    depends_on: [guacamole]
    ports:
      - "${HTTPS_PORT}:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
YAML
  sed -i "s|NGINX_IMAGE_PLACEHOLDER|$NGINX_IMAGE|" "$BASE/docker-compose.yml"

  local allow="" c
  for c in ${ALLOW_CIDRS//,/ }; do allow+="    allow ${c};"$'\n'; done
  cat > "$BASE/nginx.conf" <<NGINX
# gjeneruar nga guacamole.sh — ndryshimet: sudo bash guacamole.sh --reconfigure
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;
    http2 on;
    server_name _;
    server_tokens off;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;

    # Vetëm rrjeti lokal (Docker i anashkalon ufw → kufizimi bëhet këtu)
${allow}    allow 127.0.0.1;
    deny all;

    client_max_body_size 1g;      # ngarkim file-sh me SFTP
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy no-referrer always;

    # Guacamole revokon sesionin e vjetër kur hapet një lidhje e re në tab tjetër (DELETE /api/session).
    # Kjo do ta mbyllte lidhjen e parë → këtu injorohet; sesionet mbyllen vetë pas mosaktivitetit.
    location = /api/session {
        if (\$request_method = DELETE) { return 204; }
        proxy_pass http://guacamole:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass http://guacamole:8080;
        proxy_buffering off;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 12h;
        proxy_send_timeout 12h;
        access_log off;
    }
}
NGINX
  ok "$BASE/docker-compose.yml, $BASE/nginx.conf"
}

start_stack() {
  say "Nis Guacamole (herën e parë shkarkohen imazhet, ~1-2 min)"
  compose pull -q || die "S'u shkarkuan imazhet Docker (a ka internet serveri drejt Docker Hub?)"
  compose up -d --remove-orphans || die "docker compose up dështoi — shih: docker compose -f $BASE/docker-compose.yml logs"
  local uid gid
  if uid="$(compose exec -T guacd id -u 2>/dev/null)" && gid="$(compose exec -T guacd id -g 2>/dev/null)"; then
    chown "$(trim "$uid"):$(trim "$gid")" "$REC_DIR" && chmod 700 "$REC_DIR"
  fi
  compose exec -T nginx nginx -t >/dev/null 2>&1 || die "Konfigurimi i nginx ka gabim: docker exec okremote-nginx-1 nginx -t"
  compose exec -T nginx nginx -s reload >/dev/null 2>&1 || true
  local code=""
  for _ in $(seq 1 60); do
    code="$(curl -s --noproxy '*' --max-time 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LOCAL_PORT}/" || true)"
    [[ "$code" == 200 ]] && break
    sleep 2
  done
  [[ "$code" == 200 ]] || die "Guacamole s'u nis (HTTP ${code:-pa përgjigje}). Log-et: docker logs okremote-guacamole-1"
  ok "Guacamole punon (127.0.0.1:${LOCAL_PORT})"
}

# Test i plotë i guacamole-auth-json me openssl (i njëjti format që përdor Frappe)
selftest() {
  local json data resp token
  # expires në milisekonda (+ numër i rastit): dy teste në të njëjtën sekondë s'duhet të duken si "replay"
  json="{\"username\":\"okremote-selftest-${RANDOM}\",\"expires\":$(( $(date +%s%3N) + 60000 )),\"singleUse\":true,\"connections\":{}}"
  data="$( { printf '%s' "$json" | openssl dgst -sha256 -mac HMAC -macopt "hexkey:$JSON_SECRET_KEY" -binary
             printf '%s' "$json"; } \
           | openssl enc -aes-128-cbc -K "$JSON_SECRET_KEY" -iv 00000000000000000000000000000000 | base64 -w0 )"
  resp="$(curl -s --noproxy '*' --max-time 15 --data-urlencode "data=$data" "http://127.0.0.1:${LOCAL_PORT}/api/tokens" || true)"
  token="$(printf '%s' "$resp" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("authToken",""))
except Exception: print("")' 2>/dev/null)"
  if [[ -n "$token" ]]; then
    curl -s --noproxy '*' -o /dev/null -X DELETE "http://127.0.0.1:${LOCAL_PORT}/api/tokens/$token" || true
    ok "JSON auth punon (i njëjti çelës si te Frappe)"
  else
    warn "JSON auth s'u pranua: ${resp:0:200}"
    warn "Log-et: docker logs okremote-guacamole-1 | tail -50"
    return 1
  fi
  local https_code
  https_code="$(curl -sk --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' --resolve "${DOMAIN}:${HTTPS_PORT}:${SERVER_LAN_IP}" \
                "https://${DOMAIN}:${HTTPS_PORT}/" || true)"
  case "$https_code" in
    200) ok "HTTPS (nginx) punon: https://${DOMAIN}:${HTTPS_PORT}" ;;
    403) warn "HTTPS ktheu 403: IP-ja ${SERVER_LAN_IP} s'është te 'Rrjetet e lejuara' (${ALLOW_CIDRS})" ;;
    *)   warn "HTTPS ktheu ${https_code:-asgjë} — docker logs okremote-nginx-1" ;;
  esac
}

setup_cron() {
  local f=/etc/cron.d/okremote
  if [[ "$RECORDING" == true ]]; then
    printf '%s\n' "# fshin incizimet më të vjetra se ${RECORDING_DAYS} ditë (guacamole.sh)" \
      "17 3 * * * root find $REC_DIR -type f -mtime +${RECORDING_DAYS} -delete" > "$f"
    chmod 644 "$f"
  else
    rm -f "$f"
  fi
}

summary() {
  local url="https://${DOMAIN}"
  [[ "$HTTPS_PORT" == 443 ]] || url+=":${HTTPS_PORT}"
  echo
  echo "================================================================================"
  echo " GUACAMOLE GATI"
  echo "================================================================================"
  echo " 1) DNS: rekordi A   ${DOMAIN}  →  ${SERVER_LAN_IP}"
  echo "    (te DNS-i i domain-it ose te DNS-i/router-i i zyrës)"
  echo
  echo " 2) Frappe → WireGuard VPN Settings → Qasja nga browser-i:"
  echo "      Aktivizo butonat 'Lidhu'                ✓"
  echo "      Adresa e Guacamole për punëtorët        ${url}"
  echo "      Adresa e Guacamole nga serveri Frappe   http://127.0.0.1:${LOCAL_PORT}"
  echo "      JSON Secret Key                         ${JSON_SECRET_KEY}"
  echo "      Incizo sesionet                         $([[ "$RECORDING" == true ]] && echo '✓' || echo '—')"
  echo "    → Save → Testo → Testo Guacamole"
  echo "    (nëse Frappe është në server tjetër: adresa e brendshme = ${url}, pa 'Verifiko TLS' për self-signed)"
  echo
  echo " 3) Punëtorët: roli 'VPN Remote Access' (ose VPN Manager) → VPN Dashboard → Lidhu"
  [[ "$TLS_MODE" == 1 ]] && echo "    Certifikata është self-signed: herën e parë browser-i pyet → Advanced → Proceed."
  echo
  echo " Komanda:  sudo bash $0 --status   ·   docker logs okremote-guacamole-1"
  echo "================================================================================"
}

status() {
  [[ -f "$ENV_FILE" ]] || die "S'është instaluar ende."
  load_previous
  say "Kontejnerët"; compose ps
  say "Tailscale"; tailscale status 2>/dev/null | head -5 || warn "tailscale s'po punon"
  ping -c1 -W2 "$HUB_TEST_IP" >/dev/null 2>&1 && ok "Hub-i $HUB_TEST_IP arrihet" || warn "Hub-i $HUB_TEST_IP s'arrihet"
  say "Certifikata"; openssl x509 -noout -subject -enddate -in "$CERT_DIR/fullchain.pem" 2>/dev/null || true
  say "JSON auth"; selftest || true
}

main() {
  local reconf=false a
  for a in "$@"; do
    case "$a" in
      --reconfigure) reconf=true ;;
      --status) status; exit 0 ;;
      -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
      *) die "Argument i panjohur: $a   (përdorimi: sudo bash $0 [--reconfigure|--status])" ;;
    esac
  done
  preflight
  install_packages
  install -d -m 750 "$BASE"
  if [[ ! -f "$ENV_FILE" || "$reconf" == true ]]; then
    DOMAIN="" SERVER_LAN_IP="" ALLOW_CIDRS="" HTTPS_PORT="" LOCAL_PORT="" TLS_MODE="" ACME_EMAIL="" CF_TOKEN=""
    ACME_DNS_PLUGIN="" ACME_DNS_ENV="" OWN_FULLCHAIN="" OWN_PRIVKEY="" LOGIN_SERVER="" SESSION_TIMEOUT=""
    RECORDING="" RECORDING_DAYS="" JSON_SECRET_KEY="" TS_AUTHKEY=""
    [[ -f "$ENV_FILE" ]] && load_previous
    wizard
  else
    say "Përdor konfigurimin ekzistues $ENV_FILE   (ndryshimet: sudo bash $0 --reconfigure)"
    load_previous
    TS_AUTHKEY=""
    save_env                      # GUAC_VERSION i ri pas përditësimit të script-it
  fi
  setup_tailscale
  make_certs
  write_files
  start_stack
  selftest || warn "Testi i JSON auth dështoi — shih mesazhet më sipër."
  setup_cron
  summary
}

main "$@"
