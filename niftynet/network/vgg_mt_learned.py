# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

from six.moves import range

from niftynet.layer.convolution import ConvolutionalLayer
from niftynet.layer.convolution import LearnedCategoricalGroupConvolutionalLayer
from niftynet.layer.downsample import DownSampleLayer
from niftynet.network.base_net import BaseNet
from niftynet.layer.fully_connected import FullyConnectedLayer

import tensorflow as tf

class LearnedMTVGG16Net(BaseNet):
    """
    Multi-task VGG net
    Sharing done by:
        1) split just before last FC unit that is fed into loss function
            e.g. fc (1x1) --> l2 loss (age regression)
                 fc (1x2) --> binary softmax cross entropy (gender - 0/1)

    Learn task-specific and task-invariant kernels by learning:
    1) Dirichlet p (categorical parameters)
    2) x ~ Cat(p)

    Categorical is made differentiable through Gumbel-Softmax distribution that approximates
    a categorical as temperature approaches 0

    Learning either by:
    1) Soft weighting from p
    2) Soft weighting from x ~ Gumbel-Softmax(p)
    3) Hard stochastic weighting from x ~ Gumbel-Softmax(p)

    """

    def __init__(self,
                 task1_classes,
                 task2_classes,
                 w_initializer=None,
                 w_regularizer=None,
                 b_initializer=None,
                 b_regularizer=None,
                 acti_func='relu',
                 name='MT1_VGG16Net'):

        super(LearnedMTVGG16Net, self).__init__(
            task1_classes=task1_classes,
            task2_classes=task2_classes,
            w_initializer=w_initializer,
            w_regularizer=w_regularizer,
            b_initializer=b_initializer,
            b_regularizer=b_regularizer,
            acti_func=acti_func,
            name=name)

        self.layers = [
            {'name': 'layer_1', 'n_features': 64*2, 'kernel_size': 3, 'repeat': 2},
            {'name': 'maxpool_1'},
            {'name': 'layer_2', 'n_features': 128*2, 'kernel_size': 3, 'repeat': 2},
            {'name': 'maxpool_2'},
            {'name': 'layer_3', 'n_features': 256*2, 'kernel_size': 3, 'repeat': 3},
            {'name': 'maxpool_3'},
            {'name': 'layer_4', 'n_features': 512*2, 'kernel_size': 3, 'repeat': 3},
            {'name': 'maxpool_4'},
            {'name': 'layer_5', 'n_features': 512*2, 'kernel_size': 3, 'repeat': 3},
            {'name': 'maxpool_5'},
            {'name': 'fc_1', 'n_features': 4096*2},
            {'name': 'fc_2', 'n_features': 4096*2}]

        self.task1_layers = [
            {'name': 'task_1_out', 'n_features': task1_classes}]

        self.task2_layers = [
            {'name': 'task_2_out', 'n_features': task2_classes}]

    def layer_op(self, images, is_training=True, layer_id=-1, **unused_kwargs):

        # main network graph
        with tf.variable_scope('vgg_body'):
            flow, layer_instances = self.create_main_network_graph(images, is_training)

        # add task 1 output
        task1_layer = self.task1_layers
        with tf.variable_scope('task_1_fc'):
            fc_layer = FullyConnectedLayer(
                n_output_chns=task1_layer['n_features'],
                w_initializer=self.initializers['w'],
                w_regularizer=self.regularizers['w'],
            )
            task1_out = fc_layer(flow)
            layer_instances.append((fc_layer, task1_out))

        # add task 2 output
        task2_layer = self.task2_layers
        with tf.variable_scope('task_2_fc'):
            fc_layer = FullyConnectedLayer(
                n_output_chns=task2_layer['n_features'],
                w_initializer=self.initializers['w'],
                w_regularizer=self.regularizers['w'],
            )
            task2_out = fc_layer(flow)
            layer_instances.append((fc_layer, task2_out))

        if is_training:
            self._print(layer_instances)
            return [task1_out, task2_out]

        return layer_instances[layer_id][1]

    def create_main_network_graph(self, images, is_training):

        layer_instances = []
        for layer_iter, layer in enumerate(self.layers):

            # Get layer type
            layer_type = self._get_layer_type(layer['name'])

            if 'repeat' in layer:
                repeat_conv = layer['repeat']
            else:
                repeat_conv = 1

            # first layer
            if layer_iter == 0:
                conv_layer = LearnedCategoricalGroupConvolutionalLayer(
                    n_output_chns=layer['n_features'],
                    kernel_size=layer['kernel_size'],
                    categorical=True,
                    use_hardcat=True,
                    tau=0.5,
                    acti_func=self.acti_func,
                    w_initializer=self.initializers['w'],
                    w_regularizer=self.regularizers['w'],
                    name=layer['name'])
                flow = conv_layer(images, is_training)
                layer_instances.append((conv_layer, flow))
                repeat_conv = repeat_conv - 1

            # last layer
            if layer_iter == len(self.layers) - 1:
                fc_layer = FullyConnectedLayer(
                    n_output_chns=layer['n_features'],
                    w_initializer=self.initializers['w'],
                    w_regularizer=self.regularizers['w'],
                )
                flow = fc_layer(flow)
                layer_instances.append((fc_layer, flow))
                continue

            # all other
            if layer_type == 'maxpool':
                downsample_layer = DownSampleLayer(
                    kernel_size=2,
                    func='MAX',
                    stride=2)
                flow = downsample_layer(flow)
                layer_instances.append((downsample_layer, flow))

            elif layer_type == 'layer':

                for _ in range(repeat_conv):
                    conv_layer = ConvolutionalLayer(
                        n_output_chns=layer['n_features'],
                        kernel_size=layer['kernel_size'],
                        acti_func=self.acti_func,
                        w_initializer=self.initializers['w'],
                        w_regularizer=self.regularizers['w'],
                        name=layer['name'])
                    flow = conv_layer(flow, is_training)
                    layer_instances.append((conv_layer, flow))

            elif layer_type == 'fc':

                fc_layer = FullyConnectedLayer(
                    n_output_chns=layer['n_features'],
                    acti_func=self.acti_func,
                    w_initializer=self.initializers['w'],
                    w_regularizer=self.regularizers['w'],
                )
                flow = fc_layer(flow)
                layer_instances.append((fc_layer, flow))

        return flow, layer_instances

    @staticmethod
    def _print(list_of_layers):
        for (op, _) in list_of_layers:
            print(op)

    @staticmethod
    def _get_layer_type(layer_name):
        return layer_name.split('_')[0]