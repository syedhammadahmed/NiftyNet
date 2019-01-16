import numpy as np
import argparse
from pathlib import Path
from inference.validation_loss_analysis import analyse_validation_loss
from subprocess import call
import configparser
import os

def get_user_params():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config_path", help="Configuration path.")
    parser.add_argument("-e", "--evaluation_set", help="Enter T/V/I for training, validation "
                                                      "and inference (test) data to all be evaluated", default='I')
    parser.add_argument("-i", "--training_iter", help="The training iteration for the model. "
                                                     "Default is the largest present.", default=None)
    parser.add_argument("-n", "--n_evaluations", help="The number of evaluations to perform.", default=10)
    parser.add_argument("-o", "--output_prefix", help="The prefix for the output directories", default='output')
    parser.add_argument("-t", "--tasks", help="Tasks in results", default=1)
    parser.add_argument("-x", "--method", help='either multi, class or reg')

    return parser.parse_args()


def extract_model_directory(args):
    model_directory = Path([line.strip().split('=')[-1].lstrip() for line in
                           open(args.config_path, 'r') if 'model_dir' in line][0])
    return model_directory


def find_latest_checkpoint(args):
    model_directory = extract_model_directory(args)
    model_directory = model_directory / 'models'

    model_numbers = [str(x).split('-')[-1] for x in model_directory.glob('*ckpt*meta')]
    model_max_number = max([int(x.split('.')[0]) for x in model_numbers])

    return model_max_number


def calling_function(pyconda, multi_task_app, tmp_config):

    system_command = [pyconda, multi_task_app, 'inference', '-c', tmp_config]
    print(system_command)
    call(system_command)


if __name__ == "__main__":

    multi_task_app = '/scratch2/NOT_BACKED_UP/fbragman/DeepSyn/code/NiftyNet_github_fork/NiftyNet/net_multitask.py'
    reg_app = '/scratch2/NOT_BACKED_UP/fbragman/DeepSyn/code/NiftyNet_github_fork/NiftyNet/net_regress.py'
    class_app = '/scratch2/NOT_BACKED_UP/fbragman/DeepSyn/code/NiftyNet_github_fork/NiftyNet/net_classify.py'

    pyconda = '/home/fbragman/miniconda3/envs/tf_d/bin/python'

    args = get_user_params()
    extra_args = []

    config = configparser.RawConfigParser()
    config.read(args.config_path)

    print(args.config_path)

    if args.training_iter is None:
        # get best iteration from validation loss
        model_dir = config.get('SYSTEM', 'model_dir')
        log_path = model_dir
        ckpoint_path = os.path.join(model_dir, 'models')
        args.training_iter = analyse_validation_loss(log_path, ckpoint_path, int(args.tasks))
        print('Inference on {} iteration'.format(args.training_iter))

    # for formatting of number strings
    n_to_zfill = int(np.log10(int(args.n_evaluations)) + 1)

    if args.evaluation_set == 'T':
        eval_set = 'training'
    elif args.evaluation_set == 'V':
        eval_set = 'validation'
    elif args.evaluation_set == 'I':
        eval_set = 'inference'
    else:
        eval_set = 'inference'

    if not config.has_section('INFERENCE'):
        config.add_section('INFERENCE')

    config.set('INFERENCE', 'dataset_to_infer', eval_set)
    config.set('INFERENCE', 'inference_iter', args.training_iter)
    config.set('INFERENCE', 'border', (0, 0, 0))
    config.set('INFERENCE', 'output_interp_order', -1)
    config.set('INFERENCE', 'spatial_window_size', (200, 200))

    tmp_config = os.path.splitext(args.config_path)[0] + '_tmp.ini'
    print('CREATING TMP CONFIG FILE')
    with open(tmp_config, 'w') as pf:
        config.write(pf)

    root_output = os.path.join(args.output_prefix, eval_set)

    for i in range(int(args.n_evaluations)):
        output_dir = os.path.join(root_output, 'output_iter_' + str(i).zfill(n_to_zfill))

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(output_dir)
        else:
            continue

        print(output_dir)

        print('READING TMP CONFIG FILE')
        config = configparser.RawConfigParser()
        config.read(tmp_config)
        config.set('INFERENCE', 'save_seg_dir', output_dir)
        with open(tmp_config, 'w') as pf:
            print('SAVING TMP CONFIG FILE')
            config.write(pf)

        try:
            if args.method == 'multi':
                calling_function(pyconda, multi_task_app, tmp_config)
            elif args.method == 'class':
                calling_function(pyconda, class_app, tmp_config)
            else:
                calling_function(pyconda, reg_app, tmp_config)

        except:
            print('END OF FUNCTION CALL')
            continue
