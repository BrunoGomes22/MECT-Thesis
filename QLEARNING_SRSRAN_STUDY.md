# Estudo: levar o xApp de Q-learning de fatias para o testbed srsRAN

Data: 2026-11-09. Objetivo: preencher a metade srsRAN do paper
(`~/xApp-Development/paper/main.tex`, todos os `\todo` e `\srs`) e planear a
integração do agente neste repositório.

Fontes lidas:

- este repositório (branch `sebas`, commit `82d70fd`): configs, xApps, scripts,
  resultados e notas do Bruno;
- código do srsRAN Project **25.10** (cópia do `main` de 2026-02-16 em
  `~/.local/share/Trash/files/srsRAN_Project-main`, apagada a 2026-10-14;
  recuperar ou clonar a tag `release_25_10` para citar no paper);
- `srsran/oran-sc-ric` no GitHub (lido hoje: `lib/e2sm_rc_module.py`,
  `lib/e2sm_ccc_module.py`);
- `~/xApp-Development` (agente OAI, `QLEARNING_SLICE_PLAN.md`, `paper/`).

Os caminhos `lib/...`, `apps/...` e `include/...` abaixo são relativos à raiz do
srsRAN Project.

---

## 0. Resumo

1. **Este repo é o testbed do Bruno**: CU/DU srsRAN separados por F1, OSC
   near-RT RIC, Open5GS e srsUEs por ZMQ. A 20 MHz (n3 FDD, 15 kHz) a célula tem
   **106 PRBs, o mesmo N do OAI**. Reaproveita-se a infraestrutura (configs,
   broker, scripts, mapa UE→fatia); o controlo do xApp dele não serve (ponto 2).
2. **O E2SM-RC "Slice-level PRB quota" não muda a quota da fatia no srsRAN
   25.10.** É endereçado a um UE e passa a limites de PRBs **por grant** desse
   UE, em DL e em UL; o S-NSSAI e o rácio dedicado são ignorados. É o que o xApp
   do Bruno usa.
3. **A quota da fatia muda em tempo real por dois caminhos:** E2SM-CCC
   (O-RRMPolicyRatio, por E2, ao nível da célula) e o comando WebSocket
   `rrm_policy_ratio_set` (fora do E2). Recomendo o **E2SM-CCC**; o WebSocket
   fica como plano B e para depurar.
4. **O empréstimo já existe no scheduler do srsRAN.** `min` é uma garantia por
   slot com prioridade; o que uma fatia não usa fica para as outras até ao seu
   `max`. Com `priority` 2/1/0 nas fatias, o excedente vai primeiro à alta. É o
   modelo da Sec. IV-A do paper sem mexer no DU (no OAI foi preciso
   `slice_lending.h`).
5. **Estado:** o KPM não tem etiqueta de fatia nem fila RLC. Por UE dá PRBs por
   slot, débito e um atraso RLC com um bug provável (§5.3). A fila (`dl_bs`) está
   no JSON do DU (WebSocket 8001, já ligado). Com 1 UE por fatia, "por UE" é "por
   fatia".
6. **Recalibrar:** u_sat ≈ **0.87** (a carga de PRBs satura em 87% nos logs do
   Bruno) contra 0.755 no OAI; capacidade ≈ **54.4 Mbit/s** úteis contra ≈ 59.
   As fases A/B/C passam a ×0.92.
7. **L1 já está medido no srsRAN** pelo Bruno: no cenário 12 (18/22/26 Mbit/s),
   sem controlo, cada prioridade recebeu ≈ 18.2 Mbit/s e a alta ficou a 70% da
   sua carga.
8. **Falta no repo:** o código da CU modificada que exporta `ue_slice_map.json`
   e um broker de 3 UEs a 23.04 MHz. A tabela de fatias do
   `README_20MHz_6UE.md` contradiz os `srsue_confs` (§1).

---

## 1. O que existe neste repositório

