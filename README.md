# Mecanismos de QoS com VNFs

Sistema de Qualidade de Serviço (QoS) construído com Funções de Rede Virtualizadas (VNFs) numa rede emulada no **CORE**. O tráfego dos clientes atravessa uma cadeia de quatro VNFs que o filtra, classifica, limita, prioriza e monitoriza antes de chegar aos servidores. O objetivo é medir o impacto dessa cadeia na latência, no jitter, no débito e na perda de pacotes.

Trabalho Prático 3 da unidade curricular **Qualidade de Serviço na Internet** da Universidade do Minho, ano letivo 2026/2027 — **Grupo 9**.

## Arquitetura

![Diagrama lógico da rede](docs/diagrama_qos_vnf.png)

Os clientes (e o atacante `atk`) estão do lado esquerdo e os servidores do lado direito. No sentido cliente → servidor o tráfego passa por:

| Ordem | VNF | Função | Ferramenta |
| --- | --- | --- | --- |
| 1 | `vnf-fw` | Controlo de acessos (bloqueia o `atk`) | `iptables` (filter, stateful) |
| 2 | `vnf-cls` | Classificação e marcação DSCP | `iptables` (mangle, alvo DSCP) |
| 3 | `vnf-pol` | Policiamento e agendamento | `tc` (HTB + SFQ + police) |
| 4 | `vnf-mon` | Monitorização do débito por classe | `iptables` (contadores) + Python |

A ligação `r2`–`s2` tem 20 Mbit/s e é o ponto de congestionamento. A ligação direta `r1`–`r2` (bypass) permite comparar a rede com e sem VNFs.

### Classes de tráfego (RFC 4594)

| Classe | Tráfego | Servidor | DSCP | Débito garantido / máximo | Prioridade |
| --- | --- | --- | --- | --- | --- |
| Tempo real | VoIP | srv-voip 10.0.20.10 | EF (46) | 2 / 2 Mbit/s (policiado) | 0 (alta) |
| Multimédia | Vídeo | srv-video 10.0.20.20 | AF31 (26) | 10 / 19 Mbit/s | 1 |
| Best effort | Bulk | srv-bulk 10.0.20.30 | DF (0) | 7 / 19 Mbit/s | 2 (baixa) |

### Endereçamento

| Segmento | Rede | Nós |
| --- | --- | --- |
| Clientes (s1) | 10.0.1.0/24 | c-voip .10 · c-video .20 · c-bulk .30 · atk .66 · r1 .1 |
| Servidores (s2) | 10.0.20.0/24 | srv-voip .10 · srv-video .20 · srv-bulk .30 · r2 .1 |
| Cadeia | 10.0.100.0/30 … 10.0.100.16/30 | r1 → fw → cls → pol → mon → r2 |
| Bypass | 10.0.200.0/30 | r1 ↔ r2 |

O encaminhamento é feito com rotas estáticas aplicadas por script (sem OSPF), para podermos escolher o caminho em cada teste.

## Estrutura do repositório

```text
.
├── topology/
│   ├── top_1.xml          # primeira versão (servidores à esquerda)
│   └── top_2.xml          # versão atual (clientes à esquerda, com atk)
├── scripts/
│   ├── iniciar.sh         # prepara tudo depois do play no CORE
│   ├── rotas.sh           # escolhe o caminho: cadeia ou bypass
│   ├── vnf-fw.sh          # firewall        on | off | status
│   ├── vnf-cls.sh         # classificador   on | off | status
│   ├── vnf-pol.sh         # policer/HTB     on | off | status
│   ├── vnf-mon.sh         # monitor         on [nome] | off | status
│   ├── cenarios.sh        # corre os 3 cenários e gera os resultados
│   ├── analisar.py        # tabela resumo e gráficos de comparação
│   └── grafico_mon.py     # gráfico de débito por classe (CSV da vnf-mon)
├── vnfs/monitor/
│   └── monitor.py         # programa que corre dentro da vnf-mon
├── docs/                  # diagrama, Gantt, prints e documento de ponto de situação
└── results/               # resultados das experiências (gerados pelos scripts)
```

## Como executar

### Requisitos

