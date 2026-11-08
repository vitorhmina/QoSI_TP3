#!/usr/bin/env python3
"""
Analisa os resultados do cenarios.sh e gera a tabela resumo e os gráficos.

Uso:  python3 scripts/analisar.py results/<nome>
Gera: results/<nome>/resumo.csv
      results/<nome>/latencia_voip.png
      results/<nome>/comparacao.png
"""
import csv
import json
import os
import re
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CENARIOS = [
    ("bypass", "1. Bypass (sem VNFs)"),
    ("off", "2. Cadeia desligada"),
    ("on", "3. Cadeia ligada"),
]
CORES = {"bypass": "#7a8591", "off": "#d9822b", "on": "#2a6bc6"}

# Janela em que o bulk está a correr (igual ao cenarios.sh)
BULK_INICIO, BULK_FIM = 5, 25


# ---------- leitura dos ficheiros ----------

def ler_ping(caminho):
    """Devolve (tempos, rtts, enviados, recebidos)."""
    tempos, rtts = [], []
    enviados = recebidos = 0
    t0 = None
    with open(caminho) as f:
        for linha in f:
            m = re.search(r"\[(\d+\.\d+)\].*time=([\d.]+) ms", linha)
            if m:
                t = float(m.group(1))
                t0 = t if t0 is None else t0
                tempos.append(t - t0)
                rtts.append(float(m.group(2)))
            m = re.search(r"(\d+) packets transmitted, (\d+) received", linha)
            if m:
                enviados, recebidos = int(m.group(1)), int(m.group(2))
    return tempos, rtts, enviados, recebidos


def ler_iperf(caminho):
    """Devolve o JSON do iperf3, ou None se o teste falhou."""
    try:
        with open(caminho) as f:
            j = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if "error" in j:
        print(f"  aviso: {caminho}: {j['error']}")
        return None
    return j


def media(v):
    return statistics.mean(v) if v else float("nan")


def p95(v):
    if not v:
        return float("nan")
    v = sorted(v)
    return v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]


# ---------- métricas de cada cenário ----------

def metricas(pasta):
    r = {}

    # VoIP (ping)
    t, rtt, env, rec = ler_ping(os.path.join(pasta, "ping.txt"))
    sem_carga = [x for ti, x in zip(t, rtt) if ti < BULK_INICIO]
    com_carga = [x for ti, x in zip(t, rtt) if BULK_INICIO + 1 <= ti <= BULK_FIM]
    jitter = [abs(b - a) for a, b in zip(com_carga, com_carga[1:])]
    r["voip_rtt_sem_carga_ms"] = media(sem_carga)
    r["voip_rtt_medio_ms"] = media(com_carga)
    r["voip_rtt_p95_ms"] = p95(com_carga)
    r["voip_rtt_max_ms"] = max(com_carga) if com_carga else float("nan")
    r["voip_jitter_ms"] = media(jitter)
    r["voip_perda_pct"] = 100 * (env - rec) / env if env else float("nan")
    r["_serie"] = (t, rtt)

    # Vídeo (iperf3 UDP)
    j = ler_iperf(os.path.join(pasta, "video.json"))
    if j:
        fim = j["end"]
        s = fim.get("sum_received") or fim["sum"]
        perdas = fim["sum"]
        r["video_mbps"] = s["bits_per_second"] / 1e6
        r["video_jitter_ms"] = perdas.get("jitter_ms", float("nan"))
        r["video_perda_pct"] = perdas.get("lost_percent", float("nan"))
    else:
        r["video_mbps"] = r["video_jitter_ms"] = r["video_perda_pct"] = float("nan")

    # Bulk (iperf3 TCP)
    j = ler_iperf(os.path.join(pasta, "bulk.json"))
    if j:
        r["bulk_mbps"] = j["end"]["sum_received"]["bits_per_second"] / 1e6
        r["bulk_retransmissoes"] = j["end"]["sum_sent"].get("retransmits", 0)
    else:
        r["bulk_mbps"] = r["bulk_retransmissoes"] = float("nan")

    # Firewall
    try:
        with open(os.path.join(pasta, "atk.txt")) as f:
            r["atk_bloqueado"] = "sim" if " 0 received" in f.read() else "não"
    except OSError:
        r["atk_bloqueado"] = "?"

    return r


