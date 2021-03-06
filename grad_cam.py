"""Based off of:
https://github.com/jacobgil/keras-grad-cam/blob/master/grad-cam.py
"""
from tensorflow.contrib.keras.api.keras.layers import Lambda
from tensorflow.contrib.keras.api.keras.models import load_model
from tensorflow.contrib.keras.api.keras.models import Sequential
from tensorflow.contrib.keras.api.keras import backend as k
from tensorflow.python.framework import ops
import tensorflow as tf
import numpy as np
import os
import fnmatch
from PIL import Image
import cv2
import json

# dimensions of the generated pictures for each filter.
img_width = 32
img_height = 32

# the name of the layer we want to visualize
layer_name = 'conv4'

# user input the name of the Image we want to visualize
INPUT = input('What file would you like to visualize the activation for: ')

# input directory
INPUT_FOLDER = './'
OUTPUT_FOLDER = './grad_CAMs/'
CLASS_INDEX = None


def find(pattern, path):
    result = []
    for root, dirs, files in os.walk(path):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                result.append(os.path.join(root, name))
    return result[0]


def load_image(path):
    img = Image.open(path).convert('RGB')  # read in as grayscale
    img = img.resize((img_width, img_height))
    img.load()  # loads the image into memory
    img_data = np.asarray(img, dtype="float")
    img_data = img_data / 255.
    img_data = img_data.reshape(1, img_height, img_width, 3)
    return img_data


def register_gradient():
    if "GuidedBackProp" not in ops._gradient_registry._registry:
        @ops.RegisterGradient("GuidedBackProp")
        def _GuidedBackProp(op, grad):
            dtype = op.inputs[0].dtype
            return grad * tf.cast(grad > 0., dtype) * \
                tf.cast(op.inputs[0] > 0., dtype)


def compile_saliency_function(model, activation_layer=layer_name):
    input_img = model.input
    layer_dict = dict([(layer.name, layer) for layer in model.layers[1:]])
    layer_output = layer_dict[activation_layer].output
    max_output = k.max(layer_output, axis=3)
    saliency = k.gradients(k.sum(max_output), input_img)[0]
    return k.function([input_img, k.learning_phase()], [saliency])


def modify_backprop(model, name):
    g = tf.get_default_graph()
    with g.gradient_override_map({'Relu': name}):

        # get layers that have an activation
        layer_dict = [layer for layer in model.layers[1:]
                      if hasattr(layer, 'activation')]

        # replace relu activation
        for layer in layer_dict:
            if layer.activation == tf.keras.activations.relu:
                layer.activation = tf.nn.relu

        # re-instantiate a new model
        new_model = load_model('cifarClassification.h5')
    return new_model


def deprocess_image(x):
    '''
    Same normalization as in:
    https://github.com/fchollet/keras/blob/master/examples/conv_filter_visualization.py
    '''
    if np.ndim(x) > 3:
        x = np.squeeze(x)
    # normalize tensor: center on 0., ensure std is 0.1
    x -= x.mean()
    x /= (x.std() + 1e-5)
    x *= 0.1
    # clip to [0, 1]
    x += 0.5
    x = np.clip(x, 0, 1)

    # convert to RGB array
    x *= 255
    x = np.clip(x, 0, 255).astype('uint8')
    return x


def decode_predictions(preds, top=5):
    """
    Adapted from: https://github.com/fchollet/keras/blob/master/keras/applications/imagenet_utils.py
    Decodes the prediction of an ImageNet model.
    # Arguments
        preds: Numpy tensor encoding a batch of predictions.
        top: integer, how many top-guesses to return.
    # Returns
        A list of lists of top class prediction tuples
        `(class_name, class_description, score)`.
        One list of tuples per sample in batch input.
    # Raises
        ValueError: in case of invalid shape of the `pred` array
            (must be 2D).
    """
    global CLASS_INDEX
    if len(preds.shape) != 2 or preds.shape[1] != 10:
        raise ValueError('`decode_predictions` expects '
                         'a batch of predictions '
                         '(i.e. a 2D array of shape (samples, 10)). '
                         'Found array with shape: ' + str(preds.shape))
    if CLASS_INDEX is None:
        fpath = find('class_index.json',
                     os.getcwd())
        CLASS_INDEX = json.load(open(fpath))
    results = []
    for pred in preds:
        top_indices = pred.argsort()[-top:][::-1]
        result = [tuple(CLASS_INDEX[str(i)]) + (pred[i],) for i in top_indices]
        result.sort(key=lambda x: x[1], reverse=True)
        results.append(result)
    return results


def grad_cam(input_model, image, category_index, layer_name):
    y_c = input_model.output[0, category_index]
    conv_output = input_model.get_layer(layer_name).output
    grads = k.gradients(y_c, conv_output)[0]
    gradient_function = k.function([input_model.input], [conv_output, grads])

    output, grads_val = gradient_function([image])
    output, grads_val = output[0, :], grads_val[0, :, :, :]

    weights = np.mean(grads_val, axis=(0, 1))
    cam = np.ones(output.shape[0:2], dtype=np.float32)

    for i, w in enumerate(weights):
        cam += w * output[:, :, i]

    cam = cv2.resize(cam, (img_width, img_height))
    cam = np.maximum(cam, 0)
    heatmap = cam / np.max(cam)

    # Return to BGR [0..255] from the preprocessed image
    image = image[0, :]
    image -= np.min(image)
    image = np.minimum(image, 255)

    cam = cv2.applyColorMap(np.uint8(255*heatmap), cv2.COLORMAP_JET)
    cam = np.float32(cam) + np.float32(image)
    cam = 255 * cam / np.max(cam)
    return np.uint8(cam), heatmap


preprocessed_input = load_image(find(INPUT,
                                     INPUT_FOLDER))
k.set_learning_phase(0)
model = load_model('cifarClassification.h5')
layer_dict = dict([(layer.name, layer) for layer in model.layers[1:]])
predictions = model.predict(preprocessed_input)

top_3 = decode_predictions(predictions)[0][0:3]
print('Predicted class:')
for x in range(0, len(top_3)):
    print('%s with probability %.2f' % (top_3[x][0], top_3[x][1]))

predicted_class = np.argmax(predictions)
cam, heatmap = grad_cam(model, preprocessed_input, predicted_class, layer_name)
cv2.imwrite(OUTPUT_FOLDER + "gradcam_" + INPUT[:-5] + "_" + layer_name + ".jpg", cam)
print('Gradiant class activation image saved in the current directory!')

register_gradient()
guided_model = modify_backprop(model, 'GuidedBackProp')
saliency_fn = compile_saliency_function(guided_model)
saliency = saliency_fn([preprocessed_input])
gradcam = saliency[0] * heatmap[..., np.newaxis]
cv2.imwrite(OUTPUT_FOLDER + "guided_gradcam_" + INPUT[:-5] + "_" + layer_name + ".jpg",
            deprocess_image(gradcam))
print('Guided gradient class activation map image saved in the current directory!')