| Componente | Ficheiros | Para o Q-learning |
|---|---|---|
| CU | `gnb_confs/cu.yml` (CU-CP `.239`, CU-UP `.240`, AMF `.181`, RIC `.202:36421`) | Usar tal como está |
| DU 20 MHz | `gnb_confs/du_rf_b200_zmq_multi_ue_20mhz.yml` (n3, 15 kHz, 106 PRBs, ZMQ, E2 KPM+RC, JSON + WebSocket na 8001) | Acrescentar `e2sm_ccc_enabled: true` e a `priority` das fatias (§3) |
| Fatias no DU | `slicing:` SST 1 sem SD / SD 1 / SD 2, todas `min 0 / max 100` | Já declaradas: o srsRAN só reconfigura fatias que existem no arranque |
| UEs | `srsue_confs/srsue_20mhz/ue1..6_zmq.conf` | `ue1` = sem SD (baixa), `ue2` = SD 1 (média), `ue3` = SD 2 (alta): 1 UE por fatia, como no OAI |
| Broker | `multi-ue-broker_scripts/multi_ue_scenario_headless_6_20mhz.py` | Só para 6 UEs. O de 3 UEs (`..._headless.py`) está a 11.52 MHz: criar `_3_20mhz` (`samp_rate = 23040000`) |
| xApp atual | `xApps/priority_prb_xapp_test.py` (heurística e bandit) | Reaproveitar o esqueleto: subscrições KPM Style 1/4, leitura do mapa de fatias, log CSV |
| Mapa UE→fatia | Servido pela CU em `http://192.168.81.239:8088/ue_slice_map.json` (ficheiro `/home/pc4/srsran_runtime/ue_slice_map.json`) | Necessário (o KPM não dá o S-NSSAI). **O código que o gera não está no repo:** pedir ao Bruno |
| Tráfego | `test_scripts/test_matrix_scripts/run_test_matrix_6ues_with_markers_20mhz.sh` (13 cenários × 60 s, `iperf3 -u -R` nos namespaces) | Escrever um equivalente de `qlearning_traffic.sh` com 4 fases (§6) |
| Resultados | `test_results/capacity_results/`, `testing_results_6ues_20mhz/` | Capacidade e evidência de L1 (§6) |

Factos do testbed:

- E2 node ID do DU: `gnbd_001_001_00019b_0` (gnb_id 411 = 0x19B, logo
  NCI `0x00066C000`).
- KPM no xApp do Bruno: relatório e granularidade de 1000 ms.
- Versão do srsRAN nos hosts: as configs usam `remote_control` e o JSON de
  métricas, o que aponta para a 25.10. **Confirmar** com `srsdu --version` no
  `.209`.
- **Inconsistência:** o `README_20MHz_6UE.md` sugere UE 1–2 best-effort,
  3–4 medium e 5–6 high. Os `.conf` pedem: `ue1`/`ue4` sem SD, `ue2`/`ue5`
  SD 1 e `ue3`/`ue6` SD 2. O que conta é o pedido do UE mais a subscrição no
  Open5GS; alinhar o README com os `.conf`.

---

## 2. Os schedulers do srsRAN 25.10 (Sec. III-B e Tabela I do paper)

