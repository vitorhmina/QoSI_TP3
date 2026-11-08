#!/usr/bin/env python3
"""
Gráfico do débito por classe ao longo do tempo, a partir de um CSV da vnf-mon.

Uso:  python3 scripts/grafico_mon.py results/mon_teste1.csv
Gera: results/mon_teste1.png (mesmo nome, extensão .png)
"""
import csv
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # gera o ficheiro sem precisar de abrir janela
import matplotlib.pyplot as plt

CORES = {"EF": "#c62828", "AF31": "#2a6bc6", "DF": "#7a8591"}
NOMES = {"EF": "EF (VoIP)", "AF31": "AF31 (vídeo)", "DF": "DF (bulk)"}


def main():
    ficheiro = sys.argv[1]
    saida = ficheiro.rsplit(".", 1)[0] + ".png"

    # Lê o CSV e separa os valores por classe
    tempos = defaultdict(list)
    debitos = defaultdict(list)
    with open(ficheiro) as f:
        for linha in csv.DictReader(f):
            c = linha["classe"]
            tempos[c].append(float(linha["tempo_s"]))
            debitos[c].append(float(linha["debito_mbps"]))

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    for c in ("AF31", "DF", "EF"):
        if c in tempos:
            ax.plot(tempos[c], debitos[c], label=NOMES[c], color=CORES[c], linewidth=2)

    # Referências: capacidade do estrangulamento e limite configurado na vnf-pol
    ax.axhline(20, color="#333", linestyle=":", linewidth=1)
    ax.text(0.5, 20.3, "estrangulamento 20 Mbit/s", fontsize=8, color="#333")
    ax.axhline(19, color="#999", linestyle="--", linewidth=1)
    ax.text(0.5, 18.3, "limite vnf-pol 19 Mbit/s", fontsize=8, color="#666")

    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("Débito (Mbit/s)")
    ax.set_ylim(0, 22)
    ax.grid(alpha=0.3)
    ax.legend(loc="center right", frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Débito por classe medido na vnf-mon", loc="left", fontweight="bold")

    plt.tight_layout()
    plt.savefig(saida)
    print(f"Gráfico guardado em {saida}")


if __name__ == "__main__":
    main()
