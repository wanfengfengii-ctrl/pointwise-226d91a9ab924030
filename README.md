# 试验中心测读交换卷

使用 Python 3.11 标准库和 SQLite。白班发行完整的 V1 交换卷，夜班闭站后仍可点收和核账。

```sh
python3 -B check_project.py
python3 -B demo.py
```

控制台物化项目时提供 `check_project.py`，它运行 `tests` 包的完整 unittest 套件。演示使用独立临时数据库和 `examples/workday.json`，退出时清理演示数据。演示在接纳前关闭并移除发行侧数据库。

## 一个工作日

1. 白班将 A 缺测、B 实测零和 C 的 20 半格随标定表导入草稿。
2. 质控冻结 R1，再提交 C 为 22 半格的勘误并冻结 R2。C 的原值仍是 20，两个版本的核定值分别为 10 和 11 格。
3. 夜班要求按 C、A、B 检验，白班固定这一工作单。
4. 刻录端在空介质写入完整工作单，回读一致后封卷。卷件携带 V1 封套和原介质字节。
5. 夜班离线验卷并登记正式实收。原卷重投复领同一首执。
6. 夜班固定班账。C 与 B 各计一件，A 保留出处，计数为 2，总量为 11 格。
7. 源端另冻 C 为 24 半格的新修订。质检重开接收库时仍可从旧账查到 R2 的原值 20、工作值 22、核定值 11 及原介质范围。

## Python 接口

用 `with Database(path) as db` 管理连接，`Database` 位于 `storage.py`。以该连接构造各职责的 Repository，并调用其 `initialize()`。研究库使用 `StudyRepository`，发行库使用 `OrderRepository` 和 `VolumeRepository`，接收库同时使用 `IntakeRepository` 和 `LedgerRepository`。重复初始化保留已有数据。父目录由调用者准备。

| 入口 | 输入与结果 |
| --- | --- |
| `study.imports.import_batch(study, batch_id, calibration, readings)` | 按输入顺序持久化 `OriginalReading(origin, value, unit)`，返回草稿。`CalibrationTable(version, target_unit, rules)` 接受 `CalibrationRule(unit, numerator, denominator)`。 |
| `study.revisions.freeze_revision(study, batch_id, revision_id, corrections=())` | 按批的原标定应用当版 `Correction(origin, value)`，返回不可变修订。`load_revision(study, batch_id, revision_id)` 复领固定版。 |
| `order.freeze.fix_order(orders, study, batch_id, revision_id, order_id, origins)` | 核对指定版的具名原位并按请求顺序冻结工作单。 |
| `writer.seal.write_volume(volumes, orders, order_id, media)` | 在空介质完整写入、回读、封卷并返回封卷号。`writer.media.FileMedia(path)` 支持追加、回读和同路径重开。 |
| `writer.seal.read_volume(volumes, seal_id)` | 领取带 `seal_id`、`envelope`、`payload` 的固定卷件。 |
| `intake.admission.accept_volume(intake, volume)` | 凭卷件登记或复领 `AcceptedVolume`，其中包含按选件序排列的实收项及首执。 |
| `ledger.posting.post_ledger(ledgers, shift, receipt_id, account)` | 从同一接收库的正式实收固定班账，返回固定账面数据和逐件出处。 |
| `ledger.history.read_ledger(ledgers, account)` | 按历史账号重载固定账面数据，并按原实收键解引用逐件出处。 |

批号、版号、原位等身份为非空 UTF-8 文本，按原样区分。同一批内原位唯一，修订按批号和版号标识。每次冻结以导入草稿为底稿，勘误只应用于此次冻结，重复勘误原位拒绝。显式 `None` 表示缺测，整数零是实测。按 `原工作值 × 分子 ÷ 分母` 计算整数核定值，有余数则整版拒绝。冻结值与班账总量以十进制文本持久化，班账使用精确整数运算。

## V1 卷格式与失败边界

卷件的封套为 UTF-8 JSON，含格式版本、封卷号、工作单号、批和修订、标定版本、目标单位、有序原位及逐件目录。按选件顺序连续写入独立编码的 UTF-8 JSON 正文。正文含原单位、目标单位、导入原值、工作值和核定值，值字段使用规范十进制字符串或 JSON null。正文不含原位，等值原件可有相同正文。目录用选件序号和具名原位区分原件，以实际字节的半开区间和 SHA-256 校验正文。非空区间相邻且覆盖全卷。空单的目录与介质均为空。

`storage.ValidationError` 表示输入或卷件不合约，`ConflictError` 表示固定身份被占用，`MissingError` 表示找不到固定对象。I/O 与 SQLite 异常保留原类别。不完整或未确认的刻录没有新封卷号。原介质字节保留，非空候选介质不能用于新一轮 V1 刻录。接纳端核实全部材料后才进入事务。相同封卷号与相同封套、介质字节复领原实收和首执，同号异件拒绝。班账号只能登记一次。历史账的固定来源键为实收标识及选件序号，后来收到同名原位也不会改变旧账。

## 断写续刻（V2）

`writer.seal` 另提供逐件续刻，与 V1 共用发行库和 `read_volume`：

| 入口 | 输入与结果 |
| --- | --- |
| `begin_volume(volumes, orders, volume_id, order_id, media)` | 在空介质登记 V2 卷并绑定冻结工作单，登记不追加介质。重开后同卷同单可再调用，换单拒绝。 |
| `append_record(volumes, volume_id, request_id, origin, media)` | 按冻结顺序写入或复领一件，返回该请求首次完整追加的半开字节区间。请求号在卷内绑定唯一原位，中断后重开保持绑定；已完成请求复领不再追加，未完成只能从卷尾补刻，断片原样保留。 |
| `seal(volumes, volume_id, media)` | 全部选件完整落盘后固定 V2 卷件并返回封卷号，同卷重投返回原号。 |

每次追加是一条完整记录：规范 JSON，内嵌格式标记、请求号、选件序号、原位和测读正文。一次 `media.append` 提交整条记录；中断只留下它的某个前缀。重开后以介质上的实际字节为落盘依据恢复进度，已有字节禁止覆盖、截断或搬移。V2 卷件沿用 `SealedVolume`，封套为 `version: 2` 的 UTF-8 JSON，登记卷号、工作单、批、修订、标定、目标单位、按序选件（含请求号、完整区间、SHA-256）及全部断片区间；`payload` 为介质完整原字节，包含断片。只把 V1 封套的 `version` 改成 2 不是 V2 卷件，仍按 `unsupported volume version` 拒收。

接纳端仅凭卷件核对：逐件区间指向的字节校验和一致、解码记录的请求号/序号/原位与封套一致、断片是对应请求完整记录的严格前缀、全部介质字节都有归属。正文相同的选件各自入账，物理出处不得互换。核验全部通过才进入原实收事务，缺件、错引或字节矛盾整卷拒收；`post_ledger`、`read_ledger` 沿用原入口，断片不占实收序号。

两端认定一次写入完成的事实：

- 刻录端：一次 `media.append` 返回的字节数等于该请求记录的完整长度；若回包或工作库确认丢失，则以重开后介质末尾仍保留的完整记录字节为准。首次完整落盘的区间即该请求的物理出处，此后不再改写。
- 接纳端：封套中该选件声明的完整区间指向的介质字节 SHA-256 与封套一致，且解码出的记录内嵌请求号、序号、原位与封套声明一致；该件之前的字节同样全部有归属。
