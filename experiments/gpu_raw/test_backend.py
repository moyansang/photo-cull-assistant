import cv2
import numpy as np
import pytest

from backend import Developer, orient


@pytest.fixture(scope='module')
def gpu(tmp_path_factory):
    pytest.importorskip('pyopencl')
    backend = Developer(tmp_path_factory.mktemp('cl-cache'))
    yield backend
    backend.close()


def reference(raw, pattern, params):
    h,w = raw.shape
    idx = np.tile(pattern.reshape(2,2), ((h+1)//2,(w+1)//2))[:h,:w]
    m = np.clip((raw.astype(np.float32)-params[idx])*params[4+idx],0,1)
    g = np.array([[0,0,-1,0,0],[0,0,2,0,0],[-1,2,4,2,-1],
                  [0,0,2,0,0],[0,0,-1,0,0]],np.float32)/8
    hor = np.array([[0,0,.5,0,0],[0,-1,0,-1,0],[-1,4,5,4,-1],
                    [0,-1,0,-1,0],[0,0,.5,0,0]],np.float32)/8
    opp = np.array([[0,0,-1.5,0,0],[0,2,0,2,0],[-1.5,0,6,0,-1.5],
                    [0,2,0,2,0],[0,0,-1.5,0,0]],np.float32)/8
    fg,fh,fv,fo = [cv2.filter2D(m,-1,k,borderType=cv2.BORDER_REFLECT_101) for k in (g,hor,hor.T,opp)]
    channels = np.zeros((h,w,3),np.float32)
    for y in range(h):
        for x in range(w):
            c = idx[y,x]
            if c == 0: channels[y,x] = [m[y,x],fg[y,x],fo[y,x]]
            elif c == 2: channels[y,x] = [fo[y,x],fg[y,x],m[y,x]]
            elif pattern.reshape(2,2)[y%2,(x+1)%2] == 0:
                channels[y,x] = [fh[y,x],m[y,x],fv[y,x]]
            else: channels[y,x] = [fv[y,x],m[y,x],fh[y,x]]
    linear = np.clip(np.clip(channels,0,1) @ params[8:].reshape(3,3).T,0,1)
    encoded = np.where(linear<.01805397,4.5*linear,1.09929683*linear**.45-.09929683)
    return np.rint(encoded*255).clip(0,255).astype(np.uint8)


@pytest.mark.parametrize('pattern', [[0,1,3,2],[2,3,1,0],[1,0,2,3],[3,2,0,1]])
def test_gpu_against_independent_filter_reference(gpu,pattern):
    raw = np.random.default_rng(123).integers(0,4095,(19,23),dtype=np.uint16)
    params = np.r_[np.ones(4)*64,[1,1,1,1]/np.float64(4031),np.eye(3).ravel()].astype(np.float32)
    actual,timing = gpu.run(raw,np.array(pattern),params)
    expected = reference(raw,np.array(pattern),params)
    assert np.abs(actual.astype(int)-expected.astype(int)).max() <= 1
    count = gpu.allocations
    again,_ = gpu.run(raw,np.array(pattern),params)
    assert gpu.allocations == count and np.array_equal(actual,again)
    assert timing['develop_seconds'] > 0


@pytest.mark.parametrize('flip,k',[(0,0),(3,2),(5,1),(6,3)])
def test_orientation(flip,k):
    pixels=np.arange(18,dtype=np.uint8).reshape(2,3,3)
    assert np.array_equal(orient(pixels,flip),np.rot90(pixels,k))


def test_invalid_parameters_do_not_launch_gpu(gpu):
    with pytest.raises(ValueError):
        gpu.run(np.zeros((2,2),np.uint16),np.array([0,1,3,2]),np.zeros(17))
    with pytest.raises(ValueError):
        orient(np.zeros((4,4,3),np.uint8),7)
