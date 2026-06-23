Caros Orientadores,

Segue uma versão resumida da limitação identificada na srsRAN e da forma como o meu trabalho pretende endereçá-la.
### Quais são as limitações atuais de alocação de recursos na srsRAN?
- Na versão atual da srsRAN, os recursos rádio, nomeadamente os PRBs, são alocados de forma semelhante entre todos os UEs presentes na rede. Por exemplo, se existirem três UEs a transmitir simultaneamente em downlink, não existe uma diferenciação explícita na quantidade de PRBs atribuída a cada UE quando a rede se encontra congestionada. 
- Assim, caso existam UEs prioritários na rede que necessitem de melhor desempenho em downlink, por exemplo, taxas de transmissão mais elevadas, estes podem ficar limitados por receberem uma quantidade de recursos semelhante à dos restantes UEs.
### De que forma o meu trabalho mitiga estas limitações?
- O meu trabalho introduz um mecanismo de escalonamento de recursos entre UEs quando a rede se encontra congestionada. Para isso, foi desenvolvida uma xApp que associa diferentes níveis de prioridade a cada network slice.
- Desta forma, UEs pertencentes a slices diferentes podem receber uma quantidade distinta de PRBs, de acordo com a prioridade da slice em que estão inseridos. Assim, UEs associados a uma slice de alta prioridade podem obter mais PRBs, permitindo alcançar melhores taxas de transmissão em downlink. 
- Atualmente, estão implementadas duas técnicas de alocação de recursos: uma abordagem heurística e uma abordagem baseada no algoritmo contextual bandit. Futuramente, está também prevista a implementação de uma técnica baseada em machine learning.