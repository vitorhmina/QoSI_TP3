#!/bin/bash
# VNF de classificação (vnf-cls): marca o campo DSCP de cada pacote
#
# Uso (no terminal da VM, com a sessão CORE a correr):
#   sudo ./scripts/vnf-cls.sh on      -> aplica as marcações DSCP
#   sudo ./scripts/vnf-cls.sh off     -> limpa as regras (a VNF só reencaminha)
#   sudo ./scripts/vnf-cls.sh status  -> mostra as regras e os contadores
#
# Classes (RFC 4594):
#   srv-voip  10.0.20.10 -> EF   (46)  tempo real
#   srv-video 10.0.20.20 -> AF31 (26)  multimédia
#   restante             -> DF   (0)   best effort

MODO="$1"
NO=vnf-cls

SRV_VOIP=10.0.20.10
SRV_VIDEO=10.0.20.20

# Procura a sessão CORE ativa com esta topologia
SESSAO=""
for d in $(ls -td /tmp/pycore.* 2>/dev/null); do
    if [ -S "$d/r1" ] && [ -S "$d/$NO" ]; then
        SESSAO="$d"
        break
    fi
done
if [ -z "$SESSAO" ]; then
    echo "Não encontrei nenhuma sessão CORE com a topologia top_2 a correr."
    echo "Confirma que abriste o top_2.xml e carregaste no play (ícone verde)."
    exit 1
fi

# Executa um comando dentro do nó vnf-cls
cls() {
    vcmd -c "$SESSAO/$NO" -- "$@"
}

limpar() {
    cls iptables -t mangle -F FORWARD   # apaga as regras da tabela mangle
    cls iptables -t mangle -Z FORWARD   # contadores a zero
}

case "$MODO" in
on)
    limpar

    # 1. Fronteira de confiança: tudo começa como DF (0), ignorando o que o cliente marcou
    cls iptables -t mangle -A FORWARD -j DSCP --set-dscp 0

    # 2. Tráfego para o servidor de VoIP -> EF (46)
    cls iptables -t mangle -A FORWARD -d $SRV_VOIP -j DSCP --set-dscp-class EF

    # 3. Tráfego para o servidor de vídeo -> AF31 (26)
    cls iptables -t mangle -A FORWARD -d $SRV_VIDEO -j DSCP --set-dscp-class AF31

    echo "vnf-cls ligada"
    ;;
off)
    limpar
    echo "vnf-cls desligada (só reencaminha)"
    ;;
status)
    cls iptables -t mangle -L FORWARD -v -n --line-numbers
    ;;
*)
    echo "Uso: sudo $0 on|off|status"
    exit 1
    ;;
esac
