#!/bin/bash
# VNF de monitorização (vnf-mon)
#
# Uso (no terminal da VM, com a sessão CORE a correr):
#   sudo ./scripts/vnf-mon.sh on [nome]  -> cria as regras de contagem e começa a gravar
#                                          results/mon_<nome>.csv (por omissão, data e hora)
#   sudo ./scripts/vnf-mon.sh off        -> pára a gravação e remove as regras
#   sudo ./scripts/vnf-mon.sh status     -> contadores atuais e últimas linhas do CSV
#
# Mede o sentido cliente -> servidor: pacotes que saem pela eth1 (para o r2).

MODO="$1"
NO=vnf-mon
IF=eth1

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
MONITOR="$REPO/vnfs/monitor/monitor.py"
mkdir -p "$REPO/results"

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

# Executa um comando dentro do nó vnf-mon
mon() {
    vcmd -c "$SESSAO/$NO" -- "$@"
}

parar() {
    mon pkill -f "vnfs/monitor/monitor.py" 2>/dev/null
    mon iptables -F FORWARD
    mon iptables -Z FORWARD
}

case "$MODO" in
on)
    parar

    # Regras sem alvo (-j): não alteram nada, só contam os pacotes de cada classe
    mon iptables -A FORWARD -o $IF -m dscp --dscp 46   # EF
    mon iptables -A FORWARD -o $IF -m dscp --dscp 26   # AF31
    mon iptables -A FORWARD -o $IF -m dscp --dscp 0    # DF

    NOME="${2:-$(date +%Y%m%d_%H%M%S)}"
    CSV="$REPO/results/mon_$NOME.csv"

    # Arranca o monitor dentro do nó (1 amostra por segundo).
    # O vcmd fica a correr em segundo plano na VM e mantém o monitor vivo dentro do nó;
    # as mensagens de erro, se houver, vão para results/mon_<nome>.log
    LOG="$REPO/results/mon_$NOME.log"
    setsid nohup vcmd -c "$SESSAO/$NO" -- python3 "$MONITOR" "$CSV" 1 > "$LOG" 2>&1 < /dev/null &

    # Confirma ao fim de 2 segundos que o CSV foi criado
    sleep 2
    if [ -f "$CSV" ]; then
        echo "vnf-mon ligada, a gravar em results/mon_$NOME.csv"
    else
        echo "ERRO: o monitor não arrancou. Mensagens em results/mon_$NOME.log:"
        cat "$LOG"
        exit 1
    fi
    ;;
off)
    parar
    # Os ficheiros são criados pelo root: devolve-os ao utilizador normal
    [ -n "$SUDO_USER" ] && chown -R "$SUDO_USER" "$REPO/results"
    echo "vnf-mon desligada"
    ;;
status)
    mon iptables -nvx -L FORWARD
    ULTIMO=$(ls -t "$REPO"/results/mon_*.csv 2>/dev/null | head -n 1)
    if [ -n "$ULTIMO" ]; then
        echo "=== Últimas linhas de $(basename "$ULTIMO") ==="
        tail -n 6 "$ULTIMO"
    fi
    ;;
*)
    echo "Uso: sudo $0 on [nome] | off | status"
    exit 1
    ;;
esac
