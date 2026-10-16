# 快速开始

先执行 `python aster_harness.py --help` 查看参数。仓库附带 examples/task.json 与合成概率示例，完整闭环为 freeze → run → review。任务在独立目录执行，保留 task、snapshot、state、exit、stdout 与 review 等回执；具体输出文件名以运行器为准。

```bash
python -m unittest discover -s tests -v
python aster_harness.py freeze examples/task.json runs/demo
python aster_harness.py run runs/demo
python aster_harness.py review runs/demo
python tools/spec_manager/scripts/spec_manager.py init --root examples/spec_project --name aster-demo
python tools/spec_manager/scripts/spec_manager.py check --root examples/spec_project
```

Spec 初始化不等于需求确认。将 requirements.json 中目标、验收条件和证据填入后，再 refresh 派生状态。运行器仅执行你明确选择的命令，因此先阅读 task.json 与源代码，再运行自己的任务。

每次重试使用新的run目录，成功、失败和超时的原始回执均保留。review_csv可以独立核查后来提供的概率文件，review.json记录其SHA；这个摘要指向实际复核的文件，不自动证明它等于原执行输出。运行器不是安全沙箱，只运行自己信任的任务代码。
