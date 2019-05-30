# -*- coding: utf-8 -*-
import tensorflow as tf

from niftynet.application.base_application import BaseApplication
from niftynet.engine.application_factory import \
    ApplicationNetFactory, InitializerFactory, OptimiserFactory
from niftynet.engine.application_variables import \
    CONSOLE, NETWORK_OUTPUT, TF_SUMMARIES
from niftynet.engine.sampler_grid_v2 import GridSampler
from niftynet.engine.sampler_resize_v2 import ResizeSampler
from niftynet.engine.sampler_uniform_v2 import UniformSampler
from niftynet.engine.sampler_weighted_v2 import WeightedSampler
from niftynet.engine.sampler_balanced_v2 import BalancedSampler
from niftynet.engine.windows_aggregator_grid import GridSamplesAggregator
from niftynet.engine.windows_aggregator_resize import ResizeSamplesAggregator
from niftynet.io.image_reader import ImageReader
from niftynet.layer.crop import CropLayer
from niftynet.layer.histogram_normalisation import \
    HistogramNormalisationLayer

from niftynet.layer.loss_regression import LossFunction as LossSegFunction
from niftynet.layer.loss_segmentation import LossFunction as LossRegFunction
from niftynet.layer.loss_bayesian_regression import LossBayesianRegFunction
from niftynet.layer.loss_bayesian_segmentation import LossBayesianSegFunction

from niftynet.layer.mean_variance_normalisation import \
    MeanVarNormalisationLayer
from niftynet.layer.pad import PadLayer
from niftynet.layer.post_processing import PostProcessingLayer
from niftynet.layer.rand_flip import RandomFlipLayer
from niftynet.layer.rand_rotation import RandomRotationLayer
from niftynet.layer.rand_spatial_scaling import RandomSpatialScalingLayer
from niftynet.layer.rgb_histogram_equilisation import \
    RGBHistogramEquilisationLayer
from niftynet.evaluation.regression_evaluator import RegressionEvaluator
from niftynet.layer.rand_elastic_deform import RandomElasticDeformationLayer

SUPPORTED_INPUT = set(['image_1', 'image_2', 'output_1', 'output_2', 'weight', 'sampler', 'inferred'])


