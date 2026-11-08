#!/bin/bash
# VNF de policiamento e agendamento (vnf-pol)
#
# Uso (no terminal da VM, com a sessão CORE a correr):
#   sudo ./scripts/vnf-pol.sh on      -> aplica o HTB com as três classes
#   sudo ./scripts/vnf-pol.sh off     -> remove tudo (a VNF só reencaminha)
#   sudo ./scripts/vnf-pol.sh status  -> estatísticas por classe (bytes, pacotes, perdas)
#
# Atua na interface eth1 (saída para vnf-mon, sentido cliente -> servidor).
#
# Estrutura (HTB = Hierarchical Token Bucket):
#
#   1:1  total 19 Mbit/s (abaixo dos 20 Mbit/s do estrangulamento, para a fila formar-se aqui)
#    ├── 1:10 EF   (VoIP)   garante 2 Mbit/s,  máximo 2 Mbit/s,  prioridade 0 (a mais alta)
#    ├── 1:20 AF31 (vídeo)  garante 10 Mbit/s, máximo 19 Mbit/s, prioridade 1
#    └── 1:30 DF   (bulk)   garante 7 Mbit/s,  máximo 19 Mbit/s, prioridade 2 (a mais baixa)
#
# Policiamento: o tráfego EF acima de 2 Mbit/s é descartado logo no filtro (token bucket),
# para que uma classe prioritária nunca possa abafar as outras.

MODO="$1"
NO=vnf-pol
IF=eth1

TOTAL=19mbit
EF_RATE=2mbit
AF_RATE=10mbit
DF_RATE=7mbit

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

# Executa um comando dentro do nó vnf-pol
pol() {
    vcmd -c "$SESSAO/$NO" -- "$@"
}

limpar() {
    pol tc qdisc del dev $IF root 2>/dev/null
}

case "$MODO" in
on)
    limpar

    # 1. Raiz HTB. O que não for classificado vai para a classe 1:30 (DF)
    pol tc qdisc add dev $IF root handle 1: htb default 30 r2q 100

    # 2. Classe pai com o débito total
    pol tc class add dev $IF parent 1: classid 1:1 htb rate $TOTAL ceil $TOTAL

    # 3. Classes filhas: rate = débito garantido, ceil = máximo que pode pedir emprestado
    pol tc class add dev $IF parent 1:1 classid 1:10 htb rate $EF_RATE ceil $EF_RATE prio 0
    pol tc class add dev $IF parent 1:1 classid 1:20 htb rate $AF_RATE ceil $TOTAL  prio 1
    pol tc class add dev $IF parent 1:1 classid 1:30 htb rate $DF_RATE ceil $TOTAL  prio 2

    # 4. Fila dentro de cada classe
    #    EF: fila curta (pfifo) -> se encher, descarta em vez de atrasar
    #    AF31 e DF: SFQ reparte a fila de forma justa entre fluxos
    pol tc qdisc add dev $IF parent 1:10 handle 10: pfifo limit 20
    pol tc qdisc add dev $IF parent 1:20 handle 20: sfq perturb 10
    pol tc qdisc add dev $IF parent 1:30 handle 30: sfq perturb 10

    # 5. Filtros: leem o byte TOS (DSCP << 2) que a vnf-cls marcou
    #    EF   = 46 -> 0xb8 (com policiamento: acima de 2 Mbit/s é descartado)
    #    AF31 = 26 -> 0x68
    pol tc filter add dev $IF parent 1: protocol ip prio 1 u32 \
        match ip dsfield 0xb8 0xfc \
        police rate $EF_RATE burst 20k drop flowid 1:10
    pol tc filter add dev $IF parent 1: protocol ip prio 2 u32 \
        match ip dsfield 0x68 0xfc flowid 1:20

    echo "vnf-pol ligada"
    ;;
off)
    limpar
    echo "vnf-pol desligada (só reencaminha)"
    ;;
status)
    echo "=== Classes (débito enviado, pacotes, descartes) ==="
    pol tc -s class show dev $IF
    echo "=== Filtros (inclui o policiamento EF) ==="
    pol tc -s filter show dev $IF
    ;;
*)
    echo "Uso: sudo $0 on|off|status"
    exit 1
    ;;
esac
