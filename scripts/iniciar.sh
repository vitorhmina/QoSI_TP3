#!/bin/bash
# Prepara a topologia depois de carregar no play no CORE.
# As rotas e as regras das VNFs perdem-se sempre que a sessão do CORE é reiniciada,
# por isso este script volta a aplicar tudo de uma vez.
#
# Uso:
#   sudo ./scripts/iniciar.sh          -> cadeia com as VNFs ligadas (cenário 3)
#   sudo ./scripts/iniciar.sh off      -> cadeia com as VNFs desligadas (cenário 2)
#   sudo ./scripts/iniciar.sh bypass   -> sem passar pelas VNFs (cenário 1)

DIR="$(cd "$(dirname "$0")" && pwd)"
MODO="${1:-on}"

case "$MODO" in
on)
    "$DIR/rotas.sh" cadeia || exit 1
    "$DIR/vnf-fw.sh" on
    "$DIR/vnf-cls.sh" on
    "$DIR/vnf-pol.sh" on
    ;;
off)
    "$DIR/rotas.sh" cadeia || exit 1
    "$DIR/vnf-fw.sh" off
    "$DIR/vnf-cls.sh" off
    "$DIR/vnf-pol.sh" off
    ;;
bypass)
    "$DIR/rotas.sh" bypass || exit 1
    ;;
*)
    echo "Uso: sudo $0 [on|off|bypass]"
    exit 1
    ;;
esac
