
**manter documento com todos os documentos lidos**

pontos essenciais do pitch:
- problema 
- soluções das redes 6G (com split e federated learning)
- potenciais aplicações:
	- RLC - Protocol Error Detection
	- RRC - Predict Network Conditions
	- RRC - Resource allocation optimization
	- MAC - Shared Network Access Management
- conclusão

# Discurso

## Contexto

Apesar das redes 5G continuarem em desenvolvimento e expansão, já existe um forte movimento de investigação e de primeiras implementações do seu sucessor – as redes 6G.

Estas redes prometem oferecer um ecossistema autónomo com inteligência e consciência parecida á de um ser humano. Adicionalmente, estas devem suportar:

  - comunicações holográficas que envolvem a transmissão em tempo real de projeções 3D;
  - latência extremamente baixa para comunicações da “Internet Industrial”;
  - conexão de "sub-redes" desde o espaço até ao fundo do oceano;
  - veículos completamente autónomos;
  - entre outros…

Portanto, para suportarem estas funcionalidades todas, as redes 6G devem reduzir a latência, aumentar a largura de banda e proteger melhor os dados dos utilizadores.

Estas melhorias que referi baseiam-se muito á volta de diversos algoritmos e aplicações de inteligência artificial (pois as redes 6G devem ser dinâmicas), e para serem eficazes em meios heterogéneos devem seguir um paradigma distribuído para evitar enviar toda a informação para um servidor central.

De forma a mitigar estes problemas a minha dissertação tem como objetivo explorar e implementar duas técnicas que se complementam para garantirem um melhor tratamento dos dados, o "**federated learning**" e o "**split learning**".

## Solutions for 6G Networks Applications

- No "**federated learning**"  cada dispositivo "edge" da rede treina localmente o seu próprio modelo, apenas as atualizações do modelo são enviadas para o servidor central. No servidor central serão agregados os modelos dos diferentes dos dispositivos presentes na rede;
- No "**split learning**" as primeiras camadas de um certo modelo, como por exemplo, uma rede neuronal são computadas nos dispositivos de "edge", as características intermédias obtidas do dispositivo são enviadas para o servidor e as restantes camadas do modelo são executadas.

Juntas, estas abordagens reduzem o consumo da largura de banda, melhoram a latência e permitem inteligência na rede mesmo em dispositivos com poucos recursos computacionais.

## 6G Networks Optimizations using Federated & Split Learning

Adicionalmente, o Federated e o Split Learning podem beneficiar a rede em aspetos de optimização tais como:
	- detecção de erros no protocolo RLC (Radio Link Control);
	- previsão de condições de rede no protocolo RRC (Radio Resource Control);
	- optimização de alocação de recursos no protocolo RRC;
	- gestão de acesso ao meio partilhado da rede ao nível MAC (Medium Access Control).
Estas aplicações mostram como a inteligência artificial pode possivelmente melhorar proativamente o desempenho da rede.

## Conclusão

Portanto, a minha dissertação tem como principal objetivo implementar e testar estas abordagens utilizando diversos dispositivos, tais como microcontroladores, servidores e afins de forma a simular cenários de rede 6G (redes heterogéneas) com o propósito principal de demonstrar casos em que a inteligência artificial prova ser uma mais valia para melhorar o desempenho da rede quer a nível da latência, da largura de banda e na preservação de dados privados.

