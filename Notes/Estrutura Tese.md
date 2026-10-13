
# Capítulo 1 (Introduction)
- Dar contexto e motivação do trabalho
  - Scope
  - Motivation
  - Research Questions
  - Objectives
  - Contributions
  - Document structure
  
  
# Capítulo 2 (Background and state-of-the-art)
- Background da transição do 5G para 6G
- Falar de use-cases do 6G
- falar de requisitos e densificação da rede
- Technological enablers
- porquê ORAN no 6G
	- Explicar ORAN (arquitetura, interfaces)
	- dar hint de frameworks de ORAN
	- falar das vantagens comparando a arquiteturas tradicionais
- limitações das implementações ORAN atuais que podem limitar performance do 6G
	- Discutir com detalhe as limitações da ORAN
	- Justificação da escolha do case study do resource allocation
		- A vantagem de resolver o problema de resource allocation em ORAN
- Introduzir resource allocation
	- falar de algoritmos de alocação de recursos state-of-the-art (3GPP, AI-enhanced)
	- limitações atuais de resource allocation (por causa das limitações da ORAN atualmente) 
- pegar nas limitações atuais de resource allocation em O-RAN e propor os meus contributos (que testam diversos cenários)

# Capítulo 3 (O-RAN Baseline Testbed)
- falar das frameworks disponiveis
- falar do motivo da escolha de srsRAN
- explicar srsRAN
	- colocar métricas disponiveis
- falar dos setups que fiz
	 - mostrar topologias e o porquê de adoptar a arquitetura O-RAN
- mostrar caracterização da rede
	- comentar resultados dos testes de onde foi observado saturação de bitrate em DL
- Limitações encontradas e o que vamos fazer nos próximos capítulos

# Capítulo 4 (Resource Allocation Optimization)
- ligar saturação da rede a limitações atuais e o que vou fazer para mitigar o problema
- mostrar pseudo-código da xApp
- mostrar diagrama da xApp 
	- explicar a métrica proposta
	- explicar cada componente da métrica

# Capítulo 5 (Results and discussion)
- colocar gráficos de testes de 3UEs e 6UEs (baseline vs. controlo da xApp)
- falar de limitações de expansão de 3 UEs para 6 UES (a 10MHz)
- colocar gráficos de novos testes feitos com a configuração da cell a 20 MHz  para 6 UEs (que mostram melhoria de resultados ao nível da bitrate e da atuação da xApp)

# Capítulo 6 (Conclusions and Future Work)