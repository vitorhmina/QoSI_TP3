#!/bin/bash
# Configura o encaminhamento da topologia top_2.xml (CORE).
#
# Uso (no terminal da VM, com a sessão CORE já a correr):
#   sudo ./scripts/rotas.sh cadeia   -> tráfego clientes <-> servidores passa pelas VNFs
#   sudo ./scripts/rotas.sh bypass   -> tráfego vai direto r1 <-> r2 (linha de base, sem VNFs)
#
# Só os routers r1 e r2 mudam entre os dois modos. As VNFs têm sempre
# as suas rotas configuradas, mas no modo bypass simplesmente não recebem tráfego.

MODO="$1"
if [ "$MODO" != "cadeia" ] && [ "$MODO" != "bypass" ]; then
    echo "Uso: sudo $0 cadeia|bypass"
    exit 1
fi

# Cada sessão do CORE cria uma pasta /tmp/pycore.<número> com um socket por nó.
# Procuramos, da mais recente para a mais antiga, a sessão que tem o nó r1 desta topologia.
SESSAO=""
for d in $(ls -td /tmp/pycore.* 2>/dev/null); do
    if [ -S "$d/r1" ] && [ -S "$d/vnf-fw" ]; then
        SESSAO="$d"
        break
    fi
done
if [ -z "$SESSAO" ]; then
    echo "Não encontrei nenhuma sessão CORE com a topologia top_2 a correr."
    echo "Confirma que abriste o top_2.xml e carregaste no play (ícone verde)."
    echo "Sessões existentes:"; ls -d /tmp/pycore.* 2>/dev/null || echo "  nenhuma"
    exit 1
fi

# Executa um comando dentro de um nó do CORE
no() {
    local NO="$1"; shift
    vcmd -c "$SESSAO/$NO" -- "$@"
}

CLIENTES=10.0.1.0/24
SERVIDORES=10.0.20.0/24

# Rotas das VNFs: cada uma envia para a VNF seguinte (ida) e para a anterior (volta)
#            nó        ida (para servidores)   volta (para clientes)
no vnf-fw  ip route replace $SERVIDORES via 10.0.100.6
no vnf-fw  ip route replace $CLIENTES   via 10.0.100.1
no vnf-cls ip route replace $SERVIDORES via 10.0.100.10
no vnf-cls ip route replace $CLIENTES   via 10.0.100.5
no vnf-pol ip route replace $SERVIDORES via 10.0.100.14
no vnf-pol ip route replace $CLIENTES   via 10.0.100.9
no vnf-mon ip route replace $SERVIDORES via 10.0.100.18
no vnf-mon ip route replace $CLIENTES   via 10.0.100.13

if [ "$MODO" = "cadeia" ]; then
    no r1 ip route replace $SERVIDORES via 10.0.100.2    # r1 -> vnf-fw
    no r2 ip route replace $CLIENTES   via 10.0.100.17   # r2 -> vnf-mon (volta)
else
    no r1 ip route replace $SERVIDORES via 10.0.200.2    # r1 -> r2 pelo bypass
    no r2 ip route replace $CLIENTES   via 10.0.200.1    # r2 -> r1 pelo bypass
fi

echo "Modo $MODO ativo (sessão $SESSAO)"
echo "r1: $(no r1 ip route get 10.0.20.10 | head -n 1)"
echo "r2: $(no r2 ip route get 10.0.1.10 | head -n 1)"
