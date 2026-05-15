- Não foi possível pedir os parâmetros corretos de NSSAI (slicing) no modem quectel
- Neste momento, existem 3 srsUEs ZMQ conectados à rede (nas respetivas slices configuradas)
- o setup srsUE OTA é extramamente instável, o gNB e o UE nunca estão sincronizados, foram tentadas as seguintes soluções para o problema:
	- alteração da largura de banda de 20 MHz para 10 MHz
	- foram tentadas diversas combinações de ganhos de transmissão e receção
	- foram tentadas diferentes frequências de offset
	- ambos os processos do gNB e do UE foram colocados em prioridade máxima do scheduler do CPU
	- **Nota**: na documentação da srsRAN é recomendado o uso de um clock externo de 10 MHz ou GPSDO

##### Priority PRB xApp 

![[priority_xapp.png]]