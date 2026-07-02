
### Métricas obtidas

Analisando o source code da srsRAN, é possível obter as seguintes métricas (para além das documentadas):

Ao nível da **DU**:

| Métrica                       | Descrição                           | Validade                                                                            |
| ----------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------- |
| DRB.UEThpDL                   | Downlink throughput per UE          | Valores válidos                                                                     |
| DRB.UEThpUL                   | Uplink throughput per UE            | Valores válidos                                                                     |
| CQI                           | Channel Quality Indicator           | Valor constante de 15 que não se altera mesmo que o UE se encontre afastado do USRP |
| RSRP                          | Reference Signal Received Power     | Valores que variam entre 0 e 18 (provavelmente inválidos)*                          |
| RSRQ                          | Reference Signal Received Quality   | Valores que variam entre 0 e 18 (provavelmente inválidos)*                          |
| RRU.PrbAvailDl                | unused DL PRBs                      | Valores válidos                                                                     |
| RRU.PrbAvailUl                | unused UL PRBs                      | Valores válidos                                                                     |
| RRU.PrbUsedDl                 | PRBs used in DL                     | Valores válidos                                                                     |
| RRU.PrbUsedUl                 | PRBs used in UL                     | Valores válidos                                                                     |
| RRU.PrbTotDl                  | Total percentage of used PRBs in DL | Valores válidos                                                                     |
| RRU.PrbTotUl                  | Total percentage of used PRBs in UL | Valores válidos                                                                     |
| DRB.RlcPacketDropRateDl       | DL buffering delay                  | Valores pouco fidedignos (valores negativos ou muito altos)                         |
| DRB.RlcPacketDropRateDl       | DL packet loss rate                 | O valor parece ser sempre 0 (talvez seja válido)                                    |
| DRB.RlcSduTransmittedVolumeDL | DL data volume                      | Valores válidos                                                                     |
| DRB.RlcSduTransmittedVolumeUL | UL data volume                      | Valores válidos                                                                     |
| DRB.AirIfDelayUl              | UL air interface delay              | O valor é sempre 40ms, talvez não seja válido                                       |
| DRB.RlcDelayUl                | UL RLC delay                        | Valores válidos                                                                     |
| RACH.PreambleDedCell          | random access attempts detected     | -                                                                                   |

Ao nível da **CU**:

| Métrica                      | Descrição                                 | Validade    |
| ---------------------------- | ----------------------------------------- | ----------- |
| DRB.PdcpReordDelayUl         | Average UL PDCP reordering delay          | não testado |
| DRB.PacketSuccessRateUlgNBUu | UL packet success rate on the gNB-Uu side | não testado |
Verificação do número de PRBs:
$$
51 \times 12 \times 30 \,\text{kHz} = 18.36 \,\text{MHz}
$$

### Testes realizados com a OSC RIC

#### Teste DL

![[Pasted image 20260402100417.png]]

#### Teste UL

![[Pasted image 20260402100618.png]]

#### xApp Dashboard

![[Pasted image 20260402110059.png]]

### IAB (Integrated Access and Backhaul)

- O **IAB** trata-se de um arquitetura 5G em que a mesma interface de rádio pode ser usada simultaneamente para servir UEs e estabelecer conexões entre outras base stations.

![[Pasted image 20260402114251.png]]

Componentes do IAB:
- **IAB Donor**
	- gNB conectado ao core da rede
	- gateway para o core
- **IAB Node**
	- Função de Acesso:
		- garante acesso de UEs
	- Função de Backhaul:
		- conexão wireless a outros gNBs

### Use-cases retirados de papers
1. **ColO-RAN: Developing Machine Learning-Based xApps for Open RAN Closed-Loop Control on Programmable Experimental Platforms**
	- **RAN Slicing Control xApp**
		- control dinâmico de PRBs em diversos slices da rede (eMBB, URLLC, MTC)
		- DRL (Deep Reinforcement Learning) é utilizado para o tráfego se adaptar aos diferentes QoS das slices
	- **Scheduling Policy Optimization xApp**
		- xApp responsável por atribuir diferentes algoritmos de scheduling ás slices da rede
		- o algoritmo muda conforme os requisitos das slices

2. **Open RAN xApps Design and Evaluation: Lessons Learnt and Identified Challenges**
	  -   **Beam Mobility Management xApp**
		  - reduz falhas de ligação ao rádio
		  - requer métricas de rádio e localização dos diferentes UEs
	- **Signaling Storm Detection xApp**
		- deteção de ataques ou sobre-carga da rede
		- requer verificação de retransmissão de dados, volume de dados, periodicidade de comunicação, etc.
	- **Traffic Steering xApp**
		- Redirecionamente dos UEs para diferentes cells

3. **REAL: Reinforcement Learning-Enabled xApps for Experimental Closed-Loop Optimization in O-RAN with OSC RIC and srsRAN**
	- **RL-based network slicing xApp**
		- alocação dinâmica de PRBs por diferentes slices tais como eMBB,URLLC e mMTC
		- o mecanismo de reward combina métricas de latencia, throughput e sucesso de entrega de pacotes
		
![[Pasted image 20260402142807.png]]