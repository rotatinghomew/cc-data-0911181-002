# 重叠服务圈下服务

规划服务提供居民组的最近教学点、按时段和出行条件筛选的可达课程以及容量目录。

## 运行环境

使用 Python 3.11，仅依赖标准库，无外部服务、账号或网络数据。
示例数据使用虚构标识和本地合成记录，不涉及真实学员。

```bash
python3 -m app check
python3 -m app summary
python3 -m app serve --host 127.0.0.1 --port 8080
```

服务提供只读 JSON 查询：`/api/reach`, `/api/capacity`, `/api/accessibility`, `/api/proposal-comparison`。
`/health` 显示存活及表计数，`/api/records?table=表名` 可查看已知数据表，
`/api/comparison-basis?basis=编号` 可取回已落库的比较依据。
详细字段语义、计数单位和当前能力见 `docs/数据契约.md`。

## 扩点可及性分析

`/api/accessibility` 与 `/api/proposal-comparison` 面向扩点讨论，区分“能到达学校”和“在本人可参加时间有合适课程”：

- 资格同时受按 mobility 区分的出行上限、可参加时段、共享课程余位约束；共享余位用居民组—课程二部网络的最小费用最大流测算，输出的是可服务人数能力边界，不做一次确定的报名分配；
- 未服务原因分为无可达学校、可达学校无课程、时段不合、余位不足四类；输出村级与 mobility 分组的服务率和地区差距；
- 人口与出行耗时按调查区间在 3×3 情景格点上比较，逐格排名并标记排序是否随合理取值翻转，不输出看似精确的固定排名；
- 同一居民组被多个点覆盖只计一次；新增班只承接原有可服务人群时计入“换地方”而非新增服务；重叠覆盖的重复计数在合并情景中扣除。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests smoke.py
python3 smoke.py
```

测试从实际数据装载进入领域计算、报告与真实 HTTP 服务，并用临时副本验证错误输入。
冒烟程序动态分配本地端口，启动服务后读取全部业务查询并原样输出 JSON，随后正常关闭。
查询不写回数据，因此可以直接重复运行；验证不会访问项目外文件。

## 容器

```bash
docker build -t access-equity:local .
docker run --rm --network none access-equity:local
docker run --rm --network none access-equity:local python3 smoke.py
```

镜像只封装本应用，测试使用回环网络，不依赖宿主端口。
无第三方包依赖，`requirements.lock` 记录此状态。

## 业务办理

扩点办理按“生成比较依据 → 提名 → 补助审批 → 资源启用”串联，提名不占资源。补助审批必须有比较依据支撑的新增服务或地区改善证据，数据或拟投范围变化后旧依据失效即阻止沿用，同一批居民组不得重复申请补助。

业务操作以 JSON 对象 POST 到 `/api/workflow/操作名`：

- `planning.compare`：按拟投范围生成并落库比较依据，返回 `basis` 编号
- `planning.nominate`：提名并绑定比较依据、受影响人群与资源（入参 `nomination, proposal, resource, basis`）
- `funding.approve`：校验依据有效性、证据与重复受益后按额度审批
- `resources.activate`：占用实际资源启用站点，资源承诺与拨付对象一致

业务贯通用例见 `tests/test_workflows.py`，其中为可直接运行的本地调用。默认状态存于内存；需要重启后保留时，可运行 `python3 -m app serve --state /tmp/learning-service.sqlite3`。状态库只保留本机操作，不向外部发送数据。
