
### Radio Resource Allocation Control xApp Tests

Foi testada uma xApp cuja funcionalidade é alterar o limite de PRBs disponibilizados (entre 10% e 100%) a cada 20 MB de dados recebidos em Downlink por cada UE presente na rede.

#### **Resultados**

- Mudança de limite de PRBs para 100%:

Logs da **xApp**
![[xApp_logs_PRB100.jpg]]

Logs da **DU**
![[DU_logs_PRB100.png]]

- Mudança de limite de PRBs para 10%:

Logs da **xApp**
![[xApps_logs_PRB10.jpg]]

Logs da **DU**
![[DU_logs_PRB10.png]]

Gráfico de performance da rede durante o teste
![[KPM_plots.png]]

**Nota**: Só foi usado um UE (modem quectel) para todos os testes

### Como é feita a Resource Allocation na srsRAN?

1. O scheduler é executado a cada time slot (~1 ms )
2. No início de cada time slot são recolhidas informações como:
	- buffers de cada UE
	- qualidade do canal
	- retransmissões (HARQ)
3. Antes de distribuir PRBs para dados, existe uma reserva de recursos para mensagens de controlo
4. Se o slicing estiver ativado:
	- cada slice tem um limite mínimo e máximo de PRBs
	- slices abaixo do mínimo têm prioridade
	- slices acima do máximo não podem receber mais PRBs
	- a alocação é feita primeiro entre slices, e depois entre UEs dentro de cada slice
5. Dentro de cada slice:
	- primeiro são tratadas retransmissões
	- depois são alocados PRBs para novos dados
6. Os UEs são servidos conforme a scheduling policy configurada:
	- **RR** (Round Robin): prioridade para UEs menos recentemente servidos
	- **PF** (Proportional Fair): dá prioridade aos UEs com boa qualidade no canal de comunicação sem fazer starving dos restantes

### Diferenças srsUE 

#### srsUE Over-The-Air
- Usa um USRP para transmitir e receber sinais de RF
- Liga-se a um gNB real
- Pode coexistir com **UEs comerciais (COTS)** na mesma célula
- Comporta-se como um **telemóvel real** do ponto de vista da rede
- Usa um **USIM em software** (IMSI, K, OPc definidos em config)

![[Pasted image 20260417144737.png]]

#### srsUE ZMQ
- O ZMQ é responsável por simular o sinal rádio entre o gNB e o srsUE
- Não usa RF, a comunicação é feita via **ZeroMQ**
- Liga-se a um **gNB também em modo ZMQ**
- Não pode ser usado juntamente com **UEs reais**
	- seria necessário dois gNBs em separado, um simulado e outro real

![[Pasted image 20260417145230.png]]