# 青湾供水日仓

东、西泵站的值班员把带仪表标识和来源编号的用量观测送入站库。归档员向中心报送具名增量块，凭持久首执清理站端原读数。审计员用复核表核定替换底账。接班员凭账号领取固定日账，核对分站贡献和所用出处。

## 运行

使用 Python 3.11、标准库和 SQLite。项目工作目录中的 `check_project.py` 由运行环境提供。

```text
python3 -B check_project.py
python3 -B demo.py
```

演示使用独立临时库。东站按 1/2 换算，班报原值 `8.0`、`6/1` 得到 4、3 L。西站按 2/5 换算，`12.5`、`5` 得到 5、2 L。普通账为 14 L。各站冻结第 1 笔后签发 100 L 复核底账。已经确认的跨界块完成退役，关源后应用更正得到 105 L。随后收到已发行的西站第 3 笔 6 L，后续账号为 111 L。三个账号各保留自己的出处。

## Python 接口

1. `stations.store.open_station_store(path)` 和 `receiving.store.open_center_store(path)` 打开并初始化 SQLite 连接。调用者负责关闭连接。`initialize_station_store(conn)` 和 `initialize_center_store(conn)` 可在事务外重复运行，保留已有口径和业务状态。
2. `stations.register.register_station_day(conn, day, station, meter_id, scale)` 接受 `stations.units.MeterScale(numerator, denominator)`。`stations.import_rows.import_rows(conn, day, station, rows)` 接受有序 `Reading(meter_id, source_id, raw)`，返回 `StationEvent` 元组。经营日为 `YYYY-MM-DD`，站别为 `east` 或 `west`。标识使用非空、无首尾空白的可打印字符串。原值使用整数或标准库 `Fraction` 接受的数字字符串，精确换算成整数升。允许有符号净量和零量。来源号随站序保留。同配置登记可重做，冲突配置、空批、缺值、错仪表和非整升批次抛出 `ValueError`。`stations.journal.read_event_window(conn, day, station, first, last)` 读首尾均含的完整窗口，已退役行的 `raw` 为 `None`。
3. `shipping.pack.issue_block(conn, day, station, block_id, first, last)` 发行固定 `IncrementBlock`。证据含站序、整数升、仪表和来源编号，原始读值留在站库。请求同号同窗可复领，同号异窗和非法窗口抛出 `ValueError`。`shipping.evidence.canonical_block` 提供稳定的交接正文，`block_from_body` 还原交接对象。
4. `receiving.intake.accept_block(conn, block)` 提交实收、覆盖和首执后返回 `AcceptanceReceipt`。日、站、序号相同且金额及来源相同的重叠保留首份出处，补入其余位置。冲突重叠或同号异正文整件拒绝。`receiving.receipts.load_receipt(conn, kind, material_id)` 领取固定首执，未实收返回 `None`。`kind` 为 `block` 或 `correction`。
5. 源端从可信的中心交接渠道取得首执。`shipping.acknowledge.record_acknowledgement(conn, block_id, receipt)` 保存与已发行正文绑定的确认。`shipping.retire.retire_release(conn, block_id, receipt=None)` 可使用已有确认，也可在同一源事务中接收首执并退役。返回的 `Retirement` 固定记载窗口、首执编号和本次清理行数。缺失或不符的确认抛出 `ValueError`，原读数保持可用。固定发行和站序仍可领取。
6. `audit.cuts.freeze_station_cut(conn, day, station, cut_id)` 取本站已提交水位，空前缀为 0。`audit.reconcile.record_reconciliation(conn, reconciliation_id, day, east_cut_id, west_cut_id, base_liters, review_ref)` 绑定同日两站封点和复核依据。`audit.release.issue_correction(conn, notice_id, reconciliation_id)` 签发固定 `CorrectionNotice`。`audit.revisions.canonical_notice` 和 `notice_from_body` 用于传递正文。`receiving.audit_intake.accept_correction(conn, notice)` 在一次中心提交中实收并签出更正账，返回带 `account_id` 的首执。同参重投复用原首执及账号。首次接纳的有效通知成为该日当前底账。
7. `accounts.publish.publish_day(conn, day)` 从中心同一快照发布固定账号。无更正时底账为 0 L。应用更正后，每站只计严格大于本站封点的唯一实收净量，缺席站贡献为 0 L。覆盖内的迟到件可追溯，封点外的有效迟到件在后续发布时计入。`accounts.history.get_account(conn, account_id)` 返回固定 `AccountSnapshot`，含账版底账、分站值、总量和按站序排列的出处。未知账号抛出 `KeyError`。账版领取不依赖源库。

中心提交报错通过 `receiving.store.CenterCommitError` 报告，并保留原 SQLite 异常作为原因。校验冲突抛出 `ValueError`。读取未知的必需站日或已发行复核记录时报告 `KeyError`，可选查询以 `None` 表示不存在。`stage_*`、`append_events`、`clear_raw_readings`、`save_release`、`store_new_coverage` 和 `save_account_provenance` 是供业务模块协作的事务内写入入口，由调用方负责所属事务。普通发布和换账的账务出处都取自中心实收事实。

完整回归覆盖导入、精确换算、重投、重叠、退役、晚到更正、接纳及发布回滚、重开和历史核账。演示及回归使用各自的临时目录。跨经营日冲销、多中心复制及连续多轮更正的合并审批在当前业务范围之外。