| Linha da Tabela I | srsRAN 25.10 | Onde |
|---|---|---|
| DL policies | `time_rr` (RR) e `time_qos` (por omissão). O `time_qos` é PF com peso r/R̄^c e **c = `pf_fairness_coeff` = 2.0**, multiplicado por pesos de GBR, prioridade ARP×5QI e PDB/atraso HOL (todos ligados por omissão). Com todos os UEs em 5QI 9 sem GBR, fica PF com c = 2 | `lib/scheduler/policy/scheduler_time_qos.cpp`, `include/srsran/scheduler/config/scheduler_expert_config.h:63-69` |
| UL policies | As mesmas | idem |
| Policy selectable at run time | Não. Escolhe-se por célula ou **por fatia** (`slicing[].sched_cfg.policy: {rr_sched \| qos_sched}`), só no arranque | `apps/units/flexible_o_du/o_du_high/du_high/du_high_config_cli11_schema.cpp` (`configure_cli11_slicing_scheduling_args`) |
| Allocation per UE and slot | **Por confirmar no código.** Com a config deste testbed (`dci_format_0_1_and_1_1: false`, DCI 1_0) a alocação é RA tipo 1, contígua. Falta ver se as retx vêm primeiro e qual é o mínimo de PRBs | — |
| PRB slicing in scheduler | Sim. Há uma instância de fatia por S-NSSAI de `cell_cfg.slicing`, mais uma fatia de SRB (prioridade máxima) e uma fatia DRB por omissão | `lib/scheduler/slicing/inter_slice_scheduler.cpp` (construtor) |
| Slice quota type | `min` / `max` / `dedicated` em % de N (`floor(ratio·N/100)`), **por slot**, aplicados a PDSCH **e** PUSCH | `lib/scheduler/slicing/ran_slice_instance.cpp:50-62`, `inter_slice_scheduler.cpp:130,178` |
| Unused PRBs lent to other slices | **Sim, de origem.** Abaixo do `min`, a fatia tem um candidato com prioridade até ao `min`; depois, um segundo candidato até ao `max`. O `min` não usado fica para as outras. O `dedicated` é reservado e não se empresta | `inter_slice_scheduler.cpp:130-140, 333` |
| Quota control over E2 | **E2SM-CCC** O-RRMPolicyRatio (célula, por S-NSSAI). O E2SM-RC 2/6 é limite por UE e por grant (§3) | `lib/e2/e2sm/e2sm_ccc/`, `lib/e2/e2sm/e2sm_rc/` |
| Priority between slices | **Sim, estática:** `priority` 0–254 por fatia, só na config (a reconfiguração muda só `rbs`) | `inter_slice_scheduler.cpp:364-378, 423` |

Ordem dos candidatos de fatia em cada slot (bits de `get_prio`, do mais para o
menos significativo):

1. distância de slot;
2. "ainda abaixo do `min`";
3. `priority` da fatia;
4. slots desde o último PDSCH;
5. round-robin.

Consequências para o paper:

- **L1 e L3 confirmam-se no srsRAN com a config por omissão** (prioridades
  todas a 0).
- **L2 não se aplica** com `max = 100`, porque o empréstimo é nativo. Só
  aparece com `min = max`.
- **L4 continua:** a quota estática certa depende do objetivo. Há um esquema
  novo só do srsRAN: **prioridade estrita sem quotas** (`min 0`, `priority`
  0/1/2). É o equivalente nativo do 5/5/96 do OAI e merece uma linha na
  Tabela III.

---

## 3. Caminho de controlo

| Caminho | O que faz no srsRAN 25.10 | Serve? |
|---|---|---|
| E2SM-RC estilo 2 ação 6 "Slice-level PRB quota" (`control_slice_level_prb_quota`, usado pelo Bruno) | Cabeçalho com `gNB-CU-UE-F1AP-ID`. O DU reconfigura **esse UE**: `pdsch_grant_size_limits` e `pusch_grant_size_limits` = [min, max]·N/100. Só usa a 1.ª política; o S-NSSAI e o `dedicated` são ignorados. Cada pedido dispara uma reconfiguração MAC do UE | **Não** (não há quota de fatia nem garantia) |
| **E2SM-CCC** O-RRMPolicyRatio (`e2sm_ccc_module.py`, `control_o_rrm_policy_ratio`) | Ao nível da célula. Lista de estruturas, cada uma com membros PLMN+S-NSSAI e min/max/dedicated. Aplica-se às fatias existentes em `inter_slice_scheduler::handle_slice_reconfiguration_request` | **Sim** (recomendado, é E2) |
| WebSocket `rrm_policy_ratio_set` (porta 8001, `remote_control`) | O mesmo procedimento do DU que o CCC. Sem NR-CGI, aplica-se a todas as células | Sim, fora do E2 (plano B, depuração) |

