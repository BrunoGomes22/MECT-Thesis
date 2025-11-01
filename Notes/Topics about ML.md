## Training, Validation and Testing (3 key steps of ML)

| Phase      | What Happens                                              |
| ---------- | --------------------------------------------------------- |
| Training   | The model optimizes its weights to minimize loss function |
| Validation | The model is fine-tuned to prevent overfitting            |
| Testing    | The model is evaluated on completely new, unseen data     |
**Note**: Training and Validation are performed in loop
## Accuracy vs. Loss

- **Loss** -> This is a continuous value that measures of far the model is from the desired outputs (it should be minimized by the cost function)
- **Accuracy** -> This value measures how many correct predictions the model made

## ReLU (Rectified Linear Unit)

ReLU is an activation function defined as $f(x)=max⁡(0,x)$
- it outputs **x** if positive, or **0** if negative

In CNNs (image detection):
- Each filter (kernel) does a dot product between the weights of the filter and the pixel values of the input image and produces a **feature map**
- The **feature map** is a matrix of values that highlight the detected patterns in the image
	- These feature maps have **activation values**, that tell how strongly the pattern encoded in the filter match a certain region of the image
	- **Large positive values** mean that there is a **strong match**
	- **Large negative values** mean that an **inverse of the pattern is present**
	- Values near zero mean that there is no match

The ReLU function acts upon the produced values of the feature maps:
- It keeps the positive values (where a pattern is detected)
- It turns negative values to zeros 
- So ReLU is a gate that only allows the patterns encoded by each respective filter, everything else is removed

Benefits of ReLU:
- Adds **non-linearity** the network, so it can learn complex patterns
- Only significant features are active, which simplifies computation and speeds up training
- Makes optimization easier and faster

Drawback:
- Neurons that always output negative values can die
- **LeakyReLU** or **Parametric ReLU** solve this issue

**Additional notes**: 
- The kernel weights are optimized using backpropagation
- A convolution is a dot product