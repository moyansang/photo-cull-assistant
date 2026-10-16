"""Actual pose inverse mapping with synthetic network outputs; no model accuracy claim."""
import pytest
import cv2
import numpy as np
from ai_cull_assistant import body_focus
class FakeNet:
 def __init__(self,point):self.point=point
 def setInput(self,x):pass
 def getUnconnectedOutLayersNames(self):return []
 def forward(self,*args):
  p=np.zeros((39,5),np.float32);p[:,:2]=self.point;p[:,3:]=10
  return [p.reshape(1,-1),np.array([[.99]],np.float32),np.ones((1,256,256),np.float32)]
def run(module,angle):
 hip=np.array([100.,100.],np.float32);radians=np.deg2rad(angle);full=hip+80*np.array([np.sin(radians),-np.cos(radians)],np.float32)
 person=np.r_[np.array([70.,20.,130.,80.]),hip,full,np.zeros(4),.9].astype(np.float32)
 radius=float(np.linalg.norm(hip-full));left,top=np.floor(hip-radius).astype(int);right,bottom=np.ceil(hip+radius).astype(int)
 local_hip=hip-[left,top];local_full=full-[left,top]
 radians=np.pi/2-np.arctan2(-(local_full[1]-local_hip[1]),local_full[0]-local_hip[0]);a=float(np.rad2deg(radians-2*np.pi*np.floor((radians+np.pi)/(2*np.pi))))
 rotation=cv2.getRotationMatrix2D(tuple(local_hip),a,1)
 expected=np.array([120.,110.]);local=expected-[left,top];transformed=np.r_[local,1]@rotation.T
 model_point=transformed/np.array([right-left,bottom-top])*256
 estimator=module._PoseEstimator.__new__(module._PoseEstimator);estimator.confidence=.5;estimator.net=FakeNet(model_point)
 result=estimator.infer(np.zeros((240,240,3),np.uint8),person)
 return float(np.linalg.norm(result['landmarks'][0,:2]-expected))

@pytest.mark.parametrize("angle",[0,30,-30,60,-60,90,-90])
def test_pose_inverse_rotation_roundtrip(angle):
    assert run(body_focus,angle)<1e-4
