from types import SimpleNamespace

from ai_cull_assistant import update_ui


class DeferredThread:
    created=[]

    def __init__(self, target, daemon):
        self.target=target
        self.daemon=daemon
        self.created.append(self)

    def start(self):
        pass


class FakeApp:
    def __init__(self, settings_dir):
        self.settings_dir=settings_dir
        self._processing_busy=False
        self.no_updates_var=SimpleNamespace(get=lambda:False)
        self.update_busy_calls=[]
        self.restored=0
        self.logs=[]
        self.scheduled=[]

    def after(self, delay, callback):
        self.scheduled.append((delay,callback))

    def _set_update_busy(self, busy):
        self.update_busy_calls.append(busy)

    def _review_busy(self):
        return False

    def _restore_scan_progress(self):
        self.restored+=1

    def _log(self, value):
        self.logs.append(value)

    def winfo_children(self):
        return []


def test_background_check_does_not_block_main_controls(tmp_path, monkeypatch):
    DeferredThread.created=[]
    monkeypatch.setattr(update_ui.threading,'Thread',DeferredThread)
    app=FakeApp(tmp_path)
    controller=update_ui.UpdateController(app)

    controller.check(manual=True)

    assert controller.checking
    assert not controller.busy
    assert app.update_busy_calls==[]
    app._processing_busy=True  # A scan can begin while the network check is pending.
    assert len(DeferredThread.created)==1


def test_check_then_processing_race_still_blocks_download(tmp_path, monkeypatch):
    DeferredThread.created=[]
    monkeypatch.setattr(update_ui.threading,'Thread',DeferredThread)
    monkeypatch.setattr(update_ui.updater,'latest_release',lambda *args,**kwargs:{
        'version':'v99.0.0','build':1,'url':'https://example.test/package.zip',
        'sha256':'a'*64,'size':20,
    })
    monkeypatch.setattr(update_ui.messagebox,'askyesno',lambda *args,**kwargs:True)
    notices=[]
    monkeypatch.setattr(update_ui.messagebox,'showinfo',lambda title,message,**kwargs:notices.append((title,message)))
    monkeypatch.setattr(update_ui.sys,'frozen',True,raising=False)
    app=FakeApp(tmp_path)
    controller=update_ui.UpdateController(app)

    controller.check(manual=True)
    app._processing_busy=True
    DeferredThread.created[0].target()
    controller.poll()

    assert not controller.checking
    assert not controller.busy
    assert app.update_busy_calls==[]
    assert app.restored==1
    assert notices and '请先完成扫描' in notices[-1][1]
