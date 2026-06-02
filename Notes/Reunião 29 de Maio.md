
## Atualmente, a xApp disponibiliza duas técnicas de partilha de PRBs entre UEs

### Heuristic path
![[xapp_heuristic.png]]

As caps/limites de PRBs dos UEs são calculadas da seguinte maneira:
![[Pasted image 20260529135558.png]]

### Contextual Bandit path (simple RL)
![[xapp_contex_bandit.png]]

O contextual bandit, escolhe uma ação de 5 perfis disponíveis de forma a maximizar a sua recompensa para o estado atual da rede
![[Pasted image 20260529140231.png]]

A recompensa, neste momento, está a ser calculada da seguinte maneira:
![[Pasted image 20260529140918.png]]

- **Contextual bandit**: best action for the current situation
- **Reinforcement Learning**:  best sequence of actions over time
	- **RL** learns how to choose the current action based on both its immediate reward and its expected effect on future network states

## Testes de performance realizados

### Teste sem xApp
![[normal_sharing_tests.png]]
### Teste com xApp (Heuristic Path)
![[heuristic_sharing_tests.png]]
### Teste com xApp (Contextual Bandit Path)
![[context_bandit_sharing.png]]