# ---------- gráficos ----------

def grafico_latencia(res, saida):
    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    ax.axvspan(BULK_INICIO, BULK_FIM, color="#f2f2f2", zorder=0)
    ax.text((BULK_INICIO + BULK_FIM) / 2, 0.97, "bulk TCP ativo", ha="center", va="top",
            transform=ax.get_xaxis_transform(), fontsize=8, color="#666")
    for c, nome in CENARIOS:
        if c in res:
            t, rtt = res[c]["_serie"]
            ax.plot(t, rtt, label=nome, color=CORES[c], linewidth=1.8)
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("RTT do VoIP (ms)")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper right")
    ax.set_title("Latência do VoIP com vídeo e bulk em simultâneo", loc="left", fontweight="bold")
    plt.tight_layout()
    plt.savefig(saida)
    plt.close(fig)


def grafico_comparacao(res, saida):
    cs = [c for c, _ in CENARIOS if c in res]
    nomes = [n.split(". ")[1] for c, n in CENARIOS if c in res]
    cores = [CORES[c] for c in cs]

    fig, eixos = plt.subplots(1, 3, figsize=(12, 4), dpi=150)
    paineis = [
        ("voip_rtt_medio_ms", "RTT médio do VoIP com carga (ms)", "{:.0f}"),
        ("video_perda_pct", "Perda de pacotes do vídeo (%)", "{:.1f}"),
        ("bulk_mbps", "Débito do bulk (Mbit/s)", "{:.1f}"),
    ]
    for ax, (chave, titulo, fmt) in zip(eixos, paineis):
        valores = [res[c][chave] for c in cs]
        barras = ax.bar(nomes, valores, color=cores)
        for b, v in zip(barras, valores):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt.format(v),
                    ha="center", va="bottom", fontsize=9)
        ax.set_title(titulo, fontsize=10, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="x", labelsize=8)
        ax.margins(y=0.15)
    fig.suptitle("Comparação dos três cenários", x=0.01, ha="left", fontweight="bold")
    plt.tight_layout()
    plt.savefig(saida)
    plt.close(fig)


# ---------- principal ----------

def main():
    pasta = sys.argv[1]
    res = {}
    for c, _ in CENARIOS:
        if os.path.isdir(os.path.join(pasta, c)):
            res[c] = metricas(os.path.join(pasta, c))

    colunas = [
        ("atk_bloqueado", "atk bloqueado", "{}"),
        ("voip_rtt_sem_carga_ms", "VoIP RTT sem carga (ms)", "{:.1f}"),
        ("voip_rtt_medio_ms", "VoIP RTT médio com carga (ms)", "{:.1f}"),
        ("voip_rtt_p95_ms", "VoIP RTT p95 com carga (ms)", "{:.1f}"),
        ("voip_rtt_max_ms", "VoIP RTT máximo (ms)", "{:.1f}"),
        ("voip_jitter_ms", "VoIP jitter (ms)", "{:.2f}"),
        ("voip_perda_pct", "VoIP perdas (%)", "{:.1f}"),
        ("video_mbps", "Vídeo débito (Mbit/s)", "{:.2f}"),
        ("video_jitter_ms", "Vídeo jitter (ms)", "{:.2f}"),
        ("video_perda_pct", "Vídeo perdas (%)", "{:.2f}"),
        ("bulk_mbps", "Bulk débito (Mbit/s)", "{:.2f}"),
        ("bulk_retransmissoes", "Bulk retransmissões", "{}"),
    ]

    # Tabela resumo (CSV + ecrã)
    with open(os.path.join(pasta, "resumo.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metrica"] + [c for c in res])
        for chave, rotulo, fmt in colunas:
            w.writerow([rotulo] + [fmt.format(res[c][chave]) for c in res])

    largura = 32
    print(f"{'':{largura}}" + "".join(f"{c:>12}" for c in res))
    for chave, rotulo, fmt in colunas:
        print(f"{rotulo:{largura}}" + "".join(f"{fmt.format(res[c][chave]):>12}" for c in res))

    grafico_latencia(res, os.path.join(pasta, "latencia_voip.png"))
    grafico_comparacao(res, os.path.join(pasta, "comparacao.png"))
    print(f"\nResumo e gráficos guardados em {pasta}/")


if __name__ == "__main__":
    main()
