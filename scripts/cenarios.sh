#!/bin/bash
# Corre os três cenários de teste com o mesmo tráfego e grava os resultados.
#
# Uso (no terminal da VM, com a sessão CORE a correr):
#   sudo ./scripts/cenarios.sh [nome]
#
# Cenários:
#   bypass -> r1 -> r2 direto, sem VNFs            (linha de base)
#   off    -> pela cadeia, VNFs sem regras          (custo de atravessar a cadeia)
#   on     -> pela cadeia, VNFs todas ligadas       (efeito dos mecanismos de QoS)
#
# Tráfego em cada cenário (igual nos três):
#   t = 0 s   VoIP  : ping a cada 0,2 s do c-voip para o srv-voip, durante 35 s
#   t = 0 s   vídeo : iperf3 UDP 8 Mbit/s do c-video para o srv-video, durante 30 s
#   t = 5 s   bulk  : iperf3 TCP com 4 fluxos do c-bulk para o srv-bulk, durante 20 s
#   antes de tudo: 3 pings do atk para verificar a firewall
#
# Resultados em results/<nome>/<cenário>/  e gráficos em results/<nome>/

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
NOME="${1:-$(date +%Y%m%d_%H%M%S)}"
OUT="$REPO/results/$NOME"

PORTA=5301          # porta própria, para não chocar com iperf3 -s abertos à mão
DUR_PING=35
DUR_VIDEO=30
DUR_BULK=20
ATRASO_BULK=5
VIDEO_BW=8M

# Procura a sessão CORE ativa com esta topologia
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
    exit 1
fi

# Executa um comando dentro de um nó
no() {
    local N="$1"; shift
    vcmd -c "$SESSAO/$N" -- "$@"
}

parar_servidores() {
    for s in srv-video srv-bulk; do
        no $s pkill -f "iperf3 -s -p $PORTA" 2>/dev/null
    done
}

mkdir -p "$OUT"
echo "Resultados em results/$NOME"

# Servidores iperf3 nos nós de destino (ficam a correr durante os três cenários)
parar_servidores
no srv-video iperf3 -s -p $PORTA > /dev/null 2>&1 &
no srv-bulk  iperf3 -s -p $PORTA > /dev/null 2>&1 &
sleep 1

for C in bypass off on; do
    echo ""
    echo "=== Cenário $C ==="
    mkdir -p "$OUT/$C"

    # Prepara o cenário (rotas e VNFs)
    "$DIR/iniciar.sh" $C > /dev/null || exit 1
    sleep 2

    # Firewall: o atk consegue chegar ao servidor?
    if no atk ping -c 3 -W 1 10.0.20.10 > "$OUT/$C/atk.txt" 2>&1; then
        echo "atk -> srv-voip: passou"
    else
        echo "atk -> srv-voip: bloqueado"
    fi

    # Começa a gravar na vnf-mon
    "$DIR/vnf-mon.sh" on "${NOME}_$C" > /dev/null || exit 1

    # Tráfego
    echo "A gerar tráfego durante ${DUR_PING} s..."
    no c-voip ping -i 0.2 -w $DUR_PING -D 10.0.20.10 > "$OUT/$C/ping.txt" 2>&1 &
    P1=$!
    no c-video iperf3 -c 10.0.20.20 -p $PORTA -u -b $VIDEO_BW -t $DUR_VIDEO -J > "$OUT/$C/video.json" 2>&1 &
    P2=$!
    sleep $ATRASO_BULK
    no c-bulk iperf3 -c 10.0.20.30 -p $PORTA -t $DUR_BULK -P 4 -J > "$OUT/$C/bulk.json" 2>&1 &
    P3=$!
    wait $P1 $P2 $P3

    # Pára a gravação e guarda o CSV junto dos outros resultados
    "$DIR/vnf-mon.sh" off > /dev/null
    mv "$REPO/results/mon_${NOME}_$C.csv" "$OUT/$C/mon.csv" 2>/dev/null
    mv "$REPO/results/mon_${NOME}_$C.log" "$OUT/$C/mon.log" 2>/dev/null
    echo "Cenário $C concluído"
done

parar_servidores

# Deixa a topologia no cenário 3 (tudo ligado)
"$DIR/iniciar.sh" on > /dev/null

# Análise e gráficos
echo ""
python3 "$DIR/analisar.py" "$OUT"
python3 "$DIR/grafico_mon.py" "$OUT/on/mon.csv" > /dev/null

# Os ficheiros foram criados pelo root: devolve-os ao utilizador normal
[ -n "$SUDO_USER" ] && chown -R "$SUDO_USER" "$REPO/results"
