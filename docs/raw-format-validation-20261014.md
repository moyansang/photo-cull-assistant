# RAW单样本补测（2026-10-14）

本轮没有修改生产源码或原有测试断言，没有上传原片；来源、型号、文件SHA、具体来源模式/尺寸/单次耗时见同名JSON。测试平台Linux，Python3.12/rawpy0.27.1/LibRaw0.22.1/Pillow12.3.0。执行时源码commit f36376dc37319ec80df17852f7366a604182ff1e。

## 既有两项恢复

CR2 RAW_CANON_40D_SRAW_V103.CR2、NEF iss030e122639.NEF：已有tests/test_image_codec_integration.py中两项通过，0失败/0跳过，9项未选；0.95秒。这是原有用例补跑，不是新增测试。CR2来自rawpy测试目录，维护方注明rawsamples.ch、CC BY-NC-SA4.0；NEF由NASA官方JPEG的RawFileName及相机/时间元数据关联，按非商业信息性解码研究使用并署名NASA，不暗示背书。来源研究记录单列，项目MIT许可不作为照片许可。

## 五个新增真实文件探针

均来自raw.pixls.us官方目录，每项标CC0-1.0并提供SHA256，下载后及测试后哈希匹配。每文件只测正常首读和强制完整RAW；下面两次模式均实际调用原应用load_display_image，不以改后缀合成文件代替。

|格式/相机|首读来源、尺寸、秒|完整RAW尺寸、秒|结果|
|---|---|---|---|
|.CR3 / Canon EOS R|raw_embedded_jpeg，6720×4480，0.212|6742×4498，0.773|两阶段通过，原片不变|
|.ARW / Sony ILCE-7M3|raw_embedded_jpeg，1616×1080，0.022|6024×4024，0.685|两阶段通过，原片不变|
|.RAF / Fujifilm X-T4|raw_embedded_jpeg，4416×2944，0.119|6246×4170，2.479|两阶段通过，原片不变|
|.DNG / Ricoh GR III|raw_embedded_jpeg，6000×4000，0.139|6020×4024，0.967|两阶段通过，原片不变|
|.ORF / OM System OM-1|raw_embedded_jpeg，3200×2400，0.045|5220×3912，1.029|两阶段通过，原片不变|

完整RAW五项均为raw_full_decode、RGB、full_resolution=true；首读均为raw_embedded_jpeg。嵌入JPEG即使尺寸很大也不冒充完整RAW证据。现有基本尺寸条件保留：完整结果宽/高均不小于预览短边；Canon sRAW不要求达到相机传感器标称像素。

## 能证明与不能证明

五文件共10次解码探针，与前两项pytest分开记录，不把它们相加到上一轮全套1037项总数。耗时只有本次顺序单次墙钟，解码缓存每阶段清空，但未清系统文件缓存；不是稳定性能基准或软件间对比。

结合已有Windows RW2用户样本，八个支持RAW扩展名目前均至少有一份跨轮次真实文件解码证据。平台与样本不同，不能写成“八种在Windows全通过”，更不代表所有相机、压缩/位深变体、方向、色彩/HDR/锐度正确性或坏文件容错全面覆盖。WindowsCR2/NEF及新五份尚无独立本轮结果。

后续应按用户实际机型/压缩方式补有界变体，不因扩展名各有一份就关闭全机型兼容门槛。普通PNG/BMP/TIFF/WebP/AVIF主要仍为合成/协议测试，生产导出变体不作全面保证。

来源：rawpy/test/README.txt、raw.pixls.us官方逐条目录、NASA Earth Observatory照片档案。相关原始来源记录留在验证证据包；原始照片不加入Git/源码交付。
