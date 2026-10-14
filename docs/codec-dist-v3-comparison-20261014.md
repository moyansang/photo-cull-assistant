# HEIF Windows dist 与既有 v3 来源证据对照

日期：2026-10-14。仅核验已有文字/哈希材料，未下载新的第三方软件，未安装、加载DLL、修改生产代码或发布。证据包13,543字节，SHA256 eac92fc1eb793cd5d846b884c7bba137f2619c45c9b761efadcd56294f788b48。

## 已关闭的缺口

本地只读采集记录六个native文件在隔离dist、安装wheel、wheel RECORD和构建manifest中一致。本次独立解码RECORD的URL-safe base64 SHA256并逐项比较，与JSON、文件大小和manifest六项均一致；两份实际license文本及哈希也匹配。云端收到的是采集证据，不是DLL本体，未另行直接对DLL计算hash。

其中四个文件的完整SHA256进一步与既有HEIF v3的package-to-wheel proof逐字相等：

- libde265：d7499c1efed7cdf5bb99ab535de4b2d853015df42f8ecc496e720fad1e29c5c1
- libgcc：df510fab08619449d9e57500f0e0a4d3284fd43aa60ce6301b372518f721665f
- libstdc++：4e9d25ae95db62d51804096544a1bd8fdc4a424990b706c3c4167fc8c5054f79
- libwinpthread：1be1ab2940d5d04137beed0ce27c7f8a2461fb783453d3ed26720802a6249341

因此四个最终dist DLL对应此前已静态关联的MSYS2发行包：libde2651.1.1-1、gcc-libs16.1.0-5、libwinpthread14.0.0.r59.g93753750c-1。无需再把这四项描述为最终二进制身份未知。历史proof不等于从源码重编译的可复现构建证明。

libheif SHA256为00eca3b84743121b1eff89a0538fb88b396ff10b452605782f2c31897d34b491；_pi_heif.pyd为d45944eeaaa17fd139c9a64ed6d45501534d3b439fa70d325b2f5b8bc7b50413。这两项在本地三组记录一致，但现有v3补充包没有其旧独立hash条目，不能把“四项v3匹配”写成“六项v3匹配”。pi-heif元数据版本确认为1.4.0；实际libheif1.23.0的版本说明来自既有Windows报告，本次未解析DLL或调用其API复查。

## 真实捆绑notice缺口

实际LICENSE.txt是pi-heif自身BSD文本。LICENSES_bundled.txt仅572字节，SHA256 8155948291bc988b17fa600308e359be32111c1a22aef1dab7b330f17b8106d3；声明二进制wheel为LGPLv3，但源码链接仍写libheif v1.18.1、libde265 v1.0.15，与已记录实际版本不对应。该文件也没有列出libgcc/libstdc++/winpthread，内容不是完整LGPL/GPL例外/Winpthreads许可文本。

这是已确认的两份文件内容，不代表整个1218文件构建没有任何其他notice。本次收到的是选定条目，未穷尽全包所有许可证文本，不能直接作全包合规或违规结论。

## 发布前仍需补齐

1. 在保留上游原notice的同时，准备准确的组件补充表、版权/许可全文和实际版本对应源码说明；不可只把旧版本号替换掉就宣称完备。
2. 原v3报告记载取得过源码归档、配方和补丁，但补充ZIP只含报告/JSON/校验脚本；当前恢复资料没有那些源码材料本体。需从仍存在的授权副本取得，或另行按允许方式获取后核验既有hash，形成可交付的对应源码候选集，含相关包装/修改脚本及构建说明。当前任务没有新增下载。
3. 补回libheif及pi-heif构建来源的既有原始审计记录，和本次两个完整hash对应；仅wheel名称/版本不能替代这一核对。
4. 冻结程序正常HEIF加载已验；兼容修改版动态库替换/调试权利的实际可行性仍未验。真实干净机器独立部署也不能由当前已装环境加载直接推定。
5. 决定源码提供/获取方式，复核应用条款与依赖许可义务以及独立专利和维护问题。这里是工程证据整理，没有法律合规批准，也没有对外发布授权。

现状态：四运行库最终字节对应关闭；六native文件构建一致性关闭；源码/notice交付、两项原始审计对应、替换能力及发布决定仍开放。无需重跑全套图像测试来代替这些材料工作。
