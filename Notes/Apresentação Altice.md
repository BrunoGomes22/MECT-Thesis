
### Slide srsRAN

- O srsRAN Project é uma implementação open-source de uma RAN 5G que segue normas do 3GPP e O-RAN Alliance. 
- É fornecido um gNB 5G NR que suporta uma separação explícita entre a DU e CU
- Integração com SDRs e ambientes emulados
- Suporte da interface E2

(explicar porque razão srsRAN foi escolhida ao invés de OAI)
- documentação mais clara e centralizada
- tutoriais e exemplos mais diretamente aplicáveis
- processo de instalação e configuração simples
- Isto permite concentrar o trabalho no desenvolvimento da xApp, sem ter de gastar muito tempo na configuração da infraestrutura 5G

### Slide Dissertação Bruno

(Talvez mostrar arquitetura do meu setup ORAN)

(Explicar em que consiste o meu projeto)

(E depois explicar o flow mais ou menos como está aqui abaixo)
1. Mostra o setup atual que segue a arquitetura ORAN
2. Há uma Near-RT RIC no qual reside uma xApp
3. a RIC comunica com a DU através da interface E2
4. Através da interface E2 pode-se obter diversas métricas da RAN, e também se podem enviar pedidos de controlo à RAN
5. No meu trabalho obtemos métricas ao nível da Cell como a % de PRBs utilizados, ao nível dos UEs como velocidades DL, delays RLC no DL
6. A partir destas métricas é calculado um QoS-score (composto por x,y,z) que identifica o quão congestionada se encontra a rede
7. Este QoS-score influencia as caps de PRBs atribuidas a cada UE
8. Quanto maior o QoS-score maiores vão ser as caps atribuidas a cada UE
9. No entanto, UEs com prioridades diferentes (que dependem da slice no qual estão registados), têm acesso a uma maior ou menor número de PRBs
10. UEs com prioridades altas têm limites de utilização maiores (100 - 0 %) enquanto UEs de prioridades mais baixas têm menores limites de utlização (0 - 20%)