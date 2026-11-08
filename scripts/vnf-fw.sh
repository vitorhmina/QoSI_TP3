#!/bin/bash
# VNF de controlo de acessos (vnf-fw)
#
# Uso (no terminal da VM, com a sessão CORE a correr):
#   sudo ./scripts/vnf-fw.sh on      -> aplica as regras da firewall
#   sudo ./scripts/vnf-fw.sh off     -> limpa as regras (a VNF só reencaminha)
#   sudo ./scripts/vnf-fw.sh status  -> mostra as regras e os contadores de pacotes

MODO="$1"
NO=vnf-fw

CLIENTES=10.0.1.0/24
SERVIDORES=10.0.20.0/24
ATK=10.0.1.66

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

# Executa um comando dentro do nó vnf-fw
fw() {
    vcmd -c "$SESSAO/$NO" -- "$@"
}

limpar() {
    fw iptables -P FORWARD ACCEPT    # política por omissão volta a "deixar passar"
    fw iptables -F FORWARD           # apaga todas as regras da cadeia FORWARD
    fw iptables -Z FORWARD           # põe os contadores a zero
}

case "$MODO" in
on)
    limpar

    # 1. Política por omissão: tudo o que não for explicitamente permitido é descartado
    fw iptables -P FORWARD DROP

    # 2. O atacante é descartado logo à entrada (regra própria para termos um contador só dele)
    fw iptables -A FORWARD -s $ATK -j DROP

    # 3. Respostas de ligações já aceites (ex. servidor -> cliente) podem passar
    fw iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

    # 4. Clientes podem iniciar ligações para os servidores
    fw iptables -A FORWARD -s $CLIENTES -d $SERVIDORES -j ACCEPT

    echo "vnf-fw ligada"
    ;;
off)
    limpar
    echo "vnf-fw desligada (só reencaminha)"
    ;;
status)
    fw iptables -L FORWARD -v -n --line-numbers
    ;;
*)
    echo "Uso: sudo $0 on|off|status"
    exit 1
    ;;
esac
