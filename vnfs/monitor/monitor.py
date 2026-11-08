#!/usr/bin/env python3
"""
VNF de monitorização (corre DENTRO do nó vnf-mon).

A cada intervalo lê os contadores das regras iptables que contam os pacotes
por classe DSCP e escreve no CSV o débito de cada classe nesse intervalo.

Uso: python3 monitor.py <ficheiro.csv> [intervalo_em_segundos]
"""
import re
import signal
import subprocess
import sys
import time

# Valor DSCP (como o iptables o mostra) -> nome da classe
CLASSES = {"0x2e": "EF", "0x1a": "AF31", "0x00": "DF"}


def ler_contadores():
    """Devolve {classe: (pacotes, bytes)} lidos da cadeia FORWARD."""
    saida = subprocess.run(
        ["iptables", "-nvx", "-L", "FORWARD"], capture_output=True, text=True
    ).stdout
    contadores = {}
    for linha in saida.splitlines():
        m = re.search(r"DSCP match (0x[0-9a-f]+)", linha)
        if m and m.group(1) in CLASSES:
            campos = linha.split()
            contadores[CLASSES[m.group(1)]] = (int(campos[0]), int(campos[1]))
    return contadores


def main():
    ficheiro = sys.argv[1]
    intervalo = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    # Ao receber SIGTERM (vnf-mon.sh off) termina de forma limpa
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    with open(ficheiro, "w") as f:
        f.write("tempo_s,classe,pacotes,bytes,debito_mbps\n")
        f.flush()

        inicio = time.time()
        anterior = ler_contadores()
        while True:
            time.sleep(intervalo)
            atual = ler_contadores()
            t = round(time.time() - inicio, 1)
            for classe in ("EF", "AF31", "DF"):
                p0, b0 = anterior.get(classe, (0, 0))
                p1, b1 = atual.get(classe, (0, 0))
                dp, db = p1 - p0, b1 - b0
                mbps = db * 8 / intervalo / 1e6
                f.write(f"{t},{classe},{dp},{db},{mbps:.3f}\n")
            f.flush()
            anterior = atual


if __name__ == "__main__":
    main()
