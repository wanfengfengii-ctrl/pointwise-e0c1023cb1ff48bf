# 月结更正交割

站库、中心日仓的入账与历史接口见 `README.md`。月结仓使用独立 SQLite 文件。同一经营日交割一次，可先发原单，之后追加一次更正。原账是已存在的冻结账。更正账是同日 `receiving.audit_intake.accept_correction` 签出的较晚账号。交割保存这两个账号及其当时账额。两库通过可信转交渠道递送消息，分别提交和重开。在月结侧，原单登载与封止谁先成功落定，谁决定原单的终局资格。更正办理可有中间回包。中心核认更正实际登载的首执后，交割才完成。

## 中心入口

通过 `receiving.store.open_center_store(path)` 打开。以下函数由 `accounts.publish` 导出，`conn` 是调用者负责关闭的连接。交割号沿用现有标识规则。

| 调用 | 结果 |
| --- | --- |
| `start_handoff(conn, handoff_id, original_account_id)` | 发起交割，返回可转交的原单。已有同参请求返回首次原单。 |
| `amend_handoff(conn, handoff_id, corrected_account_id)` | 追加更正，返回该更正的首件。原单是否已获执都可办理。同参重投返回首次更正件。 |
| `resume_handoff(conn, handoff_id, kind)` | 读取当前可以重投或继续转交的该路材料，`kind` 为 `original` 或 `correction`。某路未发起、中心已核认其登载或已获证其被封止时，该路返回 `None`。 |
| `acknowledge_handoff(conn, handoff_id, reply)` | 核认对应的月结回包并持久推进该交割，返回 `None`。旧回包合法重投不得回退状态。 |
| `get_handoff(conn, handoff_id)` | 返回交割字典。未知交割返回 `None`。 |

交割字典含 `handoff_id`、`day`、`original_account_id`、`corrected_account_id`、`status`、`original_receipt`、`correction_receipt`。无更正时 `corrected_account_id` 为 `None`，`status` 为 `original`。已发更正但尚未核认实际登载时为 `pending`，核认后为 `complete`。两个首执字段记录各自已核认的正式首执，未核认或相应分录未登载时为 `None`。中心可在更正完成后收到先前失答的合法原首执。

## 月结入口

由 `receiving.monthly` 导出 `open_month_store(path)`、`accept_month_message(conn, message)`、`get_month_receipt(conn, handoff_id, kind)` 和 `read_month_entries(conn, day)`。开库返回由调用者关闭的 SQLite 连接。收件返回应答字典。按交割号和 `original` 或 `correction` 查已登载首执，缺席返回 `None`。查日分录按登载顺序返回列表，每项用该分录的正式首执字典表示。

正式首执含正整数 `receipt_id`、`handoff_id`、`day`、`kind`、`original_account_id`、`corrected_account_id`、布尔值 `original_posted` 和整数 `liters`。`liters` 为本次实际登载的升数。原分录的 `corrected_account_id` 为 `None`，`original_posted` 为真，后来有更正也保持原样。更正分录的 `original_posted` 记录原单是否实际登载。正、负及零差额均为正式分录。

消息和应答是可 JSON 往返的字典。应答含 `status`，值为 `posted` 表示实际登载，并在 `receipt` 附对应首执。旧单已被封止时返回 `blocked`，该路始终没有成功首执。更正尚需续办的应答为 `pending`。其余传输字段由实现定义，可随应答带上中心核认所需的绑定证据。调用方将应答原样交回中心，并可用 `resume_handoff` 读取后续材料。必要材料最终送达后可以完成交割。旧单已登载时更正只登载差额，原分录及首执仍可领取。旧单先被封止时，哪怕更正尚待实际登载，旧单也不能再登载。成功接件的同件重投返回该件的原应答，已经登载的首执也可独立复领。

新入口遇未知必要绑定、同日换交割号、同号换账、跨日错件、材料无效或应答与已发材料不符时抛 `ValueError`。仅凭金额相等不能核认账号或首执。双方提交失败均须向调用者报错并保持上次成功状态，未成功提交的裁决、分录、首执或中心进度均不可见。可信转交可丢失应答、迟送或复制消息，双方不访问对方库文件。范围为单个中心与一个月结仓，每经营日一次更正，不涉及跨日转记。

## 夹具与交付

`python3 -B handoff_fixture.py` 通过 V1 入口生成和读取冻结账。测试可在空目录调用 `handoff_fixture.prepare_case(directory, review_base=100)`，得到两个 `AccountSnapshot`，并可在 `directory/center.sqlite` 重开中心。站端原读数已退役且源库已关。默认两账为 14 L 和 105 L。`review_base=5` 则得到 14 L 和 10 L。`wire(message)` 复制转交字典。可通过暂存消息改变到达顺序，通过丢弃应答制造失答。`reject_commit(conn)` 在指定连接上拒绝 SQLite 提交，供可重跑场景使用。

原单先落定但中心丢失回包时，月结应保留 14 L 首执及 91 L 更正分录，中心补认后才完成。更正先成功封止时，即使应答未交回中心，迟到原单也不得登载，最终只见 105 L。若月结在成功裁决前提交失败，重开后可以由原单先登载，使更正按 91 L 续办。若更正账为 10 L，原单先落定后应追加 −4 L。用两库重开和重投核对原应答及首执，检查错件和改绑不改变已发布结果。

保留原测试，并在 `.pointwise-runtime.json` 的 `verify` 命令中运行原回归和交割核验，任何核验失败应返回非零。`demo` 展示两种到达顺序下的实际月结分录及失答补认后的中心状态。运行清单沿用现有 CLI 格式和 `setup`，`verify` 与 `demo` 仍用 argv 数组。