Referências:

- E2SM-RC: `lib/e2/e2sm/e2sm_rc/e2sm_rc_control_action_du_executor.cpp:91,246`
  e `lib/du/du_high/du_manager/procedures/du_ue_ric_configuration_procedure.cpp:102-119`.
- E2SM-CCC: `lib/e2/e2sm/e2sm_ccc/e2sm_ccc_control_action_du_executor.cpp`
  (`convert_to_du_config_request`) e
  `lib/du/du_high/du_manager/du_cell_manager.cpp:126-167`.
- WebSocket:
  `apps/units/flexible_o_du/o_du_high/du_high/commands/du_high_remote_commands.cpp:96`
  e `lib/du/du_high/du_manager/procedures/du_param_config_procedure.cpp`
  (`handle_cell_config_updates`).

O que mudar para usar o E2SM-CCC:

- **DU:** `e2: e2sm_ccc_enabled: true`. O RAN function ID por omissão na lib é 4
  (KPM = 2, RC = 3); confirmar no log do E2 Setup.
- **`e2sm_ccc_module.py` upstream:**
  - a célula está fixa (`nr_cgi = "00066C000"`), o que serve para este DU;
  - envia **só `sst: 1`, com o `sd` comentado**, por isso só atinge a fatia sem
    SD;
  - envia uma estrutura por mensagem.

  Alterar para passar `sst`/`sd` e mandar **as 3 fatias numa só mensagem**: o
  DU aplica-as num único pedido.
- O `resourceType` (`PRB_DL`) é ignorado pelo DU: as mesmas `rbs` valem para DL
  e UL.

Mapeamento do agente:

- **Ações:** as fronteiras β1, β2 passam a garantias `min_s` em %, com Σ = 100,
  `max = 100` e `dedicated = 0`. Passo de 5% (≈ 5 PRBs) e mínimo de 5%
  (`floor(5·106/100)` = 5 PRBs). Não há janelas contíguas: no srsRAN uma fatia é
  uma quota, não um intervalo de PRBs.
- **Prioridades nas corridas com empréstimo:** `priority` baixa/média/alta =
  0/1/2 no DU. Assim o excedente vai à alta primeiro, como no OAI.
- **Esquema rígido:** `min = max`.

---

## 4. Caminho de estado

| Entrada do agente (OAI) | Fonte no srsRAN |
|---|---|
| q_s, fila RLC DL da fatia | **Não existe no KPM.** JSON do DU (WebSocket 8001, `metrics.enable_json`): `ue_list[].dl_bs` (bytes, amostra no fim do período) |
| T_s, débito DL | KPM `DRB.UEThpDl` por UE (kbit/s) ou JSON `dl_brate` |
| u_s, utilização | KPM `RRU.PrbUsedDl` por UE = `tot_pdsch_prbs_used / nof_dl_slots` (PRBs por slot, inteiro). Usar u_s = PRBs_s / n_s (pode passar de 1 com empréstimo) |
| Alternativa a q_s ≥ T_s·δ | `DRB.RlcSduDelayDl` ≥ δ (é a mesma condição, q/T ≥ δ), mas ver o bug em §5.3 |
| UE → fatia | `ue_slice_map.json` da CU modificada (chave `gnb_cu_ue_f1ap_id`) |

Notas:

- **IDs:** o KPM usa `gNB-CU-UE-F1AP-ID`; o JSON usa o índice de UE do DU e o
  RNTI. Com 3 UEs ligados uma vez, 0/1/2 coincidem. Verificar e registar o
  RNTI no log.
- **Relógio:** o ZMQ pode correr mais devagar do que o tempo real (broker com
  `slow_down_ratio`, CPU). Comparar `nof_dl_slots` por relatório com 1000
  (15 kHz). Se divergir, medir o passo pelo relógio do gNB, como no OAI.
