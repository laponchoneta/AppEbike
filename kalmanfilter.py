import numpy as np
from filterpy.kalman import KalmanFilter
from filterpy.common import Q_discrete_white_noise

def KalmanWrapper(*args) -> object:
    f = KalmanFilter(dim_x=1, dim_z=1)
    f.x = np.array([0., 1.]) # initial values
    f.F = np.array([[1., 1.],
                    [0., 1.]]) #transition state matrix
    f.H = np.array([1.]) # measurement function
    f.P *= 100 # covariance matrix
    f.R = 5 #noise
    f.Q = Q_discrete_white_noise(dim=1, dt=1., var=0.5) # process noise
   
    return f

