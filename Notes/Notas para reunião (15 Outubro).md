
## Federated Learning
Federated learning (FL) allows distributed collaborative training of machine learning
(ML) models over a group of participating devices, with a centralized server used merely 
for training orchestration, essentially aggregating clients model updates and sharing
the newly-created model within the group

- Ideal for privacy preserving distributed AI, especially when using Deep Learning (DL) models;
- currently most popular FL framework -> [Flower](https://flower.ai/docs/framework/index.html)
- There's currently a testbed to evaluate trade-offs of different FL algorithms ([CoLExT](https://github.com/sands-lab/colext))
- "FedAvg" and "FedProx" are the most popular FL algorithms

**Federated Averaging (FedAvg)**

$$
w_{t+1} = \sum_{k} (\frac{n_{k}}{n}) w^{k}_{t}
$$
Meaning of each component:

| Symbol      | Meaning                                    |
| ----------- | ------------------------------------------ |
| $w_{t+1}$   | New global model weights after aggregation |
| $k$         | Index of each client device                |
| $w^{k}_{t}$ | Local model weights from client $k$        |
| $n_k$       | Number of training samples on client $k$   |
| $n$         | Total number of samples across all clients |

How does it work:
- each client trains its model locally and produces $w^{k}_{t}$
- The server collects all $w^{k}_{t}$
- It computes a **weighted average**, giving more weight to clients with more data
- The result is the **new global model** $w_{t+1}$ 
- The global model is then sent back to all clients for the next training round

**Pros**: simple, scalable and works well when client data is similar
**Cons**: struggles if client data is **non-IID** (Independent and Identically Distributed)


**Federated Proximal (FedProx)**

**FedProx** extends the **FedAvg** local training loss function $F_k(w)$ with a proximal term $\frac{\mu}{2} \| w - w_{t} \|^2$, where μ is a non-negative constant, that **penalizes large deviations** of the **local model** from the **global model**.

$$
F_k^{prox}(w) = F_k(w) + \frac{\mu}{2} \| w - w_{t} \|^2
$$
Meaning of each component:

| Symbol                            | Meaning                                               |
| --------------------------------- | ----------------------------------------------------- |
| $F_k(w)$                          | Local loss function for client $k$                    |
| $w_t$                             | Global model weights from the previous training round |
| $w$                               | Local model weights being optimized for client $k$    |
| μ                                 | Proximal term coefficient                             |
| $\frac{\mu}{2} \| w - w_{t} \|^2$ | Proximal regularization term                          |
How does it work:
- Each client still trains locally, but the extra **proximal term** discourages the local model from drifting too far from the global one
- This helps when clients have **very different data distributions** (non-IID) or **unequal compute power** which is common in 6G and edge scenarios
## Split Learning

Split Learning is a collaborative training approach where the server handles most of the heavy workload. This method improves data privacy and demonstrates its advantages by supporting tasks across a variety of devices with different computing capabilities

How it works:
- Each client executes the **first few layers** of the model using its own data, producing intermediate features (compressed data)
- The **intermediate features** are **sent to the server**, which **runs** the **remaining layers of the model**, performs **backpropagation**, and **sends gradient updates** back **to the clients**
 **Note**: The **server** not only adjusts its own weights but also computes **gradients** for the layers of the client side ("cut layer")

What is **backpropagation**?
- backpropagation is the algorithm that allows a neural network to learn from its mistakes
- How does it work?
	1. The network makes a prediction and compares the prediction value with the correct answer, in order to computer the **error** (loss)
	2. After getting the error, the networks starts working backwards, layer by layer, calculating how much each weight contributed to that error
	3. Using gradients, the networks updates the weights to make the next predictions more accurate

**Advantages** of using Split Learning:
- **Privacy**, because raw data never leaves the device
- **Reduces computational load** on edge devices, only the early layers are trained locally
- **Enables heterogeneous collaboration**, allowing both powerful and constrained devices to participate in the same training process
- **Improves scalability**

**Challenges** of using Split Learning:
- Increased **communication cost**, since intermediate features are exchanged frequently
- Finding the **optimal cut layer** is vital because it affects privacy, latency and accuracy

## Combing Split Learning and Federated Learning

How it works:

- Step 1 - **Model partitioning**
	- Each client and server share a single model, but it's divided into two segments:
		- Client-side model (front layers)
		- Server-side model (back layers)
	- The point where the model is separated is called **cut layer**

- Step 2 - **Local data processing and forwarding**
	- Each client inputs its **own local data** into the first few layers
	- The output of those layers (**intermediate features**) is sent to the server

- Step 3 - **Server data processing + backpropagation**
	- The server **receives intermediate features** from many clients
	- It runs the **remaining layers** of the model to produce predictions
	- It computes the **loss** and performs **backpropagation** on its own layers
	- The servers sends **gradients** back to each client so that they can update their own layers

- Step 4 - **Federated Aggregation**
	- Each client's local model will have slightly different weights (because of its unique data)
	- The server performs a **global aggregation** across all clients local models
	- The aggregated model is then **redistributed** to all clients for the next training round

- Step 5 - **Iteration**
	- The process repeats over multiple rounds:
		- Clients perform split training locally with the server
		- The global model is updated through federated aggregation
	- Eventually, the global model becomes well-trained across all clients data without seeing any raw data
## srsRAN

srsRAN is an open-source software radio framework that implements the 4G LTE and 5G NR protocol stacks

It allows to build, test, and analyze cellular networks entirely in software, without the need for commercial hardware.

Main Components:

| Component  | Role                                                             |
| ---------- | ---------------------------------------------------------------- |
| srsUE      | User equipment                                                   |
| srsGNB     | Base station (gNodeB)                                            |
| srs5G Core | Core network (manages sessions, authentications,handovers, etc.) |
**Note**: Software Defined Radios (SDRs) can also be integrated

Uses cases for my thesis:
- Gives me a **real network environment** to test AI-based optimizations
- Allows me to observe and manipulate layers such as **RLC**, **RRC**, and **MAC** where Split/Federated Learning might help improve resource allocation, error detection, or latency
