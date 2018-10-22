import numpy as np


def gumbel_softmax_decay(current_iter, r):
    """
    Annealing schedule used in Gumbel-Softmax paper
    Jang et al. Categorical Reparameterization with Gumbel Softmax ICLR 2017
    :param current_iter: current training iteration
    :param r: hyperparameter, suggested range {1e-5, 1e-4}
    :return: temperature
    """
    return np.max(0.5, np.exp(-r * current_iter))


def exponential_decay(current_iter, initial_lr, k):
    """
    Exponential decay: alpha = alpha_0 * exp(-k*t)
    Can be used for learning rate or other parameter dependent on iter
    :param current_iter:
    :param initial_lr:
    :param k:
    :return:
    """
    return initial_lr * np.exp(-k * current_iter)