// Experimental Malvar-He-Cutler (2004) 5x5 Bayer interpolation.
// Filter equations: see references and third-party notice in README.md.
__kernel void normalize_raw(__global const ushort *raw, __global float *mosaic,
                            __global const int *pattern, __global const float *p,
                            int w, int h) {
    int x=get_global_id(0), y=get_global_id(1);
    if(x>=w || y>=h) return;
    int c=pattern[(y&1)*2+(x&1)];
    mosaic[y*w+x]=clamp(((float)raw[y*w+x]-p[c])*p[4+c],0.0f,1.0f);
}

// Whole-sample reflection retains Bayer parity at the image borders.
int mirror(int a,int n) { if(a<0) return -a; if(a>=n) return 2*n-2-a; return a; }
float px(__global const float *m,int x,int y,int w,int h) {
    return m[mirror(y,h)*w+mirror(x,w)];
}
float encode(float x) {
    x=clamp(x,0.0f,1.0f);
    return x<0.01805397f ? 4.5f*x : 1.09929683f*pow(x,0.45f)-0.09929683f;
}
__kernel void develop(__global const float *m, __global uchar *out,
                      __global const int *pattern, __global const float *p,
                      int w,int h) {
    int x=get_global_id(0),y=get_global_id(1);
    if(x>=w || y>=h) return;
    float v=px(m,x,y,w,h);
    float lr=px(m,x-1,y,w,h)+px(m,x+1,y,w,h);
    float ud=px(m,x,y-1,w,h)+px(m,x,y+1,w,h);
    float lr2=px(m,x-2,y,w,h)+px(m,x+2,y,w,h);
    float ud2=px(m,x,y-2,w,h)+px(m,x,y+2,w,h);
    float diag=px(m,x-1,y-1,w,h)+px(m,x+1,y-1,w,h)
              +px(m,x-1,y+1,w,h)+px(m,x+1,y+1,w,h);
    float green=(4*v+2*(lr+ud)-lr2-ud2)/8;
    float horizontal=(5*v+4*lr-lr2+0.5f*ud2-diag)/8;
    float vertical=(5*v+4*ud-ud2+0.5f*lr2-diag)/8;
    float opposite=(6*v+2*diag-1.5f*(lr2+ud2))/8;
    int c=pattern[(y&1)*2+(x&1)];
    float3 rgb;
    if(c==0) rgb=(float3)(v,green,opposite);
    else if(c==2) rgb=(float3)(opposite,green,v);
    else if(pattern[(y&1)*2+((x+1)&1)]==0) rgb=(float3)(horizontal,v,vertical);
    else rgb=(float3)(vertical,v,horizontal);
    rgb=clamp(rgb,0.0f,1.0f);
    for(int channel=0;channel<3;channel++) {
        int j=8+3*channel;
        float val=dot(rgb,(float3)(p[j],p[j+1],p[j+2]));
        out[3*(y*w+x)+channel]=convert_uchar_sat_rte(255.0f*encode(val));
    }
}
