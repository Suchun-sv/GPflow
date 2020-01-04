# Copyright 2016 James Hensman, Mark van der Wilk, Valentine Svensson, alexggmatthews, fujiisoup
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import warnings
from typing import Optional, Tuple, TypeVar

import numpy as np
import tensorflow as tf

from ..base import Module
from ..config import default_float, default_jitter
from ..kernels import Kernel
from ..likelihoods import Likelihood
from ..inducing_variables import InducingVariables
from ..mean_functions import MeanFunction, Zero
from ..conditionals import conditional
from ..utilities import ops
from .util import inducingpoint_wrapper

Data = TypeVar('Data', Tuple[tf.Tensor, tf.Tensor], tf.Tensor)
DataPoint = tf.Tensor
MeanAndVariance = Tuple[tf.Tensor, tf.Tensor]


class BayesianModel(Module):
    """ Bayesian model. """

    def neg_log_marginal_likelihood(self, *args, **kwargs) -> tf.Tensor:
        msg = "`BayesianModel.neg_log_marginal_likelihood` is deprecated and " \
              " and will be removed in a future release. Please update your code " \
              " to use `BayesianModel.log_marginal_likelihood`."
        warnings.warn(msg, category=DeprecationWarning)
        return - self.log_marginal_likelihood(*args, **kwargs)

    def log_marginal_likelihood(self, *args, **kwargs) -> tf.Tensor:
        return self.log_likelihood(*args, **kwargs) + self.log_prior()

    def log_prior(self) -> tf.Tensor:
        log_priors = [p.log_prior() for p in self.trainable_parameters]
        if log_priors:
            return tf.add_n(log_priors)
        else:
            return tf.convert_to_tensor(0., dtype=default_float())

    @abc.abstractmethod
    def log_likelihood(self, *args, **kwargs) -> tf.Tensor:
        raise NotImplementedError



class GPPosterior(object):
    """

     TODO 

    These predictions are then pushed through the likelihood to obtain means
    and variances of held out data, self.predict_y.

    The predictions can also be used to compute the (log) density of held-out
    data via self.predict_log_density.
    """

    def __init__(self,
                 mean_function: MeanFunction, 
                 kernel: Kernel, 
                 inducing_variable: InducingVariables,
                 likelihood: Likelihood, 
                 whiten: bool,
                 mean: np.ndarray,
                 variance_sqrt: np.ndarray) -> None:
        self.mean_function = mean_function
        self.kernel = kernel
        self.inducing_variable = inducingpoint_wrapper(inducing_variable)
        self.likelihood = likelihood
        self.whiten = whiten
        self.mean = tf.constant(mean)
        self.variance_sqrt = tf.constant(variance_sqrt)

        # TODO: assert shapes?

    def predict_f(self, X_new: DataPoint, full_cov: bool = False,
                  full_output_cov: bool = False) -> MeanAndVariance:
        mu, var = conditional(X_new,
                              self.inducing_variable,
                              self.kernel,
                              self.mean,
                              q_sqrt=self.variance_sqrt,
                              white=self.whiten,
                              full_cov=full_cov,
                              full_output_cov=full_output_cov)

        return mu + self.mean_function(X_new), var

    def predict_f_samples(self,
                          predict_at: DataPoint,
                          num_samples: int = 1,
                          full_cov: bool = True,
                          full_output_cov: bool = False) -> tf.Tensor:
        """
        Produce samples from the posterior latent function(s) at the input points.
        """
        mu, var = self.predict_f(predict_at, full_cov=full_cov)  # [N, P], [P, N, N]
        num_latent = var.shape[0]
        num_elems = var.shape[1]
        var_jitter = ops.add_to_diagonal(var, default_jitter())
        L = tf.linalg.cholesky(var_jitter)  # [P, N, N]
        V = tf.random.normal([num_latent, num_elems, num_samples], dtype=mu.dtype)  # [P, N, S]
        LV = L @ V  # [P, N, S]
        mu_t = tf.linalg.adjoint(mu)  # [P, N]
        return tf.transpose(mu_t[..., np.newaxis] + LV)  # [S, N, P]

    def predict_y(self, predict_at: DataPoint, full_cov: bool = False,
                  full_output_cov: bool = False) -> MeanAndVariance:
        """
        Compute the mean and variance of the held-out data at the input points.
        """
        f_mean, f_var = self.predict_f(predict_at, full_cov=full_cov, full_output_cov=full_output_cov)
        return self.likelihood.predict_mean_and_var(f_mean, f_var)

    def predict_log_density(self, data: Data, full_cov: bool = False, full_output_cov: bool = False):
        """
        Compute the log density of the data at the new data points.
        """
        x, y = data
        f_mean, f_var = self.predict_f(x, full_cov=full_cov, full_output_cov=full_output_cov)
        return self.likelihood.predict_density(f_mean, f_var, y)


class GPModel(BayesianModel):
    r"""
    A base class for Gaussian process models, that is, those of the form

    .. math::
       :nowrap:

       \\begin{align}
       \\theta & \sim p(\\theta) \\\\
       f       & \sim \\mathcal{GP}(m(x), k(x, x'; \\theta)) \\\\
       f_i       & = f(x_i) \\\\
       y_i\,|\,f_i     & \sim p(y_i|f_i)
       \\end{align}


    """

    def __init__(self,
                 kernel: Kernel,
                 likelihood: Likelihood,
                 mean_function: Optional[MeanFunction] = None
                 ) -> None:

        super().__init__()
        if mean_function is None:
            mean_function = Zero()

        self.mean_function = mean_function
        self.kernel = kernel
        self.likelihood = likelihood

    def get_posterior(self, data: Data) -> GPPosterior:
        raise(NotImplementedError)


