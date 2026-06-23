### Esquema ilustrativo do deployment atual
![[Deployment4.png]]

### Estrutura do ficheiro que é enviado à xApp
![[ue_slice_map.png]]

Agora a xApp tem a noção das slices presentes na rede e pode definir prioridades com base no tipo de slice onde o UE está inserido, ao invés de definir prioridades com base em IDs temporários


### Comando para executar xApp antiga
```
docker compose exec python_xapp_runner ./priority_prb_xapp_test.py   
	--e2_node_id gnbd_001_001_00019b_0  
	--best_effort_ues 2   
	--medium_ues 0   
	--high_ues 1 
	--enable_control 
```

### Comando para executar xApp atual
```
docker compose exec python_xapp_runner ./priority_prb_xapp_test.py
	--e2_node_id gnbd_001_001_00019b_0
	--ue_slice_map_source http://192.168.81.239:8088/ue_slice_map.json
	--slice_priority_map "1-ffffff=best_effort,1-000001=medium,1-000002=high" 
	--enable_control
```