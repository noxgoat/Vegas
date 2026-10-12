#!/usr/bin/env bash
# Instala o JurisBusca SP em um servidor Ubuntu (ex.: Oracle Cloud Always Free, região São Paulo).
# Execute DENTRO da pasta juris-busca descompactada:   sudo bash deploy/instalar.sh
# Opcional: sudo DOMINIO=meusite.duckdns.org bash deploy/instalar.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Execute com sudo."; exit 1; fi
cd "$(dirname "$0")"

echo "==> Instalando Docker (se necessário)"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi

echo "==> Liberando as portas 80 e 443 no firewall do sistema"
# As imagens Ubuntu da Oracle bloqueiam portas via iptables por padrão.
for p in 80 443; do
  iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || iptables -I INPUT 6 -p tcp --dport "$p" -j ACCEPT
done
if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save; fi

if [ ! -f .env ]; then
  IP="$(curl -fsS https://api.ipify.org || true)"
  if [ -z "${DOMINIO:-}" ]; then
    if [ -z "$IP" ]; then echo "Não consegui descobrir o IP público. Rode com DOMINIO=seu.dominio"; exit 1; fi
    DOMINIO="$(echo "$IP" | tr . -).sslip.io"
  fi
  TOKEN="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"
  cat > .env <<EOF
DOMINIO=$DOMINIO
JURIS_APP_TOKEN=$TOKEN
JURIS_AGENDADOR=1
JURIS_OCR=1
# Para o resumo por IA (opcional):
ANTHROPIC_API_KEY=
EOF
  chmod 600 .env
  echo "==> Configuração criada em deploy/.env"
fi

mkdir -p dados
chown 999:999 dados   # usuário 'juris' do contêiner
chmod 700 dados

echo "==> Construindo e iniciando (pode levar alguns minutos)"
docker compose up -d --build

set -a; . ./.env; set +a
echo
echo "============================================================"
echo " Pronto! Acesse:  https://$DOMINIO"
echo " Token de acesso: $JURIS_APP_TOKEN"
echo " (guarde o token; ele também está em deploy/.env)"
echo " O certificado HTTPS pode levar 1-2 minutos na primeira vez."
echo "============================================================"
