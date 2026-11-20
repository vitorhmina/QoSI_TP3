# Classificação por aprendizagem automática (vnf-cls)

Modo extra do classificador: em vez de decidir pela regra de protocolo/porta no primeiro pacote, observa os primeiros pacotes de cada fluxo e uma **árvore de decisão** (scikit-learn) decide a classe (VoIP → EF, vídeo → AF31, bulk → BE) pelo **comportamento** do tráfego. IPs e portas não entram no modelo de propósito.

| Ficheiro | Função |
| --- | --- |
| `features.py` | Janela por fluxo (8 pacotes ou 1 s) e as 13 features: protocolo, tamanhos (média, desvio, mín, máx, fração pequenos/grandes), intervalos entre pacotes (média, desvio, máx) e débito |
| `synth.py` | Gerador de fluxos sintéticos (RTP com vários codecs, pings, iperf3 UDP/TCP, vídeo por frames, upload TCP, QUIC, tráfego de controlo) |
| `train.py` | Compara árvore de decisão e random forest (teste 25% + validação cruzada 5-fold, F1, tempo por fluxo), mostra o relatório e a matriz de confusão e grava `model.joblib` + `model.meta.json` |
| `model.py` | Carrega o modelo e recarrega-o sozinho quando o ficheiro muda |
| `data/` | CSVs com fluxos reais recolhidos no laboratório (entram no treino da imagem) |

Teste do modo ML (faz parte do teste da VNF): `sudo bash vnfs/cls/test.sh ml` (ou sem argumento para regras + ML).

O modelo por omissão é treinado quando a imagem é construída (`docker compose build`). A árvore é o modelo por omissão: tem a mesma exatidão que a random forest e é ~75× mais rápida por fluxo; `python3 train.py --model forest` usa a floresta.

## Modos e troca em tempo real

```bash
docker exec vnf-cls python3 /app/vnf.py mode            # modo atual
docker exec vnf-cls python3 /app/vnf.py mode ml         # troca já, sem reiniciar
docker exec vnf-cls python3 /app/vnf.py mode rules
docker exec vnf-cls python3 /app/vnf.py mode collect    # regras + grava fluxos para treino
```

Dentro do terminal da VNF também existe o atalho `clsmode ml`. A troca limpa o conntrack da VNF, por isso os fluxos que já estão a correr são reclassificados no novo modo. Nos modos `ml` e `collect` os primeiros pacotes de um fluxo seguem como BE enquanto ele é observado.

## Treinar com tráfego real

```bash
docker exec vnf-cls python3 /app/vnf.py mode collect
sudo python3 tests/traffic.py --duration 300            # gera tráfego variado
docker exec vnf-cls python3 /app/vnf.py train           # retreina; a VNF recarrega o modelo
docker exec vnf-cls python3 /app/vnf.py status          # exatidão do modelo e exatidão ao vivo
```

A classe verdadeira de cada fluxo vem do servidor de destino (10.0.20.10/20/30) e só é usada como etiqueta. Como o CORE recria o contentor a cada sessão, para guardar os dados copia-se o CSV para o repositório e reconstrói-se a imagem:

```bash
docker cp vnf-cls:/var/log/cls.flows.csv vnfs/cls/ml/data/lab_$(date +%F).csv
sudo docker compose build cls
```

Para comparar regras e ML na fase 3: `python3 tests/phase3.py --name cls --scenarios on --cls-mode rules ml`.
