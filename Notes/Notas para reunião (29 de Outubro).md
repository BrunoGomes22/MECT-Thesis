
## AI Inference vs. Training

- **Training** -> This is the first phase for an AI model, where the model learns patterns from a large dataset.
- **Inference** -> This is the process that follows AI training, it uses the already-trained model to make predictions on new data.

**Training** requires significantly more computational and energy resources than **inference**, since it **involves** both **forward** and **backward passes** with iterative parameter updates, while **inference** only performs a single forward pass.

Reminder:
- **Forward Pass** -> make a prediction
- **Backward Pass** (backprop) -> send the error signal backward, update the weights to make the model better.

Hardware requirements:
- **Training**, often requires high-performance hardware like **GPUs** and **TPUs**, to process large datasets and perform complex calculations.
- **Inference** is a process that can run on a single GPU or even a CPU, it usually demands efficiency in real-time scenarios with lighter computational requirements. 

## Training, Validation and Testing in ML

| **Stage**         | **What Happens**                                                                    | **Goal**                                | **Data Used**      |
| ----------------- | ----------------------------------------------------------------------------------- | --------------------------------------- | ------------------ |
| **1. Training**   | The model learns patterns by minimizing the loss through backpropagation            | Learn from known data                   | **Training set**   |
| **2. Validation** | The model is tested during training to tune hyperparameters and prevent overfitting | Optimize and fine-tune the model        | **Validation set** |
| **3. Testing**    | The final model is evaluated on completely unseen data                              | Measure true generalization performance | **Test set**       |
**Note**: **Overfitting** is when a machine learning model becomes too specialized to its training data, causing it to perform poorly on unseen data.
## O-RAN

**O-RAN (Open Radio Access Network)** is an **open, standardized, and disaggregated version** of the traditional Radio Access Network (RAN) used in 4G/5G/6G.

- Traditional RAN: hardware and software are **vendor-locked**, which means that one company controls the whole base station.
- O-RAN: splits the RAN into **modular components** with **open interfaces**, so parts from different vendors can interoperate.

The main goals of O-RAN is to provide **flexibility**, **interoperability**, **cost reduction**, and **integration of AI**.

### O-RAN functional units

O-RAN separates the RAN into **three main functional units** plus **controllers** that manage them.

The **three main functional units** are: **O-RU** (radio unit), **O-DU** (distributed unit), **O-CU** (centralized unit)

| Component | Location            | **Function**                                    | Example Tasks                          |
| --------- | ------------------- | ----------------------------------------------- | -------------------------------------- |
| **O-RU**  | Antenna site        | RF transmission/reception, front-end processing | Beamforming, digital-analog conversion |
| **O-DU**  | Edge site           | Real-time baseband processing                   | Scheduling, MAC, RLC                   |
| **O-CU**  | Regional/cloud site | Higher-layer control                            | Mobility, encryption, PDCP, RRC        |
The **O-RAN ALLIANCE** has defined different interfaces within the RAN including those for:
- **Fronthaul** between the **Radio Unit** and the **Distributed Unit**.
- **Midhaul** between the **Distributed Unit** and the **Centralised Unit**.

![[Pasted image 20251028174719.png]]
### RAN Intelligent Controllers (RICs)

There are two **AI-driven** control layers that make the RAN smart and adaptable:

| Controller         | Timescale               | Role                                                                                               |
| ------------------ | ----------------------- | -------------------------------------------------------------------------------------------------- |
| Near-Real-Time RIC | ~10 ms -1 s             | Optimizes radio resources in real time via "xApps"                                                 |
| Non-Real-Time RIC  | > 1s (seconds to hours) | Uses “rApps” for long-term optimization, policy management, data analytics, and ML model training. |
**Note**: "xApps" and "rApps" are small apps that control the RAN dynamically.

### Open Interfaces

![[O-RAN.png]]

| Interface          |                              Connects | Function                                                                                                                |
| ------------------ | ------------------------------------: | ----------------------------------------------------------------------------------------------------------------------- |
| **Open Fronthaul** |                       **O-RU ↔ O-DU** | Real-time radio transport and lower-PHY functions (user plane, control plane, management plane).                        |
| **F1**             |       **O-DU ↔ O-CU (CU-UP / CU-CP)** | Splits RAN between DU and CU: carries user plane and control plane messages (scheduling / RRC signaling bridging).      |
| **E1**             | **CU-CP ↔ CU-UP** (internal CU split) | Internal split inside CU (control plane vs user plane). Allows CU components to be scaled independently.                |
| **E2**             |         **Near-RT RIC ↔ O-CU / O-DU** | Near-real-time control and telemetry: xApps communicate with CU/DU for real-time RAN optimization (latencies ~10ms–1s). |
| **A1**             |         **Non-RT RIC  ↔ Near-RT RIC** | Policy and enrichment interface: non-real-time decisions (rApps) push policies/models to Near-RT RIC.                   |
| **O1**             |                 **SMO ↔ (DU/CU/RIC)** | Configuration, fault, performance, and lifecycle management telemetry.                                                  |
| **O2**             |                    **SMO ↔ O-Cloud ** | Orchestration, cloud resource management, VNF/CNF lifecycle (placement, scaling).                                       |
| **D2**             |         **Near-RT RIC ↔ Near-RT RIC** | P2P coordination between different RICs                                                                                 |
## Relevant Split and federated learning scenarios in 6G

- **Autonomous Driving**
- **Industrial IoT (Smart Factories)**
- **Smart Cities**

## Meta Learning Applied to FL
- Meta-federated learning trains a meta-model at the server so clients can adapt quickly to their own local data; 

- Each round the server sends the meta-model to selected clients and each client fine-tunes it locally for a few steps and sends back the adapted updates;

- The server uses those updates to improve the meta-model so future clients need even fewer local steps. 

- This helps with non-IID data and fast personalization in dynamic 6G environments.

**FedMeta** is a well-known federated meta-learning framework.