- VM Ubuntu com o [CORE](https://github.com/coreemu/core) instalado
- `iperf3`, `tcpdump`, `iptables`, `iproute2`
- Python 3 com matplotlib: `sudo apt install python3-matplotlib`

### 1. Abrir a topologia

No CORE: **File → Open → `topology/top_2.xml`** e carregar no **play**.

> As rotas e as regras das VNFs perdem-se sempre que a sessão do CORE é reiniciada. Depois de cada play é preciso correr o `iniciar.sh`, e os terminais dos nós abertos antes do reinício deixam de funcionar (fechar e abrir de novo).

### 2. Preparar a rede

```bash
sudo ./scripts/iniciar.sh          # cenário 3: cadeia com as VNFs ligadas
sudo ./scripts/iniciar.sh off      # cenário 2: cadeia com as VNFs desligadas
sudo ./scripts/iniciar.sh bypass   # cenário 1: tráfego pelo bypass, sem VNFs
```

Cada VNF também pode ser ligada, desligada ou inspecionada à parte, por exemplo `sudo ./scripts/vnf-pol.sh status`.

### 3. Correr as experiências

```bash
sudo ./scripts/cenarios.sh final1
```

Demora cerca de 2 minutos. Corre os três cenários com o mesmo tráfego:

| Tempo | Tráfego |
| --- | --- |
| antes | 3 pings do `atk` para verificar a firewall |
| 0–35 s | VoIP: ping a cada 0,2 s do c-voip para o srv-voip |
| 0–30 s | Vídeo: iperf3 UDP a 8 Mbit/s do c-video para o srv-video |
| 5–25 s | Bulk: iperf3 TCP com 4 fluxos do c-bulk para o srv-bulk |

Os resultados ficam em `results/final1/`:

- `resumo.csv` — tabela com as métricas dos três cenários
- `latencia_voip.png` — latência do VoIP ao longo do tempo
- `comparacao.png` — comparação dos três cenários
- `on/mon.png` — débito por classe medido pela vnf-mon
- `<cenário>/` — dados brutos (ping, JSON do iperf3, CSV da vnf-mon)

## Resultados preliminares (execução `final1`)

| Métrica | Bypass | Cadeia desligada | Cadeia ligada |
| --- | ---: | ---: | ---: |
| atk bloqueado | não | não | **sim** |
| VoIP RTT sem carga (ms) | 21,0 | 21,6 | 23,4 |
| VoIP RTT médio com carga (ms) | 465,2 | 452,3 | **23,2** |
| VoIP RTT máximo (ms) | 621,0 | 620,0 | **45,3** |
| VoIP jitter (ms) | 13,76 | 13,30 | **2,96** |
| Vídeo débito (Mbit/s) | 8,00 | 8,00 | 8,00 |
| Vídeo jitter (ms) | 0,69 | 0,72 | **0,04** |
| Vídeo perdas (%) | 0,15 | 0,16 | 0,38 |
| Bulk débito (Mbit/s) | 8,52 | 8,89 | 6,59 |
| Bulk retransmissões | 317 | 311 | 112 |

Com a cadeia ligada a latência do VoIP com carga desce cerca de 20 vezes e o atacante é bloqueado. Atravessar a cadeia custa menos de 3 ms. As perdas ligeiramente maiores do vídeo e o débito mais baixo do bulk serão investigados na fase de otimização.

## Progresso

- [x] Fase 1: ambiente, topologia e proposta
- [x] Fase 2: implementação e teste individual das VNFs
- [ ] Fase 3: integração, testes e otimização (cenários automáticos já feitos; falta otimizar e repetir)
- [ ] Fase 4: experiências finais, análise e relatório final

## Entregas

| Entrega | Ligação |
| --- | --- |
| Proposta | *A adicionar* |
| Relatório de progresso | *A adicionar* |
| Relatório final | *A adicionar* |
| Vídeo de demonstração | *A adicionar* |

## Segurança e ética

Todo o tráfego é gerado num ambiente emulado e isolado. Não execute estes cenários em redes reais sem autorização.

## Autores

| Nome | Número |
| --- | --- |
| Luís Lopes | PG64182 |
| Paulo Brochado | PG64183 |
| Vítor Mina | PG64044 |

O registo das contribuições de cada elemento do grupo está disponível no [histórico de commits](../../commits).

## Referências

- ETSI, [Network Functions Virtualisation (NFV)](https://www.etsi.org/technologies/nfv)
- RFC 2474, [Definition of the Differentiated Services Field](https://www.rfc-editor.org/rfc/rfc2474)
- RFC 2475, [An Architecture for Differentiated Services](https://www.rfc-editor.org/rfc/rfc2475)
- RFC 4594, [Configuration Guidelines for DiffServ Service Classes](https://www.rfc-editor.org/rfc/rfc4594)
- [CORE — Common Open Research Emulator](https://github.com/coreemu/core)
- [Linux Advanced Routing & Traffic Control HOWTO](https://lartc.org)
