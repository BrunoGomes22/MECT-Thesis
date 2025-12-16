
### Which srsRAN branch is closest to the ORAN architecture?

The most popular srsRAN repositories are:
- [srsRAN_4G](https://github.com/srsran/srsRAN_4G) ->  Legacy LTE/4G implementation (eNB, EPC, UE)
- [srsRAN_Project](https://github.com/srsran/srsRAN_Project)-> Open source O-RAN 5G CU/DU solution from SRS

srsRAN_Project will be the chosen repository due to its architecture:

![[Pasted image 20251107093202.png]]
Fig. 1: Split 7.2x Architecture

![[Pasted image 20251107094938.png]]
Fig. 2: Simplified overview of O-RAN gNB architecutre

![[Pasted image 20251107092817.png]]
Fig. 3: High level overview of srsRAN Project codebase architecture showing all main components and interfaces

**Present O-RAN architecture features:**
- CU/DU split via the F1 interface
- DU/RU split via Open Fronthaul interface
- CU-CP/CU-UP split via E1 interface
- E2 interface for near-RT RIC control

All of these elements have been implemented in software and are fully performant, customizable and compliant with the O-RAN standard. Users can also integrate 3rd-party RICs, RUs, and gNB components with srsRAN Project components.

### In which RICs should Split and Federated Learning algorithms  be applied?

**Note:** The E2 interface that is responsible to communicate with the RICs is still under development and its current version is limited in features and operation, however, the E2 interface can support communications with some third-party RICs such as [ORAN SC RIC](https://docs.o-ran-sc.org/en/latest/projects.html#near-realtime-ran-intelligent-controller-ric) and [FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric).

![[Pasted image 20251107113544.png]]
Fig. 4: Example of a RIC framework implementation

O-RAN introduces two RICs:

| Controller  | Timescale   | Location      | Hosts |
| ----------- | ----------- | ------------- | ----- |
| Near-RT RIC | ~10 ms - 1s | Edge cloud    | xApps |
| Non-RT RIC  | > 1s        | Central cloud | rApps |

**Purpose of xApps**
- xApps run inside the **Near-Real-Time RIC**, and they monitor and control the RAN through the **E2 interface**
- They can optimize radio resources, handover, load balacing, etc., in near-real-time
- xApps use data collected from DUs and CUs

**Purpose of rApps**
- Handle higher-level, policy-driven tasks that don't need instant reaction
- They analyze long-term patterns and optimize network performance at a strategic level
- They communicate with the Near-RT RIC via the **A1 interface**, providing guidance, policies and ML models to steer the behavior of xApps

**Simple use case:**
1. Each xApp can train locally using network data from nearby DUs
2. Non-RT RIC aggregates models
3. Split learning can operate between DUs and xApps to offload heavy computation

**Problem:** Most of this would be done inside the cloud which defeats the purpose of using split and federated learning algorithms

### RAN processing functions to apply Split and Federated Learning algorithms

Example use cases:

| Function | Possible ML Application                   |
| -------- | ----------------------------------------- |
| RLC      | Error detection                           |
| RRC      | Mobility prediction / Resource Allocation |
| MAC      | Scheduling and interference management    |
| PHY      | Channel estimation / adaptive modulation  |
- Channel estimation: understand how the wireless medium distorts the signal
- Adapative Modulation: choose transmission scheme based on channel quality

###  How to install and run srsRAN

- https://docs.srsran.com/projects/project/en/latest/user_manuals/source/installation.html (Split 7.2 or Vanilla installation?)
- https://docs.srsran.com/projects/project/en/latest/user_manuals/source/running.html

Components to install:
- 1 gnB (srsRAN)
- 1 5G core (which?)
- 1 UE (my PC)