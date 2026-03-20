### 3º Deployment concluído

**Topologia**:
![[Deployment3.png|2000x300]]

**Benchmark**:
![[Pasted image 20260319093457.png]]

Notou-se um ligeiro aumento na velocidade de Downlink e diminuição na velocidade de Uplink.

### RICs Compatíveis com srsRAN

- [ORAN SC (Software Community) RIC](https://docs.o-ran-sc.org/en/latest/projects.html#near-realtime-ran-intelligent-controller-ric)
- [FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric)

### FlexRIC vs. ORAN SC RIC

-  **FlexRIC**
	- Vantagens:
		-  Fácil de dar deploy
		- Arquitetura mais simples
		- Não é necessário uma forte componente computacional
		- Mais fácil testar algoritmos
	- Limitações:
		-  A FlexRIC só suporta integração com a interface E2
		-  Não é a referência oficial da ORAN-Alliance
		-  Menos ferramentas de monitorização

- **ORAN SC RIC**
	- Vantagens:
		-  Parecido a "industry-grade" O-RAN deployments
		-  Suporta um maior leque de interfaces como A1, O1, SMO
		- Mais ferramentas de monitorização
	- Limitações:
		- Muito mais complexo de dar deploy e manter
		- Mais recursos computacionais necessários
		- Mais difícil testar e integrar algoritmos

### Limitações da interface E2 da srsRAN

- **Limitação 1**: Controlo da RAN limitado
	-  Na DU as xApps apenas conseguem alterar limites de PRBs por slice da rede
	- Na CU as xApps conseguem controlar quando se dá trigger dos handovers
- **Limitação 2**: métricas chegam a uma taxa de 1s
	- Isto pode ser um problem porque as decisões das xApps normalmente acontecem entre 10ms - 1s, funções de controlo que tenham de operar nesse timescale podem ser complicadas de implementar
- **Limitação 3**:  Poucas métricas são expostas
	- DL throughput
	- UL throughput
	- UL packet success rate
	- RLC DL packet rate
	- RLC DL transmitted SDU volume
	- RLC UL transmitted SDU volume
- Métricas dummy:
	- CQI (channel quality estimation)
	- RSRP (reference signal received power)
	- RSRQ (reference signal received quality)


Exemplo de xApp que obtem dados de UL e DL de um UE da rede:

![[Pasted image 20260319100124.png]]