class MultiInputApplication(BaseApplication):
    REQUIRED_CONFIG_SECTION = "MULTIINPUT"

    def __init__(self, net_param, action_param, action):
        BaseApplication.__init__(self)
        tf.logging.info('starting multi-task application')
        self.action = action

        self.net_param = net_param
        self.action_param = action_param

        self.data_param = None
        self.multiinput_param = None
        self.SUPPORTED_SAMPLING = {
            'uniform_1': (self.initialise_uniform_sampler_input_1,
                          self.initialise_grid_sampler,
                          self.initialise_grid_aggregator),
            'uniform_2': (self.initialise_uniform_sampler_input_2,
                          self.initialise_grid_sampler,
                          self.initialise_grid_aggregator),
            'weighted': (self.initialise_weighted_sampler,
                         self.initialise_grid_sampler,
                         self.initialise_grid_aggregator),
            'resize': (self.initialise_resize_sampler,
                       self.initialise_resize_sampler,
                       self.initialise_resize_aggregator),
            'balanced': (self.initialise_balanced_sampler,
                         self.initialise_grid_sampler,
                         self.initialise_grid_aggregator),
        }

    def initialise_dataset_loader(
            self, data_param=None, task_param=None, data_partitioner=None):

        self.data_param = data_param
        self.multiinput_param = task_param

        # initialise input image readers
        if self.is_training:
            reader_names_input_1 = ('image_1', 'output_1', 'weight_1', 'sampler')
            reader_names_input_2 = ('image_2', 'output_2', 'weight_2', 'sampler')
        elif self.is_inference:
            # in the inference process use `image` input only
            reader_names_input_1 = 'image_1'
            reader_names_input_2 = 'image_2'
        elif self.is_evaluation:
            reader_names_input_1 = ('image_1', 'output_1', 'inferred_1')
            reader_names_input_2 = ('image_2', 'output_2', 'inferred_2')
        else:
            tf.logging.fatal(
                'Action `%s` not supported. Expected one of %s',
                self.action, self.SUPPORTED_PHASES)
            raise ValueError
        try:
            reader_phase = self.action_param.dataset_to_infer
        except AttributeError:
            reader_phase = None
        file_lists = data_partitioner.get_file_lists_by(
            phase=reader_phase, action=self.action)

        self.readers_input_1 = [
            ImageReader(reader_names_input_1).initialise(
                data_param, task_param, file_list) for file_list in file_lists]

        self.readers_input_2 = [
            ImageReader(reader_names_input_2).initialise(
                data_param, task_param, file_list) for file_list in file_lists]

        # initialise input preprocessing layers
        mean_var_normaliser = MeanVarNormalisationLayer(image_name='image') \
            if self.net_param.whitening else None
        histogram_normaliser = HistogramNormalisationLayer(
            image_name='image',
            modalities=vars(task_param).get('image'),
            model_filename=self.net_param.histogram_ref_file,
            norm_type=self.net_param.norm_type,
            cutoff=self.net_param.cutoff,
            name='hist_norm_layer') \
            if (self.net_param.histogram_ref_file and
                self.net_param.normalisation) else None
        rgb_normaliser = RGBHistogramEquilisationLayer(
            image_name='image',
            name='rbg_norm_layer') if self.net_param.rgb_normalisation else None

        normalisation_layers = []
        if histogram_normaliser is not None:
            normalisation_layers.append(histogram_normaliser)
        if mean_var_normaliser is not None:
            normalisation_layers.append(mean_var_normaliser)
        if rgb_normaliser is not None:
            normalisation_layers.append(rgb_normaliser)

        volume_padding_layer = []
        if self.net_param.volume_padding_size:
            volume_padding_layer.append(PadLayer(
                image_name=SUPPORTED_INPUT,
                border=self.net_param.volume_padding_size,
                mode=self.net_param.volume_padding_mode))

        # initialise training data augmentation layers
        augmentation_layers = []
        if self.is_training:
            train_param = self.action_param
            if train_param.random_flipping_axes != -1:
                augmentation_layers.append(RandomFlipLayer(
                    flip_axes=train_param.random_flipping_axes))
            if train_param.scaling_percentage:
                augmentation_layers.append(RandomSpatialScalingLayer(
                    min_percentage=train_param.scaling_percentage[0],
                    max_percentage=train_param.scaling_percentage[1],
                    antialiasing=train_param.antialiasing,
                    isotropic=train_param.isotropic_scaling))
            if train_param.rotation_angle:
                rotation_layer = RandomRotationLayer()
                if train_param.rotation_angle:
                    rotation_layer.init_uniform_angle(
                        train_param.rotation_angle)
                augmentation_layers.append(rotation_layer)
            if train_param.do_elastic_deformation:
                spatial_rank = list(self.readers[0].spatial_ranks.values())[0]
                augmentation_layers.append(RandomElasticDeformationLayer(
                    spatial_rank=spatial_rank,
                    num_controlpoints=train_param.num_ctrl_points,
                    std_deformation_sigma=train_param.deformation_sigma,
                    proportion_to_augment=train_param.proportion_to_deform))

        # augmentation + pre-processing on both readers
        self.readers_input_1[0].add_preprocessing_layers(
            volume_padding_layer + normalisation_layers + augmentation_layers)
        self.readers_input_2[0].add_preprocessing_layers(
            volume_padding_layer + normalisation_layers + augmentation_layers)

        for reader in self.readers_input_1[1:]:
            reader.add_preprocessing_layers(
                volume_padding_layer + normalisation_layers)

        for reader in self.readers_input_2[1:]:
            reader.add_preprocessing_layers(
                volume_padding_layer + normalisation_layers)

    def initialise_uniform_sampler_input_1(self):
        self.sampler_input_1 = [[UniformSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            windows_per_image=self.action_param.sample_per_volume,
            queue_length=self.net_param.queue_length) for reader in
            self.readers_input_1]]

    def initialise_uniform_sampler_input_2(self):
        self.sampler_input_2 = [[UniformSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            windows_per_image=self.action_param.sample_per_volume,
            queue_length=self.net_param.queue_length) for reader in
            self.readers_input_2]]

    def initialise_weighted_sampler(self):
        self.sampler = [[WeightedSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            windows_per_image=self.action_param.sample_per_volume,
            queue_length=self.net_param.queue_length) for reader in
            self.readers]]

    def initialise_resize_sampler(self):
        self.sampler = [[ResizeSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            shuffle=self.is_training,
            smaller_final_batch_mode=self.net_param.smaller_final_batch_mode,
            queue_length=self.net_param.queue_length) for reader in
            self.readers]]

    def initialise_grid_sampler(self):
        self.sampler = [[GridSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            spatial_window_size=self.action_param.spatial_window_size,
            window_border=self.action_param.border,
            smaller_final_batch_mode=self.net_param.smaller_final_batch_mode,
            queue_length=self.net_param.queue_length) for reader in
            self.readers]]

    def initialise_balanced_sampler(self):
        self.sampler = [[BalancedSampler(
            reader=reader,
            window_sizes=self.data_param,
            batch_size=self.net_param.batch_size,
            windows_per_image=self.action_param.sample_per_volume,
            queue_length=self.net_param.queue_length) for reader in
            self.readers]]

    def initialise_grid_aggregator(self):
        self.output_decoder = GridSamplesAggregator(
            image_reader=self.readers[0],
            output_path=self.action_param.save_seg_dir,
            window_border=self.action_param.border,
            interp_order=self.action_param.output_interp_order,
            postfix=self.action_param.output_postfix,
            fill_constant=self.action_param.fill_constant)

    def initialise_resize_aggregator(self):
        self.output_decoder = ResizeSamplesAggregator(
            image_reader=self.readers[0],
            output_path=self.action_param.save_seg_dir,
            window_border=self.action_param.border,
            interp_order=self.action_param.output_interp_order,
            postfix=self.action_param.output_postfix)

    def initialise_sampler(self):
        if self.is_training:
            self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_1][0]()
            self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_2][0]()
        elif self.is_inference:
            self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_1][1]()
            self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_2][0]()

    def initialise_aggregator(self):
        self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_1][2]()
        self.SUPPORTED_SAMPLING[self.multiinput_param.window_sampling_2][2]()

    def initialise_network(self):
        w_regularizer = None
        b_regularizer = None
        reg_type = self.net_param.reg_type.lower()
        decay = self.net_param.decay
        if reg_type == 'l2' and decay > 0:
            from tensorflow.contrib.layers.python.layers import regularizers
            w_regularizer = regularizers.l2_regularizer(decay)
            b_regularizer = regularizers.l2_regularizer(decay)
        elif reg_type == 'l1' and decay > 0:
            from tensorflow.contrib.layers.python.layers import regularizers
            w_regularizer = regularizers.l1_regularizer(decay)
            b_regularizer = regularizers.l1_regularizer(decay)

        self.net = ApplicationNetFactory.create(self.net_param.name)(
            num_classes=self.multiinput_param.num_classes,
            w_initializer=InitializerFactory.get_initializer(
                name=self.net_param.weight_initializer),
            b_initializer=InitializerFactory.get_initializer(
                name=self.net_param.bias_initializer),
            w_regularizer=w_regularizer,
            b_regularizer=b_regularizer,
            acti_func=self.net_param.activation_function)

    def connect_data_and_network(self,
                                 outputs_collector=None,
                                 gradients_collector=None):

        def switch_sampler(for_training, input_number):
            with tf.name_scope('train' if for_training else 'validation'):
                if input_number == 1:
                    sampler = self.get_sampler_input_1()[0][0 if for_training else -1]
                else:
                    sampler = self.get_sampler_input_2()[0][0 if for_training else -1]
                return sampler.pop_batch_op()

        def get_data_dict(input_number):
            if self.action_param.validation_every_n > 0:
                data_dict = tf.cond(tf.logical_not(self.is_validation),
                                    lambda: switch_sampler(for_training=True, input_number=input_number),
                                    lambda: switch_sampler(for_training=False, input_number=input_number))
            else:
                data_dict = switch_sampler(for_training=True, input_number=input_number)
            return data_dict

        if self.is_training:

            data_dict_input_1 = get_data_dict(1)
            data_dict_input_2 = get_data_dict(2)

            image_input_1 = tf.cast(data_dict_input_1['image_1'], tf.float32)
            image_input_2 = tf.cast(data_dict_input_2['image_2'], tf.float32)
            image = tf.stack([image_input_1, image_input_2], axis=0)

            net_args = {'is_training': self.is_training,
                        'keep_prob': self.net_param.keep_prob}

            # net_out is a dictionary of tensors with following possible fields:
            # task_i_prediction - regression/segmentation/classification for task_i
            # task_i_noise - if modelled, heteroscedastic noise for task_i
            net_out = self.net(image, **net_args)

            with tf.name_scope('Optimiser'):
                optimiser_class = OptimiserFactory.create(
                    name=self.action_param.optimiser)
                self.optimiser = optimiser_class.get_instance(
                    learning_rate=self.action_param.lr)
            loss_func = LossRegFunction(loss_type=self.action_param.loss_type)

            crop_layer = CropLayer(border=self.regression_param.loss_border)
            # weight_map = data_dict.get('weight', None)
            # weight_map = None if weight_map is None else crop_layer(weight_map)
            # data_loss = loss_func(
            #     prediction=crop_layer(net_out),
            #     ground_truth=crop_layer(data_dict['output']),
            #     weight_map=weight_map)
            # reg_losses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
            # if self.net_param.decay > 0.0 and reg_losses:
            #     reg_loss = tf.reduce_mean(
            #         [tf.reduce_mean(reg_loss) for reg_loss in reg_losses])
            #     loss = data_loss + reg_loss
            # else:
            #     loss = data_loss
            data_loss = None
            loss = None

            # Get all vars
            to_optimise = tf.trainable_variables()
            vars_to_freeze = \
                self.action_param.vars_to_freeze or \
                self.action_param.vars_to_restore
            if vars_to_freeze:
                import re
                var_regex = re.compile(vars_to_freeze)
                # Only optimise vars that are not frozen
                to_optimise = \
                    [v for v in to_optimise if not var_regex.search(v.name)]
                tf.logging.info(
                    "Optimizing %d out of %d trainable variables, "
                    "the other variables are fixed (--vars_to_freeze %s)",
                    len(to_optimise),
                    len(tf.trainable_variables()),
                    vars_to_freeze)

            grads = self.optimiser.compute_gradients(
                loss, var_list=to_optimise, colocate_gradients_with_ops=True)
            # collecting gradients variables
            gradients_collector.add_to_collection([grads])
            # collecting output variables
            outputs_collector.add_to_collection(
                var=data_loss, name='loss',
                average_over_devices=False, collection=CONSOLE)
            outputs_collector.add_to_collection(
                var=data_loss, name='loss',
                average_over_devices=True, summary_type='scalar',
                collection=TF_SUMMARIES)
        elif self.is_inference:
            data_dict = switch_sampler(for_training=False)
            image = tf.cast(data_dict['image'], tf.float32)
            net_args = {'is_training': self.is_training,
                        'keep_prob': self.net_param.keep_prob}
            net_out = self.net(image, **net_args)
            net_out = PostProcessingLayer('IDENTITY')(net_out)

            outputs_collector.add_to_collection(
                var=net_out, name='window',
                average_over_devices=False, collection=NETWORK_OUTPUT)
            outputs_collector.add_to_collection(
                var=data_dict['image_location'], name='location',
                average_over_devices=False, collection=NETWORK_OUTPUT)
            self.initialise_aggregator()

    def interpret_output(self, batch_output):
        if self.is_inference:
            return self.output_decoder.decode_batch(
                batch_output['window'], batch_output['location'])
        return True

    def initialise_evaluator(self, eval_param):
        self.eval_param = eval_param
        self.evaluator = RegressionEvaluator(self.readers[0],
                                             self.regression_param,
                                             eval_param)

    def add_inferred_output(self, data_param, task_param):
        return self.add_inferred_output_like(data_param, task_param, 'output')