- **Limiares:** u_sat ≈ 0.87 (logs do Bruno: `dl_load_raw` satura em 87.0,
  ≈ 92 PRBs por slot). O limiar de folga 0.6·u_sat passa a ≈ 0.52. Medir u_sat
  por fatia com 1 UE saturado antes de fixar. Manter δ = 100 ms e
  q_min = 15 kB.
- **Período:** KPM a 1 s e `du_report_period` = 1000 ms, alinhados com o passo
  de 1 s.

---

## 5. Interações e limitações do srsRAN (L5 e discussão)

1. **O RC 2/6 reconfigura o UE.** `inter_slice_scheduler::reconf_ue` tira o UE
   das fatias até `config_applied` (`inter_slice_scheduler.cpp:209`). Pode haver
   um intervalo curto sem scheduling a cada controlo; não está medido. Pode
   explicar o *hold-off* de 5 s do Bruno.

   Além disso, o `--min_prb 10` do xApp do Bruno impõe um **grant mínimo de 10
   PRBs** e o cap também limita os grants UL.

2. **A quota vale para DL e UL.** Com `max = 100` não há problema. Nos esquemas
   rígidos o UL também fica cortado: ver se aparece o mesmo bloqueio de ACKs do
   RLC AM que no OAI (falta confirmar o mínimo de PUSCH no srsRAN).

3. **Bug provável em `DRB.RlcSduDelayDl` por UE**
   (`lib/e2/e2sm/e2sm_kpm/e2sm_kpm_du_meas_provider_impl.cpp:1103-1114`):
   - O numerador é a soma das latências dos SDUs **retirados**
     (`tx_low.sum_sdu_latency_us`); o denominador é o número de SDUs
     **chegados** (`tx_high.num_sdus`). O caminho ao nível do nó (`:1067`) usa
     `tx_low.num_of_pulled_sdus`, que é o correto.
   - `std::accumulate(..., 0, ...)` soma em `int`: transborda acima de
     ≈ 2.1·10⁹ µs por janela. Por exemplo, 3000 SDUs com 1 s de fila dá
     3·10⁹ µs e um valor negativo.

   Explica os "valores negativos ou muito altos" das notas de 2 de abril. A
   correção é pequena (denominador `num_of_pulled_sdus`, acumular em
   `uint64_t`) e é uma lição de implementação para o paper.

