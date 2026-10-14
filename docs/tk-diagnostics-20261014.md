# Windows Tk 关闭诊断分类（2026-10-14）

基于已回传最终全套日志静态分类，没有重跑Windows，没有修改代码或隐藏日志。精确行号与源日志SHA见同名JSON。

65条invalid command name：lambda 28、_poll_processing 15、poll 15、append 5、_poll_api 1、_save_preferences 1。两种呈现：36条直接after脚本错误；29条bgerror再次因application has been destroyed失败。29条bgerror是这些事件的伴随处理失败，不能再加成94个独立事件。

含义：Tcl定时器触发时，其Python注册命令已经不可用。它与窗口销毁/清理顺序一致，但静态日志没有每次调度的拥有者、test node ID或调用栈，不能据函数名确定全部根因，更不能直接认定无害。全套pytest仍1028通过/2跳过；独立截图探针0回调异常；两者不矛盾。

候选定位点：app.py初始化及_poll_processing持续after调度、append日志投递、偏好设置延时保存；update_ui.py的poll；ai_review_ui.py的_poll_api。泛称poll及lambda不唯一，不能把所有事件归给某一个模块。App._close已有取消Tcl after scheduling再destroy的逻辑，直接root.destroy的夹具可能绕过它，也可能有异步完成后重新调度；这些均是待验证假设。

最小本地复现实验：保留原正常关闭测试，临时记录每次after的timer ID、widget/解释器、callback名、测试node与关闭时after info；比较正常_close和测试直接destroy路径。先处理有明确名称的_poll_processing/poll，再追踪lambda。对复现做有针对性修复，运行对应测试及整套并保留stderr计数。不得通过过滤stderr、吞异常或删除断言达成“零诊断”。

验收：窗口关闭后无遗留计时器访问已销毁解释器；关闭/取消期间设置和任务状态正确；无挂起线程/额外付费请求。仅更改测试清理方式时，明确其不等同修复真实应用退出路径。当前状态为已分类、待真实平台定位，没有声称已修复。
