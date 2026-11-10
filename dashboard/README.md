# Painel do testbed srsRAN

Página web para correr e observar o testbed srsRAN desta máquina (tudo
simulado): Open5GS, OSC near-RT RIC, CU e DU do srsRAN por ZMQ (20 MHz, 106
PRBs), broker GNU Radio e 3 srsUEs, um por slice (baixa SST 1, média SST 1/SD
1, alta SST 1/SD 2).

É uma cópia adaptada do painel do testbed OAI (`~/xApp-Development/dashboard`,
commit `5ea46ee`). Os dois podem estar instalados ao mesmo tempo: este usa a
porta **8091**, o serviço `srsran-dashboard` e unidades `srsdash-*`; o do OAI
usa a 8090, `testbed-dashboard` e `tbdash-*`. Não corras os dois RAN ao mesmo
tempo (competem pela CPU).

## Instalar

```bash
cd ~/MECT-Thesis/dashboard && sudo ./install.sh
```

Abre `http://127.0.0.1:8091/`. Logs do próprio painel:
`journalctl -u srsran-dashboard -f` ou o separador Logs → "Dashboard".
Desinstalar: `sudo ./install.sh uninstall` (o que estiver a correr continua).

## Usar

- **Start all** arranca, por esta ordem e à espera de cada passo: RIC (`docker
  compose down` + `up`), CU (espera a ligação ao AMF), DU (espera o F1), UE1,
  UE2, UE3 e o broker. O core (Open5GS) arranca primeiro se estiver parado.
  **Stop RAN** para tudo na ordem inversa; o core fica a correr.
- Cada componente tem Start / Stop / Restart e o seu log. O broker pede as
  perdas de entrada (0/10/20 dB por omissão; ver o próprio script).
- **Tasks**: tráfego das fases do paper, iperf DL manual para os 3 UEs, iperf
  UL, teste de stress, quota de PRBs de uma slice ao vivo, análise da última
  corrida, ping, nós E2 e subscritores do Open5GS.
- **Metrics**: por UE e por slice, débito DL/UL, MCS, CQI, BLER, buffer e
  atraso da fila RLC, descartes. Não há PRBs por UE: o JSON do DU não os tem.
- **Logs**: os de cada componente e tarefa, os ficheiros `/tmp/{du,cu,ue*}.log`
  do srsRAN e os do Open5GS.

## Como funciona (diferenças para o painel do OAI)

- **Core**: `core.type: systemd`. Os serviços `open5gs-*` aparecem como
  "contentores"; Start/Stop fazem `systemctl start/stop` de todos. gNBs e UEs
  registados vêm de `/var/log/open5gs/amf.log` (`tbdash/core.py`,
  `SystemdCore`).
- **Métricas**: `tools/srsran_exporter.py` lê o WebSocket do DU
  (`ws://127.0.0.1:8001`, `metrics_subscribe`) e escreve as mesmas linhas JSON
  que a xApp exportadora do OAI, por isso os gráficos são os mesmos. O débito
  acumulado é integrado a partir dos débitos por período; o atraso da fila é
  buffer RLC ÷ débito DL (o contador de latência do RLC do DU transborda com
  carga). Os nós E2 vêm do e2mgr da RIC (`http://10.0.2.11:3800`). Corre
  enquanto o DU estiver ativo (`metrics.anchor: du`).
- **Slices dos UEs**: os UEs partilham a SST 1 e diferem no SD, por isso cada
  componente UE tem `slice` (0/1/2) e o exportador tira a slice de
  `gnb_confs/local/ue_slice_map.json` (índice do UE no DU = ID F1AP; os UEs
  entram por ordem ue1, ue2, ue3 graças às perdas de entrada do broker).
- **RIC**: componente `docker compose ... up` em primeiro plano; o `pre` faz
  `down` antes (um DU reiniciado é recusado com E2 Setup Failure por um e2mgr
  que ainda tem o nó antigo). Se a RIC foi arrancada com `up -d` (script
  `run_local/start_srsran_local.sh`), aparece como "outside dashboard" pelo
  contentor `ric_e2term`, e Stop faz `docker compose down`.
- **IP dos UEs**: interface `tun_srsue` (além de `oaitun`).
- **Saída sem buffer**: CU, DU e UEs correm com `stdbuf -oL`, para os
  marcadores de saúde aparecerem logo no log.

Os ficheiros `tbdash/units.py`, `config.py`, `core.py`, `logs.py`,
`manager.py`, `metrics.py` e `static/app.js` têm as alterações; o resto é igual
ao original.

## Desenvolver sem root

```bash
TBDASH_SYSTEMD_USER=1 python3 server.py --config config.yaml --port 18091 --log-dir /tmp/srsdash
```

Mostra o estado, as métricas e os logs; arrancar/parar componentes precisa de
root.