4. **`last_ue_metrics[ue_idx]`** (`:489, :581`): usa a posição no vetor como
   índice de UE do DU. Está certo enquanto os UEs forem 0..n−1 sem buracos.
   Depois de um *detach*/*reattach*, um UE pode ler as métricas de outro (a
   verificar). Nas corridas: ligar os 3 UEs uma vez e não os reiniciar.

5. **Só fatias declaradas no arranque** podem ser reconfiguradas (log
   `No RRM policy member found`).

6. **A `priority` da fatia é estática:** mudá-la implica reiniciar o DU.

---

## 6. Experiências equivalentes às do OAI

Escalar as fases para a mesma fração da capacidade (54.4/59 ≈ 0.92, em Mbit/s
úteis):

| Fase | OAI (baixa/média/alta) | srsRAN | % da capacidade |
|---|---|---|---|
| A, A2 | 20 / 31 / 10 (61) | 18 / 29 / 9 (56) | 103% |
| B | 45 / 10 / 11 (66) | 41 / 9 / 10 (60) | 110% |
| C | 20 / 20 / 40 (80) | 18 / 18 / 37 (73) | 134% |

A capacidade de 54.4 Mbit/s foi medida com 6 UEs
(`test_results/capacity_results/capacity_results.csv`). **Medir de novo com 3
UEs** antes de fixar as fases.

Esquemas (linhas srsRAN da Tabela III):

| Esquema | Config srsRAN |
|---|---|
| Sem fatias, PF | Config atual (fatias 0/100, `priority` 0), `time_qos` |
| Sem fatias, RR | Idem, com `policy: rr_sched` |
| 35/35/36 rígida | `min = max` = 33/33/34% |
| 35/35/36 + empréstimo | `min` 33/33/34%, `max 100`, `priority` 0/1/2 |
| 5/5/96 + empréstimo | `min` 5/5/90%, `max 100`, `priority` 0/1/2 |
| Prioridade estrita nativa (nova) | `min 0`, `max 100`, `priority` 0/1/2 |
| Q + empréstimo (vazia, ×5; pré-treinada, ×3) | `priority` 0/1/2, `min` pelo agente via E2SM-CCC |

Evidência já disponível de L1 no srsRAN
(`test_results/testing_results_6ues_20mhz/final_comparison_plots_6ues_20mhz/final_6ue_priority_metrics_mean_sd.csv`,
5 corridas, 2 UEs por prioridade):

- **Cenário 12** (18/22/26 Mbit/s):
  - baseline: alta 18.1 (70% da carga), média 18.2, baixa 17.8;
  - heurística do Bruno: alta 23.6 (91%), média 17.1, baixa 13.2.
- **Cenário 03** (20/20/20): baseline ≈ 18.3 para todos (91.5%).

O tráfego e o número de UEs são diferentes dos do paper; serve para motivar,
não para a tabela.

Experiência opcional para "What the two stacks show": carregar no srsRAN uma
Q-table treinada no OAI. A transferência é possível porque os estados e as
ações são os mesmos; mede quanto do treino depende da pilha.

---

## 7. Plano de integração (por ordem)

1. **Confirmar versões:** `srsdu --version` no `.209` e `srscu` no `.239`;
   confirmar que o `oran-sc-ric` no `.202` tem `e2sm_ccc_module.py`. Pedir ao
   Bruno o código do exportador do `ue_slice_map.json`.
2. **Broker de 3 UEs a 23.04 MHz** e teste de capacidade com `ue1..3`.
3. **Config do DU:** `e2sm_ccc_enabled: true` e `priority` por fatia. Testar à
   mão:
   - o `rrm_policy_ratio_set` pelo WebSocket;
   - um controlo CCC (`simple_ccc_xapp.py` com `sd`).

   Confirmar a mudança no log do scheduler (`SLICE RECONF`).
4. **Baselines:** sem fatias PF e RR, estáticas e prioridade nativa.
5. **xApp em Python** (`xApps/qlearning_slice_xapp.py`), com o esqueleto do
   `priority_prb_xapp_test.py`:
   - estado por KPM Style 4 + JSON `dl_bs`;
   - controlo por E2SM-CCC (3 fatias numa mensagem);
   - os parâmetros da Tabela II do paper, exceto u_sat;
   - log e Q-table no mesmo formato do agente C, para reutilizar
     `qlearning_results.py`.
6. **Tráfego:** 4 fases com `iperf3 -u -R` nos namespaces `ue1..3`, com
   marcadores de fase no formato de `qlearning_traffic.sh`.
7. **Corridas finais:** Q com tabela vazia ×5 e pré-treinada ×3, mais as
   baselines.

---

## 8. A confirmar

- [ ] Versão do srsRAN nos hosts do testbed (o estudo assume 25.10).
- [ ] Ordem retx/dados novos e mínimo de PRBs por grant em DL/UL no srsRAN
      (Tabela I, linha "Allocation").
- [ ] Interrupção de scheduling causada por cada controlo RC 2/6
      (`reconf_ue`).
- [ ] u_sat por fatia e velocidade real do ZMQ (`nof_dl_slots` por relatório).
- [ ] Correspondência entre `gnb_cu_ue_f1ap_id` (KPM) e índice/RNTI (JSON).
- [ ] RAN function ID do E2SM-CCC no E2 Setup e latência do ACK de controlo.
- [ ] Bug do `DRB.RlcSduDelayDl` (§5.3) reproduzido no testbed.

Nota para o Bruno: o README e o capítulo 4 descrevem o controlo como "E2SM-RC
Slice-Level PRB Quota". No srsRAN 25.10 esse controlo é, na prática, um limite
de PRBs por grant de cada UE (§3). Vale a pena ajustar a descrição.
